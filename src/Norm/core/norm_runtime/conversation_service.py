from __future__ import annotations

from .secret_redaction import redact

import hashlib
import json
import logging
import re
import time
import uuid
from difflib import SequenceMatcher
from dataclasses import dataclass

import psycopg

from .conversation_store import ConversationStore
from .file_tool_executor import FileToolExecutor
from .ollama_client import OllamaClient
from .models import StepResult, StepStatus, TaskPlan, TaskStep, utc_now
from .prompt_queue import PromptJob, RedisPromptQueue
from .protocol import PROTOCOL_VERSION, command_schema, normalize_command
from .resource_status import full_context_status, impaired_context_status, merge_resource_status


@dataclass(frozen=True)
class RouteDecision:
    thread_ids: tuple[str, ...]
    primary_thread_id: str | None
    confidence: float
    clarification_question: str | None = None
    created_thread: bool = False


class ConversationService:
    def __init__(
        self,
        store: ConversationStore,
        ollama: OllamaClient,
        *,
        routing_threshold: float = 0.58,
        candidate_threads: int = 12,
        recent_messages: int = 12,
        file_tools: FileToolExecutor | None = None,
        max_tool_rounds: int = 8,
        prompt_queue: RedisPromptQueue | None = None,
        coordinator=None,
        durable=None,
        wait_timeout_seconds: float = 86_400,
        persistent_instructions: list[str] | tuple[str, ...] = (),
        ingrained_details_enabled: bool = True,
        unresolved_test_limit: int = 3,
        unresolved_explore_every_tasks: int = 4,
        unresolved_delete_after_trials: int = 15,
        unresolved_delete_after_domains: int = 3,
    ) -> None:
        self.store = store
        self.ollama = ollama
        self.routing_threshold = routing_threshold
        self.candidate_threads = candidate_threads
        self.recent_message_limit = recent_messages
        self.file_tools = file_tools
        self.max_tool_rounds = max(1, int(max_tool_rounds))
        self.prompt_queue = prompt_queue
        self.coordinator = coordinator
        self.durable = durable
        self.wait_timeout_seconds = max(1.0, float(wait_timeout_seconds))
        self.persistent_instructions = tuple(str(v).strip() for v in persistent_instructions if str(v).strip())
        self.ingrained_details_enabled = bool(ingrained_details_enabled)
        self.unresolved_test_limit = max(1, min(int(unresolved_test_limit), 8))
        self.unresolved_explore_every_tasks = max(1, int(unresolved_explore_every_tasks))
        self.unresolved_delete_after_trials = max(1, int(unresolved_delete_after_trials))
        self.unresolved_delete_after_domains = max(1, int(unresolved_delete_after_domains))

    def _limit_human_reply(self, task_id: str, reply: str) -> str:
        storage = getattr(self.file_tools, "task_storage", None) if self.file_tools is not None else None
        limit = 384 * 1024
        workspace = None
        if storage is not None:
            try:
                limit = max(16_384, int(storage.config.get("human_output_bytes", limit)))
                workspace = storage.workspace_root
            except Exception:
                workspace = None
        encoded = str(reply).encode("utf-8")
        if len(encoded) <= limit:
            return str(reply)
        artifact = None
        if workspace is not None:
            try:
                outdir = workspace / "large-responses"
                outdir.mkdir(parents=True, exist_ok=True)
                artifact = outdir / f"{task_id}.md"
                tmp = artifact.with_name(f".{artifact.name}.tmp")
                tmp.write_text(str(reply), encoding="utf-8")
                tmp.replace(artifact)
            except Exception:
                logging.exception("Could not persist oversized human reply artifact task=%s", task_id)
                artifact = None
        notice = (
            "\n\n[Human-facing reply capped at 384 KiB. Full durable task summary remains in PostgreSQL"
            + (f" and was written to {artifact}.]" if artifact else ".]")
        )
        notice_bytes = notice.encode("utf-8")
        prefix_budget = max(0, limit - len(notice_bytes))
        prefix = encoded[:prefix_budget].decode("utf-8", errors="ignore")
        return prefix + notice

    def _persistent_instruction_text(self) -> str:
        return "\n".join(f"- {item}" for item in self.persistent_instructions)

    @staticmethod
    def _resume_state_key(thread_id: str) -> str:
        return f"suppressed_resume_pending:{thread_id}"

    def _best_suppressed_match(self, message: str) -> tuple[dict | None, float]:
        if self.durable is None or not hasattr(self.durable, "suppressed_tasks"):
            return None, 0.0
        candidates = self.durable.suppressed_tasks(limit=200)
        if not candidates:
            return None, 0.0
        query = message.lower()
        qtokens = set(re.findall(r"[a-z0-9_]+", query))
        scored = []
        for item in candidates:
            text = (str(item.get("title") or "") + " " + str(item.get("original_request") or "")).lower()
            ttokens = set(re.findall(r"[a-z0-9_]+", text))
            overlap = len(qtokens & ttokens) / max(1, len(qtokens))
            seq = SequenceMatcher(None, query[:1200], text[:2400]).ratio()
            scored.append((0.72 * overlap + 0.28 * seq, item))
        scored.sort(key=lambda pair: pair[0], reverse=True)
        return scored[0][1], float(scored[0][0])

    def _resume_suppressed_and_wait(self, task_id: str, *, thread_ids: list[str], primary_thread_id: str, user_message_id: str) -> tuple[str, str, dict]:
        payload = self.durable.suppression_payload(task_id)
        tree_items = payload.get("tasks") if isinstance(payload.get("tasks"), list) else None
        if tree_items is not None:
            raw_jobs = []
            member_ids = []
            for task_item in tree_items:
                if not isinstance(task_item, dict):
                    continue
                member_id = str(task_item.get("task_id") or "").strip()
                if member_id:
                    member_ids.append(member_id)
                queue_payload = task_item.get("queue") if isinstance(task_item.get("queue"), dict) else {}
                raw_jobs.extend(item for item in list(queue_payload.get("jobs") or []) if isinstance(item, dict))
        else:
            raw_jobs = [item for item in list(payload.get("jobs") or []) if isinstance(item, dict)]
            member_ids = [task_id]
        if not raw_jobs:
            return task_id, "I found the suppressed task, but it has no captured executable queue state to resume safely.", full_context_status()
        for item in raw_jobs:
            job = item.get("job") if isinstance(item, dict) else None
            if not isinstance(job, dict):
                continue
            metadata = job.get("metadata") if isinstance(job.get("metadata"), dict) else {}
            metadata["conversation"] = {"thread_ids": thread_ids, "primary_thread_id": primary_thread_id, "user_message_id": user_message_id}
            metadata["resumed_from_suppressed"] = True
            job["metadata"] = metadata

        if tree_items is not None and hasattr(self.durable, "resume_suppressed_tree"):
            self.durable.resume_suppressed_tree(task_id, member_ids)
        else:
            self.durable.resume_suppressed_task(task_id)
        # A manual resume starts a fresh large-source processing allowance while
        # preserving lifetime byte accounting and the same task identity.
        try:
            storage = getattr(self.file_tools, "task_storage", None) if self.file_tools is not None else None
            if storage is not None:
                reset_ids = member_ids if member_ids else [task_id]
                for reset_id in reset_ids:
                    storage.bind(reset_id, None)
                    storage.reset_pass()
        except Exception:
            logging.exception("Could not reset task processing-pass counter task=%s", task_id)
        try:
            restored = self.prompt_queue.restore_task_jobs(payload)
            if not restored:
                raise RuntimeError("suppressed task had no restorable jobs")
        except Exception:
            reason = "Resume enqueue failed; task was safely re-suppressed."
            if tree_items is not None and hasattr(self.durable, "suppress_task_tree"):
                self.durable.suppress_task_tree(task_id, member_ids, reason, payload)
                for member_id in member_ids:
                    self.prompt_queue.cleanup_task(member_id)
            else:
                self.durable.suppress_task(task_id, reason, payload)
                self.prompt_queue.cleanup_task(task_id)
            raise
        deadline = time.monotonic() + self.wait_timeout_seconds
        while time.monotonic() < deadline:
            status = self.durable.task_status(task_id)
            if status == "completed":
                return task_id, self.durable.latest_summary(task_id) or "Resumed task completed.", full_context_status()
            if status in {"failed", "cancelled"}:
                return task_id, self.durable.latest_summary(task_id) or f"Resumed task ended {status}.", full_context_status()
            if status == "suppressed":
                return task_id, "The task was suppressed again before completion.", full_context_status()
            time.sleep(0.1)
        raise TimeoutError(f"resumed suppressed task did not finish within {self.wait_timeout_seconds:g} seconds")

    def _suppressed_resume_flow(self, message: str, *, primary_thread_id: str, thread_ids: list[str], user_message_id: str):
        if self.durable is None or self.prompt_queue is None:
            return None
        key = self._resume_state_key(primary_thread_id)
        pending = self.durable.get_runtime_state(key, None)
        lowered = re.sub(r"\s+", " ", message.strip().lower())
        if isinstance(pending, dict) and pending.get("task_id"):
            age = time.time() - float(pending.get("created_at") or 0)
            if age > 3600:
                self.durable.delete_runtime_state(key)
                pending = None
            elif lowered in {"yes", "y", "confirm", "yes resume", "resume it", "do it", "go ahead"}:
                task_id = str(pending["task_id"])
                self.durable.delete_runtime_state(key)
                return self._resume_suppressed_and_wait(task_id, thread_ids=thread_ids, primary_thread_id=primary_thread_id, user_message_id=user_message_id)
            elif lowered in {"no", "n", "cancel", "never mind", "nevermind"}:
                self.durable.delete_runtime_state(key)
                return None, "Okay. I left the suppressed task parked and unchanged.", full_context_status()
        if "resume" not in lowered or "task" not in lowered:
            return None
        # Explicit operator form:
        #   resume task 0
        #   /resume-task 0
        #   resume task <task-id-or-unique-prefix>
        #
        # Numeric selectors use the same zero-based convention as /resume-queue.
        # Do not run a fuzzy match when the operator supplied an explicit index.
        explicit_resume = re.fullmatch(
            r"/?resume(?:-|\s+)task(?:\s+(.+))?",
            message.strip(),
            flags=re.IGNORECASE,
        )
        if explicit_resume:
            selector = str(explicit_resume.group(1) or "").strip()
            candidates = self.durable.suppressed_tasks(limit=200)

            if not candidates:
                return (
                    None,
                    "There are no suppressed tasks to resume.",
                    full_context_status(),
                )

            if not selector:
                lines = ["Suppressed tasks (newest first; indexes are zero-based):"]
                for index, item in enumerate(candidates[:25]):
                    task_id = str(item.get("task_id") or "")
                    title = str(item.get("title") or "").strip() or "[untitled]"
                    lines.append(f"  {index}: {title}  ({task_id})")
                return None, "\n".join(lines), full_context_status()

            selected = None

            if selector.isdigit():
                index = int(selector)
                if index < 0 or index >= len(candidates):
                    return (
                        None,
                        f"Suppressed task index {index} is out of range "
                        f"0..{len(candidates) - 1}.",
                        full_context_status(),
                    )
                selected = candidates[index]
            else:
                exact = [
                    item for item in candidates
                    if str(item.get("task_id") or "") == selector
                ]
                if len(exact) == 1:
                    selected = exact[0]
                else:
                    prefix = [
                        item for item in candidates
                        if str(item.get("task_id") or "").startswith(selector)
                    ]
                    if len(prefix) == 1:
                        selected = prefix[0]
                    elif len(prefix) > 1:
                        return (
                            None,
                            f"Task-id prefix {selector!r} matches multiple "
                            "suppressed tasks; use a longer prefix.",
                            full_context_status(),
                        )

            if selected is not None:
                return self._resume_suppressed_and_wait(
                    str(selected["task_id"]),
                    thread_ids=thread_ids,
                    primary_thread_id=primary_thread_id,
                    user_message_id=user_message_id,
                )

        candidate, score = self._best_suppressed_match(message)
        if not candidate:
            return None
        self.durable.set_runtime_state(key, {"task_id": candidate["task_id"], "created_at": time.time(), "match_score": score})
        steps = candidate.get("steps") or []
        done = sum(1 for step in steps if step.get("status") == "completed")
        original = str(candidate.get("original_request") or "").strip()
        if len(original) > 450:
            original = original[:447] + "..."
        reply = (f"The best suppressed-task match I found is **{candidate.get('title') or candidate['task_id']}**. "
                 f"It has {done}/{len(steps)} recorded steps completed. The original request was: {original or '[original request unavailable]'}\n\n"
                 "Do you want me to resume that task from its saved PostgreSQL/queue state?")
        return None, reply, full_context_status()

    def chat(
        self, message: str, *, project_id: str = "default", thread_id: str | None = None,
        source_prompt_id: str | None = None,
    ) -> dict:
        message = message.strip()
        if not message:
            raise ValueError("message cannot be empty")

        try:
            self.store.ensure_project(project_id)
            route = self._route(project_id, message, explicit_thread_id=thread_id)

            if route.clarification_question:
                return {
                    "reply": route.clarification_question,
                    "project_id": project_id,
                    "needs_clarification": True,
                    "thread_ids": list(route.thread_ids),
                    "primary_thread_id": route.primary_thread_id,
                    "confidence": route.confidence,
                    "resource_status": full_context_status(),
                }

            if not route.primary_thread_id:
                raise RuntimeError("Routing did not produce a primary thread")

            thread_ids = list(route.thread_ids)
            if route.primary_thread_id not in thread_ids:
                thread_ids.insert(0, route.primary_thread_id)

            user_id = self.store.add_message("user", message, thread_ids, route.primary_thread_id)
            special = self._suppressed_resume_flow(
                message, primary_thread_id=route.primary_thread_id,
                thread_ids=thread_ids, user_message_id=user_id,
            )
            turn_interpretation = {"primary_request": message, "command": None, "ingrained_details": []}
            ingrained_state = {"captured": 0, "resolved": 0, "unresolved": 0, "current_task_context": [], "records": []}
            related_running_work: list[dict] = []
            if special is None:
                related_running_work = self._related_running_work(project_id, route.primary_thread_id)
                turn_interpretation = self._classify_turn(
                    message, project_id=project_id, thread_ids=thread_ids, related_running_work=related_running_work
                )
                try:
                    ingrained_state = self._apply_ingrained_details(
                        project_id=project_id, thread_ids=thread_ids, source_message_id=user_id,
                        details=list(turn_interpretation.get("ingrained_details") or []),
                    )
                except Exception:
                    logging.exception("Ingrained-detail routing failed; primary task will continue")
                    ingrained_state = {"captured": 0, "resolved": 0, "unresolved": 0, "current_task_context": [], "records": []}
                primary_request = str(turn_interpretation.get("primary_request") or message).strip() or message
                prompt, resource_status = self._answer_prompt(
                    project_id, thread_ids, primary_request,
                    original_user_message=message,
                    current_task_context=list(ingrained_state.get("current_task_context") or []),
                )
            else:
                prompt = ""
                resource_status = full_context_status()
        except psycopg.Error as exc:
            return self._degraded_postgres_chat(message, project_id, exc)

        if special is None:
            primary_request = str(turn_interpretation.get("primary_request") or message).strip() or message
            task_id, reply, task_resource_status = self._enqueue_and_wait(
                prompt,
                original_user_prompt=message,
                task_user_prompt=primary_request,
                project_id=project_id,
                thread_ids=thread_ids,
                primary_thread_id=route.primary_thread_id,
                user_message_id=user_id,
                resource_status=resource_status,
                preclassified_command=(turn_interpretation.get("command") if isinstance(turn_interpretation.get("command"), dict) else None),
                related_running_work=related_running_work,
                ingrained_state=ingrained_state,
                source_prompt_id=str(source_prompt_id or ""),
            )
        else:
            task_id, reply, task_resource_status = special
        resource_status = merge_resource_status(resource_status, task_resource_status)

        from .secret_redaction import redact
        reply = redact(reply)
        assistant_id = None
        try:
            assistant_id = self.store.add_message("assistant", reply, thread_ids, route.primary_thread_id)
        except psycopg.Error as exc:
            resource_status = merge_resource_status(
                resource_status,
                impaired_context_status("postgres", f"{type(exc).__name__}: {exc}"),
            )
            logging.warning("Assistant response persistence unavailable; returning generated reply with impaired context: %s", exc)

        if assistant_id:
            try:
                self._refresh_summary(route.primary_thread_id, assistant_id)
            except Exception:
                logging.exception("Summary refresh failed")

            try:
                self._extract_memories(project_id, thread_ids, user_id, message, reply)
            except Exception:
                logging.exception("Memory extraction failed")

        return {
            "reply": reply,
            "project_id": project_id,
            "needs_clarification": False,
            "thread_ids": thread_ids,
            "primary_thread_id": route.primary_thread_id,
            "confidence": route.confidence,
            "user_message_id": user_id,
            "assistant_message_id": assistant_id,
            "task_id": task_id,
            "resource_status": resource_status,
        }

    def _degraded_postgres_chat(self, message: str, project_id: str, exc: Exception) -> dict:
        resource_status = impaired_context_status("postgres", f"{type(exc).__name__}: {exc}")
        prompt = (
            f"PERSISTENT OPERATING PRINCIPLES:\n{self._persistent_instruction_text()}\n\n"
            "PostgreSQL conversation/context storage is unavailable. Answer the current user request using only the current request and any explicitly needed available tools. "
            "Do not invent missing conversation history or durable memory. If a required tool/resource is also unavailable, say so in the result rather than pretending it was used.\n\n"
            f"RESOURCE STATUS JSON:\n{json.dumps(resource_status, ensure_ascii=False)}\n\n"
            f"CURRENT USER REQUEST:\n{message}"
        )
        if self.file_tools is not None:
            prompt = self.file_tools.instructions() + "\n\n" + prompt
            reply = self._answer_with_tools(prompt)
        else:
            reply = self.ollama.generate(prompt, think=False, temperature=0.2)
        return {
            "reply": reply,
            "project_id": project_id,
            "needs_clarification": False,
            "thread_ids": [],
            "primary_thread_id": None,
            "confidence": 0.0,
            "user_message_id": None,
            "assistant_message_id": None,
            "task_id": f"degraded-{uuid.uuid4()}",
            "resource_status": resource_status,
        }

    def _related_running_work(self, project_id: str, primary_thread_id: str | None) -> list[dict]:
        if self.prompt_queue is None or self.durable is None:
            return []
        try:
            inspected = self.prompt_queue.inspect_queue(count=500)
        except Exception:
            logging.exception("Could not inspect related running work for append classification")
            return []
        task_ids: list[str] = []
        for bucket in ("work", "retry"):
            for item in inspected.get(bucket, []):
                task_id = str(item.get("task_id") or "").strip()
                if not task_id or task_id.startswith("child-") or task_id in task_ids:
                    continue
                if str(item.get("project_id") or "") != str(project_id):
                    continue
                # Same-project unfinished work is relevant even when routing placed a follow-up
                # in a different conversation thread. The classifier decides semantic dependency.
                try:
                    if self.durable.task_status(task_id) != "running":
                        continue
                except Exception:
                    continue
                task_ids.append(task_id)
                if len(task_ids) >= 8:
                    break
            if len(task_ids) >= 8:
                break
        snapshots = []
        for task_id in task_ids:
            try:
                snapshot = self.durable.task_plan_snapshot(task_id)
            except Exception:
                logging.exception("Could not load related running task plan task=%s", task_id)
                continue
            if snapshot:
                snapshots.append(snapshot)
        return snapshots

    @staticmethod
    def _validate_append_target(command: dict, related_running_work: list[dict]) -> dict:
        if command.get("category") != "append":
            return command
        previous_task_id = str(command.get("previous_task_id") or "")
        by_task = {str(item.get("task_id") or ""): item for item in related_running_work}
        if previous_task_id not in by_task:
            raise ValueError("append dependency must reference a supplied related running task")
        previous_step_id = str(command.get("previous_step_id") or "")
        if previous_step_id:
            valid_steps = {str(step.get("id") or "") for step in by_task[previous_task_id].get("steps", [])}
            if previous_step_id not in valid_steps:
                raise ValueError("append dependency step must exist in the referenced running task")
        return command

    def _classify_command(self, original_user_prompt: str, related_running_work: list[dict] | None = None) -> dict:
        related_running_work = list(related_running_work or [])
        prompt = (
            "Classify the user's request for Norm's internal control plane. Return only the JSON object required by the schema. "
            "Choose category from: straightforward_direction (a direct bounded instruction), simple_task (one bounded task with some work), "
            "maintenance (runtime/repository upkeep), information_retrieval (find/read/summarize existing information), task (multi-step work), "
            "task_step (an explicit continuation/step inside an already-running task), or append (new follow-up work that depends on artifacts/results "
            "an unfinished earlier task is supposed to produce). Append is backward-linked work: the new command points to its prerequisite; never modify "
            "the predecessor to point forward. Choose append only when RELATED RUNNING WORK actually supplies the predecessor. For append, set "
            "previous_task_id to an exact supplied task id. dependency_reason is required: name the prerequisite artifacts/results and why that task/step must finish first. "
            "Set previous_step_id to the EARLIEST supplied step after which every artifact the append will read OR modify has already been created by the predecessor. "
            "If a later predecessor step is still scheduled to create or substantially rewrite an artifact the append will touch (for example README, compose, Dockerfile, config), "
            "the append must wait through that later producer step rather than racing it. If you cannot determine that boundary safely, leave previous_step_id blank so the append waits "
            "for the whole predecessor task. Never invent ids. "
            "Do not classify merely because two requests share a topic. Describe intent briefly. Mark file mutation/research/media/output requirements literally. "
            "For expected_output.type, use answer for conversational text only; artifact only when the user asks Norm to create/save a file; "
            "artifact_and_response when both a saved file and conversational response are required; action_result for a non-file side effect. "
            "If expected_output is artifact or artifact_and_response, requires_file_mutation must be true.\n\n"
            f"RELATED RUNNING WORK:\n{json.dumps(related_running_work, ensure_ascii=False)}\n\n"
            f"USER REQUEST:\n{original_user_prompt}"
        )
        last_error = None
        for _ in range(2):
            try:
                parsed = self._structured_generate(prompt, command_schema(), num_predict=900, think=False)
                command = normalize_command(parsed, original_user_prompt)
                command = self._validate_append_target(command, related_running_work)
                return self._verify_append_boundary(original_user_prompt, command, related_running_work)
            except (ValueError, json.JSONDecodeError) as exc:
                last_error = exc
        logging.warning("Command classification protocol failed; using deterministic fallback: %s", last_error)
        text = original_user_prompt.strip()
        lower = text.lower()
        category = "maintenance" if any(token in lower for token in ("backup", "restart", "redis", "runtime", "shutdown")) else (
            "information_retrieval" if any(token in lower for token in ("find ", "look up", "summarize", "read ", "what does")) else
            ("straightforward_direction" if len(text) < 240 else "task")
        )
        return normalize_command({
            "schema_version": PROTOCOL_VERSION, "kind": "command", "category": category,
            "intent": text[:500] or "user request", "requires_file_mutation": False,
            "requires_external_research": False, "input_has_media": False, "output_requires_media": False,
            "expected_output": {"type": "answer", "format": "text"},
        }, original_user_prompt)

    @staticmethod
    def _turn_interpretation_schema() -> dict:
        destinations = [
            "current_task_context",
            "memory_fact",
            "memory_preference",
            "memory_decision",
            "memory_constraint",
            "memory_task",
            "memory_assumption",
            "unresolved",
        ]
        return {
            "type": "object",
            "properties": {
                "primary_request": {"type": "string", "maxLength": 8000},
                "command": command_schema(),
                "ingrained_details": {
                    "type": "array",
                    "maxItems": 12,
                    "items": {
                        "type": "object",
                        "properties": {
                            "verbatim": {"type": "string", "maxLength": 1200},
                            "normalized": {"type": "string", "maxLength": 1200},
                            "why_separate": {"type": "string", "maxLength": 700},
                            "destination": {"type": "string", "enum": destinations},
                            "also_current_task": {"type": "boolean"},
                            "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
                            "supersedes": {
                                "type": "array", "maxItems": 5,
                                "items": {"type": "string", "maxLength": 120},
                            },
                        },
                        "required": [
                            "verbatim", "normalized", "why_separate", "destination",
                            "also_current_task", "confidence", "supersedes",
                        ],
                        "additionalProperties": False,
                    },
                },
            },
            "required": ["primary_request", "command", "ingrained_details"],
            "additionalProperties": False,
        }

    @staticmethod
    def _unresolved_fit_schema() -> dict:
        outcomes = [
            "apply_current_task",
            "promote_fact",
            "promote_preference",
            "promote_decision",
            "promote_constraint",
            "promote_task",
            "promote_assumption",
            "not_relevant",
            "ambiguous",
        ]
        return {
            "type": "object",
            "properties": {
                "task_domain": {"type": "string", "maxLength": 120},
                "results": {
                    "type": "array", "maxItems": 8,
                    "items": {
                        "type": "object",
                        "properties": {
                            "bit_id": {"type": "string", "maxLength": 120},
                            "outcome": {"type": "string", "enum": outcomes},
                            "normalized_content": {"type": "string", "maxLength": 1200},
                            "also_current_task": {"type": "boolean"},
                            "reason": {"type": "string", "maxLength": 700},
                        },
                        "required": ["bit_id", "outcome", "normalized_content", "also_current_task", "reason"],
                        "additionalProperties": False,
                    },
                },
            },
            "required": ["task_domain", "results"],
            "additionalProperties": False,
        }

    @staticmethod
    def _memory_type_from_destination(destination: str) -> str | None:
        mapping = {
            "memory_fact": "fact",
            "memory_preference": "preference",
            "memory_decision": "decision",
            "memory_constraint": "constraint",
            "memory_task": "task",
            "memory_assumption": "assumption",
        }
        return mapping.get(str(destination or ""))

    def _classify_turn(
        self,
        original_user_prompt: str,
        *,
        project_id: str,
        thread_ids: list[str],
        related_running_work: list[dict],
    ) -> dict:
        """Separate the executable request from meaningful sidecar details.

        `primary_request` is the bounded work Norm should execute now.  An
        ingrained detail is meaningful user-provided information that is not
        wholly confined to that work.  The full user message remains stored
        verbatim in the conversation and is never replaced by this normalized
        view.
        """
        if not self.ingrained_details_enabled:
            return {
                "primary_request": original_user_prompt,
                "command": self._classify_command(original_user_prompt, related_running_work),
                "ingrained_details": [],
            }
        try:
            active = self.store.active_memories(project_id, thread_ids, limit=80)
        except Exception:
            active = []
        active_view = [
            {"memory_id": item["memory_id"], "type": item["type"], "content": item["content"]}
            for item in active
        ]
        prompt = (
            "Interpret this user turn for Norm's execution and durable-context pipeline. Return strict JSON only.\n\n"
            "FIRST identify primary_request: the part Norm should execute or answer now. Preserve the user's actual meaning, constraints, "
            "paths, names, and requested output. You may remove only side remarks that you explicitly return as ingrained_details. If the turn "
            "has no separable side remark, keep the whole turn as primary_request. Never reduce a pure factual/corrective user turn to an empty "
            "request merely because it can also be remembered.\n\n"
            "An ingrained_detail is intentionally meaningful information in the same turn that is not wholly limited to the primary task. Examples: "
            "a terminology correction, durable preference, future todo, project intention, correction to prior context, or a useful aside. Do NOT "
            "extract filler, greetings, rhetorical glue, or details already required to understand the primary task. A detail that affects the current "
            "task AND is reusable may be routed to a memory destination with also_current_task=true.\n\n"
            "Route confident details directly: memory_fact for stable facts/terminology/corrections; memory_preference for stable preferences; "
            "memory_decision for decisions; memory_constraint for reusable constraints; memory_task for future actions/todos/backlog; memory_assumption "
            "only for explicitly tentative beliefs; current_task_context for material that belongs only in the present task. Use unresolved only when "
            "the detail is meaningful but its proper durable home is genuinely unclear. For a correction that clearly replaces an active memory, put "
            "that exact memory_id in supersedes. Do not invent ids.\n\n"
            "Also classify the PRIMARY REQUEST for Norm's control plane using the nested command schema. Choose append only when RELATED RUNNING WORK "
            "supplies a real unfinished prerequisite. The command must describe primary_request, not the sidecar details.\n\n"
            f"ACTIVE MEMORIES:\n{json.dumps(active_view, ensure_ascii=False)}\n\n"
            f"RELATED RUNNING WORK:\n{json.dumps(related_running_work, ensure_ascii=False)}\n\n"
            f"FULL USER TURN:\n{original_user_prompt}"
        )
        last_error = None
        for _ in range(2):
            try:
                parsed = self._structured_generate(
                    prompt, self._turn_interpretation_schema(), num_predict=2600, think=False
                )
                primary = str(parsed.get("primary_request") or "").strip()
                if not primary:
                    primary = original_user_prompt.strip()
                command = normalize_command(parsed.get("command") or {}, primary)
                command = self._validate_append_target(command, related_running_work)
                command = self._verify_append_boundary(primary, command, related_running_work)
                raw_details = parsed.get("ingrained_details")
                if not isinstance(raw_details, list):
                    raise ValueError("ingrained_details must be a list")
                valid_ids = {str(item.get("memory_id") or "") for item in active_view}
                details = []
                for item in raw_details:
                    if not isinstance(item, dict):
                        continue
                    verbatim = str(item.get("verbatim") or "").strip()[:1200]
                    normalized = str(item.get("normalized") or verbatim).strip()[:1200]
                    why = str(item.get("why_separate") or "").strip()[:700]
                    destination = str(item.get("destination") or "unresolved").strip()
                    if destination not in {
                        "current_task_context", "memory_fact", "memory_preference", "memory_decision",
                        "memory_constraint", "memory_task", "memory_assumption", "unresolved",
                    }:
                        destination = "unresolved"
                    if not normalized:
                        continue
                    supersedes = item.get("supersedes") if isinstance(item.get("supersedes"), list) else []
                    supersedes = [str(v) for v in supersedes if str(v) in valid_ids]
                    details.append({
                        "verbatim": verbatim or normalized,
                        "normalized": normalized,
                        "why_separate": why,
                        "destination": destination,
                        "also_current_task": bool(item.get("also_current_task")),
                        "confidence": max(0.0, min(float(item.get("confidence") or 0.0), 1.0)),
                        "supersedes": supersedes,
                    })
                return {"primary_request": primary, "command": command, "ingrained_details": details}
            except (ValueError, json.JSONDecodeError, TypeError) as exc:
                last_error = exc
        logging.warning("Turn interpretation protocol failed; preserving full turn as primary request: %s", last_error)
        return {
            "primary_request": original_user_prompt,
            "command": self._classify_command(original_user_prompt, related_running_work),
            "ingrained_details": [],
        }

    def _apply_ingrained_details(
        self,
        *,
        project_id: str,
        thread_ids: list[str],
        source_message_id: str,
        details: list[dict],
    ) -> dict:
        result = {"captured": 0, "resolved": 0, "unresolved": 0, "current_task_context": [], "records": []}
        for item in details:
            if not isinstance(item, dict):
                continue
            normalized = str(item.get("normalized") or "").strip()
            if not normalized:
                continue
            result["captured"] += 1
            destination = str(item.get("destination") or "unresolved")
            record = {
                "normalized": normalized,
                "destination": destination,
                "why_separate": str(item.get("why_separate") or ""),
            }
            if bool(item.get("also_current_task")) or destination == "current_task_context":
                result["current_task_context"].append(normalized)
            memory_type = self._memory_type_from_destination(destination)
            if memory_type is not None:
                new_id = self.store.upsert_memory(
                    project_id, memory_type, normalized, thread_ids,
                    source_message_id=source_message_id,
                )
                supersedes = [str(v) for v in item.get("supersedes") or [] if str(v) and str(v) != new_id]
                if supersedes:
                    self.store.supersede_memories(supersedes, new_id)
                record["memory_id"] = new_id
                result["resolved"] += 1
            elif destination == "current_task_context":
                result["resolved"] += 1
            else:
                bit_id = self.store.add_unresolved_bit(
                    project_id,
                    verbatim=str(item.get("verbatim") or normalized),
                    normalized=normalized,
                    why_separate=str(item.get("why_separate") or ""),
                    suggested_destination=destination,
                    confidence=float(item.get("confidence") or 0.0),
                    source_message_id=source_message_id,
                    thread_ids=thread_ids,
                )
                record["bit_id"] = bit_id
                result["unresolved"] += 1
            result["records"].append(record)
        # Preserve order while removing duplicate current-task additions.
        result["current_task_context"] = list(dict.fromkeys(result["current_task_context"]))
        return result

    @staticmethod
    def _meaningful_tokens(text: str) -> set[str]:
        stop = {
            "the", "and", "for", "that", "this", "with", "from", "have", "what", "when", "where",
            "would", "could", "should", "about", "into", "your", "you", "but", "not", "just", "then",
            "they", "them", "there", "here", "some", "need", "want", "later", "also", "like",
        }
        return {token for token in re.findall(r"[a-z0-9_]{3,}", str(text).lower()) if token not in stop}

    def _choose_unresolved_candidates(self, task_id: str, task_request: str, rows: list[dict]) -> list[dict]:
        if not rows:
            return []
        task_tokens = self._meaningful_tokens(task_request)
        scored = []
        for row in rows:
            bit_tokens = self._meaningful_tokens(str(row.get("normalized") or ""))
            overlap = len(task_tokens & bit_tokens)
            scored.append((overlap, int(row.get("trial_count") or 0), row))
        matched = [item for item in scored if item[0] > 0]
        matched.sort(key=lambda item: (-item[0], item[1]))
        selected = [item[2] for item in matched[: self.unresolved_test_limit]]
        if selected:
            return selected
        digest = hashlib.sha256(str(task_id).encode("utf-8")).digest()
        explore = int.from_bytes(digest[:4], "big") % self.unresolved_explore_every_tasks == 0
        if not explore:
            return []
        scored.sort(key=lambda item: (item[1], str(item[2].get("created_at") or "")))
        return [item[2] for item in scored[: self.unresolved_test_limit]]

    def _test_unresolved_bits(
        self,
        *,
        project_id: str,
        task_id: str,
        task_request: str,
        thread_ids: list[str],
        current_user_message_id: str,
    ) -> list[str]:
        if not self.ingrained_details_enabled:
            return []
        try:
            pool = self.store.unresolved_candidates(
                project_id, exclude_message_id=current_user_message_id, limit=max(12, self.unresolved_test_limit * 4)
            )
        except Exception:
            logging.exception("Could not load unresolved bits")
            return []
        candidates = self._choose_unresolved_candidates(task_id, task_request, pool)
        if not candidates:
            return []
        prompt = (
            "Norm is executing a real task. Test whether any of these previously unresolved user-provided context bits now have a concrete home. "
            "Return strict JSON only. Do not force a match. task_domain should be a short stable domain label such as software-development, "
            "document-analysis, personal-planning, markets, image-work, hardware, or another concise domain.\n\n"
            "For each bit choose exactly one outcome:\n"
            "- apply_current_task: materially helps this task but does not warrant a broader durable memory; it will be copied into this task and removed from the unresolved pool.\n"
            "- promote_fact/preference/decision/constraint/task/assumption: the bit now has that durable final home. Set also_current_task=true when it should also influence the task being executed now.\n"
            "- not_relevant: it was genuinely considered and does not help this task.\n"
            "- ambiguous: still not enough evidence to place it.\n"
            "Use normalized_content to express the information faithfully without adding facts.\n\n"
            f"CURRENT TASK:\n{task_request}\n\n"
            f"CANDIDATE UNRESOLVED BITS:\n{json.dumps(candidates, ensure_ascii=False)}"
        )
        try:
            parsed = self._structured_generate(prompt, self._unresolved_fit_schema(), num_predict=1800, think=False)
        except Exception:
            logging.exception("Unresolved-bit fit test failed")
            return []
        task_domain = str(parsed.get("task_domain") or "unknown").strip().lower()[:120] or "unknown"
        by_id = {str(item.get("bit_id") or ""): item for item in candidates}
        results = parsed.get("results") if isinstance(parsed.get("results"), list) else []
        additions: list[str] = []
        promote_map = {
            "promote_fact": "fact",
            "promote_preference": "preference",
            "promote_decision": "decision",
            "promote_constraint": "constraint",
            "promote_task": "task",
            "promote_assumption": "assumption",
        }
        handled: set[str] = set()
        for item in results:
            if not isinstance(item, dict):
                continue
            bit_id = str(item.get("bit_id") or "")
            source = by_id.get(bit_id)
            if source is None or bit_id in handled:
                continue
            handled.add(bit_id)
            outcome = str(item.get("outcome") or "ambiguous")
            content = str(item.get("normalized_content") or source.get("normalized") or "").strip()
            reason = str(item.get("reason") or "").strip()
            useful = outcome == "apply_current_task" or outcome.startswith("promote_")
            try:
                self.store.record_unresolved_trial(
                    bit_id, task_id=task_id, task_domain=task_domain, outcome=outcome, reason=reason, useful=useful
                )
                if outcome == "apply_current_task":
                    if content:
                        additions.append(content)
                    self.store.delete_unresolved_bit(bit_id)
                elif outcome in promote_map:
                    final_threads = list(dict.fromkeys([*list(source.get("source_thread_ids") or []), *thread_ids]))
                    final_content = content or str(source.get("normalized") or "")
                    self.store.upsert_memory(
                        project_id, promote_map[outcome], final_content, final_threads,
                        source_message_id=str(source.get("last_source_message_id") or source.get("source_message_id") or "") or None,
                    )
                    if bool(item.get("also_current_task")) and final_content:
                        additions.append(final_content)
                    self.store.delete_unresolved_bit(bit_id)
                elif outcome == "not_relevant":
                    self.store.prune_unresolved_bit_if_exhausted(
                        bit_id,
                        min_trials=self.unresolved_delete_after_trials,
                        min_domains=self.unresolved_delete_after_domains,
                    )
            except Exception:
                logging.exception("Could not persist unresolved-bit trial bit=%s task=%s", bit_id, task_id)
        return list(dict.fromkeys(additions))

    @staticmethod
    def _append_boundary_schema() -> dict:
        return {
            "type": "object",
            "properties": {
                "previous_step_id": {"type": "string", "maxLength": 120},
                "reason": {"type": "string", "maxLength": 500},
            },
            "required": ["previous_step_id", "reason"],
            "additionalProperties": False,
        }

    def _verify_append_boundary(self, original_user_prompt: str, command: dict, related_running_work: list[dict]) -> dict:
        if command.get("category") != "append":
            return command
        previous_task_id = str(command.get("previous_task_id") or "")
        task = next((item for item in related_running_work if str(item.get("task_id") or "") == previous_task_id), None)
        if not task:
            raise ValueError("append predecessor disappeared before boundary verification")
        prompt = (
            "Independently choose the safe backward dependency boundary for this append. Return strict JSON only. "
            "The previous_task_id is already fixed and valid. previous_step_id may be an exact supplied step id or blank. "
            "Choose the EARLIEST step after which every artifact/result the follow-up will read or modify has already been created by the predecessor, "
            "AND no later predecessor step is scheduled to create or substantially rewrite one of those same artifacts. If uncertain, return blank to wait "
            "for the whole predecessor task. Never invent a step id. The reason must name the concrete prerequisite artifacts/results. "
            "The runtime will still wait for the predecessor task's structured final verification to accept before executing the append; "
            "previous_step_id records prerequisite provenance and must not be treated as permission to race final verification.\n\n"
            f"FOLLOW-UP REQUEST:\n{original_user_prompt}\n\n"
            f"INITIAL APPEND CLASSIFICATION:\n{json.dumps(command, ensure_ascii=False)}\n\n"
            f"PREDECESSOR PLAN:\n{json.dumps(task, ensure_ascii=False)}"
        )
        parsed = self._structured_generate(prompt, self._append_boundary_schema(), num_predict=600, think=False)
        step_id = str(parsed.get("previous_step_id") or "").strip()
        reason = str(parsed.get("reason") or "").strip()[:500]
        if not reason:
            raise ValueError("append boundary verifier returned blank reason")
        valid_steps = {str(step.get("id") or "") for step in task.get("steps", [])}
        if step_id and step_id not in valid_steps:
            step_id = ""
            reason = (reason + " Safety fallback: unknown step id was discarded; wait for the whole predecessor task.")[:500]
        result = dict(command)
        result["previous_step_id"] = step_id
        result["dependency_reason"] = reason
        if self.durable and hasattr(self.durable, "task_uuid"):
            result["previous_task_uuid"] = self.durable.task_uuid(previous_task_id)
            if step_id and hasattr(self.durable, "node_uuid"):
                result["previous_node_id"] = self.durable.node_uuid(previous_task_id, step_id)
            else:
                result["previous_node_id"] = ""
        return result

    @staticmethod
    def _planner_schema() -> dict:
        return {
            "type": "object",
            "properties": {
                "title": {"type": "string", "maxLength": 120},
                "steps": {
                    "type": "array", "minItems": 1, "maxItems": 25,
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string", "maxLength": 120},
                            "description": {"type": "string", "maxLength": 1200},
                            "verify": {"type": "string", "maxLength": 500},
                        },
                        "required": ["name", "description", "verify"],
                        "additionalProperties": False,
                    },
                },
            },
            "required": ["title", "steps"],
            "additionalProperties": False,
        }

    @staticmethod
    def _plan_verifier_schema() -> dict:
        return {
            "type": "object",
            "properties": {
                "accepted": {"type": "boolean"},
                "issues": {"type": "array", "maxItems": 4, "items": {"type": "string", "maxLength": 240}},
            },
            "required": ["accepted", "issues"],
            "additionalProperties": False,
        }
    def _structured_generate(self, prompt: str, schema: dict, *, num_predict: int, think: bool = False) -> dict:
        try:
            raw = self.ollama.generate(
                prompt, think=think, num_predict=num_predict, temperature=0.0,
                response_format=schema,
            )
        except TypeError as exc:
            if "response_format" not in str(exc):
                raise
            raw = self.ollama.generate(prompt, think=think, num_predict=num_predict, temperature=0.0)
        if len(raw) > 12_000:
            raise ValueError("structured planning output exceeded 12000 characters")
        return self.ollama.parse_json(raw)

    @staticmethod
    def _normalize_plan(parsed: dict) -> dict:
        title = str(parsed.get("title") or "Task").strip()[:120]
        raw_steps = parsed.get("steps")
        if not isinstance(raw_steps, list) or not 1 <= len(raw_steps) <= 25:
            raise ValueError("planner must return between 1 and 25 actionable steps")
        steps = []
        for item in raw_steps:
            if not isinstance(item, dict):
                raise ValueError("planner step must be an object")
            name = str(item.get("name") or "").strip()[:120]
            description = str(item.get("description") or "").strip()[:1200]
            verify = str(item.get("verify") or "").strip()[:500]
            if not name or not description or not verify:
                raise ValueError("every planner step requires name, description, and verify")
            steps.append({"name": name, "description": description, "verify": verify})
        return {"title": title or "Task", "steps": steps}

    def _generate_plan(self, original_user_prompt: str, execution_context: str, command_envelope: dict) -> dict:
        prompt = (
            f"PERSISTENT OPERATING PRINCIPLES:\n{self._persistent_instruction_text()}\n\n"
            "Create a concrete execution plan for Norm before any task work begins. "
            "Break substantial work into small observable actions that can each finish within a bounded "
            "model/tool slice. Aim for about 6 actionable steps for substantial work, but use exactly ONE step when the request can be completed as one bounded action. Never manufacture a separate preparation step plus synthesis step for a trivial request. Use more steps only when the work genuinely benefits from decomposition, up to a hard maximum of 25. Preserve every user constraint, especially "
            "read-only/no-modification constraints. Do not include planning or plan-verification as steps; "
            "the runtime adds those separately. For a multi-step plan, the FINAL step must synthesize the completed prior work into "
            "the direct user-facing answer. For a one-step plan, that single step is itself the final user-facing action/answer and no separate synthesis step is required. Do not hide the whole job inside one generic 'respond' step when "
            "the request has multiple inspect/analyze/build phases. If one phase must produce more than about 8 rich repeated items/records/paths, split that work into multiple bounded batch steps (normally no more than 8 rich items per generation step) and add a later merge/dedupe/selection step when needed. Do not put dozens of detailed repeated items into one model response. Return strict JSON only.\n\n"
            f"AUTHORITATIVE PRIMARY TASK REQUEST:\n{original_user_prompt}\n\n"
            f"STRUCTURED COMMAND ENVELOPE:\n{json.dumps(command_envelope, ensure_ascii=False)}\n\n"
            f"Execution context available to the worker:\n{execution_context}"
        )
        return self._normalize_plan(
            self._structured_generate(prompt, self._planner_schema(), num_predict=3200, think=False)
        )

    def _check_plan(self, original_user_prompt: str, plan: dict) -> tuple[bool, list[str]]:
        prompt = (
            f"PERSISTENT OPERATING PRINCIPLES:\n{self._persistent_instruction_text()}\n\n"
            "Independently verify this proposed task plan before execution. Reject only for BLOCKING defects: a dropped or changed "
            "user constraint; unauthorized or unsafe action; a materially contradictory or impossible design; missing concrete verification "
            "for a material output; an action too large for a bounded worker slice; or, for a MULTI-STEP plan, no final synthesis step capable "
            "of answering the user from prior results. Do NOT reject for advisory style, naming, organization, or merely because a step contains "
            "multiple tightly coupled subcomponents. A multi-part step is an opaque catch-all only when it is vague/generic or hides materially "
            "distinct phases that cannot reasonably complete in one bounded slice. Explicitly named related modules/functions may share one step "
            "when their roles are clear and the verification independently covers them. Redundant mechanisms are not a blocking defect unless they "
            "create a contradiction, ambiguity that prevents implementation, or violate the user's request. Do not invent hypothetical implementation "
            "defects or boundary-value failures when the plan explicitly inspects/reuses existing code or includes execution tests that will verify the "
            "relevant invariant; those details belong to execution and step verification unless the plan itself mandates unsafe behavior. Reject a plan "
            "that asks one step to generate more than about 8 rich repeated items when the work can be safely batched and merged. If only advisory "
            "concerns remain, accept the plan with an empty issues list. Return strict JSON only: {\"accepted\":true|false,\"issues\":[...]}.\n\n"
            f"AUTHORITATIVE PRIMARY TASK REQUEST:\n{original_user_prompt}\n\n"
            f"PROPOSED PLAN:\n{json.dumps(plan, ensure_ascii=False)}"
        )
        last_error = None
        for _ in range(2):
            try:
                parsed = self._structured_generate(
                    prompt, self._plan_verifier_schema(), num_predict=384, think=False
                )
                accepted = parsed.get("accepted")
                issues = parsed.get("issues")
                if not isinstance(accepted, bool) or not isinstance(issues, list):
                    raise ValueError("invalid plan-verifier shape")
                clean = [str(v).strip()[:240] for v in issues if str(v).strip()][:4]
                if accepted and clean:
                    raise ValueError("plan verifier accepted while reporting issues")
                if not accepted and not clean:
                    raise ValueError("plan verifier rejected without issues")
                return accepted, clean
            except (ValueError, json.JSONDecodeError) as exc:
                last_error = exc
        raise RuntimeError(f"plan verification protocol failed: {last_error}")
    def _repair_plan(self, original_user_prompt: str, plan: dict, issues: list[str]) -> dict:
        prompt = (
            "Repair this execution plan so every BLOCKING verifier issue is materially resolved while preserving the authoritative "
            "user request. Make the smallest changes necessary and do not merely restate the same rejected plan. Return strict JSON only in the same schema. "
            "Use exactly ONE action when the request is a single bounded operation; otherwise aim for about 6 actionable steps, with fewer or more when warranted "
            "and a hard maximum of 25. Preserve cohesive bounded steps: do not split an explicit step solely because it contains multiple tightly coupled "
            "subcomponents with distinct verification criteria. Do not add speculative fixes for implementation details that an inspection or test step is already "
            "responsible for resolving. Do not create a redundant preparation step before synthesis. For multi-step work, the final step must synthesize prior results "
            "into the direct user-facing answer. Batch any phase that would otherwise require one response to generate more than about 8 rich repeated items, then "
            "merge/verify the batches in a later step.\n\n"
            f"AUTHORITATIVE PRIMARY TASK REQUEST:\n{original_user_prompt}\n\n"
            f"CURRENT PLAN:\n{json.dumps(plan, ensure_ascii=False)}\n\n"
            f"VERIFIER ISSUES:\n{json.dumps(issues, ensure_ascii=False)}"
        )
        return self._normalize_plan(
            self._structured_generate(prompt, self._planner_schema(), num_predict=3200, think=False)
        )

    def _build_verified_plan(
        self, task_id: str, original_user_prompt: str, execution_context: str, command_envelope: dict,
        task_uuid: str | None = None, *, source_prompt_id: str = "", source_user_message_id: str = "",
        ingrained_detail_count: int = 0, ingrained_task_context: list[str] | tuple[str, ...] = (),
    ) -> tuple[TaskPlan, str, str]:
        plan_data = self._generate_plan(original_user_prompt, execution_context, command_envelope)
        accepted = False
        issues: list[str] = []
        max_plan_repairs = 3
        for repair_cycle in range(max_plan_repairs + 1):
            accepted, issues = self._check_plan(original_user_prompt, plan_data)
            if accepted:
                break
            if repair_cycle >= max_plan_repairs:
                raise RuntimeError(
                    "Norm task plan failed independent verification after "
                    f"{max_plan_repairs} repair cycles: " + "; ".join(issues)
                )
            logging.warning(
                "Plan verifier rejected plan; repairing cycle=%s/%s issues=%s",
                repair_cycle + 1,
                max_plan_repairs,
                issues,
            )
            plan_data = self._repair_plan(original_user_prompt, plan_data, issues)

        plan_json = json.dumps({"schema_version": PROTOCOL_VERSION, "kind": "verified_plan", "command": command_envelope, "plan": plan_data}, ensure_ascii=False, indent=2)
        plan_step = TaskStep(
            id="plan-breakdown",
            name="Plan and break down request",
            description=(
                "AUTHORITATIVE PRIMARY TASK REQUEST:\n" + original_user_prompt
                + "\n\nGenerate explicit bounded actionable steps before execution."
            ),
            verify="Plan must preserve the original request and expose actionable bounded work.",
        )
        verify_step = TaskStep(
            id="verify-plan",
            name="Independently verify task plan",
            description="Double-check the generated plan for coverage, scope, ordering, boundedness, and final synthesis.",
            verify="Independent verifier accepted the generated plan.",
            depends_on=(plan_step.id,),
        )

        generated: list[TaskStep] = []
        previous = verify_step.id
        total = len(plan_data["steps"])
        for index, item in enumerate(plan_data["steps"], start=1):
            step_id = f"step-{index:02d}"
            prompt = (
                "PROMPT ORIGIN: norm_generated_step\n"
                f"TASK STEP: {index}/{total}\n"
                f"STEP NAME: {item['name']}\n\n"
                "AUTHORITATIVE PRIMARY TASK REQUEST:\n"
                + original_user_prompt
                + "\n\nNORM-GENERATED EXECUTION STEP:\n"
                + item["description"]
                + "\n\nVERIFICATION REQUIREMENT:\n"
                + item["verify"]
                + "\n\nDo only this step. Do not silently absorb later steps. Return a concise durable result for subsequent steps. "
                + ("Because this is the final step, return the direct user-facing answer using prior completed step results." if index == total else "Do not present this intermediate result as the final answer.")
            )
            generated.append(TaskStep(
                id=step_id,
                name=item["name"],
                description=prompt,
                verify=item["verify"],
                depends_on=(previous,),
            ))
            previous = step_id

        final_dependency = generated[-1].id if generated else verify_step.id
        final_verify_step = TaskStep(
            id="final-verify",
            name="Verify final candidate envelope",
            description=(
                "Validate the queued final-candidate JSON envelope against the runtime schema, then independently verify "
                "that its user-facing result satisfies the authoritative request and its artifact claims match observed evidence."
            ),
            verify="Structured final verification must accept schema, requirements, and artifact evidence.",
            depends_on=(final_dependency,),
        )
        return TaskPlan(
            task_id=task_id,
            title=plan_data["title"],
            steps=(plan_step, verify_step, *generated, final_verify_step),
            task_uuid=task_uuid or str(uuid.uuid4()),
            original_request=original_user_prompt,
            source_prompt_id=str(source_prompt_id or ""),
            source_user_message_id=str(source_user_message_id or ""),
            ingrained_detail_count=max(0, int(ingrained_detail_count or 0)),
            ingrained_task_context=tuple(str(v) for v in ingrained_task_context if str(v).strip()),
        ), plan_json, "Independent plan verification accepted."

    @staticmethod
    def _deferred_append_placeholder(
        task_id: str, original_user_prompt: str, command_envelope: dict, *,
        source_prompt_id: str = "", source_user_message_id: str = "",
        ingrained_detail_count: int = 0, ingrained_task_context: list[str] | tuple[str, ...] = (),
    ) -> TaskPlan:
        predecessor = str(command_envelope.get("previous_task_id") or "")
        step = str(command_envelope.get("previous_step_id") or "")
        boundary = f"{predecessor}/{step}" if step else predecessor
        return TaskPlan(
            task_id=task_id,
            title="Deferred append awaiting verified predecessor",
            steps=(TaskStep(
                id="deferred-plan",
                name="Wait for verified predecessor, then plan follow-up",
                description=(
                    "AUTHORITATIVE PRIMARY TASK REQUEST:\n" + original_user_prompt
                    + "\n\nThis is a durable scheduler placeholder, not an execution plan. "
                    + f"Do not generate the real plan until predecessor {boundary} is fully verified."
                ),
                verify="Predecessor task must complete and pass structured final verification before execution planning begins.",
            ),),
            original_request=original_user_prompt,
            source_prompt_id=str(source_prompt_id or ""),
            source_user_message_id=str(source_user_message_id or ""),
            ingrained_detail_count=max(0, int(ingrained_detail_count or 0)),
            ingrained_task_context=tuple(str(v) for v in ingrained_task_context if str(v).strip()),
        )

    @staticmethod
    def _action_jobs_for_plan(
        plan: TaskPlan,
        *,
        original_user_prompt: str,
        task_user_prompt: str,
        project_id: str,
        user_message_id: str,
        thread_ids: list[str],
        primary_thread_id: str,
        command_envelope: dict,
        related_running_work: list[dict],
        resource_status: dict | None,
        ingrained_state: dict | None = None,
    ) -> list[PromptJob]:
        jobs: list[PromptJob] = []
        action_steps = [step for step in plan.steps[2:] if step.id != "final-verify"]
        final_verify = next(step for step in plan.steps if step.id == "final-verify")
        source_request_type = str(command_envelope["category"])
        for index, step in enumerate(action_steps):
            jobs.append(PromptJob(
                message_id=step.node_id,
                task_id=plan.task_id,
                step_id=step.id,
                prompt=step.description,
                project_id=project_id,
                context="",
                attempt=0,
                created_at=time.time(),
                metadata={
                    "step_name": step.name,
                    "verification": step.verify,
                    "prompt_origin": "norm_generated_step",
                    "context_id": plan.task_id,
                    "original_user_message_id": user_message_id,
                    "original_user_prompt": original_user_prompt,
                    "task_user_prompt": task_user_prompt,
                    "ingrained_detail_count": int((ingrained_state or {}).get("captured") or 0),
                    "ingrained_task_context": list((ingrained_state or {}).get("current_task_context") or []),
                    "source_prompt_id": str(plan.source_prompt_id or ""),
                    "generated_step_index": index + 1,
                    "generated_step_count": len(action_steps),
                    "source_request_type": source_request_type,
                    "command_envelope": command_envelope,
                    "related_running_work": related_running_work,
                    "resource_status": merge_resource_status(resource_status),
                    "is_final_candidate": index == len(action_steps) - 1,
                    "final_verification_step_id": "final-verify",
                    "final_verification_node_id": final_verify.node_id,
                    "task_uuid": plan.task_uuid,
                    "node_id": step.node_id,
                    "queue_protocol_version": PROTOCOL_VERSION,
                    "coordinator_source": "local",
                    "conversation": {
                        "thread_ids": thread_ids,
                        "primary_thread_id": primary_thread_id,
                        "user_message_id": user_message_id,
                    },
                },
                chain_id=plan.task_uuid,
                previous_prompt_id="",
                next_prompt_id="",
                chain_index=index,
                recovery_attempted=False,
                task_uuid=plan.task_uuid,
                node_id=step.node_id,
                chain_uuid=plan.task_uuid,
                request_type=(source_request_type if len(action_steps) == 1 else
                              ("conclusion" if index == len(action_steps) - 1 else "step")),
            ))
        if not jobs:
            raise RuntimeError("verified plan produced no action jobs")
        return jobs

    def materialize_deferred_append(self, redis_id: str, job: PromptJob) -> None:
        metadata = dict(job.metadata or {})
        original_user_prompt = str(metadata.get("original_user_prompt") or "").strip()
        task_user_prompt = str(metadata.get("task_user_prompt") or original_user_prompt).strip()
        execution_context = str(metadata.get("execution_context") or "")
        command_envelope = self._validate_append_target(
            self._command_from_deferred_metadata(metadata, task_user_prompt),
            list(metadata.get("related_running_work") or []),
        )
        plan, plan_json, plan_verification = self._build_verified_plan(
            job.task_id, task_user_prompt, execution_context, command_envelope, task_uuid=job.task_uuid or job.chain_uuid,
            source_prompt_id=str(metadata.get("source_prompt_id") or ""),
            source_user_message_id=str(metadata.get("original_user_message_id") or ""),
            ingrained_detail_count=int(metadata.get("ingrained_detail_count") or 0),
            ingrained_task_context=list(metadata.get("ingrained_task_context") or []),
        )
        self.durable.start_task(plan)
        if hasattr(self.coordinator.live, "update_plan"):
            self.coordinator.live.update_plan(job.task_id, plan.title, plan.as_dict())
        else:
            self.coordinator.live.start_task(job.task_id, plan.title, plan.as_dict())
        planning_records = (
            (plan.steps[0], plan_json, "Generated plan parsed and bounded to explicit actionable steps."),
            (plan.steps[1], plan_verification, plan_verification),
        )
        for step, summary, verification in planning_records:
            now = utc_now()
            self.coordinator.live.step_started(job.task_id, step.id, step.name)
            self.durable.checkpoint_step(StepResult(
                task_id=job.task_id, step_id=step.id, name=step.name, status=StepStatus.COMPLETED,
                summary=summary, verification=verification, started_at=now, completed_at=now,
            ))
            self.coordinator.live.step_completed(job.task_id, step.id, summary, verification)
        previous_snapshot = self.durable.task_plan_snapshot(str(command_envelope.get("previous_task_id") or ""))
        related = [previous_snapshot] if previous_snapshot else list(metadata.get("related_running_work") or [])
        jobs = self._action_jobs_for_plan(
            plan,
            original_user_prompt=original_user_prompt,
            task_user_prompt=task_user_prompt,
            project_id=job.project_id,
            user_message_id=str(metadata.get("original_user_message_id") or ""),
            thread_ids=list((metadata.get("conversation") or {}).get("thread_ids") or []),
            primary_thread_id=str((metadata.get("conversation") or {}).get("primary_thread_id") or ""),
            command_envelope=command_envelope,
            related_running_work=related,
            resource_status=metadata.get("resource_status") if isinstance(metadata.get("resource_status"), dict) else None,
            ingrained_state={
                "captured": int(metadata.get("ingrained_detail_count") or 0),
                "current_task_context": list(metadata.get("ingrained_task_context") or []),
            },
        )
        self.prompt_queue.replace_claimed_with_chain(redis_id, jobs)
        logging.info("Deferred append plan materialized task=%s action_steps=%s", job.task_id, len(jobs))

    @staticmethod
    def _command_from_deferred_metadata(metadata: dict, original_user_prompt: str) -> dict:
        raw = metadata.get("command_envelope")
        if not isinstance(raw, dict):
            raise ValueError("deferred append is missing command_envelope")
        return normalize_command(raw, original_user_prompt)

    def _enqueue_and_wait(
        self,
        prompt: str,
        *,
        original_user_prompt: str,
        task_user_prompt: str,
        project_id: str,
        thread_ids: list[str],
        primary_thread_id: str,
        user_message_id: str,
        resource_status: dict | None = None,
        preclassified_command: dict | None = None,
        related_running_work: list[dict] | None = None,
        ingrained_state: dict | None = None,
        source_prompt_id: str = "",
    ) -> tuple[str, str, dict]:
        if self.prompt_queue is None or self.coordinator is None or self.durable is None:
            raise RuntimeError("queued conversation execution is not configured")

        task_id = f"chat-{uuid.uuid4()}"
        task_user_prompt = str(task_user_prompt or original_user_prompt).strip() or original_user_prompt
        related_running_work = list(related_running_work or self._related_running_work(project_id, primary_thread_id))
        initial_related_ids = {str(item.get("task_id") or "") for item in related_running_work}
        if isinstance(preclassified_command, dict):
            command_envelope = normalize_command(preclassified_command, task_user_prompt)
            command_envelope = self._validate_append_target(command_envelope, related_running_work)
        else:
            command_envelope = self._classify_command(task_user_prompt, related_running_work)

        # Narrow the concurrent-request race BEFORE generating a plan. If an earlier task became
        # visible while classification was running, reclassify once against that new predecessor.
        if command_envelope.get("category") != "append":
            late_related = self._related_running_work(project_id, primary_thread_id)
            late_ids = {str(item.get("task_id") or "") for item in late_related}
            if late_ids - initial_related_ids:
                late_command = self._classify_command(task_user_prompt, late_related)
                if late_command.get("category") == "append":
                    command_envelope = late_command
                    related_running_work = late_related

        ingrained_state = dict(ingrained_state or {})
        current_context = list(ingrained_state.get("current_task_context") or [])
        test_fit_context = self._test_unresolved_bits(
            project_id=project_id, task_id=task_id, task_request=task_user_prompt, thread_ids=thread_ids,
            current_user_message_id=user_message_id,
        )
        if test_fit_context:
            current_context.extend(test_fit_context)
            current_context = list(dict.fromkeys(current_context))
            ingrained_state["current_task_context"] = current_context
            prompt += (
                "\n\nPreviously unresolved user context that was test-fit and found relevant to THIS task only:\n- "
                + "\n- ".join(test_fit_context)
            )

        if command_envelope.get("category") == "append":
            placeholder = self._deferred_append_placeholder(
                task_id, task_user_prompt, command_envelope,
                source_prompt_id=source_prompt_id,
                source_user_message_id=user_message_id,
                ingrained_detail_count=int(ingrained_state.get("captured") or 0),
                ingrained_task_context=current_context,
            )
            self.coordinator.start(placeholder)
            deferred_step = placeholder.steps[0]
            deferred_job = PromptJob(
                message_id=deferred_step.node_id,
                task_id=task_id,
                step_id="deferred-plan",
                prompt=("Deferred append planning control job. Do not execute user work directly. "
                        "Wait for the verified predecessor, then materialize the execution plan."),
                project_id=project_id,
                context="",
                attempt=0,
                created_at=time.time(),
                metadata={
                    "step_name": "Wait for verified predecessor, then plan follow-up",
                    "verification": "Real execution planning starts only after predecessor final verification.",
                    "prompt_origin": "runtime_deferred_append",
                    "context_id": task_id,
                    "original_user_message_id": user_message_id,
                    "original_user_prompt": original_user_prompt,
                    "task_user_prompt": task_user_prompt,
                    "ingrained_detail_count": int(ingrained_state.get("captured") or 0),
                    "ingrained_task_context": current_context,
                    "source_prompt_id": str(source_prompt_id or ""),
                    "execution_context": prompt,
                    "source_request_type": "append",
                    "command_envelope": command_envelope,
                    "related_running_work": related_running_work,
                    "resource_status": merge_resource_status(resource_status),
                    "queue_protocol_version": PROTOCOL_VERSION,
                    "coordinator_source": "local",
                    "task_uuid": placeholder.task_uuid,
                    "node_id": deferred_step.node_id,
                    "conversation": {
                        "thread_ids": thread_ids,
                        "primary_thread_id": primary_thread_id,
                        "user_message_id": user_message_id,
                    },
                },
                chain_id=placeholder.task_uuid,
                previous_prompt_id="",
                next_prompt_id="",
                chain_index=0,
                recovery_attempted=False,
                request_type="deferred_append_plan",
                task_uuid=placeholder.task_uuid,
                node_id=deferred_step.node_id,
                chain_uuid=placeholder.task_uuid,
            )
            if hasattr(self.durable, "record_dependency_edge"):
                self.durable.record_dependency_edge(
                    str(command_envelope.get("previous_task_id") or ""),
                    str(command_envelope.get("previous_step_id") or ""),
                    task_id,
                    "",
                    edge_type="append",
                    metadata={"legacy_previous_task_id": command_envelope.get("previous_task_id"), "legacy_previous_step_id": command_envelope.get("previous_step_id")},
                )
            self.prompt_queue.enqueue(deferred_job)
            logging.info(
                "Append accepted as durable deferred plan task=%s predecessor=%s/%s",
                task_id, command_envelope.get("previous_task_id"),
                command_envelope.get("previous_step_id") or "<task-complete>",
            )
        else:
            plan, plan_json, plan_verification = self._build_verified_plan(
                task_id, task_user_prompt, prompt, command_envelope,
                source_prompt_id=source_prompt_id,
                source_user_message_id=user_message_id,
                ingrained_detail_count=int(ingrained_state.get("captured") or 0),
                ingrained_task_context=current_context,
            )
            self.coordinator.start(plan)
            planning_records = (
                (plan.steps[0], plan_json, "Generated plan parsed and bounded to explicit actionable steps."),
                (plan.steps[1], plan_verification, plan_verification),
            )
            for step, summary, verification in planning_records:
                now = utc_now()
                self.coordinator.live.step_started(task_id, step.id, step.name)
                self.durable.checkpoint_step(StepResult(
                    task_id=task_id, step_id=step.id, name=step.name, status=StepStatus.COMPLETED,
                    summary=summary, verification=verification, started_at=now, completed_at=now,
                ))
                self.coordinator.live.step_completed(task_id, step.id, summary, verification)
            jobs = self._action_jobs_for_plan(
                plan,
                original_user_prompt=original_user_prompt,
                task_user_prompt=task_user_prompt,
                project_id=project_id,
                user_message_id=user_message_id,
                thread_ids=thread_ids,
                primary_thread_id=primary_thread_id,
                command_envelope=command_envelope,
                related_running_work=related_running_work,
                resource_status=resource_status,
                ingrained_state=ingrained_state,
            )
            self.prompt_queue.enqueue_chain(jobs)

        deadline = time.monotonic() + self.wait_timeout_seconds
        while time.monotonic() < deadline:
            status = self.durable.task_status(task_id)
            if status == "completed":
                reply = self.durable.latest_summary(task_id)
                if not reply:
                    raise RuntimeError("queued task completed without a durable result")
                task_resource_status = (
                    self.durable.task_resource_status(task_id)
                    if hasattr(self.durable, "task_resource_status")
                    else full_context_status()
                )
                return task_id, self._limit_human_reply(task_id, reply), merge_resource_status(resource_status, task_resource_status)
            if status == "failed":
                raise RuntimeError(f"queued task failed: {task_id}")
            if status == "cancelled":
                raise RuntimeError(f"queued task cancelled: {task_id}")
            time.sleep(0.1)
        raise TimeoutError(f"queued task did not finish within {self.wait_timeout_seconds:g} seconds")

    def list_threads(self, project_id: str = "default", limit: int = 50) -> list[dict]:
        self.store.ensure_project(project_id)
        return self.store.list_threads(project_id, limit=max(1, min(int(limit), 200)))

    def create_named_thread(self, project_id: str = "default", title: str | None = None) -> dict:
        self.store.ensure_project(project_id)
        clean_title = (title or "New thread").strip()[:120] or "New thread"
        thread_id = self.store.create_thread(project_id, clean_title)
        return {"thread_id": thread_id, "title": clean_title, "project_id": project_id}

    def _route(self, project_id: str, message: str, explicit_thread_id: str | None = None) -> RouteDecision:
        candidates = self.store.list_threads(project_id, limit=self.candidate_threads)
        if explicit_thread_id is not None:
            if not self.store.thread_exists(project_id, explicit_thread_id):
                raise ValueError(f'Unknown thread_id: {explicit_thread_id}')
            return RouteDecision((explicit_thread_id,), explicit_thread_id, 1.0)
        if not candidates:
            title = message.strip()[:80] or 'New thread'
            new_id = self.store.create_thread(project_id, title)
            return RouteDecision((new_id,), new_id, 1.0, created_thread=True)
        routing_context = []
        for c in candidates:
            recent = self.store.recent_messages(c['thread_id'], limit=4)
            routing_context.append({
                'thread_id': c['thread_id'],
                'title': c['title'],
                'summary': c['summary'],
                'recent': [{'role': m['role'], 'content': m['content'][:800]} for m in recent]
            })
        prompt = (
            "Choose thread membership based on semantic relevance of title, summary, and recent content. "
            "Do not use memory types. Return STRICT JSON object: "
            '{"thread_ids":["..."],"primary_thread_id":"..."|null,"confidence":0.0,"clarification_question":"..."|null,"create_thread_title":"..."|null}\n'
            f"Current message: {message}\n"
            f"Context: {json.dumps(routing_context, ensure_ascii=False)}"
        )
        raw = self.ollama.generate(prompt, think=False, temperature=0.0)
        parsed = self.ollama.parse_json(raw)
        valid_ids = {c['thread_id'] for c in candidates}
        selected = []
        for tid in parsed.get('thread_ids', []):
            if isinstance(tid, str) and tid in valid_ids and tid not in selected:
                selected.append(tid)
        primary = parsed.get('primary_thread_id')
        if primary not in valid_ids:
            primary = None
        try:
            confidence = float(parsed.get('confidence', 0.0))
        except (ValueError, TypeError):
            confidence = 0.0
        confidence = max(0.0, min(1.0, confidence))
        create_title = parsed.get('create_thread_title')
        if isinstance(create_title, str) and create_title.strip():
            new_id = self.store.create_thread(project_id, create_title.strip()[:120])
            if new_id not in selected:
                selected.insert(0, new_id)
            primary = new_id
            return RouteDecision(tuple(selected), primary, max(confidence, 0.75), created_thread=True)
        if primary is None and selected:
            primary = selected[0]
        if not selected and confidence >= self.routing_threshold:
            primary = candidates[0]['thread_id']
            selected = [primary]
        if confidence < self.routing_threshold:
            q = parsed.get('clarification_question')
            if not q or not str(q).strip():
                q = 'Which existing thread should I attach this to, or should I start a new one?'
            return RouteDecision(tuple(selected), primary, confidence, clarification_question=q)
        if not primary:
            title = message.strip()[:80] or 'New thread'
            new_id = self.store.create_thread(project_id, title)
            return RouteDecision((new_id,), new_id, 1.0, created_thread=True)
        return RouteDecision(tuple(selected or [primary]), primary, confidence)

    def _answer_prompt(
        self, project_id: str, thread_ids: list[str], message: str, *,
        original_user_message: str | None = None, current_task_context: list[str] | None = None,
    ) -> tuple[str, dict]:
        resource_status = full_context_status()
        branches = []
        for thread_id in thread_ids:
            try:
                recent = self.store.recent_messages(thread_id, limit=self.recent_message_limit)
                summary = self.store.latest_summary(thread_id)
            except psycopg.Error as exc:
                resource_status = merge_resource_status(
                    resource_status,
                    impaired_context_status("postgres", f"{type(exc).__name__}: {exc}"),
                )
                recent = []
                summary = ""
                logging.warning("PostgreSQL branch context unavailable; generating with impaired context: %s", exc)
            branches.append({
                "thread_id": thread_id,
                "summary": summary,
                "recent": [{"role": m["role"], "content": m["content"]} for m in recent]
            })
        try:
            memories = self.store.active_memories(project_id, thread_ids, limit=40)
        except psycopg.Error as exc:
            resource_status = merge_resource_status(
                resource_status,
                impaired_context_status("postgres", f"{type(exc).__name__}: {exc}"),
            )
            memories = []
            logging.warning("PostgreSQL durable memories unavailable; generating with impaired context: %s", exc)
        memory_view = [{"memory_id": m["memory_id"], "type": m["type"], "content": m["content"]} for m in memories]
        background = ""
        history_view = []
        if self.durable is not None and hasattr(self.durable, "background_memory_context"):
            try:
                background = self.durable.background_memory_context()
            except psycopg.Error as exc:
                resource_status = merge_resource_status(
                    resource_status,
                    impaired_context_status("postgres", f"{type(exc).__name__}: {exc}"),
                )
                logging.warning("PostgreSQL background memory unavailable; generating with impaired context: %s", exc)
        if self.durable is not None and hasattr(self.durable, "relevant_task_history"):
            try:
                history_view = self.durable.relevant_task_history(message, limit=6)
            except psycopg.Error as exc:
                resource_status = merge_resource_status(
                    resource_status,
                    impaired_context_status("postgres", f"{type(exc).__name__}: {exc}"),
                )
                logging.warning("PostgreSQL compact task history unavailable; generating with impaired context: %s", exc)
        parts = [
            f"Persistent operating principles:\n{self._persistent_instruction_text()}",
            "Answer the user directly. Use the supplied branch context and durable memory only when relevant. Use stable user-language memory to interpret recurring shorthand, terminology, intended meanings, tone, and prose preferences, and match those preferences naturally when appropriate without copying obvious typos or one-off emotional spikes. Do not mention routing, memory machinery, database storage, or hidden context. If stored context conflicts with the current user message, prefer the current user message.",
            f"Resource status JSON: {json.dumps(resource_status, ensure_ascii=False)}",
            f"Project: {project_id}",
            f"Selected branches: {json.dumps(branches, ensure_ascii=False)}",
            f"Active durable memories: {json.dumps(memory_view, ensure_ascii=False)}",
            f"Cross-task PostgreSQL background memory: {background}",
            f"Relevant prior task history: {json.dumps(history_view, ensure_ascii=False)}",
            f"Current primary task request: {message}",
            (
                "Original user turn for provenance only. Meaningful sidecar details may already have been routed to durable homes; "
                "do not create extra task work from them unless they are also listed in current-task ingrained context: "
                + str(original_user_message or message)
            ),
            "Current-task ingrained context: " + json.dumps(list(current_task_context or []), ensure_ascii=False),
        ]
        if self.file_tools is not None:
            parts.insert(1, self.file_tools.instructions())
        return "\n".join(parts), resource_status

    def _answer_with_tools(self, prompt: str) -> str:
        if self.file_tools is None:
            return self.ollama.generate(prompt, think=False, temperature=0.35)
        history: list[dict] = []
        for _ in range(self.max_tool_rounds):
            working_prompt = prompt
            if history:
                working_prompt += (
                    "\n\nTool interaction history (trusted executor results):\n"
                    + json.dumps(history, ensure_ascii=False)
                    + "\nContinue the task. Call more tools with strict JSON or answer the user normally."
                )
            raw = self.ollama.generate(working_prompt, think=False, temperature=0.2)
            calls = self._parse_tool_calls(raw)
            if calls is None:
                return raw
            results = []
            for call in calls:
                name = call.get("name")
                arguments = call.get("arguments")
                if not isinstance(name, str) or not isinstance(arguments, dict):
                    results.append({"ok": False, "error": "invalid tool call shape"})
                    continue
                results.append(self.file_tools.execute(name, arguments))
            history.append(redact({"assistant_tool_calls": calls, "tool_results": results}))
        return "I stopped after the safe tool-call limit. No deletion was performed. Review the latest tool results before continuing."

    @staticmethod
    def _parse_tool_calls(raw: str) -> list[dict] | None:
        try:
            parsed = OllamaClient.parse_json(raw)
        except (ValueError, json.JSONDecodeError):
            return None
        calls = parsed.get("tool_calls")
        if not isinstance(calls, list) or not calls:
            return None
        if not all(isinstance(call, dict) for call in calls):
            return None
        return calls

    def _refresh_summary(self, thread_id: str, assistant_message_id: str) -> None:
        old = self.store.latest_summary(thread_id)
        recent = self.store.recent_messages(thread_id, limit=20)
        recent_view = [{"role": m["role"], "content": m["content"]} for m in recent]
        prompt = (
            "Return STRICT JSON object exactly shaped {\"summary\":\"...\"}.\n"
            "The summary must be a concise rolling branch state preserving: durable facts, decisions, constraints, current work/state, unresolved questions, and stable user terminology/meaning conventions or communication preferences when they materially affect future interpretation.\n"
            "Explicitly distinguish superseded/outdated information from current information.\n"
            "Do not add facts not present in the provided context.\n\n"
            f"Old summary:\n{old}\n\n"
            f"Recent messages:\n{json.dumps(recent_view, ensure_ascii=False)}"
        )
        raw = self.ollama.generate(prompt, think=False, temperature=0.1, num_predict=900)
        try:
            parsed = self.ollama.parse_json(raw)
        except ValueError:
            logging.warning("Skipping malformed summary JSON for thread %s", thread_id)
            return
        summary = parsed.get("summary")
        if not isinstance(summary, str) or not summary.strip():
            logging.warning("Skipping empty summary for thread %s", thread_id)
            return
        self.store.save_summary(thread_id, summary.strip(), covers_through_message_id=assistant_message_id)

    def _extract_memories(self, project_id: str, thread_ids: list[str], source_message_id: str, user_message: str, assistant_reply: str) -> None:
        active = self.store.active_memories(project_id, thread_ids, limit=60)
        active_view = [{"memory_id": m["memory_id"], "type": m["type"], "content": m["content"]} for m in active]
        valid_memory_ids = {m["memory_id"] for m in active}
        prompt = (
            "You are Norm. Extract durable, reusable memories from the conversation below.\n"
            "Return STRICT JSON only, exactly shaped:\n"
            '{"items":[{"type":"fact|preference|decision|constraint|task|assumption","content":"...","supersedes":["memory_id"]}]}\n'
            "Rules:\n"
            "- Include only durable reusable information, not transient filler.\n"
            "- Do not create a duplicate for information already represented by an active memory; ingrained details from this same turn may already have been routed before task execution.\n"
            "- Preserve explicit or recurring communication preferences: tone, prose style, preferred directness/detail, recurring shorthand, and stable word/phrase meanings or usage conventions. Record tone/prose preferences as preference memories and stable terminology meanings/usages as fact memories.\n"
            "- Do not promote a one-off typo, temporary mood, isolated profanity, or accidental wording into a stable language memory unless the user explicitly says it is intentional or it recurs consistently.\n"
            "- Use supersedes only when the new item clearly replaces an older active memory.\n"
            "- Do not infer unsupported facts.\n"
            "- If there are no durable memories, return {\"items\":[]}.\n\n"
            "Active memories:\n"
            + json.dumps(active_view, indent=2)
            + "\n\nUser message:\n"
            + user_message
            + "\n\nAssistant reply:\n"
            + assistant_reply
        )
        raw = self.ollama.generate(prompt, think=False, temperature=0.0, num_predict=1400)
        try:
            parsed = self.ollama.parse_json(raw)
        except ValueError:
            logging.warning("Skipping malformed memory JSON")
            return
        items = parsed.get("items")
        if not isinstance(items, list):
            logging.warning("Skipping memory extraction with non-list items")
            return
        valid_types = {"fact", "preference", "decision", "constraint", "task", "assumption"}
        for item in items:
            if not isinstance(item, dict):
                continue
            memory_type = item.get("type")
            content = item.get("content")
            if not isinstance(memory_type, str) or memory_type not in valid_types:
                continue
            if not isinstance(content, str) or not content.strip():
                continue
            supersedes = item.get("supersedes", [])
            if not isinstance(supersedes, list):
                supersedes = []
            supersedes = [x for x in supersedes if isinstance(x, str) and x in valid_memory_ids]
            new_id = self.store.upsert_memory(project_id, memory_type, content.strip(), thread_ids, source_message_id=source_message_id)
            supersedes = [x for x in supersedes if x != new_id]
            if supersedes:
                self.store.supersede_memories(supersedes, new_id)

