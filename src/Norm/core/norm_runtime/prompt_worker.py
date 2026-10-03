from __future__ import annotations

from .secret_redaction import redact

import json
import logging
import re
import socket
import uuid
from pathlib import Path
from datetime import datetime, timedelta, timezone
from threading import Event, Lock, Thread
from time import monotonic, time

import psycopg
import redis

from .history_maintenance import DeepHistoryMaintainer
from .temp_cleanup import cleanup_task_temp, cleanup_temp_root
from .models import StepResult, StepStatus, TaskPlan, TaskStep
from .ollama_client import ModelGenerationCancelled, ModelOutputTruncated, OllamaClient
from .prompt_queue import PromptJob, RedisPromptQueue, normalize_request_type
from .protocol import PROTOCOL_VERSION, final_verification_schema, normalize_command, step_verification_schema, validate_final_candidate, validate_final_verification, validate_step_verification
from .resource_status import full_context_status, impaired_context_status, merge_resource_status
from .settings import load_path_settings


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)



class VerifierProtocolError(RuntimeError):
    pass


class TaskRoundBudgetExceeded(RuntimeError):
    pass


class PromptWorker:
    def __init__(
        self,
        queue: RedisPromptQueue,
        client: OllamaClient,
        live,
        durable,
        *,
        heartbeat_seconds: int = 300,
        claim_idle_seconds: int = 900,
        poll_ms: int = 5000,
        consumer: str | None = None,
        deferred_append_planner=None,
        context_injections=None,
    ) -> None:
        self.queue = queue
        self.client = client
        self.live = live
        self.durable = durable
        self.heartbeat_seconds = max(1, int(heartbeat_seconds))
        self.claim_idle_ms = max(1000, int(claim_idle_seconds) * 1000)
        self.poll_ms = max(100, int(poll_ms))
        self.consumer = consumer or f"{socket.gethostname()}-{id(self):x}"
        self.queue.consumer = self.consumer
        self._stop = Event()
        self._drain = Event()
        self._idle = Event()
        self._idle.set()
        self._thread: Thread | None = None
        self._blank_claims = 0
        self._last_memory_maintenance_check = 0.0
        self._last_redis_maintenance_check = 0.0
        self._last_weekly_cleanup_check = 0.0
        self._weekly_cleanup_redis = None
        self._stop_after_step = Event()
        self._stop_all_now = Event()
        self._state_lock = Lock()
        self._active_task_id = ""
        self._suppress_requested: set[str] = set()
        self._manual_memory_condense_mode = ""
        self.deferred_append_planner = deferred_append_planner
        self.context_injections = context_injections

    def start(self) -> Thread:
        if self._thread and self._thread.is_alive():
            return self._thread
        self._stop.clear()
        self._drain.clear()
        self._stop_after_step.clear()
        self._stop_all_now.clear()
        self._idle.set()
        self._thread = Thread(target=self.run_forever, name="norm-prompt-worker", daemon=True)
        self._thread.start()
        return self._thread

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=timeout)

    def request_drain(self) -> None:
        self._drain.set()

    def request_stop_after_current_step(self) -> None:
        self._stop_after_step.set()

    def request_stop_all_now(self) -> None:
        self._stop_all_now.set()
        self._drain.set()

    def active_task_id(self) -> str:
        with self._state_lock:
            return self._active_task_id

    def _consume_context_injections(self, job: PromptJob) -> list[dict]:
        if self.context_injections is None:
            return []
        metadata = dict(job.metadata or {})
        try:
            after = int(metadata.get("context_injection_cursor") or 0)
        except (TypeError, ValueError):
            after = 0
        entries = list(self.context_injections.read(job.task_id, after=after) or [])
        if not entries:
            return []
        cursor = max(int(item.get("id") or 0) for item in entries)
        applied = list(metadata.get("applied_context_injections") or [])
        seen = {int(item.get("id") or 0) for item in applied if isinstance(item, dict)}
        for item in entries:
            iid = int(item.get("id") or 0)
            if iid and iid not in seen:
                applied.append({"id": iid, "content": str(item.get("content") or "")})
                seen.add(iid)
        metadata["context_injection_cursor"] = cursor
        metadata["applied_context_injections"] = applied[-32:]
        job.metadata = metadata
        logging.info("Applied user context injection(s) task=%s step=%s ids=%s", job.task_id, job.step_id, [item.get("id") for item in entries])
        return entries

    @staticmethod
    def _context_injection_block(entries: list[dict]) -> str:
        if not entries:
            return ""
        parts = [
            "[USER CONTEXT INTERJECTION FOR THE ACTIVE TASK]",
            "The following text was supplied by the user after this task began. It is user-authored context, not a Norm-generated step. Incorporate it into the current task and adapt generated work when it changes the intended interpretation.",
        ]
        for item in entries:
            parts.append(f"Injection {int(item.get('id') or 0)}:\n{str(item.get('content') or '').strip()}")
        return "\n\n".join(parts)

    def _prompt_with_context_injections(self, job: PromptJob, base_prompt: str, *, consume: bool = True) -> str:
        if consume:
            self._consume_context_injections(job)
        metadata = job.metadata or {}
        applied = list(metadata.get("applied_context_injections") or [])
        block = self._context_injection_block(applied)
        if not block:
            return str(base_prompt)
        return str(base_prompt).rstrip() + "\n\n" + block

    def _suppression_requested(self, task_id: str) -> bool:
        with self._state_lock:
            return task_id in self._suppress_requested

    def _task_suppressed(self, task_id: str) -> bool:
        if self._suppression_requested(task_id):
            return True
        if self.durable is not None:
            try:
                if self.durable.is_task_tree_suppressed(task_id):
                    return True
            except Exception:
                pass
        return False

    def _clear_suppression_request(self, task_id: str) -> None:
        with self._state_lock:
            self._suppress_requested.discard(task_id)

    def request_suppress_task(self, task_id: str | None = None, reason: str = "") -> dict:
        explicit = bool(str(task_id or "").strip())
        seed = str(task_id or "").strip() or self.active_task_id() or str(self.queue.oldest_task_id() or "")
        if not seed:
            return {"status": "ok", "suppressed": False, "reason": "no active or queued task"}

        target = seed
        tree = []
        if self.durable:
            # No-ID suppression is an operator command against the user-facing work
            # tree, not whichever internal child happens to own the model call.
            if not explicit and hasattr(self.durable, "root_task_id"):
                target = str(self.durable.root_task_id(seed) or seed)
            if hasattr(self.durable, "task_tree"):
                tree = list(self.durable.task_tree(target) or [])
        if not tree:
            status = self.durable.task_status(target) if self.durable else None
            tree = [{"task_id": target, "status": status or "running", "task_kind": "root", "task_depth": 0}]

        terminal = {"completed", "failed", "cancelled"}
        members = [item for item in tree if str(item.get("status") or "") not in terminal]
        if not members:
            status = self.durable.task_status(target) if self.durable else None
            return {"status": "conflict", "suppressed": False, "task_id": target, "task_status": status}

        member_ids = [str(item.get("task_id") or "") for item in members if str(item.get("task_id") or "")]
        reason = reason or "Operator requested /suppress-task."
        payload = {
            "schema_version": 2,
            "suppression_scope": "task_tree",
            "root_task_id": target,
            "captured_at": time(),
            "tasks": [],
        }
        for item in members:
            member_id = str(item.get("task_id") or "")
            payload["tasks"].append({
                "task_id": member_id,
                "task_kind": str(item.get("task_kind") or "root"),
                "task_depth": int(item.get("task_depth") or 0),
                "status_before": str(item.get("status") or "running"),
                "queue": self.queue.snapshot_task_jobs(member_id),
            })

        with self._state_lock:
            self._suppress_requested.update(member_ids)
        try:
            if self.durable and hasattr(self.durable, "suppress_task_tree"):
                snapshot = self.durable.suppress_task_tree(target, member_ids, reason, payload)
            elif self.durable:
                snapshot = self.durable.suppress_task(target, reason, payload)
            else:
                snapshot = {"task_id": target}
            # Soft-delete only explicitly ephemeral+reproducible task-created assets.
            # Suppression is conservative because resume may need review/durable intermediates.
            for member_id in member_ids:
                self._soft_delete_task_assets(member_id, suppressed=True)
            # PostgreSQL now owns the complete durable resume snapshot. Once that
            # transaction succeeds, live Redis copies are redundant and must be removed
            # so the worker can advance immediately to unrelated queued work. Resume
            # restores from the captured snapshot; /flush-suppressed is deletion only.
            queue_cleanup = {"work": 0, "retry": 0, "escalation": 0, "dead": 0, "round_key": 0}
            for member_id in member_ids:
                cleaned = self.queue.cleanup_task(member_id)
                for key in queue_cleanup:
                    queue_cleanup[key] += int(cleaned.get(key) or 0)
            active = self.active_task_id()
            if active in member_ids:
                OllamaClient.cancel_active()
            return {
                "status": "ok", "suppressed": True, "task_id": target,
                "title": snapshot.get("title", ""), "suppressed_task_ids": member_ids,
                "queue_cleanup": queue_cleanup,
            }
        finally:
            # PostgreSQL status is the durable suppression gate. This in-memory set
            # is only a transition/cancellation hint and must not poison later resume.
            with self._state_lock:
                for member_id in member_ids:
                    self._suppress_requested.discard(member_id)

    def flush_suppressed(self) -> dict:
        """Permanently flush every durably suppressed task without one stale Redis error blocking the batch."""
        if self.durable and hasattr(self.durable, "suppressed_task_ids"):
            task_ids = [str(x) for x in self.durable.suppressed_task_ids(limit=10000) if str(x).strip()]
        else:
            tasks = self.durable.suppressed_tasks(limit=10000) if self.durable else []
            task_ids = [str(item.get("task_id") or "") for item in tasks if str(item.get("task_id") or "").strip()]
        queue_cleanup = {"work": 0, "retry": 0, "escalation": 0, "dead": 0, "round_key": 0}
        cleanup_errors: list[str] = []
        for task_id in task_ids:
            try:
                counts = self.queue.cleanup_task(task_id)
                for key in queue_cleanup:
                    queue_cleanup[key] += int(counts.get(key) or 0)
            except Exception as exc:
                cleanup_errors.append(f"queue:{task_id}:{type(exc).__name__}:{exc}")
                logging.warning("Suppressed queue cleanup failed task=%s: %s", task_id, exc)
            try:
                if hasattr(self.live, "cleanup"):
                    self.live.cleanup(task_id)
            except Exception as exc:
                cleanup_errors.append(f"live:{task_id}:{type(exc).__name__}:{exc}")
                logging.warning("Suppressed live-state cleanup failed task=%s: %s", task_id, exc)
            self._clear_suppression_request(task_id)
        # PostgreSQL is authoritative.  Even if a stale Redis artifact could not be
        # removed, do not leave the suppressed task itself immortal. Startup/queue
        # reconciliation can later remove any orphaned live entry because its task no
        # longer exists durably.
        deleted = self.durable.flush_suppressed() if self.durable else 0
        return {
            "status": "ok" if not cleanup_errors else "partial",
            "deleted": deleted,
            "suppressed_ids": task_ids,
            "queue_cleanup": queue_cleanup,
            "cleanup_errors": cleanup_errors,
        }

    def wait_idle(self, timeout: float | None = None) -> bool:
        return self._idle.wait(timeout=timeout)

    def is_idle(self) -> bool:
        return self._idle.is_set()

    def request_memory_condense(self, full: bool = False) -> dict:
        """Schedule background-memory condensation without creating a user task."""
        if not self.durable:
            return {"status": "unavailable", "scheduled": False, "reason": "durable store unavailable"}
        requested = "full" if bool(full) else "incremental"
        with self._state_lock:
            current = self._manual_memory_condense_mode
            if current != "full":
                self._manual_memory_condense_mode = requested
            scheduled = self._manual_memory_condense_mode
        return {
            "status": "ok",
            "scheduled": True,
            "mode": scheduled,
            "runs_when": "worker_idle",
            "worker_idle": self.is_idle(),
        }

    def _run_manual_memory_condense_if_requested(self) -> bool:
        with self._state_lock:
            mode = self._manual_memory_condense_mode
            if not mode:
                return False
            self._manual_memory_condense_mode = ""
        self._idle.clear()
        memory_cfg = self._runtime_config().get("memory", {})
        full = mode == "full"
        try:
            maintainer = DeepHistoryMaintainer(
                self.durable, self.client, runtime_root=self._runtime_root(), config=memory_cfg,
                queue=self.queue, drain_event=self._drain,
            )
            recovery_cleanup = maintainer.cleanup_recovery_state()
            summary = maintainer.rebuild_background_snapshot(incremental=not full)
            details = {
                "status": "success", "mode": mode, "background_chars": len(summary or ""),
                "recovery_cleanup": recovery_cleanup,
            }
            self.durable.record_maintenance_note(
                "manual_background_condensation",
                "[manual_background_condensation] Manual background-memory condensation completed.",
                details=details,
            )
            logging.info("Manual memory condensation completed mode=%s chars=%s", mode, len(summary or ""))
        except Exception as exc:
            try:
                self.durable.record_maintenance_note(
                    "manual_background_condensation",
                    "[manual_background_condensation] Manual background-memory condensation failed; checkpoint retained for retry.",
                    details={"status": "failed", "mode": mode, "error": f"{type(exc).__name__}: {exc}"[:1200]},
                )
            except Exception:
                logging.exception("Could not record failed manual memory-condensation note")
            logging.exception("Manual memory condensation failed mode=%s", mode)
        finally:
            self._idle.set()
        return True

    def run_forever(self) -> None:
        logging.info("Prompt worker started consumer=%s", self.consumer)
        try:
            self._startup_recovery()
        except Exception:
            logging.exception("Startup Redis recovery failed; preserving Redis state")
        while not self._stop.is_set():
            if self._drain.is_set() or self._stop_after_step.is_set():
                break
            try:
                self._maybe_reconcile_redis(reason='periodic')
                self.queue.reclaim_stale()
                try:
                    claimed = self.queue.read_one(block_ms=self.poll_ms)
                except redis.TimeoutError:
                    claimed = None
                if claimed is not None and (self._drain.is_set() or self._stop_after_step.is_set()):
                    redis_id, job = claimed
                    self.queue.ack(redis_id)
                    self.queue.enqueue(job)
                    break
                if claimed is None:
                    restored = self.queue.restore_parked_if_idle()
                    if restored:
                        logging.info("Restored parked prompts count=%s", restored)
                    else:
                        if not self._run_manual_memory_condense_if_requested():
                            self._maybe_consolidate_background_memory()
                    continue
                redis_id, job = claimed
                if not job.prompt or not job.prompt.strip():
                    self._handle_blank(redis_id)
                    continue
                self._blank_claims = 0
                self._idle.clear()
                with self._state_lock:
                    self._active_task_id = job.task_id
                try:
                    self._process(redis_id, job)
                finally:
                    with self._state_lock:
                        if self._active_task_id == job.task_id:
                            self._active_task_id = ""
                    self._idle.set()
            except Exception:
                logging.exception("Prompt worker loop error")
                self._stop.wait(2)
        logging.info("Prompt worker stopped consumer=%s", self.consumer)

    def _process(self, redis_id: str, job: PromptJob) -> None:
        metadata = job.metadata or {}
        request_type = normalize_request_type(job.request_type)
        name = str(metadata.get("step_name") or job.step_id)
        logging.info("Processing queued request task=%s step=%s type=%s", job.task_id, job.step_id, request_type)
        if self._task_suppressed(job.task_id):
            # A durable suppression snapshot is the resume source of truth. Delete any
            # stale live Redis copy so a suppressed tree cannot block unrelated work.
            self.queue.ack(redis_id)
            self._clear_suppression_request(job.task_id)
            logging.info("Skipped suppressed queued task=%s step=%s (removed stale live copy)", job.task_id, job.step_id)
            return
        if self.durable:
            command = self._command_from_job(job)
            if command.get("category") == "append":
                state = self.durable.external_dependency_state(
                    command.get("previous_task_id", ""), command.get("previous_step_id", ""),
                    command.get("previous_task_uuid", ""), command.get("previous_node_id", "")
                )
                if state.get("state") == "waiting":
                    parked = self.queue.park_chain_for_dependencies(redis_id, job)
                    logging.info(
                        "Append parked for predecessor task=%s step=%s predecessor=%s/%s parked=%s",
                        job.task_id, job.step_id, command.get("previous_task_id"),
                        command.get("previous_step_id") or "<task-complete>", parked,
                    )
                    return
                if state.get("state") != "satisfied":
                    self._handle_append_dependency_terminal(redis_id, job, name, command, state)
                    return
            try:
                ready = self.durable.dependencies_satisfied(job.task_id, job.step_id)
            except Exception as exc:
                error = f"{type(exc).__name__}: {exc}"
                self.queue.park_chain(job.chain_id, redis_id)
                logging.exception("Prompt dependency check failed task=%s step=%s", job.task_id, job.step_id)
                return
            if not ready:
                parked = self.queue.park_chain_for_dependencies(redis_id, job)
                logging.info("Prompt parked for dependencies task=%s step=%s parked=%s", job.task_id, job.step_id, parked)
                return
        waiting_child_id = str(metadata.get("waiting_child_task_id") or "").strip()
        if waiting_child_id and self.durable:
            child_status = self.durable.task_status(waiting_child_id)
            if child_status not in {"completed", "failed", "cancelled"}:
                self.queue.park_chain_for_child(redis_id, job, waiting_child_id)
                logging.info("Parent deferred until child terminal parent=%s/%s child=%s status=%s", job.task_id, job.step_id, waiting_child_id, child_status)
                return
        child_answer = self._child_answer_if_ready(job) if waiting_child_id else None
        started = _utc_now()
        started_clock = monotonic()
        self.live.step_started(job.task_id, job.step_id, name)

        heartbeat_stop = Event()
        heartbeat = Thread(
            target=self._heartbeat_loop,
            args=(heartbeat_stop, job, started_clock),
            daemon=True,
        )
        heartbeat.start()
        self.client.set_crash_context(job.task_id, job.step_id)
        try:
            if request_type == "deferred_append_plan":
                if self.deferred_append_planner is None:
                    raise RuntimeError("deferred append planner is not configured")
                self.deferred_append_planner(redis_id, job)
                logging.info("Deferred append planning completed task=%s", job.task_id)
                return
            if request_type == "final_verification":
                self._process_final_verification(redis_id, job, name, started)
                return

            task_memory = ""
            prior = ""
            background = ""
            resource_status = merge_resource_status((job.metadata or {}).get("resource_status"))
            if hasattr(self.live, "working_memory"):
                task_memory = self.live.working_memory(job.task_id)
            if not task_memory.strip() and self.durable and hasattr(self.durable, "completed_step_context"):
                try:
                    prior = self.durable.completed_step_context(job.task_id, job.step_id, max_chars=None)
                except psycopg.Error as exc:
                    resource_status = merge_resource_status(
                        resource_status,
                        impaired_context_status("postgres", f"{type(exc).__name__}: {exc}"),
                    )
                    logging.warning("PostgreSQL prior-step context unavailable; continuing impaired task=%s step=%s error=%s", job.task_id, job.step_id, exc)
            if self.durable and hasattr(self.durable, "background_memory_context"):
                try:
                    background = self.durable.background_memory_context(
                        max_chars=int(self._runtime_config().get("memory", {}).get("background_context_chars", 32000))
                    )
                except psycopg.Error as exc:
                    resource_status = merge_resource_status(
                        resource_status,
                        impaired_context_status("postgres", f"{type(exc).__name__}: {exc}"),
                    )
                    logging.warning("PostgreSQL background context unavailable; continuing impaired task=%s step=%s error=%s", job.task_id, job.step_id, exc)
            recovery = self._progress_oversize_recovery(redis_id, job, prior)
            if recovery.get("parked"):
                return
            recovery_context = str(recovery.get("context") or "").strip()
            if recovery_context:
                prior = (str(prior or "") + "\n\n" + recovery_context).strip()

            step_specific = str(job.prompt or "")
            token = "NORM-GENERATED EXECUTION STEP:"
            if token in step_specific:
                step_specific = step_specific.split(token, 1)[1]
                if "VERIFICATION REQUIREMENT:" in step_specific:
                    step_specific = step_specific.split("VERIFICATION REQUIREMENT:", 1)[0]
            count = self._large_explicit_count(step_specific)
            count_split_ok = self._count_child_decomposition_allowed(step_specific)
            if not recovery_context and child_answer is None and request_type != "conclusion" and (job.metadata or {}).get("allow_subtask", True) is not False and count and count_split_ok:
                self._spawn_count_child_task(redis_id, job, prior, count)
                return
            prompt = self._typed_prompt(
                job, request_type, task_memory=task_memory, prior=prior, background=background,
                resource_status=resource_status,
            )
            if child_answer is not None:
                answer = self._verify_and_correct_without_tools(job, prompt, child_answer)
            else:
                answer = self._generate_with_budget(
                    redis_id, job, prompt, started, name, started_clock
                )
                if answer is None:
                    return
            if self._suppression_requested(job.task_id) or (self.durable and self.durable.task_status(job.task_id) == "suppressed"):
                self._handle_suppressed(redis_id, job, reason="Suppression requested during execution.")
                return
            structured_summary = self._step_result_envelope(job, request_type, answer, resource_status=resource_status)
            result = StepResult(
                task_id=job.task_id,
                step_id=job.step_id,
                name=name,
                status=StepStatus.COMPLETED,
                summary=structured_summary,
                verification=str(metadata.get("verification") or ""),
                started_at=started,
                completed_at=_utc_now(),
            )
            task_finished = False
            if self.durable:
                self.durable.checkpoint_step(result)
                task_finished = self.durable.all_steps_completed(job.task_id)
                if task_finished:
                    self.durable.finalize_task(job.task_id, answer, 'completed')
                    if not self.durable.terminal_summary_verified(job.task_id):
                        raise RuntimeError('durable terminal summary verification failed')
            self.live.step_completed(
                job.task_id,
                job.step_id,
                result.summary,
                result.verification,
                preserve_model_buffer=self._stop_after_step.is_set(),
            )
            if bool(metadata.get("is_final_candidate")) and not task_finished:
                self._enqueue_final_verification(job, answer)
            if task_finished:
                self.live.finish(job.task_id, answer)
                self._clear_terminal_redis(job.task_id)
            else:
                self.queue.ack(redis_id)
            logging.info("Prompt completed task=%s step=%s type=%s", job.task_id, job.step_id, request_type)
        except ModelGenerationCancelled as exc:
            if self._suppression_requested(job.task_id) or (self.durable and self.durable.task_status(job.task_id) == "suppressed"):
                self._handle_suppressed(redis_id, job, reason=str(exc) or "Suppression requested during model generation.")
            elif self._stop_all_now.is_set():
                self._handle_stop_all_now(redis_id, job, name, started, str(exc))
            else:
                self._handle_cancelled(redis_id, job, name, started, str(exc))
            return
        except TaskRoundBudgetExceeded as exc:
            self._handle_pathological_failure(redis_id, job, name, started, "task-round-budget", str(exc))
            return
        except VerifierProtocolError as exc:
            self._handle_pathological_failure(redis_id, job, name, started, "verifier", str(exc))
            return
        except Exception as exc:
            if self._suppression_requested(job.task_id) or (self.durable and self.durable.task_status(job.task_id) == "suppressed"):
                self._handle_suppressed(redis_id, job, reason=f"Suppression completed at safe boundary after {type(exc).__name__}.")
                return
            error = f"{type(exc).__name__}: {exc}"
            terminal = self._recover_or_escalate(redis_id, job, error)
            result = StepResult(
                task_id=job.task_id,
                step_id=job.step_id,
                name=name,
                status=StepStatus.FAILED if terminal else StepStatus.RUNNING,
                summary="Step failed" if terminal else "Step is recovering and will retry",
                error=error if terminal else None,
                started_at=started,
                completed_at=_utc_now(),
            )
            if self.durable:
                try:
                    self.durable.checkpoint_step(result)
                    if terminal:
                        terminal_summary = f"Task failed at {job.step_id}: {error}"
                        self.durable.finalize_task(job.task_id, terminal_summary, 'failed')
                        if not self.durable.terminal_summary_verified(job.task_id):
                            raise RuntimeError('durable terminal failure verification failed')
                except Exception:
                    logging.exception(
                        "Failed to checkpoint/finalize prompt failure task=%s step=%s",
                        job.task_id,
                        job.step_id,
                    )
                    terminal = False
            if terminal:
                self.live.step_failed(job.task_id, job.step_id, error)
                self._clear_terminal_redis(job.task_id)
                logging.exception("Prompt failed terminally task=%s step=%s", job.task_id, job.step_id)
            else:
                logging.warning("Prompt scheduled for recovery task=%s step=%s error=%s", job.task_id, job.step_id, error)
        finally:
            self.client.clear_crash_context()
            heartbeat_stop.set()
            heartbeat.join(timeout=1)

    def _handle_suppressed(self, redis_id: str, job: PromptJob, reason: str) -> None:
        status = self.durable.task_status(job.task_id) if self.durable else None
        if self.durable and status is not None and status != "suppressed":
            payload = self.queue.snapshot_task_jobs(job.task_id)
            self.durable.suppress_task(job.task_id, reason or "Suppressed by operator.", payload)
        self._soft_delete_task_assets(job.task_id, suppressed=True)
        # The suppression payload is the durable resume source. Remove all live
        # copies for this task so a cancelled active model call cannot leave queue
        # debris that shadows unrelated work.
        self.queue.cleanup_task(job.task_id)
        if hasattr(self.live, "cleanup"):
            self.live.cleanup(job.task_id)
        self._clear_suppression_request(job.task_id)
        logging.info("Task suppressed task=%s step=%s reason=%s (live queue copies removed)", job.task_id, job.step_id, reason)

    def _handle_append_dependency_terminal(self, redis_id: str, job: PromptJob, name: str, command: dict, state: dict) -> None:
        previous_task_id = str(command.get("previous_task_id") or "")
        previous_step_id = str(command.get("previous_step_id") or "")
        reason = (
            f"Append skipped because prerequisite {previous_task_id}"
            + (f"/{previous_step_id}" if previous_step_id else "")
            + f" is not satisfiable (state={state.get('state')}, task_status={state.get('task_status')}, "
              f"step_status={state.get('step_status')})."
        )
        now = _utc_now()
        if self.durable:
            self.durable.checkpoint_step(StepResult(
                task_id=job.task_id, step_id=job.step_id, name=name, status=StepStatus.CANCELLED,
                summary=reason, verification="append-dependency-unsatisfied",
                started_at=now, completed_at=now,
            ))
            self.durable.finalize_task(job.task_id, reason, "cancelled")
            if not self.durable.terminal_summary_verified(job.task_id):
                raise RuntimeError("durable append dependency cancellation verification failed")
        self.live.step_cancelled(job.task_id, job.step_id, reason)
        self._clear_terminal_redis(job.task_id)
        logging.warning("Append cancelled for unsatisfied predecessor task=%s step=%s", job.task_id, job.step_id)

    def _handle_stop_all_now(self, redis_id: str, job: PromptJob, name: str, started, reason: str) -> None:
        task_rounds = self.queue.task_rounds(job.task_id)
        checkpoint = (
            "STOP ALL -NOW CHECKPOINT\n"
            "The active Ollama generation was intentionally interrupted for a controlled full stop. "
            "Raw partial model output remains in the Redis model buffer for this task/step. "
            "Resume this same step from durable PostgreSQL/Redis state and verify prior side effects before repeating them. "
            f"Reason: {reason or 'user requested immediate stop'}"
        )
        if self.durable:
            result = StepResult(
                task_id=job.task_id, step_id=job.step_id, name=name,
                status=StepStatus.RUNNING, summary=checkpoint,
                verification="yielded:stop_all_now", started_at=started, completed_at=_utc_now(),
            )
            self.durable.checkpoint_step(result)
        self.queue.yield_job(
            redis_id, job, checkpoint, reason="stop_all_now",
            step_rounds=0, task_rounds=task_rounds,
        )
        logging.info("Prompt checkpointed for stop-all-now task=%s step=%s", job.task_id, job.step_id)

    def _handle_cancelled(self, redis_id: str, job: PromptJob, name: str, started, reason: str) -> None:
        result = StepResult(
            task_id=job.task_id, step_id=job.step_id, name=name,
            status=StepStatus.CANCELLED, summary="Stopped by user request",
            verification="cancelled", started_at=started, completed_at=_utc_now(),
        )
        if self.durable:
            self.durable.checkpoint_step(result)
            self.durable.finalize_task(job.task_id, result.summary, 'cancelled')
            if not self.durable.terminal_summary_verified(job.task_id):
                raise RuntimeError('durable cancellation summary verification failed')
        self.live.step_cancelled(job.task_id, job.step_id, reason or "generation cancelled")
        self._clear_terminal_redis(job.task_id)
        logging.info("Prompt cancelled by user task=%s step=%s", job.task_id, job.step_id)

    @staticmethod
    def _command_from_job(job: PromptJob) -> dict:
        raw = (job.metadata or {}).get("command_envelope")
        if isinstance(raw, dict):
            try:
                return normalize_command(raw, job.prompt)
            except ValueError:
                pass
        source = str((job.metadata or {}).get("source_request_type") or "task")
        if source not in {"straightforward_direction", "simple_task", "maintenance", "information_retrieval", "task", "task_step", "append"}:
            source = "task"
        return normalize_command({
            "schema_version": PROTOCOL_VERSION, "kind": "command", "category": source,
            "intent": str((job.metadata or {}).get("task_user_prompt") or (job.metadata or {}).get("original_user_prompt") or job.prompt)[:500],
            "requires_file_mutation": False, "requires_external_research": False,
            "input_has_media": False, "output_requires_media": False,
            "expected_output": {"type": "answer", "format": "text"},
        }, job.prompt)

    def _typed_prompt(self, job: PromptJob, request_type: str, *, task_memory: str = "", prior: str = "", background: str = "", resource_status: dict | None = None) -> str:
        directives = {
            "prompt": "Answer the supplied prompt directly while preserving its context and constraints.",
            "internal_instruction": "Treat this as an internal runtime instruction, not user-authored content; apply only its bounded scope.",
            "internal_direction": "Follow this coordinator direction exactly and return the requested internal result.",
            "straightforward_direction": "Execute this bounded direction directly; do not expand it into an unnecessary task plan.",
            "simple_task": "Complete this single bounded task directly and verify its result.",
            "information_retrieval": "Retrieve and report the requested existing information without inventing missing facts.",
            "task_step": "Continue only the specified step of the larger task and preserve prior completed work.",
            "append": "Execute this backward-dependent follow-up only after its predecessor is satisfied; preserve predecessor artifacts instead of recreating the foundation in parallel.",
            "deferred_append_plan": "Materialize the append execution plan only after the predecessor task has passed final verification; do not execute user work in the control job.",
            "step": "Execute only this bounded task step and return a durable result for later steps.",
            "task": "Execute the scheduled task work for this node; do not claim unscheduled work was completed.",
            "maintenance": "Perform only the bounded maintenance operation and report observed before/after state.",
            "conclusion": "Synthesize prior task results into the direct final candidate; do not redo completed work unless verification requires it.",
            "final_verification": "Validate the final-candidate envelope and verify completion without redoing completed acquisition or mutation work.",
        }
        metadata = job.metadata or {}
        prompt_origin = str(metadata.get("prompt_origin") or ("user_prompt" if request_type == "prompt" else "norm_internal"))
        is_user_prompt = request_type == "prompt" and prompt_origin in {"user_prompt", "prompt_interface", "gui_prompt", "ssh_prompt"}
        original_user_prompt = str(metadata.get("original_user_prompt") or (job.prompt if is_user_prompt else "")).strip()
        task_user_prompt = str(metadata.get("task_user_prompt") or original_user_prompt).strip()
        instruction = str(job.prompt or "")
        if not is_user_prompt:
            instruction = (
                "[NORM-INTERNAL WORK ITEM; NOT USER-AUTHORED]\n"
                f"type={request_type}; prompt_origin={prompt_origin}\n"
                "Do not describe this generated instruction as something the user directly said or asked.\n\n"
                + instruction
            )
        payload = {
            "schema_version": PROTOCOL_VERSION,
            "kind": "work_request",
            "request_type": request_type,
            "prompt_origin": prompt_origin,
            "instruction_source": "user_prompt_interface" if is_user_prompt else "norm_internal",
            "processing_contract": directives[request_type],
            "persistent_instructions": list(self._runtime_config().get("persistent_instructions", [])),
            "task_id": job.task_id,
            "step_id": job.step_id,
            "project_id": job.project_id,
            "command": self._command_from_job(job),
            "original_user_request": original_user_prompt,
            "primary_task_request": task_user_prompt,
            "instruction": instruction,
            "context": {
                "task_working_memory": task_memory,
                "postgres_prior_steps": prior,
                "background_memory": background,
                "continuation": job.context,
                "related_running_work": list((job.metadata or {}).get("related_running_work") or []),
                "ingrained_task_context": list((job.metadata or {}).get("ingrained_task_context") or []),
                "dependency_safety": "If required files/structure are unexpectedly absent and related_running_work shows unfinished work intended to create them, do not silently fabricate a parallel foundation; identify the dependency conflict in the step result.",
                "resource_status": merge_resource_status(resource_status),
            },
        }
        return json.dumps(payload, ensure_ascii=False)

    @staticmethod
    def _artifact_snapshot(evidence: list[dict]) -> list[dict]:
        writes: dict[str, str] = {}
        verified: set[tuple[str, str]] = set()
        for item in evidence:
            if not isinstance(item, dict):
                continue
            tool = str(item.get("tool") or "")
            result = item.get("result") if isinstance(item.get("result"), dict) else {}
            if not result.get("ok"):
                continue
            path = str(result.get("path") or "")
            sha = str(result.get("sha256") or "")
            if (tool in {"write_file", "replace_text"} or result.get("file_mutation")) and path:
                writes[path] = sha
                verified = {entry for entry in verified if entry[0] != path}
            if tool == "read_file" and path and sha and writes.get(path) == sha:
                verified.add((path, sha))
        return [
            {"path": path, "sha256": sha, "verified": (path, sha) in verified}
            for path, sha in sorted(writes.items())
        ]

    @staticmethod
    def _final_artifact_state(candidate: dict) -> bool:
        expected_type = str(candidate.get("command", {}).get("expected_output", {}).get("type") or "answer")
        requires_artifact = expected_type in {"artifact", "artifact_and_response"}
        artifacts = candidate.get("artifacts") or []
        if artifacts:
            return all(bool(item.get("verified")) for item in artifacts)
        return not requires_artifact

    @staticmethod
    def _resource_status_from_evidence(evidence: list[dict]) -> dict:
        statuses: list[dict] = []
        for item in evidence:
            result = item.get("result") if isinstance(item, dict) and isinstance(item.get("result"), dict) else {}
            status = result.get("resource_status") if isinstance(result, dict) else None
            if isinstance(status, dict):
                statuses.append(status)
                continue
            storage_context = result.get("storage_context") if isinstance(result, dict) else None
            if isinstance(storage_context, dict) and isinstance(storage_context.get("context_status"), dict):
                statuses.append(storage_context["context_status"])
        return merge_resource_status(*statuses)

    def _step_result_envelope(self, job: PromptJob, request_type: str, answer: str, *, resource_status: dict | None = None) -> str:
        evidence = self.live.task_evidence(job.task_id) if hasattr(self.live, "task_evidence") else []
        resource_status = merge_resource_status(
            resource_status,
            (job.metadata or {}).get("resource_status"),
            self._resource_status_from_evidence(evidence),
        )
        payload = {
            "schema_version": PROTOCOL_VERSION, "kind": "step_result", "status": "completed",
            "task_id": job.task_id, "step_id": job.step_id, "request_type": request_type,
            "command_category": self._command_from_job(job)["category"],
            "content": answer,
            "artifacts": self._artifact_snapshot(evidence),
            "verification_requirement": str((job.metadata or {}).get("verification") or ""),
            "evidence_count": len(evidence),
            "resource_status": resource_status,
        }
        return json.dumps(payload, ensure_ascii=False)

    def _build_final_candidate(self, job: PromptJob, answer: str) -> dict:
        evidence = self.live.task_evidence(job.task_id) if hasattr(self.live, "task_evidence") else []
        resource_status = merge_resource_status(
            (job.metadata or {}).get("resource_status"),
            self._resource_status_from_evidence(evidence),
        )
        payload = {
            "schema_version": PROTOCOL_VERSION, "kind": "final_candidate",
            "task_id": job.task_id, "step_id": job.step_id, "status": "completed",
            "command": self._command_from_job(job),
            "user_reply": answer,
            "artifacts": self._artifact_snapshot(evidence),
            "requirements": {
                "verification_requirement": str((job.metadata or {}).get("verification") or ""),
                "expected_output": self._command_from_job(job).get("expected_output", {}),
            },
            "evidence_count": len(evidence),
            "resource_status": resource_status,
        }
        return validate_final_candidate(payload)

    def _enqueue_final_verification(self, job: PromptJob, answer: str) -> None:
        candidate = self._build_final_candidate(job, answer)
        message_id = str((job.metadata or {}).get("final_verification_node_id") or "")
        if not message_id and self.durable and hasattr(self.durable, "node_uuid"):
            message_id = self.durable.node_uuid(job.task_id, "final-verify")
        if not message_id:
            message_id = str(uuid.uuid4())
        task_uuid = job.task_uuid or (self.durable.task_uuid(job.task_id) if self.durable and hasattr(self.durable, "task_uuid") else job.chain_uuid or job.chain_id)
        if hasattr(self.queue, "contains_message_id") and self.queue.contains_message_id(message_id):
            logging.info("Final verification already queued task=%s", job.task_id)
            return
        metadata = {
            "step_name": "Verify final candidate envelope",
            "verification": "Structured final verification must accept schema, requirements, and artifact evidence.",
            "prompt_origin": "runtime_final_candidate",
            "context_id": job.task_id,
            "original_user_prompt": str((job.metadata or {}).get("original_user_prompt") or ""),
            "task_user_prompt": str((job.metadata or {}).get("task_user_prompt") or (job.metadata or {}).get("original_user_prompt") or ""),
            "ingrained_task_context": list((job.metadata or {}).get("ingrained_task_context") or []),
            "command_envelope": self._command_from_job(job),
            "verification_cycle": 1,
            "queue_protocol_version": PROTOCOL_VERSION,
            "coordinator_source": "local",
            "task_uuid": task_uuid,
            "node_id": message_id,
        }
        verify_job = PromptJob(
            message_id=message_id, task_id=job.task_id, step_id="final-verify",
            prompt=json.dumps(candidate, ensure_ascii=False), project_id=job.project_id, context="",
            attempt=0, created_at=__import__("time").time(), metadata=metadata, chain_id=task_uuid,
            previous_prompt_id=job.node_id or job.message_id, next_prompt_id="", chain_index=job.chain_index + 1,
            recovery_attempted=False, request_type="final_verification",
            task_uuid=task_uuid, node_id=message_id, chain_uuid=task_uuid,
            previous_node_id=job.node_id or job.message_id, next_node_id="",
        )
        self.queue.enqueue(verify_job)
        logging.info("Queued structured final verification task=%s", job.task_id)

    def _structured_verifier_result(self, prompt: str) -> dict:
        last_error = None
        schema = final_verification_schema()
        for attempt in range(1, 4):
            try:
                raw = self.client.generate(
                    prompt, think=False, num_predict=1400, temperature=0.0, response_format=schema
                )
                return validate_final_verification(self.client.parse_json(raw))
            except (ValueError, json.JSONDecodeError, RuntimeError) as exc:
                last_error = exc
                logging.warning("Structured verifier protocol retry attempt=%s/3 error=%s", attempt, exc)
        raise VerifierProtocolError(f"structured verifier failed after 3 attempts: {last_error}")

    def _final_verification_prompt(self, job: PromptJob, candidate: dict, artifact_state: bool) -> str:
        prior = ""
        if self.durable and hasattr(self.durable, "completed_step_context"):
            prior = self.durable.completed_step_context(job.task_id, job.step_id, max_chars=36000)
        request = str((job.metadata or {}).get("task_user_prompt") or (job.metadata or {}).get("original_user_prompt") or "")
        return (
            "You are the final independent verifier for a queued Norm result. The candidate envelope has already passed deterministic JSON shape validation. "
            "Return only the structured JSON object required by the supplied schema. Set verdict=accept only if the user_reply directly and completely satisfies the authoritative request, "
            "the requirements are complete, and artifact claims are supported. schema_complete refers to the candidate control envelope. artifact_verified must be true when no artifact is required; "
            "when an artifact is required, it may be true only if the deterministic artifact state below is true. If revision is needed, list concrete issues and select the narrowest repair_scope. "
            "Do not demand stylistic rewrites when the result is materially correct. Reject results that took an avoidable shortcut, substituted an assumption for a material fact that available tools/evidence could reasonably verify, or skipped necessary verification.\n\n"
            f"PERSISTENT OPERATING PRINCIPLES:\n{chr(10).join('- '+str(v) for v in self._runtime_config().get('persistent_instructions', []))}\n\n"
            f"AUTHORITATIVE USER REQUEST:\n{request}\n\n"
            f"FINAL CANDIDATE JSON:\n{json.dumps(candidate, ensure_ascii=False)}\n\n"
            f"DETERMINISTIC ARTIFACT STATE: {str(artifact_state).lower()}\n\n"
            f"PRIOR COMPLETED TASK STATE/EVIDENCE:\n{prior}"
        )

    def _repair_final_reply(self, job: PromptJob, candidate: dict, verification: dict) -> str:
        prior = ""
        if self.durable and hasattr(self.durable, "completed_step_context"):
            prior = self.durable.completed_step_context(job.task_id, job.step_id, max_chars=36000)
        prompt = (
            f"PERSISTENT OPERATING PRINCIPLES:\n{chr(10).join('- '+str(v) for v in self._runtime_config().get('persistent_instructions', []))}\n\n"
            "Repair only the user-facing reply in this final candidate. Preserve completed work and observed evidence; do not rerun tools or invent actions. "
            "Resolve every verifier issue using the authoritative request and prior completed task state. Return only the replacement user-facing reply, not JSON and not a review report.\n\n"
            f"AUTHORITATIVE PRIMARY TASK REQUEST:\n{(job.metadata or {}).get('task_user_prompt') or (job.metadata or {}).get('original_user_prompt','')}\n\n"
            f"CURRENT CANDIDATE:\n{json.dumps(candidate, ensure_ascii=False)}\n\n"
            f"VERIFICATION ISSUES:\n{json.dumps(verification.get('issues', []), ensure_ascii=False)}\n\n"
            f"PRIOR COMPLETED TASK STATE/EVIDENCE:\n{prior}"
        )
        corrected = self._generate_complete_text(prompt, think=False, num_predict=16000, temperature=0.1)
        if not corrected.strip():
            raise VerifierProtocolError("final candidate repair returned blank user_reply")
        return corrected.strip()

    def _process_final_verification(self, redis_id: str, job: PromptJob, name: str, started) -> None:
        try:
            candidate = validate_final_candidate(json.loads(job.prompt))
        except (json.JSONDecodeError, ValueError) as exc:
            raise VerifierProtocolError(f"queued final candidate failed deterministic schema validation: {exc}") from exc
        artifact_state = self._final_artifact_state(candidate)
        verification = self._structured_verifier_result(
            self._final_verification_prompt(job, candidate, artifact_state)
        )
        if verification["verdict"] == "accept" and not artifact_state:
            verification = {
                "schema_version": PROTOCOL_VERSION, "kind": "final_verification", "verdict": "revise",
                "schema_complete": True, "requirements_complete": verification["requirements_complete"],
                "artifact_verified": False, "issues": ["Required artifact is missing deterministic write/read verification."],
                "repair_scope": "artifact",
            }
        cycle = max(1, int((job.metadata or {}).get("verification_cycle", 1)))
        if verification["verdict"] == "accept":
            summary = json.dumps(verification, ensure_ascii=False)
            result = StepResult(
                task_id=job.task_id, step_id=job.step_id, name=name, status=StepStatus.COMPLETED,
                summary=summary, verification="structured-final-verification:accepted",
                started_at=started, completed_at=_utc_now(),
            )
            if self.durable:
                self.durable.checkpoint_step(result)
                if not self.durable.all_steps_completed(job.task_id):
                    raise RuntimeError("final verification accepted before all planned steps completed")
                self.durable.finalize_task(job.task_id, candidate["user_reply"], "completed")
                if not self.durable.terminal_summary_verified(job.task_id):
                    raise RuntimeError("durable terminal summary verification failed")
            self.live.step_completed(job.task_id, job.step_id, summary, result.verification)
            self.live.finish(job.task_id, candidate["user_reply"])
            self._clear_terminal_redis(job.task_id)
            logging.info("Structured final verification accepted task=%s cycle=%s", job.task_id, cycle)
            return
        if cycle >= 3:
            raise VerifierProtocolError("final candidate remained rejected after three structured verification cycles: " + "; ".join(verification["issues"]))
        if verification["repair_scope"] not in {"response", "requirements"}:
            raise VerifierProtocolError("final verification found a non-text repair requirement: " + "; ".join(verification["issues"]))
        candidate["user_reply"] = self._repair_final_reply(job, candidate, verification)
        candidate = validate_final_candidate(candidate)
        metadata = dict(job.metadata or {})
        metadata["verification_cycle"] = cycle + 1
        metadata["last_verification"] = verification
        job.metadata = metadata
        job.prompt = json.dumps(candidate, ensure_ascii=False)
        job.created_at = __import__("time").time()
        retry_summary = json.dumps({
            "schema_version": PROTOCOL_VERSION, "kind": "final_verification_retry",
            "cycle": cycle, "next_cycle": cycle + 1, "issues": verification["issues"],
        }, ensure_ascii=False)
        if self.durable:
            self.durable.checkpoint_step(StepResult(
                task_id=job.task_id, step_id=job.step_id, name=name, status=StepStatus.RUNNING,
                summary=retry_summary, verification="structured-final-verification:revise",
                started_at=started, completed_at=_utc_now(),
            ))
        self.queue.ack(redis_id)
        self.queue.enqueue(job)
        logging.info("Requeued repaired final candidate task=%s cycle=%s", job.task_id, cycle + 1)

    @staticmethod
    def _is_literal_request(text: str) -> bool:
        compact = str(text or "").strip()
        lower = compact.lower()
        if not compact:
            return True
        if len(compact) <= 180 and (lower.startswith("answer with exactly:") or lower.startswith("respond with exactly:")):
            return True
        arithmetic = re.fullmatch(r"(?:what(?:'s| is)?\s+)?[0-9\s\+\-\*\/\(\)\.\^%]+\??", lower)
        return bool(arithmetic)

    def _ensure_effectiveness_note(self, task_id: str) -> None:
        if not self.durable or not hasattr(self.durable, "task_terminal_context") or not hasattr(self.durable, "set_effectiveness_note"):
            return
        try:
            context = self.durable.task_terminal_context(task_id)
            if not context or context.get("effectiveness_note"):
                return
            request = str(context.get("original_request") or "").strip()
            if self._is_literal_request(request):
                return
            status = str(context.get("status") or "unknown")
            summary = str(context.get("summary") or "")[:5000]
            prompt = (
                "Write one concise internal note (max 500 characters) that would make a future Norm run of the same kind of task more effective. "
                "Focus on what workflow to repeat or change, what validation mattered, pitfalls to avoid, or useful prior context to reuse. "
                "Do not merely restate the answer and do not include credentials. Return only the note.\n\n"
                f"REQUEST:\n{request}\n\nTERMINAL STATUS: {status}\n\nTERMINAL RESULT:\n{summary}"
            )
            note = ""
            try:
                note = self.client.generate(prompt, think=False, temperature=0.0, num_predict=220).strip()
            except Exception:
                logging.exception("Effectiveness-note generation failed task=%s; using fallback", task_id)
            if not note:
                if status == "completed":
                    note = "Reuse the verified prior workflow and confirmed context; preserve the request's constraints and avoid redoing settled work unless new evidence changed."
                elif status == "failed":
                    note = "On retry, reuse completed durable evidence and repair the failed stage directly instead of restarting the whole task; verify the corrected stage before continuing."
                else:
                    note = "If resumed, reuse durable completed work first and confirm the current request still matches the interrupted task before continuing."
            self.durable.set_effectiveness_note(task_id, note[:1200])
            logging.info("Recorded future-effectiveness note task=%s", task_id)
        except Exception:
            logging.exception("Could not record future-effectiveness note task=%s", task_id)

    def _soft_delete_task_assets(self, task_id: str, *, suppressed: bool) -> dict:
        """Batch soft-delete only Norm-created assets whose manifest says recreation is cheap."""
        try:
            tools = self._worker_tools()
            if tools is None or tools.task_storage is None or tools.deletion_queue is None:
                return {"staged": 0, "skipped": "unavailable"}
            tools.set_task_context(task_id, None)
            candidates = tools.task_storage.cleanup_candidates(suppressed=suppressed)
            if not candidates:
                return {"staged": 0, "skipped": 0}
            result = tools.deletion_queue.stage_batch(
                candidates, task_id=task_id,
                batch_reason="suppressed task cleanup" if suppressed else "terminal task reproducible-artifact cleanup",
                checkpoint=True,
            )
            staged = len(result.get("staged") or [])
            logging.info(
                "Task artifact soft-delete task=%s suppressed=%s candidates=%s staged=%s skipped=%s",
                task_id, suppressed, len(candidates), staged, len(result.get("skipped") or []),
            )
            return {"staged": staged, "skipped": len(result.get("skipped") or []), "batch_id": result.get("batch_id")}
        except Exception as exc:
            logging.exception("Task artifact soft-delete failed task=%s suppressed=%s", task_id, suppressed)
            return {"staged": 0, "error": f"{type(exc).__name__}: {exc}"}

    def _clear_terminal_redis(self, task_id: str) -> None:
        if not self.durable or not self.durable.terminal_summary_verified(task_id):
            raise RuntimeError(f"refusing Redis cleanup before durable terminal summary verification: {task_id}")
        self._ensure_effectiveness_note(task_id)
        context = self.durable.task_terminal_context(task_id) if hasattr(self.durable, "task_terminal_context") else {}
        request = str((context or {}).get("original_request") or "").strip()
        note = str((context or {}).get("effectiveness_note") or "").strip()
        if not self._is_literal_request(request) and not note:
            raise RuntimeError(f"refusing raw-thinking purge before effectiveness note: {task_id}")
        status = self.durable.task_status(task_id) if self.durable else None
        self._soft_delete_task_assets(task_id, suppressed=(status != "completed"))
        if hasattr(self.durable, "purge_raw_thinking"):
            purged = self.durable.purge_raw_thinking(task_id)
            if purged:
                logging.info("Purged terminal raw thinking task=%s rows=%s; condensed notes retained", task_id, purged)
        queue_counts = self.queue.cleanup_task(task_id)
        self.live.cleanup(task_id)
        self._clear_processed_sos(task_id)
        try:
            from norm_runtime.settings import load_path_settings
            _paths = load_path_settings(self._runtime_root())
            _retention = Path(_paths["workspace_root"]) / str(self._runtime_config().get("task_storage", {}).get("retention_manifest_dir", ".norm-task-retention"))
            temp_result = cleanup_task_temp(_paths["temp_root"], task_id, self.durable, retention_root=_retention, deletion_queue=(self._worker_tools().deletion_queue if self._worker_tools() is not None else None))
            if temp_result.get("removed"):
                logging.info("Soft-deleted verified terminal task temp task=%s staged=%s preserved=%s", task_id, temp_result.get("files_soft_deleted", 0), temp_result.get("files_preserved", 0))
        except Exception:
            logging.exception("Terminal task temp cleanup failed task=%s; leaving temp material in place", task_id)
        logging.info("Terminal Redis cleanup task=%s queue=%s live=cleared", task_id, queue_counts)

    def _clear_processed_sos(self, task_id: str) -> None:
        """Clean only the legacy root SOS.readme; current temp/recovery SOS is handled conservatively by temp cleanup."""
        if not self.durable or not self.durable.terminal_summary_verified(task_id):
            return
        path = self._runtime_root() / "SOS.readme"
        if not path.is_file():
            return
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            logging.exception("Could not inspect legacy SOS before cleanup task=%s", task_id)
            return
        if f"Task ID: {task_id}" not in {line.strip() for line in lines}:
            return
        try:
            path.unlink()
        except OSError:
            logging.exception("Could not remove processed legacy SOS task=%s", task_id)
            return
        logging.info("Processed legacy SOS removed after durable task resolution task=%s", task_id)

    def _all_redis_task_ids(self) -> set[str]:
        task_ids = set(self.queue.task_ids())
        if hasattr(self.live, "task_ids"):
            task_ids.update(self.live.task_ids())
        return task_ids

    def _startup_recovery(self) -> None:
        cfg = self._runtime_config().get("maintenance", {})
        if not bool(cfg.get("startup_redis_reconcile", True)):
            return
        self._last_redis_maintenance_check = monotonic()
        self.durable.record_maintenance_note("startup", "Redis startup scan began.", details={"pending_requeued": 0})
        # PostgreSQL terminal state is authoritative. Reconcile stale Redis/PEL
        # entries before requeueing anything so completed/failed/cancelled tasks
        # cannot be resurrected simply because an old pending delivery survived.
        self._reconcile_redis(reason="startup", finalize_unrecoverable=True, include_durable_running=True)
        requeued = self.queue.requeue_pending_on_startup()
        self.durable.record_maintenance_note("startup", "Redis startup requeue completed.", details={"pending_requeued": requeued})
        try:
            temp_result = self._purge_temp_outputs()
            logging.info("Startup temp cleanup result=%s", temp_result)
        except Exception:
            logging.exception("Startup temp cleanup failed; preserving temp material")
        logging.info("Startup Redis recovery pending_requeued=%s", requeued)

    def _reconcile_redis(
        self, *, reason: str, finalize_unrecoverable: bool, include_durable_running: bool = False
    ) -> None:
        task_ids = self._all_redis_task_ids()
        if include_durable_running and hasattr(self.durable, "running_tasks"):
            task_ids.update(
                str(item.get("task_id") or "")
                for item in self.durable.running_tasks(limit=1000)
                if str(item.get("task_id") or "")
            )
        cleaned = recovered = finalized = unknown = held = 0
        for task_id in sorted(task_ids):
            status = self.durable.task_status(task_id)
            locations = self.queue.task_locations(task_id)
            recoverable = bool(locations["work"] or locations["retry"])
            if status == "suppressed":
                # Only discard live queue copies when a durable resume snapshot exists.
                # That makes startup cleanup safe for both current and legacy rows.
                payload = self.durable.suppression_payload(task_id) if hasattr(self.durable, "suppression_payload") else {}
                has_resume_snapshot = bool((payload or {}).get("tasks") or (payload or {}).get("jobs"))
                if has_resume_snapshot:
                    self.queue.cleanup_task(task_id)
                    cleaned += 1
                else:
                    held += 1
                continue
            if status in {"completed", "failed", "cancelled"}:
                if not self.durable.terminal_summary_verified(task_id):
                    self.durable.ensure_terminal_summary(task_id)
                if self.durable.terminal_summary_verified(task_id):
                    self._clear_terminal_redis(task_id)
                    cleaned += 1
                else:
                    held += 1
                continue
            if status == "running" and recoverable:
                recovered += 1
                continue
            if status == "running" and finalize_unrecoverable:
                why = f"no recoverable work/retry entry; Redis locations={locations}"
                summary = self.durable.finalize_interrupted_task(task_id, why)
                self.durable.record_maintenance_note(reason, "Unrecoverable running task was summarized and cleared.", task_id=task_id, details={"locations": locations, "summary": summary})
                self._clear_terminal_redis(task_id)
                finalized += 1
                continue
            if status is None and finalize_unrecoverable:
                self.durable.record_maintenance_note(reason, "Redis task had no PostgreSQL task record; it could not be resumed and was cleared.", task_id=task_id, details={"locations": locations})
                self.queue.cleanup_task(task_id)
                self.live.cleanup(task_id)
                unknown += 1
                continue
            held += 1
        self.durable.record_maintenance_note(reason, "Redis reconciliation completed.", details={"inspected": len(task_ids), "cleaned_terminal": cleaned, "recoverable_left_for_processing": recovered, "finalized_unrecoverable": finalized, "cleared_unknown": unknown, "held": held})
        logging.info("Redis maintenance reason=%s inspected=%s cleaned=%s recoverable=%s finalized=%s unknown=%s held=%s", reason, len(task_ids), cleaned, recovered, finalized, unknown, held)

    def _maybe_reconcile_redis(self, *, force: bool = False, reason: str = "periodic") -> None:
        if not self.durable:
            return
        cfg = self._runtime_config().get("maintenance", {})
        if not bool(cfg.get("redis_reconcile_enabled", True)):
            return
        now = monotonic()
        interval = max(300, int(cfg.get("redis_reconcile_seconds", 14400)))
        if not force and now - self._last_redis_maintenance_check < interval:
            return
        self._last_redis_maintenance_check = now
        self._reconcile_redis(reason=reason, finalize_unrecoverable=True)

    def note_shutdown_state(self) -> None:
        if not self.durable:
            return
        self._reconcile_redis(reason="shutdown", finalize_unrecoverable=False)
        unfinished = 0
        for task_id in sorted(self._all_redis_task_ids()):
            status = self.durable.task_status(task_id)
            if status in {"completed", "failed", "cancelled", "suppressed"}:
                continue
            locations = self.queue.task_locations(task_id)
            live_state = self.live.state(task_id) if hasattr(self.live, "state") else {}
            stuck = not bool(locations["work"] or locations["retry"])
            note = "Unfinished task preserved for startup recovery." if not stuck else "Task appears stuck at shutdown and was preserved for startup review."
            self.durable.record_maintenance_note("shutdown", note, task_id=task_id, details={"postgres_status": status, "locations": locations, "live_state": live_state, "stuck": stuck})
            unfinished += 1
        logging.info("Shutdown Redis scan preserved unfinished tasks=%s", unfinished)

    def _write_sos(self, job: PromptJob, stage: str, error: str, suggestion: str) -> None:
        task_rounds = self.queue.task_rounds(job.task_id)
        safe_error = str(error).replace("\x00", "")[:2000]
        body = (
            "Norm SOS\n"
            f"Generated: {_utc_now().isoformat()}\n"
            f"Task ID: {job.task_id}\n"
            f"Project: {job.project_id}\n"
            f"Stage: {stage}\n"
            f"Step: {job.step_id}\n"
            f"Attempt: {job.attempt}\n"
            f"Task rounds: {task_rounds}\n"
            f"Error: {safe_error}\n"
            f"Suggested recovery: {suggestion}\n"
            "No prompt contents, credentials, or secrets are included in this file.\n"
        )
        (self._runtime_root() / "SOS.readme").write_text(body, encoding="utf-8")

    def _handle_pathological_failure(self, redis_id: str, job: PromptJob, name: str, started, stage: str, error: str) -> None:
        self._write_sos(job, stage, error, "Inspect SOS and logs; fix the failing stage, then resubmit deliberately.")
        result = StepResult(
            task_id=job.task_id, step_id=job.step_id, name=name,
            status=StepStatus.FAILED, summary=f"Stopped safely during {stage}",
            verification=f"safety-stop:{stage}", error=error[:2000],
            started_at=started, completed_at=_utc_now(),
        )
        if self.durable:
            self.durable.checkpoint_step(result)
            self.durable.finalize_task(job.task_id, result.summary + ': ' + error[:1200], 'failed')
            if not self.durable.terminal_summary_verified(job.task_id):
                raise RuntimeError('durable failure summary verification failed')
        self.live.step_failed(job.task_id, job.step_id, error[:2000])
        self._clear_terminal_redis(job.task_id)
        logging.error("Prompt safety-stopped task=%s step=%s stage=%s", job.task_id, job.step_id, stage)

    def _runtime_root(self):
        from pathlib import Path
        import sys

        if getattr(sys, "frozen", False):
            return Path(sys.executable).resolve().parent.parent
        here = Path(__file__).resolve()
        if here.parent.name.lower() == "staging":
            return here.parent.parent
        return here.parents[2]

    def _runtime_config(self) -> dict:
        cached = getattr(self, "_slice_config", None)
        if cached is not None:
            return cached
        # Use the same resolver as the host/conversation path. Reading runtime.json
        # directly leaves placeholders such as {runtime_root} literal and can make
        # optional tool configuration fail even for ordinary text work.
        from runtime_bootstrap import load_config

        self._slice_config = load_config(self._runtime_root())
        return self._slice_config

    def _slice_limits(self) -> tuple[int, int]:
        worker = self._runtime_config().get("worker", {})
        step_limit = max(1, int(worker.get("step_round_limit", 16)))
        task_limit = max(step_limit, int(worker.get("task_round_limit", 128)))
        return step_limit, task_limit

    def _worker_tools(self):
        if hasattr(self, "_slice_file_tools"):
            return self._slice_file_tools
        from norm_runtime.file_tool_executor import FileToolExecutor

        tools = self._runtime_config().get("tools", {})
        if not bool(tools.get("enabled", False)):
            self._slice_file_tools = None
            return None
        root = self._runtime_root()
        from norm_runtime.deletion_queue import RedisDeletionQueue
        from norm_runtime.file_access_policy import load_file_access_policy
        from norm_runtime.settings import load_path_settings, load_plugin_settings
        path_cfg = load_path_settings(root)
        plugin_cfg = load_plugin_settings(root)
        workspace_root = path_cfg["workspace_root"]
        temp_root = path_cfg["temp_root"]
        file_policy = load_file_access_policy(root)
        allowed_roots = sorted({str(p) for p in (*file_policy.read_roots, *file_policy.write_roots)})
        redis_cfg = self._runtime_config().get("redis", {})
        dq = self._runtime_config().get("deletion_queue", {})
        deletion_queue = RedisDeletionQueue(
            host=str(dq.get("host", redis_cfg.get("host", "127.0.0.1"))),
            port=int(dq.get("port", redis_cfg.get("port", 6379))),
            db=int(dq.get("db", 2)),
            stream=str(dq.get("stream", "norm:deletion:queue")),
            trash_root=str(dq.get("trash_root", root / "state" / "deletion-trash")),
            items_key=str(dq.get("items_key", "norm:trash:items")),
            batches_key=str(dq.get("batches_key", "norm:trash:batches")),
            cross_volume_move_max_bytes=int(dq.get("cross_volume_move_max_bytes", 268_435_456)),
        )
        self._slice_file_tools = FileToolExecutor(
            allowed_roots,
            backup_root=str(tools.get("backup_root", root / "state" / "file-backups")),
            audit_log=str(tools.get("audit_log", root / "logs" / "tool-audit.jsonl")),
            max_read_bytes=file_policy.read_processing_buffer_bytes,
            max_tool_return_bytes=file_policy.read_chunk_bytes,
            read_roots=[str(p) for p in file_policy.read_roots],
            write_roots=[str(p) for p in file_policy.write_roots],
            enforce_read_directories=file_policy.enforce_read_directories,
            enforce_write_directories=file_policy.enforce_write_directories,
            read_chunk_bytes=file_policy.read_chunk_bytes,
            read_chunk_max_bytes=file_policy.read_chunk_max_bytes,
            max_write_bytes=int(tools.get("max_write_bytes", 5_242_880)),
            blocked_write_staging_root=str(tools.get("blocked_write_staging_root", root / "docs" / "blocked-writes")),
            write_retry_count=int(tools.get("write_retry_count", 3)),
            write_retry_delay_seconds=float(tools.get("write_retry_delay_seconds", 0.25)),
            image_enabled=bool(tools.get("image_enabled", False)),
            image_python=str(tools.get("image_python", root / ".venv" / "Scripts" / "python.exe")),
            image_analyzer_script=str(tools.get("image_analyzer_script", root / "tools" / "image_analyzer.py")),
            image_output_root=str(tools.get("image_output_root", workspace_root / "images" / "analysis")),
            image_profile_budgets=dict(tools.get("image_profile_budgets", {})),
            image_max_input_bytes=int(tools.get("image_max_input_bytes", 25_000_000)),
            vision_client=self.client,
            deletion_queue=deletion_queue,
            storage_config=dict(tools.get("storage_context", {})) or None,
            shell_enabled=bool(tools.get("shell_enabled", False)),
            shell_executable=str(tools.get("shell_executable", "powershell.exe")),
            shell_timeout_seconds=int(tools.get("shell_timeout_seconds", 120)),
            shell_max_output_chars=int(tools.get("shell_max_output_chars", 20000)),
            verbatim_helper=str(path_cfg["verbatim_writer"]),
            plugin_root=str(plugin_cfg["plugin_root"]),
            plugin_registry_file=str(plugin_cfg["registry_file"]),
            temp_root=str(path_cfg["temp_root"]),
            workspace_root=str(workspace_root),
            task_storage_config=dict(self._runtime_config().get("task_storage", {})),
            connection_config={
                "postgres": self._runtime_config().get("postgres", {}),
                "redis": self._runtime_config().get("redis", {}),
                "prompt_queue": self._runtime_config().get("prompt_queue", {}),
                "ollama_base_url": f"http://{self._runtime_config().get('ollama', {}).get('host', '127.0.0.1')}:{__import__('norm_runtime.settings', fromlist=['load_ports']).load_ports(root)['ollama']}",
            },
        )
        return self._slice_file_tools

    @staticmethod
    def _review_prompt(
        original_prompt: str,
        answer: str,
        evidence: list[dict] | None = None,
        final_step: bool = True,
    ) -> str:
        return (
            original_prompt
            + "\n\nPrevious result:\n"
            + answer
            + "\n\nDo one quick completion review. Check for a better approach, missing "
            "requirements, uncaught edge cases, unsupported claims, incomplete verification, "
            "and proofreading. Fix anything material using tools if needed. If nothing material "
            "is wrong, return the previous result verbatim. Return only the user-facing answer: "
            "never describe the review, list review findings, or add new claims while reviewing. "
            "An external action may be stated only when a specific successful tool result proves it."
            + (
                "\n\nObserved tool evidence:\n"
                + PromptWorker._evidence_text(evidence)
                if evidence
                else ""
            )
            + (
                "\n\nThis is an intermediate task step. The result may be internal notes, findings, or structured data for later steps; do not force it into requester-facing prose."
                if not final_step
                else ""
            )
        )

    @staticmethod
    def _large_explicit_count(text: str) -> int | None:
        values = [int(v) for v in re.findall(r"\bexactly\s+(\d+)\b", str(text or ""), flags=re.I)]
        values = [v for v in values if v >= 12]
        return max(values) if values else None

    @staticmethod
    def _count_child_decomposition_allowed(text: str) -> bool:
        lower = str(text or "").lower()
        reduction_terms = (
            "select ", "selection", "survivor", "reduce ", "reduction", "choose ",
            "shortlist", "rank ", "ranking", "filter ", "reject ", "second-look",
            "second look", "compare ", "comparison", "dedupe", "deduplicate",
        )
        if any(term in lower for term in reduction_terms):
            return False
        return True

    def _spawn_count_child_task(self, redis_id: str, job: PromptJob, prior: str, count: int) -> str:
        if not self.durable or not hasattr(self.durable, "start_child_task"):
            raise RuntimeError("durable child-task support unavailable")
        depth = int((job.metadata or {}).get("subtask_depth", 0))
        max_depth = max(1, int(self._runtime_config().get("worker", {}).get("max_subtask_depth", 4)))
        if depth >= max_depth:
            raise RuntimeError(f"maximum nested subtask depth reached: {max_depth}")
        child_id = f"child-{uuid.uuid4()}"
        base_start, base_end = 1, count
        range_match = re.search(r"numbered\s+items?\s+(\d+)\s+(?:through|to|[-â€“â€”])\s+(\d+)", str(job.prompt or ""), re.I)
        if range_match:
            observed_start, observed_end = int(range_match.group(1)), int(range_match.group(2))
            if observed_end >= observed_start and observed_end - observed_start + 1 == count:
                base_start, base_end = observed_start, observed_end
        batch_count = min(8, max(2, (count + 7) // 8))
        batch_size = (count + batch_count - 1) // batch_count
        steps = [
            TaskStep("plan-breakdown", "Plan child decomposition", "Runtime-generated bounded child plan.", "Child plan is explicit and bounded."),
            TaskStep("verify-plan", "Verify child decomposition", "Deterministic count split verified before execution.", "Ranges cover the requested count exactly once."),
        ]
        batch_ids = []
        start = base_start
        previous = "verify-plan"
        batch_ranges = []
        while start <= base_end:
            end = min(base_end, start + batch_size - 1)
            sid = f"sub-{start:03d}-{end:03d}"
            batch_ids.append(sid)
            batch_ranges.append((sid, start, end))
            steps.append(TaskStep(
                sid, f"Generate parent items {start}-{end}",
                f"Produce exactly {end-start+1} numbered items {start} through {end} for the parent step. Preserve every parent requirement and required field. Do not produce items outside this range. Make this batch internally distinct and avoid repeating prior verified child items.",
                f"Exactly {end-start+1} items numbered {start}-{end}; all parent-required fields present; no duplicate item in this batch.",
                depends_on=(previous,),
            ))
            previous = sid
            start = end + 1
        merge_id = "merge-child-results"
        steps.append(TaskStep(
            merge_id, "Merge verified child batches",
            f"Merge all verified child batches into one complete solution for the parent step. Return exactly {count} numbered items {base_start}-{base_end} in order. Preserve every required field. Detect and replace substantive duplicates, repair missing numbering or fields, and satisfy the parent step as a whole.",
            f"Exactly {count} complete, distinct numbered items {base_start}-{base_end}; no gaps, duplicates, or missing required fields; full parent step satisfied.",
            depends_on=tuple(batch_ids),
        ))
        steps.append(TaskStep("final-verify", "Verify merged child result", "Independently verify the merged child result.", f"Merged child result satisfies parent step and contains exactly {count} valid items.", depends_on=(merge_id,)))
        original_user_prompt = str((job.metadata or {}).get("original_user_prompt") or job.prompt).strip()
        task_user_prompt = str((job.metadata or {}).get("task_user_prompt") or original_user_prompt).strip()
        plan = TaskPlan(
            task_id=child_id, title=f"Child decomposition for {job.task_id}/{job.step_id}", steps=tuple(steps),
            original_request=task_user_prompt, source_prompt_id=str((job.metadata or {}).get("source_prompt_id") or ""),
            source_user_message_id=str((job.metadata or {}).get("original_user_message_id") or ""),
            ingrained_detail_count=int((job.metadata or {}).get("ingrained_detail_count") or 0),
            ingrained_task_context=tuple((job.metadata or {}).get("ingrained_task_context") or []),
        )
        self.live.start_task(child_id, plan.title, plan.as_dict())
        self.durable.start_child_task(plan, job.task_id, job.step_id, original_request=task_user_prompt)
        now = _utc_now()
        for step in steps[:2]:
            result = StepResult(child_id, step.id, step.name, StepStatus.COMPLETED, step.description, verification=step.verify, started_at=now, completed_at=now)
            self.durable.checkpoint_step(result)
            self.live.step_started(child_id, step.id, step.name)
            self.live.step_completed(child_id, step.id, result.summary, result.verification)
        parent_context = (
            f"PARENT TASK: {job.task_id}\nPARENT STEP: {job.step_id}\n\n"
            f"PARENT STEP INSTRUCTION:\n{job.prompt}\n\n"
            f"VERIFIED PRIOR PARENT STEP CONTEXT:\n{str(prior or '')[-32000:]}"
        )
        child_request = f"Complete the parent step {job.task_id}/{job.step_id} by executing the child plan and return its complete verified solution."
        command = self._command_from_job(job)
        jobs: list[PromptJob] = []
        action_steps = [step for step in steps[2:] if step.id != "final-verify"]
        final_verify_step = next(step for step in steps if step.id == "final-verify")
        for index, step in enumerate(action_steps):
            jobs.append(PromptJob(
                message_id=step.node_id, task_id=child_id, step_id=step.id,
                prompt=step.description, project_id=job.project_id, context=parent_context,
                attempt=0, created_at=__import__("time").time(), metadata={
                    "step_name": step.name, "verification": step.verify,
                    "prompt_origin": "runtime_child_subtask", "context_id": child_id,
                    "original_user_prompt": original_user_prompt,
                    "task_user_prompt": task_user_prompt,
                    "ingrained_task_context": list((job.metadata or {}).get("ingrained_task_context") or []),
                    "ingrained_detail_count": int((job.metadata or {}).get("ingrained_detail_count") or 0),
                    "source_prompt_id": str((job.metadata or {}).get("source_prompt_id") or ""),
                    "norm_generated_instruction": child_request, "command_envelope": command,
                    "is_final_candidate": index == len(action_steps) - 1,
                    "final_verification_step_id": "final-verify", "final_verification_node_id": final_verify_step.node_id,
                    "task_uuid": plan.task_uuid, "node_id": step.node_id, "subtask_depth": depth + 1,
                    "allow_subtask": True, "parent_task_id": job.task_id, "parent_step_id": job.step_id,
                    "queue_protocol_version": PROTOCOL_VERSION, "coordinator_source": "local",
                }, chain_id=plan.task_uuid, previous_prompt_id="", next_prompt_id="", chain_index=index,
                recovery_attempted=False, request_type=("conclusion" if index == len(action_steps)-1 else "step"),
                task_uuid=plan.task_uuid, node_id=step.node_id, chain_uuid=plan.task_uuid,
            ))
        parked = self.queue.park_chain_for_child(redis_id, job, child_id)
        try:
            self.queue.enqueue_chain(jobs)
        except Exception:
            try:
                self.durable.finalize_task(child_id, "Child task failed to enqueue after parent was parked.", "failed")
            finally:
                raise
        logging.info("Spawned child task parent=%s/%s child=%s count=%s batches=%s parked_parent_nodes=%s", job.task_id, job.step_id, child_id, count, len(batch_ids), parked)
        return child_id

    def _single_runtime_child(
        self,
        parent_job: PromptJob,
        *,
        title: str,
        instruction: str,
        verify: str,
        context: str,
        prompt_origin: str,
        allow_subtask: bool,
        scope_items: list[int] | None = None,
    ) -> tuple[str, PromptJob]:
        if not self.durable or not hasattr(self.durable, "start_child_task"):
            raise RuntimeError("durable child-task support unavailable")
        depth = int((parent_job.metadata or {}).get("subtask_depth", 0))
        max_depth = max(1, int(self._runtime_config().get("worker", {}).get("max_subtask_depth", 4)))
        if depth >= max_depth:
            raise RuntimeError(f"maximum nested subtask depth reached: {max_depth}")
        child_id = f"child-{uuid.uuid4()}"
        steps = (
            TaskStep("plan-breakdown", "Plan runtime child", "Runtime-generated bounded child plan.", "Child plan is explicit and bounded."),
            TaskStep("verify-plan", "Verify runtime child plan", "Runtime child plan checked before execution.", "Child instruction is bounded and preserves parent scope.", depends_on=("plan-breakdown",)),
            TaskStep("work", title, instruction, verify, depends_on=("verify-plan",)),
            TaskStep("final-verify", "Verify runtime child result", "Independently verify the child result.", verify, depends_on=("work",)),
        )
        original_user_prompt = str((parent_job.metadata or {}).get("original_user_prompt") or parent_job.prompt).strip()
        task_user_prompt = str((parent_job.metadata or {}).get("task_user_prompt") or original_user_prompt).strip()
        plan = TaskPlan(
            task_id=child_id, title=title, steps=steps, original_request=task_user_prompt,
            source_prompt_id=str((parent_job.metadata or {}).get("source_prompt_id") or ""),
            source_user_message_id=str((parent_job.metadata or {}).get("original_user_message_id") or ""),
            ingrained_detail_count=int((parent_job.metadata or {}).get("ingrained_detail_count") or 0),
            ingrained_task_context=tuple((parent_job.metadata or {}).get("ingrained_task_context") or []),
        )
        self.live.start_task(child_id, plan.title, plan.as_dict())
        child_kind = "recovery" if "recovery" in str(prompt_origin or "").lower() else "child"
        self.durable.start_child_task(
            plan, parent_job.task_id, parent_job.step_id,
            original_request=task_user_prompt, task_kind=child_kind,
        )
        now = _utc_now()
        for step in steps[:2]:
            result = StepResult(
                child_id, step.id, step.name, StepStatus.COMPLETED, step.description,
                verification=step.verify, started_at=now, completed_at=now,
            )
            self.durable.checkpoint_step(result)
            self.live.step_started(child_id, step.id, step.name)
            self.live.step_completed(child_id, step.id, result.summary, result.verification)
        command = self._command_from_job(parent_job)
        if prompt_origin == "runtime_oversize_recovery_analysis":
            command = normalize_command({
                **command,
                "intent": instruction[:500],
                "requires_file_mutation": False,
                "input_has_media": False,
                "output_requires_media": False,
                "expected_output": {"type": "answer", "format": "json"},
            }, instruction)
        elif prompt_origin == "runtime_oversize_recovery_unit":
            # A recovery unit returns a bounded internal result to its parent.
            # It may edit an artifact when its instruction requires it, but a
            # legitimate evidence-backed no-op must not inherit the parent's
            # root-level requirement to manufacture an artifact.
            command = normalize_command({
                **command,
                "intent": instruction[:500],
                "input_has_media": False,
                "output_requires_media": False,
                "expected_output": {"type": "answer", "format": "text"},
            }, instruction)
        work_step = next(step for step in steps if step.id == "work")
        final_verify_step = next(step for step in steps if step.id == "final-verify")
        child_job = PromptJob(
            message_id=work_step.node_id, task_id=child_id, step_id="work",
            prompt=instruction, project_id=parent_job.project_id, context=context,
            attempt=0, created_at=__import__("time").time(), metadata={
                "step_name": title, "verification": verify,
                "prompt_origin": prompt_origin, "context_id": child_id,
                "original_user_prompt": original_user_prompt,
                "task_user_prompt": task_user_prompt,
                "ingrained_task_context": list((parent_job.metadata or {}).get("ingrained_task_context") or []),
                "ingrained_detail_count": int((parent_job.metadata or {}).get("ingrained_detail_count") or 0),
                "source_prompt_id": str((parent_job.metadata or {}).get("source_prompt_id") or ""),
                "norm_generated_instruction": instruction, "command_envelope": command,
                "is_final_candidate": True, "final_verification_step_id": "final-verify",
                "final_verification_node_id": final_verify_step.node_id,
                "task_uuid": plan.task_uuid, "node_id": work_step.node_id,
                "subtask_depth": depth + 1, "allow_subtask": allow_subtask,
                "allow_oversize_recovery": allow_subtask,
                "parent_task_id": parent_job.task_id, "parent_step_id": parent_job.step_id,
                "recovery_scope_items": [int(v) for v in (scope_items or [])],
                "queue_protocol_version": PROTOCOL_VERSION, "coordinator_source": "local",
            }, chain_id=plan.task_uuid, previous_prompt_id="", next_prompt_id="",
            chain_index=0, recovery_attempted=False, request_type="conclusion",
            task_uuid=plan.task_uuid, node_id=work_step.node_id, chain_uuid=plan.task_uuid,
        )
        return child_id, child_job

    @staticmethod
    def _scope_items_from_text(text: str) -> list[int]:
        raw = str(text or "")
        values: set[int] = set()
        for match in re.finditer(r"(?i)\b(?:paths?|items?|candidates?|survivors?)\s*#?\s*(\d{1,5})\s*(?:-|\u2013|\u2014|to|through)\s*(\d{1,5})\b", raw):
            a, b = int(match.group(1)), int(match.group(2))
            if a <= b and b - a <= 256:
                values.update(range(a, b + 1))
        for match in re.finditer(r"(?i)\b(?:path|item|candidate|survivor)\s*#?\s*(\d{1,5})\b", raw):
            values.add(int(match.group(1)))
        return sorted(values)

    @staticmethod
    def _normalize_scope_items(values, *, field: str) -> list[int]:
        if not isinstance(values, list):
            raise RuntimeError(f"{field} must be a list of positive integer item ids")
        clean: set[int] = set()
        for index, value in enumerate(values):
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise RuntimeError(
                    f"{field}[{index}] must be a positive integer item id; got {value!r}"
                )
            clean.add(value)
        return sorted(clean)

    def _recovery_scope_items(self, job: PromptJob) -> list[int]:
        metadata = job.metadata or {}
        supplied = metadata.get("recovery_scope_items")
        if isinstance(supplied, list) and supplied:
            return self._normalize_scope_items(supplied, field="recovery_scope_items")
        scope = self._scope_items_from_text(job.prompt)
        if not scope:
            scope = self._scope_items_from_text(str(metadata.get("step_name") or ""))
        return scope or [1]

    @staticmethod
    def _step_result_content(text: str) -> str:
        raw = str(text or "").strip()
        try:
            data = json.loads(raw)
        except Exception:
            return raw
        if isinstance(data, dict) and data.get("kind") == "step_result" and isinstance(data.get("content"), str):
            return str(data.get("content") or "").strip()
        return raw

    def _recovery_note_path(self, task_id: str, step_id: str) -> Path:
        safe_task = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(task_id))[:180]
        safe_step = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(step_id))[:120]
        temp_root = Path(load_path_settings(self._runtime_root())["temp_root"]).resolve()
        path = temp_root / "recovery" / "handoff-notes" / safe_task / f"{safe_step}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    def _persist_recovery_note(self, task_id: str, step_id: str, note: str, scope_items: list[int]) -> Path:
        clean = str(note or "").strip()
        if not clean:
            raise ValueError("recovery note must not be blank")
        path = self._recovery_note_path(task_id, step_id)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(clean, encoding="utf-8")
        tmp.replace(path)
        if self.durable and hasattr(self.durable, "record_recovery_note"):
            self.durable.record_recovery_note(task_id, step_id, clean, scope_items)
        return path

    def _ensure_recovery_handoff_note(self, job: PromptJob, prior: str) -> Path:
        scope = self._recovery_scope_items(job)
        if self.durable and hasattr(self.durable, "recovery_note"):
            existing = str(self.durable.recovery_note(job.task_id, job.step_id) or "").strip()
            if existing:
                path = self._recovery_note_path(job.task_id, job.step_id)
                if not path.exists():
                    self._persist_recovery_note(job.task_id, job.step_id, existing, scope)
                return path
        raw = self._oversize_recovery_material(job, prior)
        prompt = (
            "Create a compact durable recovery handoff note for another worker. The next worker will receive ONLY the location of this note, not the bulk parent context. "
            "Preserve: immutable scope, completed work, unresolved work, authoritative facts/constraints, exact source file locations, database locators, useful partial results, and what must happen next. "
            "Remove narration, duplicated evidence, repeated plan text, and verbose reasoning. Do not widen the scope or invent facts. Target <=4500 characters. "
            "Thinking stays enabled. Return only the compact note.\n\n" + raw[:42000]
        )
        try:
            note = str(self.client.generate(prompt, think=True, num_predict=5000, temperature=0.1)).strip()
        except ModelOutputTruncated as exc:
            note = str(exc.partial or "").strip()
        if not note:
            note = (
                f"Task {job.task_id} step {job.step_id}. Immutable scope items: {scope}.\n"
                f"Instruction: {job.prompt[:1800]}\n"
                f"Prior context locator: PostgreSQL task_steps/task_evidence for task={job.task_id}.\n"
                "Raw oversized segments: PostgreSQL norm_runtime.task_step_segments for the same task/step."
            )
        note = note[:6000]
        path = self._persist_recovery_note(job.task_id, job.step_id, note, scope)
        if self.durable and hasattr(self.durable, "compact_task_evidence"):
            self.durable.compact_task_evidence(job.task_id, max_result_chars=2400)
        return path

    def _recovery_locator_context(self, job: PromptJob, note_path: Path, *, analysis_path: Path | None = None, unit_scope: list[int] | None = None) -> str:
        scope = [int(v) for v in (unit_scope if unit_scope is not None else self._recovery_scope_items(job))]
        lines = [
            "RECOVERY POINTER HANDOFF — bulk parent work is intentionally NOT embedded here.",
            f"Immutable scope items: {scope}",
            f"Read compact handoff note first: {note_path}",
            f"PostgreSQL compact note locator: norm_runtime.task_recovery_notes task_id={job.task_id} step_id={job.step_id}",
            f"Raw durable segment fallback only if the compact note is insufficient: norm_runtime.task_step_segments task_id={job.task_id} step_id={job.step_id}",
        ]
        if analysis_path is not None:
            lines.append(f"Read structured recovery-analysis note: {analysis_path}")
        lines.append("Use source files named by those compact notes on demand. Do not reconstruct or ingest unrelated sibling scope.")
        return "\n".join(lines)

    def _compact_recovery_child_result(self, child_id: str, scope_items: list[int]) -> str:
        if not self.durable:
            return ""
        if hasattr(self.durable, "recovery_note"):
            existing = str(self.durable.recovery_note(child_id, "work") or "").strip()
            if existing:
                return existing
        raw = str(self.durable.latest_summary(child_id) or "").strip()
        content = self._step_result_content(raw)
        if len(content) <= 6000:
            note = content
        else:
            prompt = (
                "Compress this completed recovery-child result into a durable working note. Keep every material conclusion, "
                "item/path verdict, key evidence reference, unresolved issue, confirmation/invalidation condition, and scope boundary. "
                "Remove narration, repeated instructions, duplicated evidence, and verbose reasoning. Do not add facts. "
                "Target <=4500 characters. Thinking remains enabled. Return only the compact note.\n\n"
                f"SCOPE ITEMS: {scope_items}\n\nRESULT TO COMPACT:\n{content[:30000]}"
            )
            try:
                note = str(self.client.generate(prompt, think=True, num_predict=5000, temperature=0.1)).strip()
            except ModelOutputTruncated as exc:
                note = str(exc.partial or "").strip()
            if not note:
                note = content[:2200] + "\n...[raw result archived; middle omitted]...\n" + content[-2200:]
            note = note[:6000]
        self._persist_recovery_note(child_id, "work", note, scope_items)
        if hasattr(self.durable, "compact_task_evidence"):
            stats = self.durable.compact_task_evidence(child_id, max_result_chars=2400)
            if stats.get("archived_rows"):
                logging.info("Compacted recovery child evidence task=%s archived=%s before_chars=%s after_chars=%s", child_id, stats.get("archived_rows"), stats.get("before_chars"), stats.get("after_chars"))
        return note

    def _oversize_recovery_material(self, job: PromptJob, prior: str) -> str:
        segments = self.durable.step_segments(job.task_id, job.step_id) if self.durable else []
        thinking_segments = (self.durable.thinking_segments(job.task_id, job.step_id) if self.durable and hasattr(self.durable, "thinking_segments") else [])
        rendered: list[str] = []
        for item in segments[-6:]:
            content = str(item.get("content") or "")
            if len(content) > 24000:
                content = content[:12000] + "\n...[middle omitted]...\n" + content[-12000:]
            rendered.append(
                f"SEGMENT {item.get('segment_index')} items={item.get('item_start')}-{item.get('item_end')}\n"
                f"{content}\nRESUME SUMMARY:\n{item.get('resume_summary') or ''}"
            )
        thinking_notes = []
        for item in thinking_segments[-4:]:
            note = str(item.get("condensed_note") or "").strip()
            if note:
                thinking_notes.append(f"THINKING {item.get('thinking_index')} (raw preserved in PostgreSQL; compact note follows)\n{note}")
        return (
            f"PARENT TASK: {job.task_id}\nPARENT STEP: {job.step_id}\n\n"
            f"PARENT STEP INSTRUCTION:\n{job.prompt}\n\n"
            f"VERIFIED PRIOR STEP CONTEXT:\n{str(prior or '')[-32000:]}\n\n"
            f"COMPACT UNFINISHED THINKING NOTES:\n" + ("\n\n".join(thinking_notes) if thinking_notes else "(none)") + "\n\n"
            f"DURABLE OVERSIZED STEP SEGMENTS:\n" + ("\n\n".join(rendered) if rendered else "(no emitted answer segment was available)")
        )

    @staticmethod
    def _parse_recovery_analysis(text: str, parent_scope: list[int] | None = None) -> dict:
        raw = str(text or "").strip()
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            start, end = raw.find("{"), raw.rfind("}")
            if start < 0 or end <= start:
                raise RuntimeError("oversize recovery analysis did not return JSON")
            data = json.loads(raw[start:end + 1])
        if isinstance(data, dict) and data.get("kind") == "step_result":
            inner = data.get("content")
            if not isinstance(inner, str) or not inner.strip():
                raise RuntimeError("oversize recovery step_result envelope has no content")
            inner_raw = inner.strip()
            try:
                data = json.loads(inner_raw)
            except json.JSONDecodeError:
                start, end = inner_raw.find("{"), inner_raw.rfind("}")
                if start < 0 or end <= start:
                    raise RuntimeError("oversize recovery analysis content did not contain JSON")
                data = json.loads(inner_raw[start:end + 1])
        if not isinstance(data, dict):
            raise RuntimeError("oversize recovery analysis must be a JSON object")
        units = data.get("remaining_units")
        if not isinstance(units, list):
            raise RuntimeError("oversize recovery analysis missing remaining_units")
        allowed = PromptWorker._normalize_scope_items(
            list(parent_scope or []), field="parent_scope"
        )
        allowed_set = set(allowed)
        clean_units = []
        seen_items: set[int] = set()
        for item in units[:8]:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip()
            instruction = str(item.get("instruction") or "").strip()
            verify = str(item.get("verify") or "").strip()
            raw_scope = item.get("scope_items")
            if raw_scope is None:
                scope = []
            else:
                scope = PromptWorker._normalize_scope_items(
                    raw_scope, field=f"remaining_units[{len(clean_units)}].scope_items"
                )
            if not scope and allowed:
                scope = PromptWorker._scope_items_from_text(name + "\n" + instruction)
            if allowed:
                if not scope:
                    raise RuntimeError(f"recovery unit lacks explicit in-scope items: {name}")
                outside = sorted(set(scope) - allowed_set)
                if outside:
                    raise RuntimeError(f"recovery unit widened parent scope {allowed}: {name} -> outside items {outside}")
                if len(allowed) > 1:
                    overlap = seen_items.intersection(scope)
                    if overlap:
                        raise RuntimeError(f"recovery units overlap within parent scope on items {sorted(overlap)}")
                    seen_items.update(scope)
            if name and instruction and verify:
                clean_units.append({
                    "name": name[:160], "instruction": instruction[:12000], "verify": verify[:1200],
                    "scope_items": scope,
                })
        if allowed and len(allowed) == 1 and len(clean_units) > 4:
            raise RuntimeError("atomic item recovery may use at most 4 semantic subchecks")
        data["remaining_units"] = clean_units
        for key in ("useful_points", "completed_work", "merge_requirements"):
            vals = data.get(key)
            data[key] = [str(v).strip()[:2000] for v in vals if str(v).strip()] if isinstance(vals, list) else []
        return data

    def _start_oversize_analysis_child(self, redis_id: str, job: PromptJob, prior: str) -> str:
        parent_scope = self._recovery_scope_items(job)
        handoff_path = self._ensure_recovery_handoff_note(job, prior)
        material = self._recovery_locator_context(job, handoff_path, unit_scope=parent_scope)
        if parent_scope:
            material += f"\nIMMUTABLE PARENT SCOPE ITEMS: {parent_scope}. A child may only keep or shrink this scope; it may never add an item outside it."
        feedback = str((job.metadata or {}).get("oversize_analysis_feedback") or "").strip()
        if feedback:
            material += "\nRECOVERY ANALYSIS FEEDBACK FROM PRIOR INVALID CHILD:\n" + feedback
        instruction = (
            "Analyze the durable work from an oversized parent step and design a recovery decomposition. "
            "Do not redo the parent task. Identify what useful work is already complete, what remains unresolved, "
            "and split only the unresolved work into bounded non-overlapping units. "
            "Your FINAL response for this child MUST be the structured recovery record itself. "
            "Do not end with progress commentary, an intention to inspect more material, or a statement of what you will do next. "
            "Use the available tools first if needed, then finish by returning JSON only with this shape: "
            '{"useful_points":["..."],"completed_work":["..."],'
            '"remaining_units":[{"name":"...","instruction":"...","verify":"...","scope_items":[17]}],'
            '"merge_requirements":["..."]}. '
            "Use 2-8 remaining_units when decomposition is warranted. Units must be semantically meaningful, not arbitrary token ranges. "
            "If IMMUTABLE PARENT SCOPE ITEMS are present, EVERY work unit must include scope_items and those items must be a subset of the parent scope. "
            "Never move later workflow into a child: no global selection, final synthesis, or sibling-range work unless it is already inside this parent's own instruction and scope. "
            "When a range is too large, prefer processing the earliest unresolved item or a smaller prefix first; the owning parent can advance its cursor afterward. "
            "Preserve global comparison/selection requirements for the owning parent merge rather than solving them independently in a narrower child."
        )
        child_id, child_job = self._single_runtime_child(
            job,
            title=f"Analyze oversized step {job.task_id}/{job.step_id}",
            instruction=instruction,
            verify="Structured JSON identifies durable useful work, unresolved work, bounded non-overlapping child units, and merge requirements.",
            context=material,
            prompt_origin="runtime_oversize_recovery_analysis",
            allow_subtask=False,
            scope_items=parent_scope,
        )
        metadata = dict(job.metadata or {})
        metadata["oversize_recovery_stage"] = "analysis_wait"
        metadata["oversize_analysis_child_id"] = child_id
        metadata.pop("oversize_analysis_feedback", None)
        job.metadata = metadata
        parked = self.queue.park_chain_for_dependencies(redis_id, job)
        try:
            self.queue.enqueue_chain([child_job])
        except Exception:
            self.durable.finalize_task(child_id, "Oversize analysis child failed to enqueue.", "failed")
            raise
        logging.info(
            "Spawned oversize analysis child parent=%s/%s child=%s parked_parent_nodes=%s",
            job.task_id, job.step_id, child_id, parked,
        )
        return child_id

    def _spawn_recovery_unit(self, job: PromptJob, prior: str, analysis_task_id: str, unit: dict, index: int, total: int, completed_note_paths: list[str]) -> tuple[str, PromptJob]:
        handoff_path = self._ensure_recovery_handoff_note(job, prior)
        analysis_text = str(self.durable.latest_summary(analysis_task_id) or "")
        analysis_content = self._step_result_content(analysis_text)[:6000]
        analysis_path = self._persist_recovery_note(analysis_task_id, "work", analysis_content, self._recovery_scope_items(job))
        unit_scope = list(unit.get("scope_items") or [])
        context = self._recovery_locator_context(job, handoff_path, analysis_path=analysis_path, unit_scope=unit_scope)
        if completed_note_paths:
            context += "\nPreviously completed sibling compact-note locations (read only if needed):\n" + "\n".join(completed_note_paths[-8:])
        context += f"\nTHIS CHILD UNIT: {index + 1}/{total}. Process only this unit; do not advance the parent cursor yourself."
        return self._single_runtime_child(
            job,
            title=f"Oversize recovery unit {index + 1}: {unit['name']}",
            instruction=unit["instruction"],
            verify=unit["verify"],
            context=context,
            prompt_origin="runtime_oversize_recovery_unit",
            allow_subtask=True,
            scope_items=unit_scope,
        )

    def _spawn_oversize_work_children(
        self,
        redis_id: str,
        job: PromptJob,
        prior: str,
        analysis_task_id: str,
        analysis: dict,
    ) -> list[str]:
        units = list(analysis.get("remaining_units") or [])
        if not units:
            return []
        metadata = dict(job.metadata or {})
        metadata["oversize_recovery_stage"] = "children_wait"
        metadata["oversize_recovery_units"] = units
        metadata["oversize_recovery_unit_index"] = 0
        metadata["oversize_recovery_child_task_ids"] = []
        metadata["oversize_recovery_completed_note_paths"] = []
        child_id, child_job = self._spawn_recovery_unit(job, prior, analysis_task_id, units[0], 0, len(units), [])
        metadata["oversize_recovery_child_task_ids"] = [child_id]
        job.metadata = metadata
        parked = self.queue.park_chain_for_dependencies(redis_id, job)
        try:
            self.queue.enqueue_chain([child_job])
        except Exception:
            self.durable.finalize_task(child_id, "Sequential recovery child failed to enqueue.", "failed")
            raise
        logging.info(
            "Spawned sequential oversize recovery child parent=%s/%s child=%s unit=1/%s parked_parent_nodes=%s",
            job.task_id, job.step_id, child_id, len(units), parked,
        )
        return [child_id]

    def _progress_oversize_recovery(self, redis_id: str, job: PromptJob, prior: str) -> dict:
        metadata = dict(job.metadata or {})
        stage = str(metadata.get("oversize_recovery_stage") or "").strip()
        if not stage:
            return {"parked": False, "context": ""}

        if stage == "analysis_pending":
            self._start_oversize_analysis_child(redis_id, job, prior)
            return {"parked": True, "context": ""}

        analysis_task_id = str(metadata.get("oversize_analysis_child_id") or "").strip()
        if stage == "analysis_wait":
            if not analysis_task_id:
                raise VerifierProtocolError("oversize recovery analysis task id missing")
            status = self.durable.task_status(analysis_task_id)
            if status not in {"completed", "failed", "cancelled"}:
                self.queue.park_chain_for_dependencies(redis_id, job)
                logging.info(
                    "Oversize parent waiting for analysis child parent=%s/%s child=%s status=%s",
                    job.task_id, job.step_id, analysis_task_id, status,
                )
                return {"parked": True, "context": ""}
            if status != "completed":
                retry_count = int(metadata.get("oversize_analysis_child_retry_count", 0))
                if retry_count >= 2:
                    raise VerifierProtocolError(
                        f"oversize analysis child failed/cancelled too many times: {analysis_task_id} ended {status}"
                    )
                metadata["oversize_analysis_child_retry_count"] = retry_count + 1
                metadata["oversize_recovery_stage"] = "analysis_pending"
                metadata.pop("oversize_analysis_child_id", None)
                metadata["oversize_analysis_feedback"] = (
                    f"Previous analysis child {analysis_task_id} ended {status}. "
                    "Start a fresh bounded analysis child from durable parent state; do not count this as a parent execution retry."
                )
                if job.attempt:
                    metadata["oversize_parent_attempt_reset_from"] = max(
                        int(metadata.get("oversize_parent_attempt_reset_from", 0)), int(job.attempt)
                    )
                    job.attempt = 0
                job.metadata = metadata
                parked = self.queue.park_chain_for_dependencies(redis_id, job)
                logging.warning(
                    "Oversize analysis child terminal; scheduling replacement parent=%s/%s child=%s status=%s child_retry=%s/2 parked_parent_nodes=%s",
                    job.task_id, job.step_id, analysis_task_id, status, retry_count + 1, parked,
                )
                return {"parked": True, "context": ""}
            analysis_text = str(self.durable.latest_summary(analysis_task_id) or "")
            try:
                analysis = self._parse_recovery_analysis(analysis_text, parent_scope=self._recovery_scope_items(job))
            except Exception as exc:
                repair_count = int(metadata.get("oversize_analysis_repair_count", 0))
                if repair_count >= 2:
                    raise VerifierProtocolError(
                        f"oversize analysis remained invalid after bounded repair attempts: {type(exc).__name__}: {exc}"
                    ) from exc
                metadata["oversize_analysis_repair_count"] = repair_count + 1
                metadata["oversize_recovery_stage"] = "analysis_pending"
                metadata.pop("oversize_analysis_child_id", None)
                metadata["oversize_analysis_feedback"] = (
                    f"Previous analysis child {analysis_task_id} was invalid: {type(exc).__name__}: {exc}. "
                    f"Its final output was: {analysis_text[-4000:]}"
                )
                job.metadata = metadata
                self.queue.park_chain_for_dependencies(redis_id, job)
                logging.warning(
                    "Oversize analysis child malformed; scheduling fresh analysis child parent=%s/%s previous_child=%s repair=%s/2 error=%s",
                    job.task_id, job.step_id, analysis_task_id, repair_count + 1, exc,
                )
                return {"parked": True, "context": ""}
            metadata.pop("oversize_analysis_child_retry_count", None)
            metadata.pop("oversize_analysis_repair_count", None)
            job.metadata = metadata
            if analysis["remaining_units"]:
                self._spawn_oversize_work_children(redis_id, job, prior, analysis_task_id, analysis)
                return {"parked": True, "context": ""}
            metadata.pop("oversize_recovery_stage", None)
            job.metadata = metadata
            return {
                "parked": False,
                "context": f"OVERSIZE RECOVERY ANALYSIS (no child units required):\n{analysis_text}",
            }

        if stage == "children_wait":
            units = list(metadata.get("oversize_recovery_units") or [])
            child_ids = [str(v) for v in metadata.get("oversize_recovery_child_task_ids", []) if str(v)]
            completed_note_paths = [str(v) for v in metadata.get("oversize_recovery_completed_note_paths", []) if str(v)]
            unit_index = int(metadata.get("oversize_recovery_unit_index", 0))
            if not units or not child_ids or unit_index >= len(units):
                raise VerifierProtocolError("sequential oversize recovery state is incomplete")
            current_child = child_ids[-1]
            status = self.durable.task_status(current_child)
            if status in {"failed", "cancelled"}:
                retry_count = int(metadata.get("oversize_recovery_unit_retry_count", 0))
                if retry_count >= 2:
                    raise VerifierProtocolError(
                        f"oversize recovery unit {unit_index + 1} failed/cancelled too many times: {current_child} ended {status}"
                    )
                current_unit = units[unit_index]
                replacement_id, replacement_job = self._spawn_recovery_unit(
                    job, prior, analysis_task_id, current_unit, unit_index, len(units), completed_note_paths
                )
                child_ids.append(replacement_id)
                metadata["oversize_recovery_child_task_ids"] = child_ids
                metadata["oversize_recovery_unit_retry_count"] = retry_count + 1
                if job.attempt:
                    metadata["oversize_parent_attempt_reset_from"] = max(
                        int(metadata.get("oversize_parent_attempt_reset_from", 0)), int(job.attempt)
                    )
                    job.attempt = 0
                job.metadata = metadata
                parked = self.queue.park_chain_for_dependencies(redis_id, job)
                try:
                    self.queue.enqueue_chain([replacement_job])
                except Exception:
                    self.durable.finalize_task(replacement_id, "Replacement oversize recovery child failed to enqueue.", "failed")
                    raise
                logging.warning(
                    "Oversize recovery child terminal; replacing same unit parent=%s/%s old_child=%s replacement=%s status=%s unit=%s/%s child_retry=%s/2 parked_parent_nodes=%s",
                    job.task_id, job.step_id, current_child, replacement_id, status,
                    unit_index + 1, len(units), retry_count + 1, parked,
                )
                return {"parked": True, "context": ""}
            if status != "completed":
                self.queue.park_chain_for_dependencies(redis_id, job)
                logging.info(
                    "Oversize parent waiting for sequential child parent=%s/%s child=%s unit=%s/%s status=%s",
                    job.task_id, job.step_id, current_child, unit_index + 1, len(units), status,
                )
                return {"parked": True, "context": ""}

            metadata["oversize_recovery_unit_retry_count"] = 0
            current_unit = units[unit_index]
            current_scope = list(current_unit.get("scope_items") or []) if isinstance(current_unit, dict) else []
            note = self._compact_recovery_child_result(current_child, current_scope)
            note_path = str(self._recovery_note_path(current_child, "work"))
            if note_path not in completed_note_paths:
                completed_note_paths.append(note_path)
            next_index = unit_index + 1

            if next_index < len(units):
                next_unit = units[next_index]
                next_child_id, next_child_job = self._spawn_recovery_unit(
                    job, prior, analysis_task_id, next_unit, next_index, len(units), completed_note_paths
                )
                child_ids.append(next_child_id)
                metadata["oversize_recovery_unit_index"] = next_index
                metadata["oversize_recovery_child_task_ids"] = child_ids
                metadata["oversize_recovery_completed_note_paths"] = completed_note_paths
                job.metadata = metadata
                parked = self.queue.park_chain_for_dependencies(redis_id, job)
                try:
                    self.queue.enqueue_chain([next_child_job])
                except Exception:
                    self.durable.finalize_task(next_child_id, "Sequential recovery child failed to enqueue.", "failed")
                    raise
                logging.info(
                    "Advanced sequential oversize recovery cursor parent=%s/%s completed_child=%s next_child=%s unit=%s/%s note_chars=%s parked_parent_nodes=%s",
                    job.task_id, job.step_id, current_child, next_child_id, next_index + 1, len(units), len(note), parked,
                )
                return {"parked": True, "context": ""}

            analysis_path = self._recovery_note_path(analysis_task_id, "work") if analysis_task_id else None
            child_parts = [f"compact_note={path}" for path in completed_note_paths]
            for key in (
                "oversize_recovery_stage", "oversize_analysis_child_id",
                "oversize_recovery_child_task_ids", "oversize_recovery_units",
                "oversize_recovery_unit_index", "oversize_recovery_completed_note_paths",
                "oversize_recovery_unit_retry_count", "oversize_analysis_child_retry_count",
                "oversize_analysis_repair_count",
            ):
                metadata.pop(key, None)
            metadata["oversize_recovery_completed"] = True
            job.metadata = metadata
            context = (
                "OVERSIZE RECOVERY CURSOR COMPLETE. Bulk child outputs are intentionally not embedded.\n"
                + (f"Structured analysis note: {analysis_path}\n" if analysis_path else "")
                + "Verified compact child note locations:\n" + "\n".join(child_parts)
                + "\nRead compact notes on demand. Merge only within the original parent scope; consult raw PostgreSQL segments only when a compact note is insufficient."
            )
            logging.info(
                "Sequential oversize recovery complete parent=%s/%s units=%s children=%s",
                job.task_id, job.step_id, len(units), len(child_ids),
            )
            return {"parked": False, "context": context}

        raise VerifierProtocolError(f"unknown oversize recovery stage: {stage}")

    def _child_answer_if_ready(self, job: PromptJob) -> str | None:
        child_id = str((job.metadata or {}).get("waiting_child_task_id") or "").strip()
        if not child_id:
            return None
        if not self.durable:
            raise RuntimeError("parent is waiting on child task without durable store")
        status = self.durable.task_status(child_id)
        if status == "completed":
            answer = str(self.durable.latest_summary(child_id) or "").strip()
            if not answer:
                raise RuntimeError(f"completed child task has no durable result: {child_id}")
            metadata = dict(job.metadata or {})
            metadata.pop("waiting_child_task_id", None)
            metadata.pop("waiting_child_since", None)
            metadata["consumed_child_task_id"] = child_id
            job.metadata = metadata
            return answer
        if status in {"failed", "cancelled"}:
            detail = self.durable.latest_summary(child_id) or f"child task {status}"
            raise RuntimeError(f"child task {child_id} {status}: {detail}")
        raise RuntimeError(f"child task {child_id} resumed before reaching terminal state: {status}")

    def _generate_complete_text(self, prompt: str, *, think: bool, num_predict: int, temperature: float, max_segments: int = 4) -> str:
        pieces: list[str] = []
        current = str(prompt)
        for segment in range(1, max_segments + 1):
            try:
                text = self.client.generate(current, think=think, num_predict=num_predict, temperature=temperature)
                pieces.append(text)
                return "".join(pieces).strip()
            except ModelOutputTruncated as exc:
                pieces.append(exc.partial)
                joined = "".join(pieces)
                logging.warning("Model text output hit length limit segment=%s/%s chars=%s eval_count=%s; continuing", segment, max_segments, len(joined), exc.eval_count)
                if segment >= max_segments:
                    raise
                current = (
                    "Continue an incomplete response. Do not restart, summarize, or repeat already-completed material. "
                    "Continue exactly from the cutoff and finish every remaining requirement. Return only the continuation.\n\n"
                    f"ORIGINAL REQUEST/INSTRUCTIONS:\n{prompt}\n\nRESPONSE SO FAR:\n{joined}"
                )
        raise RuntimeError("unreachable text completion loop")

    def _generate_with_budget(
        self,
        redis_id: str,
        job: PromptJob,
        prompt: str,
        started,
        name: str,
        started_clock: float,
    ) -> str | None:
        base_prompt = str(prompt)
        prompt = self._prompt_with_context_injections(job, base_prompt)
        tools = self._worker_tools()
        if tools is not None and hasattr(tools, "set_task_context"):
            tools.set_task_context(job.task_id, job.step_id)
        metadata = job.metadata or {}
        if tools is None or not hasattr(self.client, "chat_with_tools"):
            answer = self._generate_complete_text(
                prompt,
                think=bool(metadata.get("think", False)),
                num_predict=int(metadata.get("num_predict") or 16000),
                temperature=float(metadata.get("temperature", 0.2)),
            )
            if not job.next_prompt_id and monotonic() - started_clock < 300:
                improved = self._generate_complete_text(
                    self._review_prompt(prompt, answer),
                    think=True,
                    num_predict=12000,
                    temperature=0.1,
                )
                answer = improved.strip() or answer
            return self._verify_and_correct_without_tools(job, prompt, answer)

        historical_evidence = self.live.task_evidence(job.task_id) if hasattr(self.live, "task_evidence") else []
        outcome = self._run_tool_slice(job, prompt, tools)
        prompt = self._prompt_with_context_injections(job, base_prompt, consume=False)
        local_evidence = list(outcome.get("evidence") or [])
        if outcome["yielded"]:
            self._checkpoint_yield(redis_id, job, name, started, outcome)
            return None
        answer = str(outcome["answer"])
        evidence = list(historical_evidence) + local_evidence
        if not job.next_prompt_id and monotonic() - started_clock < 300:
            review = self._run_tool_slice(
                job,
                self._review_prompt(prompt, answer, evidence),
                tools,
            )
            review_evidence = list(review.get("evidence") or [])
            if review["yielded"]:
                self._checkpoint_yield(redis_id, job, name, started, review)
                return None
            answer = str(review["answer"] or answer)
            evidence.extend(review_evidence)
        return self._verify_and_correct(job, prompt, answer, evidence, tools, redis_id, name, started)

    @staticmethod
    def _numbered_item_range(text: str) -> tuple[int | None, int | None]:
        values: list[int] = []
        patterns = (
            r"(?mi)^\s*(?:path|item|candidate|survivor)\s*#?\s*(\d{1,6})\b",
            r"(?mi)^\s*\*{0,2}(\d{1,6})\s*[.)]\s+",
        )
        for pattern in patterns:
            for value in re.findall(pattern, str(text or "")):
                try:
                    values.append(int(value))
                except ValueError:
                    pass
        values = [v for v in values if v > 0]
        unique = sorted(set(values))
        return (unique[0], unique[-1]) if len(unique) >= 2 else (None, None)

    @staticmethod
    def _durable_segment_summary(content: str, segment_index: int, item_start: int | None, item_end: int | None) -> str:
        clean = str(content or "").strip()
        range_text = (
            f"observed numbered items {item_start}-{item_end}"
            if item_start is not None and item_end is not None
            else "no reliable numbered-item range detected"
        )
        tail = clean[-1800:].replace("\x00", "")
        return (
            f"Durable output segment {segment_index}: {len(clean)} characters; {range_text}. "
            "The raw segment is stored in PostgreSQL task_step_segments. "
            f"Ending context for resume:\n{tail}"
        )

    def _persist_thinking_now(self, job: PromptJob, thinking: str, eval_count: int) -> int | None:
        raw = str(thinking or "")
        if not raw.strip() or not self.durable or not hasattr(self.durable, "record_thinking_segment"):
            return None
        return self.durable.record_thinking_segment(job.task_id, job.step_id, raw, eval_count=int(eval_count or 0))

    def _compact_thinking_for_resume(self, job: PromptJob, thinking: str, thinking_index: int | None) -> str:
        raw = str(thinking or "").strip()
        if not raw:
            return "No model thinking text was captured for this slice."
        material = raw if len(raw) <= 56000 else raw[:28000] + "\n...[middle omitted for condensation]...\n" + raw[-28000:]
        prompt = (
            "Condense these unfinished working notes into a durable continuation note. Preserve useful ideas, calculations, partial constructions, branches already tried, unresolved questions, and the exact next work implied by the notes. "
            "Do not invent a conclusion and do not pretend unfinished work is complete. Remove repetition and self-talk. Target <=3500 characters. Thinking remains enabled. Return only the compact working note.\n\n"
            f"TASK: {job.prompt[:4000]}\n\nRAW UNFINISHED THINKING:\n{material}"
        )
        note = ""
        try:
            note = str(self.client.generate(prompt, think=True, num_predict=4000, temperature=0.1)).strip()
        except ModelOutputTruncated as exc:
            note = str(exc.partial or "").strip()
        except Exception:
            logging.exception("Thinking condensation failed task=%s step=%s", job.task_id, job.step_id)
        if not note:
            head = raw[:1200]
            tail = raw[-4200:] if len(raw) > 4200 else raw
            note = f"UNFINISHED THINKING CHECKPOINT (deterministic fallback)\nOpening context:\n{head}\n\nLatest working state:\n{tail}"
        note = note[:6000]
        if thinking_index is not None and self.durable and hasattr(self.durable, "update_thinking_condensed"):
            self.durable.update_thinking_condensed(job.task_id, job.step_id, thinking_index, note)
        return note

    def _durable_output_checkpoint(
        self,
        job: PromptJob,
        *,
        segment_index: int,
        resume_summary: str,
        item_start: int | None,
        item_end: int | None,
    ) -> str:
        if item_end is not None:
            resume = (
                f"Numbered output through item {item_end} is durably saved. "
                f"Resume at the first missing item after {item_end}; do not regenerate saved items."
            )
        else:
            resume = (
                "A completed prefix of this step is durably saved. Resume only the missing remainder "
                "from the ending context below; do not restart or repeat saved material."
            )
        return (
            f"DURABLE OUTPUT CHECKPOINT\nOriginal goal: {job.prompt}\n"
            f"PostgreSQL segment: {segment_index}\n{resume}\n"
            f"Saved segment summary:\n{resume_summary}"
        )

    def _persist_evidence_now(self, job: PromptJob, evidence: list[dict]) -> None:
        if not evidence:
            return
        if hasattr(self.live, "record_evidence"):
            self.live.record_evidence(job.task_id, job.step_id, evidence)
        if self.durable and hasattr(self.durable, "record_evidence"):
            self.durable.record_evidence(job.task_id, job.step_id, evidence)

    def _validation_pool_config(self) -> dict:
        return dict(self._runtime_config().get("validation_pool") or {})

    @staticmethod
    def _parse_iso_timestamp(value: object) -> datetime | None:
        raw = str(value or "").strip()
        if not raw:
            return None
        try:
            parsed = datetime.fromisoformat(raw)
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)

    def _recent_validations(self, job: PromptJob, subjects: list[str] | None = None) -> list[dict]:
        cfg = self._validation_pool_config()
        if not bool(cfg.get("enabled", True)):
            return []
        limit = max(1, min(4096, int(cfg.get("emergency_row_cap", 512))))
        window = max(1, int(cfg.get("recent_window_seconds", 86400)))
        retention = max(window, int(cfg.get("observation_retention_seconds", 604800)))
        cleaned = [str(x).strip() for x in (subjects or []) if str(x).strip()]
        rows: list[dict] = []

        # Redis is the live authority for rolling recent counts and current generation counts.
        if hasattr(self.live, "global_validation"):
            try:
                if cleaned:
                    for subject in cleaned[:limit]:
                        row = self.live.global_validation(subject, recent_window_seconds=window, retention_seconds=retention)
                        if row:
                            rows.append(row)
                elif hasattr(self.live, "recent_global_validations"):
                    rows = self.live.recent_global_validations(
                        recent_window_seconds=window, retention_seconds=retention, limit=limit
                    )
            except Exception:
                logging.exception("Redis global validation-pool read failed task=%s", job.task_id)
                rows = []

        # Explicit review searches PostgreSQL too. Durable counts may lag repetitive
        # same-value Redis confirmations by the configured checkpoint interval.
        if cleaned and self.durable and hasattr(self.durable, "global_validations"):
            try:
                durable_rows = self.durable.global_validations(cleaned, limit=limit)
                durable_by = {str(row.get("subject")): row for row in durable_rows}
                live_by = {str(row.get("subject")): row for row in rows}
                merged = []
                for subject in cleaned[:limit]:
                    live_row = live_by.get(subject)
                    durable_row = durable_by.get(subject)
                    if live_row:
                        item = dict(live_row)
                        if durable_row:
                            item["durable_generation_checks"] = int(durable_row.get("generation_check_count") or 0)
                            item["durable_snapshot_at"] = durable_row.get("snapshot_at")
                        merged.append(item)
                    elif durable_row:
                        merged.append({
                            "subject": subject,
                            "value": durable_row.get("current_value"),
                            "generation_checks": int(durable_row.get("generation_check_count") or 0),
                            "generation_started_at": durable_row.get("generation_started_at"),
                            "last_checked_at": durable_row.get("last_checked_at"),
                            "source_counts": dict(durable_row.get("source_counts") or {}),
                            "recent_checks": None,
                            "storage": "postgres_snapshot",
                            "durable_generation_checks": int(durable_row.get("generation_check_count") or 0),
                            "durable_snapshot_at": durable_row.get("snapshot_at"),
                        })
                rows = merged
            except Exception:
                logging.exception("PostgreSQL global validation-pool read failed task=%s", job.task_id)

        if rows:
            return rows[:limit]

        # Compatibility fallback while older task-scoped observations age out.
        if self.durable and hasattr(self.durable, "recent_validations"):
            try:
                legacy = self.durable.recent_validations(
                    job.task_id, subjects=cleaned or None, window_seconds=window, limit=limit
                )
                for item in legacy:
                    item["storage"] = "legacy_task"
                    item["generation_checks"] = int(item.get("check_count") or 0)
                    item["recent_checks"] = None  # Legacy totals are not a rolling-window count.
                return legacy
            except Exception:
                logging.exception("Legacy validation-pool read failed task=%s", job.task_id)
        return []

    def _validation_context(self, job: PromptJob) -> str:
        rows = self._recent_validations(job)
        cfg = self._validation_pool_config()
        token_budget = max(1, int(cfg.get("context_token_budget", 2500)))
        char_budget = max(1, min(int(cfg.get("context_char_budget", 10000)), token_budget * 4))
        hours = max(1, int(cfg.get("recent_window_seconds", 86400)) // 3600)
        if not rows:
            return (
                f"RECENT VALIDATIONS: no live Redis observations in the last {hours}h. "
                "Use review_validations for a subject if a durable older PostgreSQL checklist entry may still be useful."
            )[:char_budget]
        threshold = max(1, int(cfg.get("reuse_after_checks", 3)))
        lines = [
            f"RECENT VALIDATIONS: live Redis rolling {hours}h checklist (advisory, never a permission gate):",
            "PostgreSQL is the durable compact checklist; same-value counts can lag until the next observation-triggered snapshot. "
            "A Redis/PostgreSQL count difference is expected and is not itself a reason to re-check.",
        ]
        used = sum(len(line) + 1 for line in lines)
        omitted = 0
        for item in rows:
            recent = int(item.get("recent_checks") or 0)
            generation = int(item.get("generation_checks") or item.get("check_count") or 0)
            sources = dict(item.get("source_counts") or {})
            if item.get("recent_checks") is None:
                advice = "live recent count unknown; use timestamps and volatility"
            elif recent >= max(6, threshold):
                advice = f"already checked {recent} times in {hours}h; another check is allowed but likely low-value unless this could have changed"
            elif recent >= threshold:
                advice = f"repeatedly confirmed in {hours}h; consider reuse, but check again if useful"
            else:
                advice = "recently observed; decide based on volatility and task needs"
            value = str(item.get("value") or "")
            display_value = value if len(value) <= 1000 else value[:1000] + " [truncated; review subject for full value]"
            line = (
                f"- {str(item.get('subject') or '')[:500]} = {display_value} | "
                f"storage={item.get('storage')} | recent_{hours}h={item.get('recent_checks')} | "
                f"generation_checks={generation} | sources={len(sources)} "
                f"({', '.join(sorted(sources)[:6]) or 'unspecified'}) | "
                f"generation_started={item.get('generation_started_at')} | last={item.get('last_checked_at')} | {advice}"
            )
            if used + len(line) + 80 > char_budget:
                omitted += 1
                continue
            lines.append(line)
            used += len(line) + 1
        if omitted:
            lines.append(f"{omitted} rows omitted by context budget; use review_validations for specific subjects.")
        return "\n".join(lines)[:char_budget]

    @staticmethod
    def _validation_tool_schemas() -> list[dict]:
        return [
            {"type":"function","function":{"name":"review_validations","description":"Review the shared validation checklist before reacquiring facts. Redis provides live recent counts; PostgreSQL provides durable older/current generations. Results are advisory only.","parameters":{"type":"object","properties":{"subjects":{"type":"array","items":{"type":"string"},"minItems":1}},"required":["subjects"],"additionalProperties":False}}},
            {"type":"function","function":{"name":"record_validations","description":"Batch-record semantic facts just established from observed evidence so later rounds, steps, retries, and unrelated tasks can see the current shared generation.","parameters":{"type":"object","properties":{"items":{"type":"array","minItems":1,"items":{"type":"object","properties":{"subject":{"type":"string"},"value":{"type":"string"},"source":{"type":"string"},"note":{"type":"string"}},"required":["subject","value","source"],"additionalProperties":False}}},"required":["items"],"additionalProperties":False}}},
        ]

    def _record_global_validations(self, job: PromptJob, items: list[dict]) -> list[dict]:
        cfg = self._validation_pool_config()
        window = max(1, int(cfg.get("recent_window_seconds", 86400)))
        retention = max(window, int(cfg.get("observation_retention_seconds", 604800)))
        snapshot_interval = max(60, int(cfg.get("durable_snapshot_interval_seconds", 43200)))
        subjects = [str(item.get("subject") or "").strip() for item in items if str(item.get("subject") or "").strip()]

        durable_rows = []
        if self.durable and hasattr(self.durable, "global_validations") and subjects:
            try:
                durable_rows = self.durable.global_validations(subjects, limit=max(1, len(subjects)))
            except Exception:
                logging.exception("Could not read durable global validation snapshots")
        durable_by = {str(row.get("subject")): row for row in durable_rows}

        if hasattr(self.live, "seed_global_validation"):
            for row in durable_rows:
                try:
                    self.live.seed_global_validation(row)
                except Exception:
                    logging.exception("Could not seed Redis global validation subject=%s", row.get("subject"))

        live_saved = []
        if hasattr(self.live, "record_global_validations"):
            live_saved = self.live.record_global_validations(
                job.task_id, job.step_id, items,
                recent_window_seconds=window, retention_seconds=retention,
            )

        # Completed generations survive transient PostgreSQL failures in Redis.
        if self.durable and hasattr(self.durable, "archive_global_validation_generation") and hasattr(self.live, "pending_validation_generations"):
            for previous in self.live.pending_validation_generations():
                try:
                    self.durable.archive_global_validation_generation(previous)
                    self.live.acknowledge_validation_generation(previous["_pending_id"])
                except Exception:
                    logging.exception("Validation history checkpoint deferred; Redis retains the generation")

        # Preserve the legacy task-scoped ledger for provenance/backward compatibility.
        if self.durable and hasattr(self.durable, "record_validations"):
            self.durable.record_validations(job.task_id, job.step_id, items)
        if hasattr(self.live, "record_validations"):
            self.live.record_validations(job.task_id, job.step_id, items)

        if self.durable and live_saved and hasattr(self.durable, "upsert_global_validation"):
            now = _utc_now()
            for record in live_saved:
                previous = record.get("_previous_generation")
                if previous and hasattr(self.durable, "archive_global_validation_generation"):
                    try:
                        self.durable.archive_global_validation_generation(previous)
                    except Exception:
                        logging.exception("Could not archive validation generation subject=%s", record.get("subject"))
                prior_snapshot = durable_by.get(str(record.get("subject")))
                snapshot_at = self._parse_iso_timestamp((prior_snapshot or {}).get("snapshot_at"))
                due = (
                    prior_snapshot is None
                    or bool(record.get("value_changed"))
                    or str((prior_snapshot or {}).get("current_value")) != str(record.get("value"))
                    or self._parse_iso_timestamp((prior_snapshot or {}).get("generation_started_at")) != self._parse_iso_timestamp(record.get("generation_started_at"))
                    or snapshot_at is None
                    or (now - snapshot_at).total_seconds() >= snapshot_interval
                )
                if due:
                    try:
                        saved = self.durable.upsert_global_validation(record)
                        durable_by[str(record.get("subject"))] = saved
                    except Exception:
                        logging.exception("Could not checkpoint global validation subject=%s", record.get("subject"))
        return live_saved

    def _execute_validation_tool(self, job: PromptJob, name: str, arguments: dict) -> dict:
        cfg = self._validation_pool_config()
        if not bool(cfg.get("enabled", True)):
            return {"ok": False, "tool": name, "error": "validation pool is disabled"}
        threshold = max(1, int(cfg.get("reuse_after_checks", 3)))
        hours = max(1, int(cfg.get("recent_window_seconds", 86400)) // 3600)
        snapshot_hours = max(1, int(cfg.get("durable_snapshot_interval_seconds", 43200)) // 3600)
        if name == "review_validations":
            subjects = [str(x).strip() for x in (arguments.get("subjects") or []) if str(x).strip()]
            rows = self._recent_validations(job, subjects)
            by_subject = {str(row.get("subject")): row for row in rows}
            reviewed = []
            for subject in subjects:
                row = by_subject.get(subject)
                if not row:
                    reviewed.append({
                        "subject": subject,
                        "recommendation": "check_if_useful",
                        "reason": "not present in the live recent checklist or durable current checklist",
                    })
                    continue
                recent = int(row.get("recent_checks") or 0)
                storage = str(row.get("storage") or "")
                if storage == "postgres_snapshot" and recent == 0:
                    recommendation = "reuse_or_check_freshness"
                    reason = (
                        f"durable checklist entry found; live recent count is unknown. PostgreSQL snapshots "
                        f"are due after {snapshot_hours}h on the next observation and may lag longer during inactivity or outages; "
                        "decide based on whether the fact could have changed"
                    )
                elif recent >= threshold:
                    recommendation = "consider_reuse"
                    reason = f"checked {recent} times in the live {hours}h window; another check is still allowed whenever useful"
                else:
                    recommendation = "check_or_reuse"
                    reason = "recent evidence exists but repetition pressure is low; decide based on volatility and task needs"
                reviewed.append({**row, "recommendation": recommendation, "reason": reason})
            return {
                "ok": True, "tool": name, "reviewed": reviewed,
                "policy": "advisory only; no validation count blocks another check",
            }
        if name == "record_validations":
            items = [dict(x) for x in (arguments.get("items") or []) if isinstance(x, dict)]
            if not items:
                return {"ok": False, "tool": name, "error": "items must not be empty"}
            recorded = self._record_global_validations(job, items)
            return {
                "ok": True, "tool": name, "recorded": recorded, "count": len(recorded),
                "policy": "same value continues the generation; a changed value starts a new generation at one",
            }
        return {"ok": False, "tool": name, "error": "unknown validation-pool tool"}

    def _run_tool_slice(self, job: PromptJob, prompt: str, tools) -> dict:
        import json

        step_limit, task_limit = self._slice_limits()
        task_rounds = self.queue.task_rounds(job.task_id)
        # The shared Redis verification pool is refreshed immediately before each
        # model/tool-decision round below; avoid an extra stale read here.
        validation_context = ""
        validation_instructions = (
            "VALIDATION CHECKLIST RULES:\n"
            "- This is context, not a permission system. You may always check again when you judge it useful; no count is a hard ceiling.\n"
            "- There is one shared semantic verification pool across tasks and tools. Redis is the live checklist and owns rolling recent counts/current generation counts. PostgreSQL is the compact durable checklist and repetitive same-value counts may lag Redis until the periodic snapshot; that difference is expected, not a contradiction.\n"
            "- Before re-checking a fact, inspect the recent checklist below. If the fact is absent and an older/static fact may still be useful, call review_validations for the subject; it searches the durable checklist too.\n"
            "- After establishing material semantic facts, batch-record them with record_validations. Same-value confirmations continue the current generation; observing a different value automatically starts a new generation at count 1 and resets its generation start time.\n"
            "- Prefer existing consistent evidence when another read adds little information, but re-check volatile or actually changed things whenever appropriate.\n"
        )
        tool_prompt = tools.instructions() + "\n\n" + validation_instructions + "\n" + validation_context + "\n\n" + prompt
        messages: list[dict] = [{"role": "user", "content": tool_prompt}]
        pending_verification: dict[str, str] = {}
        evidence: list[dict] = []
        step_rounds = 0
        last_content = ""
        answer_segments: list[str] = []
        blank_result_retries = 0
        persisted_segments = (
            self.durable.step_segments(job.task_id, job.step_id)
            if self.durable and hasattr(self.durable, "step_segments")
            else []
        )
        metadata = job.metadata or {}
        request_type = normalize_request_type(job.request_type)
        long_form = request_type == "conclusion" or bool(metadata.get("is_final_candidate")) or bool(re.search(r"\bexactly\s+\d+\b", prompt, re.I))
        output_budget = int(metadata.get("num_predict") or (27000 if long_form else 16000))
        while step_rounds < step_limit and task_rounds < task_limit:
            new_context = self._consume_context_injections(job)
            if new_context:
                messages.append({"role": "user", "content": self._context_injection_block(new_context)})
            if self._suppression_requested(job.task_id) or (self.durable and self.durable.task_status(job.task_id) == "suppressed"):
                raise ModelGenerationCancelled("task suppressed by operator")
            if self._drain.is_set():
                evidence.extend(self._verify_pending(job, pending_verification, tools))
                checkpoint = self._continuation_checkpoint(
                    job, prompt, messages, "shutdown_requested", step_rounds, task_rounds
                )
                return {
                    "yielded": True,
                    "checkpoint": checkpoint,
                    "reason": "shutdown_requested",
                    "step_rounds": step_rounds,
                    "task_rounds": task_rounds,
                    "evidence": evidence,
                }
            # Refresh the one shared Redis verification pool before every model/tool
            # decision round so repeated checks performed earlier in this same step
            # immediately influence whether another lookup is worth doing.
            messages[0]["content"] = (
                tools.instructions() + "\n\n" + validation_instructions + "\n"
                + self._validation_context(job) + "\n\n" + prompt
            )
            response = self.client.chat_with_tools(
                messages,
                tools.schemas() + self._validation_tool_schemas(),
                think=True,
                temperature=float(metadata.get("temperature", 0.2)),
                num_predict=output_budget,
            )
            step_rounds += 1
            task_rounds = self.queue.add_task_rounds(job.task_id, 1)
            calls = response.get("tool_calls") or []
            content = str(response.get("content", ""))
            thinking = str(response.get("thinking", ""))
            done_reason = str(response.get("done_reason") or "")
            thinking_index = self._persist_thinking_now(job, thinking, int(response.get("eval_count") or 0))
            if content:
                last_content = content
            if not calls:
                if done_reason == "length":
                    evidence.extend(self._verify_pending(job, pending_verification, tools))
                    if content.strip():
                        current_segment = content.rstrip()
                        if not self.durable or not hasattr(self.durable, "record_step_segment"):
                            raise RuntimeError("output limit reached but durable PostgreSQL segment storage is unavailable")
                        item_start, item_end = self._numbered_item_range(current_segment)
                        next_index = len(persisted_segments) + 1
                        resume_summary = self._durable_segment_summary(
                            current_segment, next_index, item_start, item_end
                        )
                        segment_index = self.durable.record_step_segment(
                            job.task_id, job.step_id, current_segment, resume_summary,
                            item_start=item_start, item_end=item_end,
                        )
                        checkpoint = self._durable_output_checkpoint(
                            job,
                            segment_index=segment_index,
                            resume_summary=resume_summary,
                            item_start=item_start,
                            item_end=item_end,
                        )
                        logging.warning(
                            "Model answer hit length limit task=%s step=%s eval_count=%s; saved PostgreSQL segment=%s and yielding to queue",
                            job.task_id, job.step_id, response.get("eval_count"), segment_index,
                        )
                        return {
                            "yielded": True,
                            "checkpoint": checkpoint,
                            "reason": "output_length_checkpoint",
                            "step_rounds": step_rounds,
                            "task_rounds": task_rounds,
                            "evidence": evidence,
                            "resume_metadata": {
                                "durable_segment_count": segment_index,
                                "durable_item_end": item_end,
                            },
                        }

                    compact_thinking = self._compact_thinking_for_resume(job, thinking, thinking_index)
                    checkpoint = (
                        "DURABLE THINKING CHECKPOINT\n"
                        f"Original goal: {job.prompt}\n"
                        "The previous model slice reached its output budget before emitting usable answer text. "
                        "Its raw unfinished thinking WAS saved in PostgreSQL norm_runtime.task_thinking_segments "
                        f"(task_id={job.task_id}, step_id={job.step_id}, thinking_index={thinking_index}). "
                        "Resume from the compact working note below, not from the bulky raw thinking. Keep normal reasoning enabled and do not repeat already-explored work.\n\n"
                        f"COMPACT WORKING NOTE:\n{compact_thinking}"
                    )
                    logging.warning(
                        "Model reached length limit before answer task=%s step=%s eval_count=%s; yielding to queue with thinking still enabled",
                        job.task_id, job.step_id, response.get("eval_count"),
                    )
                    return {
                        "yielded": True,
                        "checkpoint": checkpoint,
                        "reason": "reasoning_output_limit",
                        "step_rounds": step_rounds,
                        "task_rounds": task_rounds,
                        "evidence": evidence,
                    }
                evidence.extend(self._verify_pending(job, pending_verification, tools))
                final_text = (content or last_content).strip()
                if answer_segments:
                    if final_text:
                        answer_segments.append(final_text)
                    final_text = "".join(answer_segments).strip()
                if persisted_segments:
                    saved_prefix = "\n".join(str(item.get("content") or "").rstrip() for item in persisted_segments)
                    final_text = (saved_prefix + "\n" + final_text.lstrip()).strip()
                if not final_text:
                    blank_result_retries += 1
                    if blank_result_retries > 2:
                        raise RuntimeError("model returned blank step result after tool execution")
                    messages.append({"role": "assistant", "content": content})
                    messages.append({
                        "role": "user",
                        "content": "You returned no textual result. Using the task instructions and observed tool results above, return the required concise result for this step now. Do not repeat completed tool work unless verification is genuinely needed.",
                    })
                    continue
                return {
                    "yielded": False,
                    "answer": final_text,
                    "step_rounds": step_rounds,
                    "task_rounds": task_rounds,
                    "evidence": evidence,
                }

            messages.append(
                redact({"role": "assistant", "content": content, "tool_calls": calls})
            )
            for call in calls:
                function = call.get("function") if isinstance(call, dict) else None
                if not isinstance(function, dict):
                    name = "invalid_tool_call"
                    arguments = {}
                    result = {"ok": False, "error": "invalid native tool-call shape"}
                else:
                    name = function.get("name")
                    arguments = function.get("arguments", {})
                    if isinstance(arguments, str):
                        try:
                            arguments = json.loads(arguments)
                        except json.JSONDecodeError:
                            arguments = {}
                    if not isinstance(name, str) or not isinstance(arguments, dict):
                        result = {"ok": False, "error": "invalid tool name or arguments"}
                    elif name in {"review_validations", "record_validations"}:
                        result = self._execute_validation_tool(job, name, arguments)
                    else:
                        result = tools.execute(name, arguments)

                evidence_item = redact({"tool": str(name), "arguments": arguments, "result": result})
                evidence.append(evidence_item)
                self._persist_evidence_now(job, [evidence_item])
                if self._suppression_requested(job.task_id) or (self.durable and self.durable.task_status(job.task_id) == "suppressed"):
                    raise ModelGenerationCancelled("task suppressed by operator after current tool boundary")

                if result.get("park_required"):
                    checkpoint = str(result.get("checkpoint_zip") or "").strip()
                    reason = (
                        "Automatic park: the task reached its 3 GiB per-pass source-processing allowance. "
                        "All task state and notes were preserved"
                        + (f" in {checkpoint}." if checkpoint else ".")
                        + " Resume this same task manually to continue with a fresh processing allowance."
                    )
                    self.request_suppress_task(reason=reason)
                    raise ModelGenerationCancelled(reason)

                if result.get("ok"):
                    observed_path = str(result.get("path") or "")
                    observed_hash = str(result.get("sha256") or "")
                    if (name in {"write_file", "replace_text"} or result.get("file_mutation")) and observed_path and observed_hash:
                        pending_verification[observed_path] = observed_hash
                    elif name == "read_file" and observed_path and observed_hash:
                        if pending_verification.get(observed_path) == observed_hash:
                            pending_verification.pop(observed_path, None)
                messages.append(
                    {
                        "role": "tool",
                        "tool_name": str(name),
                        "content": json.dumps(result, ensure_ascii=False),
                    }
                )
                if result.get("staged"):
                    evidence.extend(self._verify_pending(job, pending_verification, tools))
                    return {
                        "yielded": False,
                        "answer": (
                            "The requested write could not be applied because the target stayed write-blocked after retries. "
                            f"Desired content was staged at {result.get('staged_path')}; recovery note: {result.get('note_path')}. "
                            "The target file was not changed."
                        ),
                        "step_rounds": step_rounds,
                        "task_rounds": task_rounds,
                        "evidence": evidence,
                    }

        evidence.extend(self._verify_pending(job, pending_verification, tools))
        if task_rounds >= task_limit:
            raise TaskRoundBudgetExceeded(
                f"task tool/reasoning round budget exhausted ({task_rounds}/{task_limit}); "
                "stopped instead of requeueing the same task indefinitely"
            )
        reason = "step_round_limit"
        checkpoint = self._continuation_checkpoint(
            job, prompt, messages, reason, step_rounds, task_rounds
        )
        return {
            "yielded": True,
            "checkpoint": checkpoint,
            "reason": reason,
            "step_rounds": step_rounds,
            "task_rounds": task_rounds,
            "evidence": evidence,
        }

    def _verify_pending(self, job: PromptJob, pending: dict[str, str], tools) -> list[dict]:
        """Verification is mandatory and does not consume model/tool-round budget."""
        evidence: list[dict] = []
        for path, expected_hash in tuple(pending.items()):
            observed = tools.execute("read_file", {"path": path})
            item = {
                "tool": "read_file",
                "arguments": {"path": path},
                "result": observed,
                "deterministic_verification": True,
            }
            evidence.append(item)
            self._persist_evidence_now(job, [item])
            if not observed.get("ok"):
                raise RuntimeError(
                    f"verification read failed for {path}: {observed.get('error')}"
                )
            actual_hash = str(observed.get("sha256") or "")
            if actual_hash != expected_hash:
                raise RuntimeError(
                    f"verification hash mismatch for {path}: expected {expected_hash}, "
                    f"got {actual_hash}"
                )
            pending.pop(path, None)
        return evidence

    @staticmethod
    def _evidence_text(evidence: list[dict]) -> str:
        import json

        compact: list[dict] = []
        for item in evidence:
            arguments = item.get("arguments") if isinstance(item, dict) else None
            result = item.get("result") if isinstance(item, dict) else None
            compact_args = {}
            if isinstance(arguments, dict):
                for key in ("path", "expected_sha256"):
                    if key in arguments:
                        compact_args[key] = arguments[key]
            compact_result = {}
            if isinstance(result, dict):
                for key in (
                    "ok", "path", "paths", "sha256", "file_mutation", "created", "error", "staged", "staged_path", "note_path",
                    "attempts", "answer", "content", "text", "summary", "analysis", "observations", "data", "items",
                    "image_count", "output_dir", "analysis_json", "geometry_overlay", "horizontal_consensus", "profile",
                    "device", "width", "height", "reconstruction_similarity", "artifact_fraction", "resource_status", "storage_context",
                    "command", "cwd", "exit_code", "stdout", "stderr", "timed_out", "timeout_seconds", "duration_seconds"
                ):
                    if key in result:
                        value = result[key]
                        if isinstance(value, str) and len(value) > 12000:
                            value = value[:12000] + "...[truncated]"
                        compact_result[key] = value
            compact.append({
                "tool": item.get("tool") if isinstance(item, dict) else None,
                "arguments": compact_args,
                "result": compact_result,
                "deterministic_verification": bool(item.get("deterministic_verification")) if isinstance(item, dict) else False,
            })
        return json.dumps(compact, ensure_ascii=False)

    def _verifier_prompt(self, original_prompt: str, answer: str, evidence: list[dict]) -> str:
        return (
            "Act as a fresh independent skeptical verifier. Return only the JSON object required by the supplied schema. "
            "Do not rewrite valid work for style. Inspect the original request, candidate answer, observed tool evidence, and deterministic verification. "
            "Model assertions are not evidence. Reject unsupported external-action claims, unmet requirements, contradictions, material factual/logical errors, "
            "or review-report language instead of the requested result. requirements_complete means all material request requirements are met. "
            "evidence_supported means every claim needing external/tool evidence has matching observed evidence; it is true when no such evidence is needed. "
            "Use verdict=accept only with no issues and repair_scope=none.\n\n"
            f"Complete task context:\n{original_prompt}\n\nCandidate answer:\n{answer}\n\n"
            f"Observed evidence:\n{self._evidence_text(evidence)}"
        )

    def _record_verifier_attempt(
        self, job: PromptJob, cycle: int, answer: str, report: str, accepted: bool
    ) -> None:
        import json

        path = self._runtime_root() / "logs" / "verifier-trace.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "timestamp": _utc_now().isoformat(),
            "task_id": job.task_id,
            "step_id": job.step_id,
            "cycle": cycle,
            "accepted": accepted,
            "candidate": answer,
            "verifier_report": report,
        }
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")

    def _request_verdict(
        self, original_prompt: str, answer: str, evidence: list[dict]
    ) -> tuple[bool, list[str], str]:
        prompt = self._verifier_prompt(original_prompt, answer, evidence)
        last_error: Exception | None = None
        schema = step_verification_schema()
        for attempt in range(1, 4):
            try:
                raw = self.client.generate(
                    prompt, think=False, num_predict=1400, temperature=0.0, response_format=schema
                )
                parsed = validate_step_verification(self.client.parse_json(raw))
                accepted = parsed["verdict"] == "accept"
                return accepted, list(parsed["issues"]), json.dumps(parsed, ensure_ascii=False)
            except (ValueError, json.JSONDecodeError, RuntimeError) as exc:
                last_error = exc
                logging.warning(
                    "Malformed structured verifier result; retrying verifier only attempt=%s/3 error=%s",
                    attempt, exc,
                )
        raise VerifierProtocolError(
            f"structured step verifier failed after 3 attempts: {last_error}"
        )

    def _verify_and_correct_without_tools(
        self, job: PromptJob, original_prompt: str, answer: str
    ) -> str:
        for cycle in range(1, 4):
            accepted, issues, report = self._request_verdict(original_prompt, answer, [])
            self._record_verifier_attempt(job, cycle, answer, report, accepted)
            if accepted:
                return answer
            corrected = self._generate_complete_text(
                self._correction_prompt(original_prompt, answer, issues, []),
                think=True,
                num_predict=12000,
                temperature=0.1,
            )
            answer = corrected.strip() or answer
        raise VerifierProtocolError("skeptical verifier rejected the result after three correction cycles")

    def _verify_and_correct(
        self,
        job: PromptJob,
        original_prompt: str,
        answer: str,
        evidence: list[dict],
        tools,
        redis_id: str,
        name: str,
        started,
    ) -> str | None:
        for cycle in range(1, 4):
            accepted, issues, report = self._request_verdict(original_prompt, answer, evidence)
            self._record_verifier_attempt(job, cycle, answer, report, accepted)
            if accepted:
                return answer
            correction = self._run_tool_slice(
                job,
                self._correction_prompt(original_prompt, answer, issues, evidence),
                tools,
            )
            correction_evidence = list(correction.get("evidence") or [])
            if correction["yielded"]:
                self._checkpoint_yield(redis_id, job, name, started, correction)
                return None
            answer = str(correction["answer"] or answer)
            evidence.extend(correction_evidence)
        raise VerifierProtocolError("skeptical verifier rejected the result after three correction cycles")

    @staticmethod
    def _continuation_checkpoint(
        job: PromptJob,
        prompt: str,
        messages: list[dict],
        reason: str,
        step_rounds: int,
        task_rounds: int,
    ) -> str:
        import json

        tail = json.dumps(messages[-8:], ensure_ascii=False)
        if len(tail) > 12_000:
            tail = tail[-12_000:]
        return (
            f"CONTINUATION CHECKPOINT\nOriginal goal: {job.prompt}"
            f"\nYield reason: {reason}"
            f"\nStep rounds used: {step_rounds}"
            f"\nTask rounds used: {task_rounds}"
            "\nResume from the observed execution below. Do not repeat completed work. "
            f"Verify prior side effects before relying on them.\nRecent execution: {tail}"
        )

    def _checkpoint_yield(
        self,
        redis_id: str,
        job: PromptJob,
        name: str,
        started,
        outcome: dict,
    ) -> None:
        checkpoint = str(outcome["checkpoint"])
        reason = str(outcome["reason"])
        step_rounds = int(outcome["step_rounds"])
        task_rounds = int(outcome["task_rounds"])
        resume_metadata = outcome.get("resume_metadata") if isinstance(outcome.get("resume_metadata"), dict) else {}
        metadata = dict(job.metadata or {})
        if resume_metadata:
            metadata.update(resume_metadata)
        if reason in {"output_length_checkpoint", "reasoning_output_limit"} and metadata.get("allow_oversize_recovery", True) is not False:
            cycle = int(metadata.get("oversize_recovery_cycle", 0)) + 1
            worker_cfg = self._runtime_config().get("worker", {})
            max_cycles = max(1, int(worker_cfg.get("max_oversize_recovery_cycles", 3)))
            depth = int(metadata.get("subtask_depth", 0))
            max_depth = max(1, int(worker_cfg.get("max_subtask_depth", 4)))
            if depth >= max_depth:
                metadata["depth_cap_yield_count"] = int(metadata.get("depth_cap_yield_count", 0)) + 1
                metadata.pop("oversize_recovery_stage", None)
                logging.warning(
                    "Nested recovery depth cap reached task=%s step=%s depth=%s/%s; requeueing same bounded scope from durable checkpoint",
                    job.task_id, job.step_id, depth, max_depth,
                )
            elif cycle <= max_cycles:
                metadata["oversize_recovery_cycle"] = cycle
                metadata["oversize_recovery_stage"] = "analysis_pending"
                metadata.pop("oversize_analysis_child_id", None)
                metadata.pop("oversize_recovery_child_task_ids", None)
                metadata.pop("oversize_recovery_units", None)
                metadata.pop("oversize_recovery_unit_index", None)
                metadata.pop("oversize_recovery_completed_note_paths", None)
            else:
                logging.warning(
                    "Oversize recovery cycle cap reached task=%s step=%s cycle=%s/%s; requeueing without another decomposition",
                    job.task_id, job.step_id, cycle, max_cycles,
                )
        job.metadata = metadata
        if self.durable:
            result = StepResult(
                task_id=job.task_id,
                step_id=job.step_id,
                name=name,
                status=StepStatus.RUNNING,
                summary=checkpoint,
                verification=f"yielded:{reason}",
                started_at=started,
                completed_at=_utc_now(),
            )
            self.durable.checkpoint_step(result)
        self.queue.yield_job(
            redis_id,
            job,
            checkpoint,
            reason=reason,
            step_rounds=step_rounds,
            task_rounds=task_rounds,
        )
        logging.info(
            "Prompt yielded task=%s step=%s reason=%s step_rounds=%s task_rounds=%s",
            job.task_id,
            job.step_id,
            reason,
            step_rounds,
            task_rounds,
        )

    def _recover_or_escalate(self, redis_id: str, job: PromptJob, error: str) -> bool:
        if self._task_suppressed(job.task_id):
            self.queue.cleanup_task(job.task_id)
            self._clear_suppression_request(job.task_id)
            logging.info("Skipped suppressed recovery task=%s step=%s (live queue copies removed)", job.task_id, job.step_id)
            return True
        parked = self.queue.park_chain(job.chain_id, redis_id)
        if not parked:
            logging.error("Failed prompt was not parked task=%s step=%s", job.task_id, job.step_id)
            return True
        next_attempt = job.attempt + 1
        if next_attempt < self.queue.max_attempts:
            logging.info(
                "Prompt parked for retry task=%s step=%s attempt=%s/%s",
                job.task_id,
                job.step_id,
                next_attempt,
                self.queue.max_attempts,
            )
            return False
        if job.recovery_attempted:
            count = self.queue.escalate_chain(job.chain_id, job.message_id)
            logging.error("Recovery failed; escalated prompts count=%s", count)
            return True
        try:
            revised = self._troubleshoot(job, error)
            count = self.queue.revise_retry(
                job.chain_id,
                job.message_id,
                revised,
                recovery_context=error,
            )
        except Exception:
            logging.exception("Norm troubleshooting failed")
            count = 0
        if not count:
            escalated = self.queue.escalate_chain(job.chain_id, job.message_id)
            logging.error("Could not revise retry; escalated prompts count=%s", escalated)
            return True
        else:
            logging.info("Norm revised parked prompt for one recovery attempt")
            return False

    def _troubleshoot(self, job: PromptJob, error: str) -> str:
        prompt = (
            "Hidden recovery task. Revise the failed prompt so it can succeed on one final attempt. "
            "Return only the complete revised prompt, with no commentary.\n\n"
            f"Original prompt:\n{job.prompt}\n\n"
            f"Existing context:\n{job.context}\n\n"
            f"Failure:\n{error}"
        )
        revised = self.client.generate(prompt, think=True, temperature=0.2)
        if not revised or not revised.strip():
            raise ValueError("Norm returned a blank recovery prompt")
        return revised.strip()

    def _handle_blank(self, redis_id: str) -> None:
        self.queue.ack(redis_id)
        self._blank_claims += 1
        logging.warning("Ignored blank prompt claim count=%s", self._blank_claims)
        if self._blank_claims < 3:
            return
        snapshot = self.queue.inspect_queue(count=50)
        maintenance_prompt = (
            "Hidden queue-maintenance task. Three blank Redis prompts were claimed consecutively. "
            "Inspect the supplied queue snapshot and authorize removal of blank prompt artifacts. "
            "You may only request the bounded action SCRUB_BLANKS. Reply with SCRUB_BLANKS.\n\n"
            f"Queue snapshot: {snapshot!r}"
        )
        try:
            decision = self.client.generate(maintenance_prompt, think=True, temperature=0.0)
            logging.info("Norm maintenance decision=%s", decision.strip()[:200])
        except Exception:
            logging.exception("Norm blank-maintenance turn failed; enforcing blank invariant")
        deleted = self.queue.delete_blank_artifacts()
        remaining = self.queue.inspect_queue(count=100)
        blanks = sum(
            1
            for jobs in remaining.values()
            for queued in jobs
            if not str(queued.get("prompt", "")).strip()
        )
        if blanks:
            raise RuntimeError(f"blank queue maintenance verification failed: {blanks} remain")
        logging.info("Blank maintenance complete deleted=%s verified=clean", deleted)
        self._blank_claims = 0

    def _maintenance_redis_client(self):
        if self._weekly_cleanup_redis is not None:
            return self._weekly_cleanup_redis
        cfg = self._runtime_config().get("redis", {})
        self._weekly_cleanup_redis = redis.Redis(
            host=str(cfg.get("host", "127.0.0.1")),
            port=int(cfg.get("port", 6379)),
            db=int(cfg.get("db", 0)),
            decode_responses=True,
            socket_connect_timeout=2.0,
            socket_timeout=5.0,
        )
        return self._weekly_cleanup_redis

    @staticmethod
    def _purge_workspace_directory(output_root, workspace_root) -> dict:
        from pathlib import Path

        output = Path(output_root).resolve()
        workspace = Path(workspace_root).resolve()
        try:
            output.relative_to(workspace)
        except ValueError as exc:
            raise RuntimeError(f"cleanup path is outside workspace: {output}") from exc
        if output == workspace:
            raise RuntimeError("refusing to purge workspace root")
        output.mkdir(parents=True, exist_ok=True)
        files = [p for p in output.rglob("*") if p.is_file() or p.is_symlink()]
        removed_bytes = sum((p.lstat().st_size if p.is_symlink() else p.stat().st_size) for p in files)
        for path in files:
            path.unlink()
        dirs = sorted(
            (p for p in output.rglob("*") if p.is_dir() and not p.is_symlink()),
            key=lambda p: len(p.parts), reverse=True,
        )
        for directory in dirs:
            directory.rmdir()
        return {"files_removed": len(files), "bytes_removed": int(removed_bytes), "path": str(output)}

    def _purge_image_analysis_outputs(self) -> dict:
        from pathlib import Path
        from norm_runtime.settings import load_path_settings

        tools = self._runtime_config().get("tools", {})
        output_raw = str(tools.get("image_output_root") or "").strip()
        if not output_raw:
            return {"files_removed": 0, "bytes_removed": 0, "path": "", "skipped": "image_output_root not configured"}
        workspace = Path(load_path_settings(self._runtime_root())["workspace_root"])
        return self._purge_workspace_directory(Path(output_raw), workspace)

    def _purge_temp_outputs(self) -> dict:
        from norm_runtime.settings import load_path_settings, load_settings

        paths = load_path_settings(self._runtime_root())
        settings = load_settings(self._runtime_root())
        if not settings.getboolean("temp", "cleanup_enabled", fallback=True):
            return {"path": str(paths["temp_root"]), "skipped": "disabled"}
        retention = Path(paths["workspace_root"]) / str(self._runtime_config().get("task_storage", {}).get("retention_manifest_dir", ".norm-task-retention"))
        return cleanup_temp_root(
            paths["temp_root"],
            durable=self.durable,
            max_age_hours=settings.getint("temp", "max_age_hours", fallback=72),
            recovery_max_age_hours=settings.getint("temp", "recovery_max_age_hours", fallback=168),
            retention_root=retention,
            deletion_queue=(self._worker_tools().deletion_queue if self._worker_tools() is not None else None),
        )

    @staticmethod
    def _runtime_state_time(value):
        if not value:
            return None
        try:
            text = str(value).strip().replace("Z", "+00:00")
            parsed = datetime.fromisoformat(text)
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        except Exception:
            return None

    def _maybe_consolidate_background_memory(self) -> None:
        if not self.durable or not hasattr(self.durable, "get_runtime_state"):
            return
        memory_cfg = self._runtime_config().get("memory", {})
        maint_cfg = self._runtime_config().get("maintenance", {})
        if not bool(maint_cfg.get("weekly_cleanup_enabled", True)):
            return
        now_clock = monotonic()
        check_seconds = max(60, int(memory_cfg.get("consolidation_check_seconds", 3600)))
        if now_clock - self._last_memory_maintenance_check < check_seconds:
            return
        self._last_memory_maintenance_check = now_clock
        stats = self.queue.stats()
        if stats.get("stream_length", 0) or stats.get("pending_count", 0):
            return
        client = self._maintenance_redis_client()
        active_key = str(maint_cfg.get("weekly_cleanup_active_key", "norm:maintenance:weekly_cleanup:active"))
        active_raw = client.get(active_key)
        marker = None
        if active_raw:
            try:
                marker = json.loads(active_raw)
            except Exception:
                logging.error("Weekly maintenance marker is malformed; preserving evidence: %r", active_raw)
                return
        now = _utc_now()
        regular_days = max(1, int(memory_cfg.get("consolidation_days", 7)))
        deep_days = max(1, int(memory_cfg.get("deep_history_interval_days", 21)))
        last_cleanup = self._runtime_state_time(self.durable.get_runtime_state("last_successful_cleanup_at"))
        last_deep = self._runtime_state_time(self.durable.get_runtime_state("last_successful_deep_cleanup_at"))
        regular_due = last_cleanup is None or last_cleanup <= now - timedelta(days=regular_days)
        deep_due = bool(memory_cfg.get("deep_history_enabled", True)) and (
            last_deep is None or last_deep <= now - timedelta(days=deep_days)
        )
        if marker is None and not regular_due:
            return
        resumed = marker is not None
        if marker is None:
            marker = {
                "status": "cleanup_started", "run_id": str(uuid.uuid4()),
                "mode": "deep" if deep_due else "regular",
                "phase": "starting", "resume_count": 0,
                "started_at": now.isoformat(), "started_at_epoch": now.timestamp(),
                "consumer": self.consumer, "host": socket.gethostname(),
            }
            if not client.set(active_key, json.dumps(marker, sort_keys=True), nx=True):
                return
        else:
            marker["status"] = "cleanup_resumed"
            marker["resume_count"] = int(marker.get("resume_count", 0)) + 1
            marker["resumed_at"] = now.isoformat()
            marker["consumer"] = self.consumer
            client.set(active_key, json.dumps(marker, sort_keys=True))
        run_id = str(marker.get("run_id") or "unknown")
        mode = str(marker.get("mode") or ("deep" if deep_due else "regular"))
        try:
            marker["phase"] = "temp_cleanup"
            client.set(active_key, json.dumps(marker, sort_keys=True))
            temp_cleanup = self._purge_temp_outputs()
            marker["phase"] = "trash_purge"
            client.set(active_key, json.dumps(marker, sort_keys=True))
            trash_purge = self._worker_tools().deletion_queue.purge_all() if self._worker_tools() is not None else {"purged": 0, "held": 0}
            marker["phase"] = "image_analysis_purge"
            client.set(active_key, json.dumps(marker, sort_keys=True))
            purge = {"files_removed": 0, "bytes_removed": 0, "skipped": "disabled"}
            if bool(maint_cfg.get("weekly_cleanup_purge_image_analysis", True)):
                purge = self._purge_image_analysis_outputs()
            maintainer = DeepHistoryMaintainer(
                self.durable, self.client, runtime_root=self._runtime_root(), config=memory_cfg,
                queue=self.queue, drain_event=self._drain,
            )
            marker["phase"] = "recovery_state_cleanup"
            client.set(active_key, json.dumps(marker, sort_keys=True))
            recovery_cleanup = maintainer.cleanup_recovery_state()
            marker["phase"] = "deep_history" if mode == "deep" else "background_memory"
            client.set(active_key, json.dumps(marker, sort_keys=True))
            if mode == "deep":
                result = maintainer.run()
                if result is None:
                    result = {"status": "success", "work": "no_eligible_history"}
                elif str(result.get("status", "")) not in {"success", "already_completed_before_resume"}:
                    raise RuntimeError(f"deep maintenance did not complete successfully: {result}")
            else:
                summary = maintainer.rebuild_background_snapshot(incremental=True)
                result = {"status": "success", "background_chars": len(summary or "")}
            completed = _utc_now()
            self.durable.set_runtime_state("last_successful_cleanup_at", completed.isoformat())
            if mode == "deep":
                self.durable.set_runtime_state("last_successful_deep_cleanup_at", completed.isoformat())
            self.durable.record_maintenance_note(
                "weekly_cleanup", f"{mode.capitalize()} cleanup completed successfully.",
                details={"status": "success", "mode": mode, "run_id": run_id,
                         "resumed": resumed, "temp_cleanup": temp_cleanup, "trash_purge": trash_purge, "image_analysis_purge": purge,
                         "recovery_cleanup": recovery_cleanup, "maintenance_result": result},
            )
            client.delete(active_key)
            logging.info("Weekly maintenance completed run_id=%s mode=%s temp=%s trash=%s purge=%s result=%s", run_id, mode, temp_cleanup, trash_purge, purge, result)
        except Exception as exc:
            failed = {**marker, "status": "failed", "failed_at": _utc_now().isoformat(), "error": str(exc)[:2000]}
            try:
                client.set(active_key, json.dumps(failed, sort_keys=True))
            except Exception:
                logging.exception("Could not update weekly maintenance failure marker")
            logging.exception("Weekly maintenance failed run_id=%s mode=%s; marker retained for resume", run_id, mode)

    def _heartbeat_loop(self, stop: Event, job: PromptJob, started_clock: float) -> None:
        while not stop.wait(self.heartbeat_seconds):
            elapsed = int(monotonic() - started_clock)
            try:
                self.live.heartbeat(job.task_id, job.step_id, elapsed)
            except Exception:
                logging.exception(
                    "Heartbeat failed task=%s step=%s",
                    job.task_id,
                    job.step_id,
                )
