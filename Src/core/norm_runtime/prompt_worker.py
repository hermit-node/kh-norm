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
from .chunked_evidence import ChunkedEvidenceManager
from .maintenance_model_client import MaintenanceModelClient
from .model_runtime_policy import agent_persistent_instructions
from .temp_cleanup import cleanup_task_temp, cleanup_temp_root
from .models import StepResult, StepStatus, TaskPlan, TaskStep
from .ollama_client import ModelContextExceeded, ModelGenerationCancelled, ModelOutputTruncated, OllamaClient
from .prompt_queue import PromptJob, RedisPromptQueue, normalize_request_type
from .protocol import PROTOCOL_VERSION, final_verification_schema, normalize_command, step_verification_schema, validate_final_candidate, validate_final_verification, validate_step_verification
from .resource_status import full_context_status, impaired_context_status, merge_resource_status
from .settings import load_path_settings
from .voice_profile_context import load_active_voice_context
from .invocation_source import invocation_header, work_invoke_source
from .contract_boundary import validate_recovery_analysis_child, validate_recovery_handoff


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)



class VerifierProtocolError(RuntimeError):
    pass


class TaskRoundBudgetExceeded(RuntimeError):
    def __init__(self, message: str, *, checkpoint: str = "", step_rounds: int = 0, task_rounds: int = 0) -> None:
        super().__init__(message)
        self.checkpoint = str(checkpoint or "")
        self.step_rounds = int(step_rounds or 0)
        self.task_rounds = int(task_rounds or 0)


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
        n1_gatekeeper=None,
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
        self._last_validation_pool_migration_check = 0.0
        self._last_weekly_cleanup_check = 0.0
        self._weekly_cleanup_redis = None
        self._stop_after_step = Event()
        self._stop_all_now = Event()
        self._state_lock = Lock()
        self._active_task_id = ""
        self._suppress_requested: set[str] = set()
        self._maintenance_suppress_requested = Event()
        self._manual_memory_condense_mode = ""
        self.deferred_append_planner = deferred_append_planner
        self.context_injections = context_injections
        self.n1_gatekeeper = n1_gatekeeper

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
        """Refresh active injection state at EVERY model boundary.

        PostgreSQL, not a previously serialized queue job, decides which entries
        are still active. Retired entries cannot reappear after a job requeue.
        """
        if self.context_injections is None:
            return []
        metadata = dict(job.metadata or {})
        try:
            cursor = int(metadata.get("context_injection_cursor") or 0)
        except (TypeError, ValueError):
            cursor = 0
        entries = list(self.context_injections.read(job.task_id, after=0) or [])
        # Keep the complete currently-active set, rather than accumulating
        # obsolete injection content in step metadata forever.
        entries = entries[-32:]
        new = [item for item in entries if int(item.get("id") or 0) > cursor]
        metadata["context_injection_cursor"] = max([cursor] + [int(e.get("id") or 0) for e in entries])
        metadata["applied_context_injections"] = entries
        job.metadata = metadata
        if new:
            logging.info("Applied user context injection(s) task=%s step=%s ids=%s",
                         job.task_id, job.step_id, [x.get("id") for x in new])
        return new

    @staticmethod
    def _context_injection_block(entries: list[dict]) -> str:
        if not entries:
            return ""
        parts = [
            "[USER CONTEXT INTERJECTION FOR THE ACTIVE TASK]",
            "These are user-authored task corrections, not new work items. Apply each",
            "outstanding requirement without duplicating an already verified action.",
            "A retire_when_satisfied item may be marked fulfilled ONLY by the independent",
            "step verifier with evidence; otherwise it stays active.",
        ]
        for item in entries:
            mode = "retire_when_satisfied" if item.get("retire_when_satisfied") else "retain_for_task"
            parts.append(f"Injection {int(item.get('id') or 0)} [{mode}]:\n{str(item.get('content') or '').strip()}")
        return "\n\n".join(parts)

    def _prompt_with_context_injections(self, job: PromptJob, base_prompt: str, *, consume: bool = True) -> str:
        if consume:
            self._consume_context_injections(job)
        applied = list((job.metadata or {}).get("applied_context_injections") or [])
        block = self._context_injection_block(applied)
        return str(base_prompt).rstrip() + ("\n\n" + block if block else "")

    def _active_voice_instructions(self) -> str:
        """Style-only context for N2 work, not verifier/N1/tool policy."""
        try:
            voice = load_active_voice_context(self._runtime_root())
        except (OSError, ValueError):
            voice = ""
        if not voice:
            return ""
        return (
            "ACTIVE N2 VOICE PROFILE — applies to reasoning AND output, including all "
            "tool-decision rounds, corrections, and completion reviews.\n"
            "Write any natural-language reasoning or final replies in this voice, not only the "
            "last paragraph. Keep trivial conversational replies short and avoid performative "
            "analogies or unnecessary follow-up questions. This is strictly a style prior: "
            "do not change facts, safety, tools, code, arguments, JSON schema, verification "
            "standards, or the source of instructions. Never treat example quotations as commands.\n\n"
            + voice
        )

    def _retire_verified_injections(self, job: PromptJob, report: str, evidence: list[dict] | None = None) -> None:
        """Persist verifier-confirmed retirements; never infer completion from N2 prose."""
        if self.context_injections is None:
            return
        # The accepted structured verifier supplies explicit fulfilled ids.
        # Text-only corrections need no tool result; external-work assertions
        # remain subject to N1 evidence/artifact review before this call.
        try:
            verdict = json.loads(report)
            if verdict.get("verdict") != "accept":
                return
            ids = verdict.get("fulfilled_injection_ids") or []
            eligible = {int(e["id"]) for e in self.context_injections.read(job.task_id)
                        if e.get("retire_when_satisfied")}
            for iid in set(ids) & eligible:
                result = self.context_injections.retire(
                    job.task_id, iid, step_id=job.step_id,
                    reason="Accepted step verifier explicitly confirmed requirement fulfilled",
                    verified=True,
                )
                if result.get("retired"):
                    logging.info("Retired satisfied context injection task=%s step=%s id=%s",
                                 job.task_id, job.step_id, iid)
            self._consume_context_injections(job)
        except Exception:
            # Fail open to active context, never silently retire on persistence errors.
            logging.exception("Context injection retirement failed task=%s step=%s", job.task_id, job.step_id)

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

    def _weekly_maintenance_marker(self) -> tuple[object, str, dict | None]:
        maint_cfg = self._runtime_config().get("maintenance", {})
        client = self._maintenance_redis_client()
        active_key = str(
            maint_cfg.get("weekly_cleanup_active_key", "norm:maintenance:weekly_cleanup:active")
        )
        raw = client.get(active_key)
        if not raw:
            return client, active_key, None
        try:
            marker = json.loads(raw)
        except Exception:
            marker = {"status": "malformed", "raw": str(raw)[:2000]}
        return client, active_key, marker

    def maintenance_status(self) -> dict:
        parked = None
        if self.durable and hasattr(self.durable, "get_runtime_state"):
            parked = self.durable.get_runtime_state("weekly_maintenance_parked")
        try:
            _client, _key, marker = self._weekly_maintenance_marker()
        except Exception as exc:
            marker = None
            marker_error = f"{type(exc).__name__}: {exc}"
        else:
            marker_error = ""
        if isinstance(parked, dict):
            return {
                "active": False,
                "parked": True,
                "status": str(parked.get("status") or "suppressed"),
                "mode": str(parked.get("mode") or (marker or {}).get("mode") or ""),
                "phase": str(parked.get("phase") or (marker or {}).get("phase") or ""),
                "run_id": str(parked.get("run_id") or (marker or {}).get("run_id") or ""),
                "reason": str(parked.get("reason") or ""),
                "marker": marker,
            }
        if marker:
            status = str(marker.get("status") or "")
            return {
                "active": status not in {"suppressed", "requires_attention"},
                "parked": status in {"suppressed", "requires_attention"},
                "status": status,
                "mode": str(marker.get("mode") or ""),
                "phase": str(marker.get("phase") or ""),
                "run_id": str(marker.get("run_id") or ""),
                "reason": str(marker.get("error") or marker.get("reason") or ""),
                "marker": marker,
            }
        return {
            "active": False,
            "parked": False,
            "status": "inactive",
            "mode": "",
            "phase": "",
            "run_id": "",
            "reason": marker_error,
        }

    def request_suppress_maintenance(self, reason: str = "") -> dict:
        if not self.durable or not hasattr(self.durable, "set_runtime_state"):
            return {
                "status": "unavailable",
                "suppressed": False,
                "reason": "durable runtime state unavailable",
            }
        try:
            client, active_key, marker = self._weekly_maintenance_marker()
        except Exception as exc:
            return {
                "status": "error",
                "suppressed": False,
                "reason": f"could not read maintenance marker: {type(exc).__name__}: {exc}",
            }
        if not marker:
            return {
                "status": "ok",
                "suppressed": False,
                "reason": "no active scheduled maintenance",
            }
        status = str(marker.get("status") or "")
        if status in {"suppressed", "requires_attention"}:
            parked = self.durable.get_runtime_state("weekly_maintenance_parked")
            return {
                "status": "ok",
                "suppressed": False,
                "maintenance": True,
                "reason": "scheduled maintenance is already parked",
                "parked": parked or marker,
            }

        now = _utc_now().isoformat()
        parked = {
            "status": "suppressed",
            "run_id": str(marker.get("run_id") or ""),
            "mode": str(marker.get("mode") or "regular"),
            "phase": str(marker.get("phase") or ""),
            "suppressed_at": now,
            "reason": reason or "Operator requested /suppress-task while scheduled maintenance was active.",
        }
        self.durable.set_runtime_state("weekly_maintenance_parked", parked)
        marker.update({
            "status": "suppressed",
            "suppressed_at": now,
            "reason": parked["reason"],
            "auto_resume": False,
        })
        client.set(active_key, json.dumps(marker, sort_keys=True))
        self._maintenance_suppress_requested.set()
        cancelled = OllamaClient.cancel_active()
        return {
            "status": "ok",
            "suppressed": True,
            "maintenance": True,
            "task_id": f"maintenance:{parked['run_id'] or 'weekly'}",
            "title": f"Weekly maintenance ({parked['mode']})",
            "mode": parked["mode"],
            "phase": parked["phase"],
            "cancelled_model_calls": cancelled,
        }

    def request_resume_maintenance(self) -> dict:
        if not self.durable or not hasattr(self.durable, "get_runtime_state"):
            return {
                "status": "unavailable",
                "resumed": False,
                "reason": "durable runtime state unavailable",
            }
        parked = self.durable.get_runtime_state("weekly_maintenance_parked")
        try:
            client, active_key, marker = self._weekly_maintenance_marker()
        except Exception as exc:
            return {
                "status": "error",
                "resumed": False,
                "reason": f"could not read maintenance marker: {type(exc).__name__}: {exc}",
            }
        if not isinstance(parked, dict) and not (
            isinstance(marker, dict)
            and str(marker.get("status") or "") in {"suppressed", "requires_attention"}
        ):
            return {
                "status": "ok",
                "resumed": False,
                "reason": "scheduled maintenance is not parked",
            }
        self.durable.delete_runtime_state("weekly_maintenance_parked")
        now = _utc_now().isoformat()
        marker = dict(marker or parked or {})
        marker.update({
            "status": "cleanup_resumed",
            "resumed_at": now,
            "resumed_by_operator": True,
            "auto_resume": True,
        })
        marker.pop("error", None)
        marker.pop("failure_kind", None)
        client.set(active_key, json.dumps(marker, sort_keys=True))
        self._maintenance_suppress_requested.clear()
        self._last_memory_maintenance_check = 0.0
        return {
            "status": "ok",
            "resumed": True,
            "maintenance": True,
            "run_id": str(marker.get("run_id") or ""),
            "mode": str(marker.get("mode") or ""),
            "phase": str(marker.get("phase") or ""),
            "runs_when": "worker_idle",
        }

    def request_suppress_task(self, task_id: str | None = None, reason: str = "") -> dict:
        explicit = bool(str(task_id or "").strip())
        seed = str(task_id or "").strip() or self.active_task_id() or str(self.queue.oldest_task_id() or "")
        if not seed:
            maintenance = self.maintenance_status()
            if maintenance.get("active"):
                return self.request_suppress_maintenance(reason)
            return {"status": "ok", "suppressed": False, "reason": "no active or queued task or maintenance job"}
        if self._suppression_requested(seed):
            return {
                "status": "ok", "suppressed": False, "task_id": seed,
                "reason": "suppression already in progress; active worker is still unwinding",
            }

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
            # Keep this transition marker until the active worker frame actually
            # acknowledges cancellation.  PostgreSQL remains the durable source of
            # truth, but this marker closes the race where /flush-suppressed can
            # delete durable rows while an Ollama cancellation is still unwinding.
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
            # Do not clear an active task's transition marker here. The model call can
            # still be unwinding after OllamaClient.cancel_active() returns. The worker
            # clears it in _handle_suppressed() once cancellation is acknowledged.
            active = self.active_task_id()
            with self._state_lock:
                for member_id in member_ids:
                    if member_id != active:
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
            # Keep the transition hint for the active task until its worker frame
            # acknowledges ModelGenerationCancelled. Clearing it here recreates a race
            # where a just-flushed task can be treated as an ordinary cancellation.
            if task_id != self.active_task_id():
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

    def recent_failed_tasks(self, limit: int = 25) -> dict:
        if not self.durable or not hasattr(self.durable, "recent_failed_tasks"):
            return {"status": "unavailable", "count": 0, "tasks": []}
        tasks = list(self.durable.recent_failed_tasks(limit=max(1, int(limit))) or [])
        return {"status": "ok", "count": len(tasks), "tasks": tasks}

    @staticmethod
    def _resolve_failed_selector(tasks: list[dict], selector: str) -> dict:
        selector = str(selector or "").strip()
        if not tasks:
            raise ValueError("there are no failed tasks")
        if not selector:
            raise ValueError("failed task selector is required")
        if selector.isdigit():
            index = int(selector)
            if index < 0 or index >= len(tasks):
                raise ValueError(f"failed task index {index} is out of range 0..{len(tasks)-1}")
            return tasks[index]
        exact = [item for item in tasks if str(item.get("task_id") or "") == selector]
        if len(exact) == 1:
            return exact[0]
        prefix = [item for item in tasks if str(item.get("task_id") or "").startswith(selector)]
        if len(prefix) == 1:
            return prefix[0]
        if len(prefix) > 1:
            raise ValueError(f"task-id prefix {selector!r} matches multiple failed tasks; use a longer prefix")
        raise ValueError(f"no failed task matched {selector!r}")

    def resume_failed_task(self, selector: str) -> dict:
        if not self.durable or not hasattr(self.durable, "recent_failed_tasks") or not hasattr(self.durable, "resume_failed_task"):
            return {"status": "unavailable", "resumed": False, "reason": "durable failed-task state unavailable"}
        tasks = list(self.durable.recent_failed_tasks(limit=500) or [])
        selected = self._resolve_failed_selector(tasks, selector)
        task_id = str(selected.get("task_id") or "")
        if not bool(selected.get("resumable")):
            return {"status": "conflict", "resumed": False, "task_id": task_id, "reason": "failed task has no resumable checkpoint"}

        payload = self.durable.resume_failed_task(task_id)
        queue_payload = payload.get("queue") if isinstance(payload, dict) else None
        if not isinstance(queue_payload, dict):
            self.durable.re_fail_task_after_resume_error(task_id)
            return {"status": "error", "resumed": False, "task_id": task_id, "reason": "failure record has no queue snapshot"}

        try:
            self.queue.reset_task_rounds(task_id)
            if hasattr(self.live, "cleanup"):
                self.live.cleanup(task_id)
            restored = self.queue.restore_task_jobs(queue_payload)
            if not restored:
                raise RuntimeError("no queued jobs were restored from the failure checkpoint")
            failed_step = str(payload.get("failed_step_id") or selected.get("step_id") or "")
            failed_name = str(payload.get("failed_step_name") or "manual-resume")
            checkpoint = str(payload.get("checkpoint") or "Manual resume from durable failure checkpoint.")
            if failed_step:
                self.durable.checkpoint_step(StepResult(
                    task_id=task_id, step_id=failed_step, name=failed_name,
                    status=StepStatus.RUNNING, summary=checkpoint[:12000],
                    verification="manual-resume", started_at=_utc_now(), completed_at=_utc_now(),
                ))
            return {
                "status": "ok", "resumed": True, "task_id": task_id,
                "title": str(selected.get("title") or ""),
                "restored_jobs": len(restored),
                "resume_count": int(selected.get("resume_count") or 0) + 1,
                "round_budget": "reset",
            }
        except Exception:
            self.durable.re_fail_task_after_resume_error(task_id)
            self.queue.cleanup_task(task_id)
            raise

    def flush_failed(self) -> dict:
        if not self.durable or not hasattr(self.durable, "recent_failed_tasks"):
            return {"status": "unavailable", "deleted": 0, "reason": "durable failed-task state unavailable"}
        failures = list(self.durable.recent_failed_tasks(limit=10000) or [])
        task_ids = [str(item.get("task_id") or "") for item in failures if str(item.get("task_id") or "").strip()]
        if not task_ids:
            return {"status": "ok", "deleted": 0, "lesson": "", "reason": "no failed tasks"}

        # Distill one general future-use lesson before deleting the detailed execution corpses.
        samples = []
        for item in failures[:80]:
            samples.append(redact(
                f"- stage={item.get('failure_stage') or 'unknown'}; title={item.get('title') or '[untitled]'}; "
                f"reason={str(item.get('failure_reason') or '')[:900]}; summary={str(item.get('summary') or '')[:900]}"
            ))
        lesson_prompt = (
            "Create one concise internal lesson for avoiding or recovering from these failed Norm tasks in the future. "
            "Generalize the recurring failure mode and the workflow change that would prevent wasted churn. Preserve useful safeguards; "
            "do not preserve task IDs, filenames that are irrelevant to the lesson, credentials, or verbose incident history. "
            "Target 300-900 characters. Return only the lesson.\n\nFAILED TASK SAMPLE:\n" + "\n".join(samples)
        )
        lesson = ""
        try:
            lesson = str(self.client.generate(lesson_prompt, think=False, temperature=0.0, num_predict=420)).strip()
        except Exception:
            logging.exception("Failed-task lesson generation failed; using deterministic fallback")
        if not lesson:
            lesson = (
                "When a task safety-stops, resume from the newest durable checkpoint and verified evidence instead of restarting broad discovery. "
                "Treat repeated checks or repeated failures without state advancement as churn; decompose the blocked stage, verify the smallest corrective action, "
                "and only grant more execution runway when durable progress is visible."
            )
        lesson = lesson[:1600]
        try:
            self.durable.record_maintenance_note(
                "failed_task_lesson", lesson, details={
                    "flushed_count": len(task_ids),
                    "failure_stages": sorted({str(item.get("failure_stage") or "unknown") for item in failures}),
                },
            )
        except Exception:
            logging.exception("Could not persist failed-task lesson; refusing destructive flush")
            raise

        queue_cleanup = {"work": 0, "retry": 0, "escalation": 0, "dead": 0, "round_key": 0, "budget_key": 0}
        cleanup_errors: list[str] = []
        for task_id in task_ids:
            try:
                counts = self.queue.cleanup_task(task_id)
                for key in queue_cleanup:
                    queue_cleanup[key] += int(counts.get(key) or 0)
            except Exception as exc:
                cleanup_errors.append(f"queue:{task_id}:{type(exc).__name__}:{exc}")
            try:
                if hasattr(self.live, "cleanup"):
                    self.live.cleanup(task_id)
            except Exception as exc:
                cleanup_errors.append(f"live:{task_id}:{type(exc).__name__}:{exc}")
            try:
                self._soft_delete_task_assets(task_id, suppressed=True)
            except Exception as exc:
                cleanup_errors.append(f"assets:{task_id}:{type(exc).__name__}:{exc}")
            try:
                if hasattr(self.durable, "purge_raw_thinking"):
                    self.durable.purge_raw_thinking(task_id)
            except Exception as exc:
                cleanup_errors.append(f"thinking:{task_id}:{type(exc).__name__}:{exc}")
            try:
                paths = load_path_settings(self._runtime_root())
                retention = Path(paths["workspace_root"]) / str(self._runtime_config().get("task_storage", {}).get("retention_manifest_dir", ".norm-task-retention"))
                cleanup_task_temp(paths["temp_root"], task_id, self.durable, retention_root=retention, deletion_queue=(self._worker_tools().deletion_queue if self._worker_tools() is not None else None))
            except Exception as exc:
                cleanup_errors.append(f"temp:{task_id}:{type(exc).__name__}:{exc}")

        history_deleted = self.durable.purge_failed_task_history(task_ids) if hasattr(self.durable, "purge_failed_task_history") else 0
        notes_deleted = self.durable.purge_task_maintenance_notes(task_ids, preserve_phase="failed_task_lesson") if hasattr(self.durable, "purge_task_maintenance_notes") else 0
        deleted = self.durable.delete_failed_task_rows(task_ids) if hasattr(self.durable, "delete_failed_task_rows") else 0
        snapshots_invalidated = self.durable.invalidate_background_snapshots() if hasattr(self.durable, "invalidate_background_snapshots") else 0
        return {
            "status": "ok" if not cleanup_errors else "partial",
            "deleted": int(deleted),
            "lesson": lesson,
            "history_deleted": int(history_deleted),
            "maintenance_notes_deleted": int(notes_deleted),
            "background_snapshots_invalidated": int(snapshots_invalidated),
            "queue_cleanup": queue_cleanup,
            "cleanup_errors": cleanup_errors,
        }

    def wait_idle(self, timeout: float | None = None) -> bool:
        return self._idle.wait(timeout=timeout)

    def is_idle(self) -> bool:
        return self._idle.is_set()

    def request_memory_condense(self, full: bool = False, deep: bool = False) -> dict:
        """Schedule recent, deep, or full condensation without creating a user task."""
        if not self.durable:
            return {"status": "unavailable", "scheduled": False, "reason": "durable store unavailable"}
        requested = "full" if bool(full) else "deep" if bool(deep) else "regular"
        priority = {"regular": 0, "deep": 1, "full": 2}
        with self._state_lock:
            current = self._manual_memory_condense_mode
            if not current or priority.get(requested, 0) >= priority.get(current, 0):
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
        memory_cfg = self._fresh_memory_config()
        full = mode == "full"
        deep = mode == "deep"
        recent_only = mode == "regular"
        try:
            condense_client = self._maintenance_condense_client(memory_cfg)
            maintainer = DeepHistoryMaintainer(
                self.durable, condense_client, runtime_root=self._runtime_root(), config=memory_cfg,
                queue=self.queue, drain_event=self._drain, n1_gatekeeper=self.n1_gatekeeper,
            )
            recovery_cleanup = (
                maintainer.cleanup_recovery_state()
                if full or deep
                else {"skipped": "regular_recent_only"}
            )
            result = maintainer.run(force=True, full=full, recent_only=recent_only)
            if result is None:
                result = {"status": "success", "work": "no_eligible_history"}
            details = {
                "status": str(result.get("status") or "success"),
                "mode": mode,
                "condensation": result,
                "recovery_cleanup": recovery_cleanup,
            }
            result_status = str(result.get("status") or "success")
            if result_status in {"success", "already_completed_before_resume"}:
                note = (
                    "[manual_background_condensation] Manual full-history hierarchical condensation completed."
                    if full
                    else "[manual_background_condensation] Manual deep-history condensation completed."
                    if deep
                    else "[manual_background_condensation] Manual recent-memory condensation completed."
                )
            else:
                note = (
                    "[manual_background_condensation] Manual memory condensation did not prune history; "
                    "validation/recovery safeguards preserved the raw source state."
                )
            self.durable.record_maintenance_note(
                "manual_background_condensation",
                note,
                details=details,
            )
            logging.info("Manual memory condensation finished mode=%s result=%s", mode, result_status)
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
                self._maybe_migrate_validation_pool()
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
        if (self.durable and self.durable.task_status(job.task_id) in {"passed_to", "completed"}):
            # This applies to EVERY step type: stale Redis entries must never
            # revive a parent task after a recovery-successor handoff.
            self.queue.ack(redis_id)
            logging.info("Discarded replay of already concluded task=%s", job.task_id)
            return
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
            metadata_resume = dict(job.metadata or {})
            resource_status = merge_resource_status(metadata_resume.get("resource_status"))
            compact_resume = self._compact_resume_requested(job)
            if metadata_resume.get("recovery_mode") and not compact_resume:
                # Recovery begins from a compact handoff; old full history is
                # durable and available selectively, never bulk-injected again.
                if self.durable and hasattr(self.durable, "completed_step_context"):
                    prior = self.durable.completed_step_context(job.task_id, job.step_id, max_chars=9000)
            elif compact_resume:
                # A context-headroom checkpoint already asked N1 to distill the full
                # active execution state into job.context. Re-injecting Redis's entire
                # task event stream here defeats that compaction and can restart the
                # fresh model slice at ~95%+ context utilization. Keep Redis/Postgres
                # lossless for audit/recovery, but feed N2 only the compact continuation.
                logging.info(
                    "Using compact continuation context task=%s step=%s continuation_chars=%s",
                    job.task_id, job.step_id, len(str(job.context or "")),
                )
            else:
                if hasattr(self.live, "working_memory"):
                    task_memory = self.live.working_memory(job.task_id)
                # Redis working_memory() serializes {task_id, state, events} even
                # when no progress events exist. Testing that JSON for an empty
                # string made the durable *completed prior steps* disappear from
                # almost every normal resume. These are complementary sources:
                # Redis contains live events; PostgreSQL contains the verified
                # results of earlier steps. Read the latter independently.
                if self.durable and hasattr(self.durable, "completed_step_context"):
                    try:
                        prior = self.durable.completed_step_context(job.task_id, job.step_id, max_chars=36000)
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
                verification=(str(metadata.get("verification") or "") + (" | n1-step-review:unavailable-pass" if (job.metadata or {}).get("n1_step_review_status") == "unavailable_after_2_attempts" else "")),
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
            self._handle_pathological_failure(
                redis_id, job, name, started, "task-round-budget", str(exc),
                resume_checkpoint=getattr(exc, "checkpoint", ""),
            )
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
                self._write_sos(job, "terminal-exception", error,
                                "A dedicated recovery task inventories surviving work before continuation.")
                self.live.step_failed(job.task_id, job.step_id, error)
                try:
                    successor = self._handoff_failed_task_to_recovery(
                        job, stage="terminal-exception", error=error, checkpoint=job.context
                    )
                except Exception:
                    logging.exception("Recovery successor unavailable; preserving failed task task=%s", job.task_id)
                else:
                    if self.durable:
                        self.durable.finalize_task(job.task_id,
                            f"PASSED_TO:{successor} — failed at {job.step_id}; recover only missing work.", "passed_to")
                    if hasattr(self.live, "transfer_task"):
                        self.live.transfer_task(job.task_id, successor,
                            f"PASSED_TO:{successor} — dedicated recovery attempt.")
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

    @staticmethod
    def _compact_resume_requested(job: PromptJob) -> bool:
        """Compact only when the *latest* handoff supplied a distilled context.

        A previous context-headroom checkpoint cannot force every subsequent
        16-round requeue onto the compact path. 0.54.2h jobs are still supported
        through their last_yield_reason marker. Manual failure resumes retain
        their explicitly compact snapshot.
        """
        metadata = dict(job.metadata or {})
        if not str(job.context or "").strip():
            return False
        reason = str(metadata.get("last_yield_reason") or "").strip()
        return reason == "context_headroom_checkpoint" or (
            bool(metadata.get("manual_failure_resume"))
            and bool(metadata.get("compact_resume_context"))
        )

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
            "conclusion": "Write the final user-facing response as N2 using current tools, context, and active voice. For multi-item results/checklists, prefer the Nabler render_report native tool when available, formatting previously verified data without suppressing detailed checks. Self-verify then submit to N1 for final acceptance. Synthesize prior results; do not redo completed work.",
            "final_verification": "Validate the final-candidate envelope and verify completion without redoing completed acquisition or mutation work.",
        }
        metadata = job.metadata or {}
        prompt_origin = str(metadata.get("prompt_origin") or ("user_prompt" if request_type == "prompt" else "norm_internal"))
        is_user_prompt = request_type == "prompt" and prompt_origin in {"user_prompt", "prompt_interface", "gui_prompt", "ssh_prompt"}
        invoke_source = work_invoke_source(request_type, prompt_origin, metadata)
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
            "invoke_source": invoke_source,
            "invoke_target": "n2",
            "instruction_source": "user_prompt_interface" if is_user_prompt else "norm_internal",
            "processing_contract": directives[request_type],
            "execution_phase": "execute_scoped_step" if request_type in {"step", "task_step", "conclusion"} or prompt_origin.startswith("runtime_child") or prompt_origin.startswith("runtime_oversize_recovery") else "execute_current_request",
            "persistent_instructions": agent_persistent_instructions(self._runtime_config(), "n2"),
            "recovery_contract": (
                "This task is a recovery attempt. The original user request is a REFERENCE for intent "
                "only. Do not execute it anew or replan from scratch; inspect verified prior progress, "
                "verify what survived, and execute only demonstrably missing work. "
                "Use the compact handoff; fetch additional archived evidence selectively."
                if metadata.get("recovery_mode") else ""
            ),
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
            "invoke_source": "n2", "invoke_target": "n1",
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
            "invoke_source": "n2", "invoke_target": "n1",
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
        if str((job.metadata or {}).get("expected_result_kind") or "") == "recovery_analysis":
            analysis = self._parse_recovery_analysis(
                answer, parent_scope=self._recovery_scope_items(job))
            answer = validate_recovery_analysis_child(
                self._runtime_root(), task_id=job.task_id, step_id=job.step_id,
                instruction=job.prompt, criterion=str((job.metadata or {}).get("verification") or ""),
                original_prompt=str((job.metadata or {}).get("original_user_prompt") or ""),
                parent_task_id=str((job.metadata or {}).get("parent_task_id") or ""),
                analysis=analysis)
        elif "Full original report:" not in str(answer):
            answer = self._prepare_final_report(job, answer)
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
            "invoke_source": "n2",
            "context_id": job.task_id,
            "original_user_prompt": str((job.metadata or {}).get("original_user_prompt") or ""),
            "task_user_prompt": str((job.metadata or {}).get("task_user_prompt") or (job.metadata or {}).get("original_user_prompt") or ""),
            "expected_result_kind": str((job.metadata or {}).get("expected_result_kind") or "final_candidate"),
            "parent_task_id": str((job.metadata or {}).get("parent_task_id") or ""),
            "recovery_scope_items": list((job.metadata or {}).get("recovery_scope_items") or []),
            "ingrained_task_context": list((job.metadata or {}).get("ingrained_task_context") or []),
            "command_envelope": self._command_from_job(job),
            "verification_cycle": int((job.metadata or {}).get("verification_cycle") or 1),
            "n2_conclusion_step_id": job.step_id,
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
            f"PERSISTENT OPERATING PRINCIPLES:\n{chr(10).join('- '+str(v) for v in agent_persistent_instructions(self._runtime_config(), 'n2'))}\n\n"
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
            f"PERSISTENT OPERATING PRINCIPLES:\n{chr(10).join('- '+str(v) for v in agent_persistent_instructions(self._runtime_config(), 'n2'))}\n\n"
            "Repair only the user-facing reply in this final candidate. Preserve completed work and observed evidence; do not rerun tools or invent actions. "
            "Resolve every verifier issue using the authoritative request and prior completed task state. Return only the replacement user-facing reply, not JSON and not a review report.\n\n"
            f"AUTHORITATIVE PRIMARY TASK REQUEST:\n{(job.metadata or {}).get('task_user_prompt') or (job.metadata or {}).get('original_user_prompt','')}\n\n"
            f"CURRENT CANDIDATE:\n{json.dumps(candidate, ensure_ascii=False)}\n\n"
            f"VERIFICATION ISSUES:\n{json.dumps(verification.get('issues', []), ensure_ascii=False)}\n\n"
            f"PRIOR COMPLETED TASK STATE/EVIDENCE:\n{prior}"
        )
        try:
            corrected = self._generate_complete_text(prompt, think=False, num_predict=87400, temperature=0.1, job=job, stage="final-repair")
        except ModelOutputTruncated:
            corrected = self._prepare_final_report(job, str(candidate.get("user_reply") or ""), forced=True)
        if not corrected.strip():
            raise VerifierProtocolError("final candidate repair returned blank user_reply")
        return corrected.strip()

    def _repair_final_reviewability(self, job: PromptJob, candidate: dict) -> str:
        """Ask N2 to make the same answer easier for N1 to process after a review failure."""
        prompt = (
            "N1 could not process your final submission. Check the formatting, structure, wording, nesting, and unnecessary complexity. "
            "Repair anything that may make the result difficult to review, then resubmit the SAME semantic work. "
            "Do not add new research, do not rerun tools, do not hunt for omissions merely because N1 failed to process it, and do not invent actions. "
            "Return only the replacement user-facing reply.\n\n"
            f"AUTHORITATIVE PRIMARY TASK REQUEST (for semantic fidelity only):\n{(job.metadata or {}).get('task_user_prompt') or (job.metadata or {}).get('original_user_prompt','')}\n\n"
            f"SUBMISSION N1 COULD NOT PROCESS:\n{candidate.get('user_reply','')}"
        )
        try:
            corrected = self._generate_complete_text(prompt, think=False, num_predict=87400, temperature=0.0, job=job, stage="final-reviewability")
        except ModelOutputTruncated:
            corrected = self._prepare_final_report(job, str(candidate.get("user_reply") or ""), forced=True)
        if not corrected.strip():
            raise VerifierProtocolError("N1-processing-failure repair returned blank user_reply")
        return corrected.strip()

    def _n1_final_verification(self, job: PromptJob, candidate: dict, artifact_state: bool) -> tuple[dict | None, bool]:
        gate = getattr(self, "n1_gatekeeper", None)
        if gate is None or not hasattr(gate, "judge_final_answer"):
            return None, False
        prior = ""
        if self.durable and hasattr(self.durable, "completed_step_context"):
            prior = self.durable.completed_step_context(job.task_id, job.step_id, max_chars=36000)
        envelope_context = {
            "original_user_request": (job.metadata or {}).get("original_user_prompt"),
            "primary_task_request": (job.metadata or {}).get("task_user_prompt"),
            "expected_result_kind": (job.metadata or {}).get("expected_result_kind"),
            "current_step": job.prompt if job.request_type != "final_verification" else (job.metadata or {}).get("step_name"),
            "verification_requirement": (job.metadata or {}).get("verification"),
            "command": candidate.get("command"),
            "requirements": candidate.get("requirements"),
            "artifacts": candidate.get("artifacts"),
            "evidence_count": candidate.get("evidence_count"),
            "resource_status": candidate.get("resource_status"),
        }
        result_kind = str((job.metadata or {}).get("expected_result_kind") or "final_candidate")
        if result_kind == "recovery_analysis":
            try:
                analysis = self._parse_recovery_analysis(
                    str(candidate.get("user_reply") or ""),
                    parent_scope=self._recovery_scope_items(job))
                validate_recovery_analysis_child(
                    self._runtime_root(), task_id=job.task_id,
                    step_id=str((job.metadata or {}).get("n2_conclusion_step_id") or job.step_id),
                    instruction=str((job.metadata or {}).get("task_user_prompt") or ""),
                    criterion=str((job.metadata or {}).get("verification") or ""),
                    original_prompt=str((job.metadata or {}).get("original_user_prompt") or ""),
                    parent_task_id=str((job.metadata or {}).get("parent_task_id") or ""),
                    analysis=analysis)
            except (ValueError, RuntimeError) as exc:
                return {"decision": "retry", "reason": str(exc),
                        "retry_instruction": "Return valid scoped recovery-analysis JSON only.",
                        "processing_failed": False}, False
        judgment = gate.judge_final_answer(
            request=str((job.metadata or {}).get("task_user_prompt") or (job.metadata or {}).get("original_user_prompt") or ""),
            candidate=str(candidate.get("user_reply") or ""),
            context=(("INTERNAL_RESULT_KIND: recovery_analysis; evaluate the structured recovery assessment, not the root deliverable.\n"
                      if result_kind == "recovery_analysis" else "")
                     + prior + "\n\nFINAL ENVELOPE METADATA:\n"
                     + json.dumps(envelope_context, ensure_ascii=False, default=str)).strip(),
        )
        if bool(judgment.get("processing_failed")):
            return judgment, True
        decision = str(judgment.get("decision") or "retry").strip().lower()
        if decision == "accept":
            if not artifact_state:
                return {
                    "schema_version": PROTOCOL_VERSION, "kind": "final_verification", "verdict": "revise",
                    "schema_complete": True, "requirements_complete": True, "artifact_verified": False,
                    "issues": ["Required artifact is missing deterministic write/read verification."],
                    "repair_scope": "artifact",
                    "n1_reason": str(judgment.get("reason") or ""),
                }, False
            return {
                "schema_version": PROTOCOL_VERSION, "kind": "final_verification", "verdict": "accept",
                "schema_complete": True, "requirements_complete": True, "artifact_verified": True,
                "issues": [], "repair_scope": "none",
                "n1_reason": str(judgment.get("reason") or ""),
            }, False
        issue = str(judgment.get("retry_instruction") or judgment.get("reason") or "N1 requested a revised final answer.").strip()
        return {
            "schema_version": PROTOCOL_VERSION, "kind": "final_verification", "verdict": "revise",
            "schema_complete": True, "requirements_complete": False, "artifact_verified": bool(artifact_state),
            "issues": [issue[:600]], "repair_scope": "response",
            "n1_reason": str(judgment.get("reason") or ""),
        }, False

    def _process_final_verification(self, redis_id: str, job: PromptJob, name: str, started) -> None:
        try:
            candidate = validate_final_candidate(json.loads(job.prompt))
        except (json.JSONDecodeError, ValueError) as exc:
            raise VerifierProtocolError(f"queued final candidate failed deterministic schema validation: {exc}") from exc
        artifact_state = self._final_artifact_state(candidate)
        n1_result, n1_processing_failed = self._n1_final_verification(job, candidate, artifact_state)
        if n1_result is None:
            raise VerifierProtocolError("N1 final reviewer is unavailable; final acceptance cannot be delegated")
        elif n1_processing_failed:
            verification = None
        else:
            # Remove N1-only explanatory metadata before strict protocol validation/checkpointing.
            verification = validate_final_verification({
                k: v for k, v in n1_result.items() if k in {
                    "schema_version", "kind", "verdict", "schema_complete",
                    "requirements_complete", "artifact_verified", "issues", "repair_scope",
                }
            })

        metadata = dict(job.metadata or {})
        if n1_processing_failed:
            failures = max(0, int(metadata.get("n1_processing_failures", 0))) + 1
            max_failures = max(2, int((self._runtime_config().get("worker") or {}).get(
                "n1_final_processing_retry_limit", 8)))
            if failures >= max_failures:
                # The candidate remains in the queued/durable final review envelope.
                # Failure to process a review is NOT acceptance.
                raise VerifierProtocolError(
                    f"N1 could not review the unchanged final candidate after {failures} "
                    "processing attempts; candidate must be recovered, not approved."
                )

            # N1's model failed, not N2's verified candidate. Requeue the SAME
            # candidate for an N1-only retry; do not rewrite it via N2.
            candidate = validate_final_candidate(candidate)
            metadata["n1_processing_failures"] = failures
            metadata["last_n1_processing_failure"] = str((n1_result or {}).get("reason") or "N1 could not process final candidate")
            job.metadata = metadata
            job.prompt = json.dumps(candidate, ensure_ascii=False)
            job.created_at = __import__("time").time()
            retry_summary = json.dumps({
                "schema_version": PROTOCOL_VERSION, "kind": "n1_final_review_processing_retry",
                "processing_failure": failures, "next_review_try": failures + 1,
                "instruction": "Retry the N1 evaluation of this unchanged validated candidate; do not regenerate N2 work.",
            }, ensure_ascii=False)
            if self.durable:
                self.durable.checkpoint_step(StepResult(
                    task_id=job.task_id, step_id=job.step_id, name=name, status=StepStatus.RUNNING,
                    summary=retry_summary, verification="n1-final-review:processing-retry",
                    started_at=started, completed_at=_utc_now(),
                ))
            self.queue.ack(redis_id)
            self.queue.enqueue(job)
            logging.warning("N1 final review processing failure; same candidate checkpointed and requeued task=%s failure=%s/%s", job.task_id, failures, max_failures)
            return

        # A successful N1 review resets the processing-failure streak for this candidate cycle.
        metadata["n1_processing_failures"] = 0
        job.metadata = metadata
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
                summary=summary, verification="n1-final-review:accepted" if self.n1_gatekeeper is not None else "structured-final-verification:accepted",
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
            logging.info("N1 final verification accepted task=%s cycle=%s", job.task_id, cycle)
            return
        max_cycles = max(3, int((self._runtime_config().get("worker") or {}).get(
            "n1_final_verification_cycle_limit", 8)))
        if cycle >= max_cycles:
            raise VerifierProtocolError("N1 did not accept the final candidate after "
                + str(cycle) + " correction cycles: " + "; ".join(verification["issues"]))
        if verification["repair_scope"] not in {"response", "requirements", "artifact"}:
            raise VerifierProtocolError("final verifier requested an unsupported repair scope: " + "; ".join(verification["issues"]))
        # N1 rejects the result; send a bounded corrective conclusion to the actual
        # N2 worker, retaining the active voice, plugins, tool policies and evidence.
        # The model-free final-review job must NOT generate replacement prose.
        metadata = dict(job.metadata or {})
        metadata["verification_cycle"] = cycle + 1
        metadata["last_verification"] = verification
        source_request = str(metadata.get("task_user_prompt") or metadata.get("original_user_prompt") or "")
        repair = (
            "[N1 REJECTED FINAL CANDIDATE — N2 CORRECTION ONLY]\n"
            "This is not a new user request. Resolve ONLY the N1 issues below using "
            "the existing verified task steps and native tools. Do not repeat completed "
            "mutations or replan the original request. Self-verify and resubmit to N1.\n"
            "N1 issues: " + json.dumps(verification["issues"], ensure_ascii=False) + "\n"
            "Previous N2 candidate (for revision, not authoritative evidence):\n"
            + str(candidate.get("user_reply") or "")[:18000]
            + "\nOriginal intent is retained in the user-role reference and metadata."
        )
        step_id = str(metadata.get("n2_conclusion_step_id") or "")
        if not step_id:
            raise VerifierProtocolError("Cannot route final correction to N2: original conclusion step id missing")
        task_uuid = str(metadata.get("task_uuid") or job.task_uuid or job.chain_uuid or job.chain_id)
        new_node = str(uuid.uuid4())
        n2_meta = dict(metadata)
        n2_meta.update({
            "prompt_origin": "runtime_final_n2_repair", "is_final_candidate": True,
            "step_name": "N2 correction of N1-rejected final answer",
            "verification": "N2 self-verifies and N1 accepts the corrected final reply",
            "n2_conclusion_step_id": step_id,
            "final_verification_node_id": job.message_id,
            "node_id": new_node,
        })
        n2_job = PromptJob(
            message_id=new_node, task_id=job.task_id, step_id=step_id,
            prompt=repair, project_id=job.project_id, context="",
            attempt=0, created_at=time(), metadata=n2_meta,
            chain_id=task_uuid, previous_prompt_id="", next_prompt_id="",
            chain_index=job.chain_index, recovery_attempted=False,
            request_type="conclusion", task_uuid=task_uuid, node_id=new_node,
            chain_uuid=task_uuid,
        )
        retry_summary = json.dumps({
            "schema_version": PROTOCOL_VERSION, "kind": "final_verification_retry",
            "cycle": cycle, "next_cycle": cycle + 1, "issues": verification["issues"],
        }, ensure_ascii=False)
        if self.durable:
            self.durable.checkpoint_step(StepResult(
                task_id=job.task_id, step_id=job.step_id, name=name, status=StepStatus.RUNNING,
                summary=retry_summary, verification="n1-final-review:revise" if self.n1_gatekeeper is not None else "structured-final-verification:revise",
                started_at=started, completed_at=_utc_now(),
            ))
        self.queue.ack(redis_id)
        self.queue.enqueue(n2_job)
        logging.info("Routed N1-rejected final candidate back to full N2 execution task=%s cycle=%s", job.task_id, cycle + 1)

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
        if self.durable.task_status(task_id) == "passed_to":
            # A recovery successor REQUIRES the old task's model buffers, SOS,
            # Redis state/evidence and intermediate files. Do not soft-delete,
            # condense/purge old raw thinking, or clear the old live history.
            # Only remove stale QUEUED jobs that could resurrect the parent.
            self.queue.cleanup_task(task_id)
            logging.info("Retained recovery parent evidence/SOS/temporary assets task=%s", task_id)
            return
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
            if status in {"completed", "failed", "cancelled", "passed_to"}:
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

    def _maybe_migrate_validation_pool(self, *, force: bool = False) -> None:
        """Collapse legacy Redis layout, then migrate eligible hot records twice daily."""
        cfg = self._validation_pool_config()
        if not bool(cfg.get("enabled", True)):
            return
        # Layout cleanup is Redis-local and should happen even if PostgreSQL is down.
        if hasattr(self.live, "migrate_legacy_validation_layout"):
            try:
                legacy = self.live.migrate_legacy_validation_layout()
                if int(legacy.get("migrated") or 0) or int(legacy.get("removed_legacy_keys") or 0):
                    logging.info("Collapsed legacy verification Redis layout into norm:validation:pool: %s", legacy)
            except Exception:
                logging.exception("Could not collapse legacy verification Redis layout; preserving legacy keys")

        if not self.durable or not hasattr(self.live, "stale_validation_records") or not hasattr(self.durable, "merge_validation_pool_record"):
            return
        now_clock = monotonic()
        interval = max(300, int(cfg.get("migration_interval_seconds", 43200)))
        if not force and now_clock - self._last_validation_pool_migration_check < interval:
            return
        self._last_validation_pool_migration_check = now_clock
        min_age = max(3600, int(cfg.get("redis_min_age_seconds", 86400)))
        migrated = 0
        try:
            records = self.live.stale_validation_records(older_than_seconds=min_age, limit=512)
            for record in records:
                record_id = str(record.get("record_id") or "")
                if not record_id:
                    continue
                # PostgreSQL combines same-value hot waves into one long-term count.
                # Delete from Redis only after the durable merge commits.
                self.durable.merge_validation_pool_record(record)
                removed = self.live.acknowledge_validation_records([record_id])
                migrated += int(removed)
            if migrated:
                logging.info(
                    "Verification-pool migration Redis->PostgreSQL records=%s min_age_seconds=%s",
                    migrated, min_age,
                )
        except Exception:
            logging.exception("Verification-pool migration deferred; eligible Redis records retained")

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

    def _handoff_failed_task_to_recovery(
        self, job: PromptJob, *, stage: str, error: str, checkpoint: str = ""
    ) -> str:
        """Promote a terminal failure to a *designated* recovery task.

        Do not restart the operator's original request. N2 must first inventory
        existing work and let N1 approve each recovery stage; full historical
        evidence remains available by ID, not copied wholesale into the prompt.
        SOS, durable failures and chunk archives are intentionally left intact.
        """
        if not self.durable or not hasattr(self.durable, "start_child_task"):
            raise RuntimeError("durable recovery-task lineage is unavailable")
        if self._task_suppressed(job.task_id):
            raise RuntimeError("suppressed tasks must never be resumed automatically")
        # An unbounded succession of crashing recovery children is not progress.
        lineage = self.durable.task_lineage(job.task_id) if hasattr(self.durable, "task_lineage") else None
        depth = int((lineage or {}).get("task_depth") or 0)
        max_depth = max(1, int(self._runtime_config().get("worker", {}).get("max_subtask_depth", 4)))
        if depth >= max_depth:
            raise RuntimeError(f"recovery lineage reached maximum depth {max_depth}")
        source = self.durable.task_plan_snapshot(job.task_id) if hasattr(self.durable, "task_plan_snapshot") else None
        original = str(
            (source or {}).get("original_request")
            or (job.metadata or {}).get("original_user_prompt")
            or (job.metadata or {}).get("task_user_prompt")
            or ""
        ).strip()
        if not original:
            raise RuntimeError("no authoritative original user request; refusing invented recovery prompt")

        child_id = "recovery-" + uuid.uuid5(
            uuid.NAMESPACE_URL, "norm:recovery:" + job.task_id + ":" + job.step_id
        ).hex[:20]
        prior_steps = list((source or {}).get("steps") or [])
        # This compact inventory is a POINTER, not an adjudication of success.
        # N2 must independently check anything reported as completed.
        prior_index = [{"step_id": str(x.get("id") or ""),
                        "status": str(x.get("status") or "unknown"),
                        "name": str(x.get("name") or "")[:100]}
                       for x in prior_steps if isinstance(x, dict)]
        info = {
            "kind": "recovery_handoff", "task_kind": "recovery",
            "concluding_from_task_id": job.task_id, "failed_step_id": job.step_id,
            "failure_stage": str(stage)[:100], "failure_reason": redact(str(error))[:900],
            "saved_checkpoint": redact(str(checkpoint))[:2200],
            "prior_step_index": prior_index[:120],
            "full_history_location": "PostgreSQL task_runs/task_steps/task_evidence, original task ID above",
            "context_policy": "This is a summary/index, not the complete history. Look up archived evidence selectively.",
        }
        compact = json.dumps(info, ensure_ascii=False)
        marker = validate_recovery_handoff(
            self._runtime_root(), parent_task_id=job.task_id,
            parent_step_id=job.step_id, successor_task_id=child_id,
            original_prompt=original, summary=compact)
        if marker != "PASSED_TO:" + child_id:
            raise VerifierProtocolError("Invalid designated recovery handoff")
        steps = (
            TaskStep("plan-breakdown", "Recovery lineage", "Existing failed task referenced; no new user request.",
                     "Lineage is recorded."),
            TaskStep("verify-plan", "Recovery scope", "Original task was handed off as recovery.",
                     "Prior work remains available for selective readback.", depends_on=("plan-breakdown",)),
            TaskStep("step-01", "N2 recovery check plan",
                     "RECOVERY MODE (NOT A NEW USER REQUEST). Review the compact handoff and the "
                     "original request solely as intent. Propose specific READ-ONLY checks for "
                     "each claimed completed artifact/step; identify uncertain or missing evidence. "
                     "Never execute the original task or re-create a file in this phase. "
                     "N1 must approve this plan before verification proceeds.",
                     "Plan includes concrete checks and preserves completed work.", depends_on=("verify-plan",)),
            TaskStep("step-02", "N2 verify surviving work",
                     "Execute the N1-approved recovery checks using the NORMAL N2 tools and "
                     "file access rules. Verify files and durable step evidence, then write an "
                     "evidence-backed completed/pending/unknown report to Redis via the ordinary "
                     "task evidence/checkpoint machinery. Do not treat an old summary as proof. "
                     "If insufficient evidence, state UNKNOWN rather than making duplicate files.",
                     "Every recovered claim has direct evidence; N1 checks N2's report.", depends_on=("step-01",)),
            TaskStep("step-03", "N2 plan remaining recovery",
                     "Use N1-reviewed surviving-work checks to propose only missing work. "
                     "Do NOT parse the original user prompt as a new assignment. Reconcile "
                     "completed/unknown targets and specify a bounded execution sequence "
                     "for what truly remains. N1 reviews this recovery plan before execution.",
                     "Recovery plan contains only grounded missing work.", depends_on=("step-02",)),
            TaskStep("step-04", "N2 complete missing work",
                     "Follow the reviewed recovery plan, executing ONLY demonstrably missing "
                     "work through the normal N2 tools. Prefer safe read-only rechecks for "
                     "UNKNOWN targets. Preserve original artifacts, use checkpoints/chunks and "
                     "existing bounded child-task mechanisms if necessary. N1 verifies outcomes.",
                     "Missing work verified; no completed work needlessly redone.", depends_on=("step-03",)),
            TaskStep("step-05", "N2 recovery conclusion",
                     "Write the full user-facing result in the active N2 voice with normal "
                     "plugins, citing recovered and newly finished work accurately. Self-verify "
                     "before submitting the final candidate to N1; do not claim uncertain "
                     "checks passed.",
                     "N2 self-check and N1 final acceptance required.", depends_on=("step-04",)),
            TaskStep("final-verify", "N1 recovery acceptance",
                     "Review N2's final candidate against the original request and evidence.",
                     "N1 accepts only when requirements and artifact proof are satisfied.",
                     depends_on=("step-05",)),
        )
        # Stable node IDs are essential: a crash between durable child creation
        # and Redis enqueue must retry the SAME child without duplicating work.
        steps = tuple(TaskStep(x.id, x.name, x.description, x.verify,
            depends_on=x.depends_on,
            node_id=str(uuid.uuid5(uuid.NAMESPACE_URL, child_id + ":" + x.id)))
            for x in steps)
        plan = TaskPlan(
            task_id=child_id, title="Recovery attempt concluding from " + job.task_id,
            task_uuid=str(uuid.uuid5(uuid.NAMESPACE_URL, child_id + ":task")),
            steps=steps, original_request=original,
            source_prompt_id=str((job.metadata or {}).get("source_prompt_id") or ""),
            source_user_message_id=str((job.metadata or {}).get("original_user_message_id") or ""),
        )
        # Persist a useful summary *before* creating runnable work. Failure to
        # enqueue is recoverable: the same deterministic ID can be retried.
        if hasattr(self.queue, "r"):
            self.queue.r.setex("norm:recovery:handoff:" + child_id, 30 * 86400, compact)
        if hasattr(self.durable, "record_maintenance_note"):
            self.durable.record_maintenance_note("recovery", "Compact handoff and old history index retained.",
                task_id=job.task_id, details=info)
        existing = self.durable.task_status(child_id)
        if existing is None:
            self.durable.start_child_task(plan, job.task_id, job.step_id,
                original_request=original, task_kind="recovery")
            self.live.start_task(child_id, plan.title, plan.as_dict())
            now = _utc_now()
            for step in steps[:2]:
                self.durable.checkpoint_step(StepResult(child_id, step.id, step.name,
                    StepStatus.COMPLETED, "Recovery handoff recorded: " + job.task_id,
                    verification=step.verify, started_at=now, completed_at=now))
                self.live.step_started(child_id, step.id, step.name)
                self.live.step_completed(child_id, step.id, "Recovery lineage recorded", step.verify)
        elif existing not in {"running", "passed_to", "completed"}:
            raise RuntimeError("existing recovery task is not safely resumable: " + existing)
        # Do not enqueue duplicate or already-completed recovery work.
        if existing in {"completed", "passed_to"}:
            return child_id
        if not self.queue.contains_message_id(steps[2].node_id):
            jobs = []
            active = steps[2:-1]
            for index, step in enumerate(active):
                meta = dict(job.metadata or {})
                meta.update({
                    "step_name": step.name, "verification": step.verify,
                    "prompt_origin": "runtime_recovery_attempt", "recovery_mode": True,
                    "recovery_parent_task_id": job.task_id,
                    "recovery_failed_step_id": job.step_id,
                    "task_user_prompt": original, "original_user_prompt": original,
                    "compact_resume_context": False, "allow_subtask": index >= 3,
                    "is_final_candidate": index == len(active)-1,
                    "generated_step_index": index + 1, "generated_step_count": len(active),
                    "final_verification_node_id": steps[-1].node_id,
                    "command_envelope": {**self._command_from_job(job),
                        "intent": original[:500]},
                })
                # Avoid injecting unrelated *old* task injections and checkpoints
                # when the new task's first step starts.
                meta.pop("applied_context_injections", None)
                meta.pop("context_injection_cursor", None)
                jobs.append(PromptJob(
                    message_id=step.node_id, task_id=child_id, step_id=step.id,
                    prompt=step.description, project_id=job.project_id,
                    context=compact if index == 0 else "",
                    attempt=0, created_at=time(), metadata=meta,
                    chain_id=plan.task_uuid, previous_prompt_id="", next_prompt_id="",
                    chain_index=index, recovery_attempted=False,
                    request_type="conclusion" if index == len(active)-1 else "step",
                    task_uuid=plan.task_uuid, node_id=step.node_id, chain_uuid=plan.task_uuid,
                ))
            self.queue.enqueue_chain(jobs)
        return child_id

    def _handle_pathological_failure(
        self, redis_id: str, job: PromptJob, name: str, started, stage: str, error: str,
        *, resume_checkpoint: str = "",
    ) -> None:
        resumable = bool(str(resume_checkpoint or "").strip())
        suggestion = (
            "Use /failed then /resume-failed <index> to continue from the durable checkpoint."
            if resumable else
            "Inspect SOS and logs; fix the failing stage, then resubmit deliberately."
        )
        self._write_sos(job, stage, error, suggestion)

        queue_snapshot = self.queue.snapshot_task_jobs(job.task_id) if resumable else {}
        checkpoint = str(resume_checkpoint or "").strip()
        if resumable and isinstance(queue_snapshot, dict):
            # Replace the currently claimed node's stale context with the compact
            # failure checkpoint. Future nodes remain intact in the same snapshot.
            for item in list(queue_snapshot.get("jobs") or []):
                data = item.get("job") if isinstance(item, dict) else None
                if not isinstance(data, dict):
                    continue
                same_node = str(data.get("message_id") or "") == str(job.message_id or "")
                same_step = str(data.get("step_id") or "") == str(job.step_id or "")
                if not (same_node or same_step):
                    continue
                meta = dict(data.get("metadata") or {})
                meta.update(dict(job.metadata or {}))
                meta["compact_resume_context"] = True
                meta["manual_failure_resume"] = True
                meta["last_yield_reason"] = "task_round_budget"
                data["metadata"] = meta
                data["context"] = checkpoint
                data["attempt"] = 0
                data["recovery_attempted"] = False
                data["created_at"] = time()
                break

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
            if hasattr(self.durable, "record_task_failure"):
                root_id = job.task_id
                if hasattr(self.durable, "root_task_id"):
                    root_id = str(self.durable.root_task_id(job.task_id) or job.task_id)
                self.durable.record_task_failure(
                    job.task_id, root_task_id=root_id, stage=stage, reason=error[:4000],
                    resume_payload={
                        "queue": queue_snapshot,
                        "checkpoint": checkpoint,
                        "failed_step_id": job.step_id,
                        "failed_step_name": name,
                    },
                    rounds_at_failure=self.queue.task_rounds(job.task_id),
                    resumable=resumable,
                )
        self.live.step_failed(job.task_id, job.step_id, error[:2000])
        try:
            successor = self._handoff_failed_task_to_recovery(
                job, stage=stage, error=error, checkpoint=checkpoint
            )
        except Exception:
            logging.exception("Recovery handoff unavailable; leaving durable failure and SOS intact task=%s", job.task_id)
        else:
            self.durable.finalize_task(job.task_id,
                f"PASSED_TO:{successor} — recovery after {stage}; original progress retained.", "passed_to")
            if hasattr(self.live, "transfer_task"):
                self.live.transfer_task(job.task_id, successor,
                    f"PASSED_TO:{successor} — recovery attempt; original request is reference only.")
            self.queue.cleanup_task(job.task_id)
            logging.warning("Failure handed off task=%s successor=%s", job.task_id, successor)
            return

        if resumable:
            # Keep durable task artifacts/history intact for an explicit operator resume,
            # but clear every live queue/counter copy so nothing auto-restarts.
            queue_counts = self.queue.cleanup_task(job.task_id)
            if hasattr(self.live, "cleanup"):
                self.live.cleanup(job.task_id)
            logging.error(
                "Prompt safety-stopped resumably task=%s step=%s stage=%s queue=%s",
                job.task_id, job.step_id, stage, queue_counts,
            )
            return

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

    def _fresh_memory_config(self) -> dict:
        """Reload memory-maintenance settings for each maintenance pass."""
        from runtime_bootstrap import load_config

        return dict(load_config(self._runtime_root()).get("memory", {}) or {})

    def _maintenance_condense_client(self, memory_cfg: dict) -> MaintenanceModelClient:
        """Build the per-pass condensation client from freshly loaded settings.

        Blank model fields intentionally resolve to Norm. The wrapper permits one
        configured backup model and always retains Norm as the final fallback.
        """
        primary = str(memory_cfg.get("condensation_model") or "").strip()
        backup = str(memory_cfg.get("condensation_backup_model") or "").strip()
        client = MaintenanceModelClient(
            self.client, primary_model=primary, backup_model=backup, norm_model="norm"
        )
        logging.info(
            "Maintenance condensation model chain=%s configured_primary=%r configured_backup=%r",
            " -> ".join(client.model_chain), primary, backup,
        )
        return client

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

    def _task_round_policy(self) -> dict[str, int]:
        worker = self._runtime_config().get("worker", {}) or {}
        step_limit = max(1, int(worker.get("step_round_limit", 16)))
        base_limit = max(step_limit, int(worker.get("task_round_limit", 128)))
        first_review = max(1, min(base_limit - 1, int(worker.get("task_round_progress_review", 96))))
        extension = max(1, int(worker.get("task_round_extension", 36)))
        hard_limit = max(base_limit, int(worker.get("task_round_hard_limit", 384)))
        return {
            "step_limit": step_limit,
            "base_limit": base_limit,
            "first_review": first_review,
            "extension": extension,
            "hard_limit": hard_limit,
        }

    def _task_progress_snapshot(self, job: PromptJob, *, previous_evidence_count: int = 0) -> tuple[dict, list[dict], list[dict]]:
        step_state: list[dict] = []
        if self.durable is not None and hasattr(self.durable, "task_plan_snapshot"):
            try:
                snap = self.durable.task_plan_snapshot(job.task_id) or {}
                step_state = list(snap.get("steps") or [])
            except Exception:
                logging.exception("Could not read task plan snapshot for budget review task=%s", job.task_id)
        evidence: list[dict] = []
        if hasattr(self.live, "task_evidence"):
            try:
                evidence = list(self.live.task_evidence(job.task_id) or [])
            except Exception:
                logging.exception("Could not read live task evidence for budget review task=%s", job.task_id)
        completed_steps = sum(1 for item in step_state if str(item.get("status") or "") == "completed")
        running_steps = sum(1 for item in step_state if str(item.get("status") or "") == "running")
        failed_steps = sum(1 for item in step_state if str(item.get("status") or "") == "failed")
        mutations = 0
        verified_mutations = 0
        distinct_targets: set[str] = set()
        distinct_observations: set[str] = set()
        repeated_observations = 0
        for item in evidence:
            if not isinstance(item, dict):
                continue
            tool = str(item.get("tool") or "")
            result = item.get("result") if isinstance(item.get("result"), dict) else {}
            arguments = item.get("arguments") if isinstance(item.get("arguments"), dict) else {}
            path = str(result.get("path") or arguments.get("path") or "").strip()
            if path:
                distinct_targets.add(path)
            if result.get("ok"):
                # An unchanged observation repeated ten times is one piece of
                # evidence, not ten advancements. Include content/hash and the
                # request so changed files or distinct offsets count separately.
                observation_args = {k: v for k, v in arguments.items() if k not in {"n1_need", "n1_target"}}
                digest = json.dumps({"tool": tool, "arguments": observation_args, "result": result},
                                    sort_keys=True, default=str, ensure_ascii=False)
                key = __import__("hashlib").sha256(digest.encode("utf-8")).hexdigest()
                if key in distinct_observations:
                    repeated_observations += 1
                distinct_observations.add(key)
            if result.get("ok") and (tool in {"write_file", "replace_text"} or result.get("file_mutation")):
                mutations += 1
            if bool(item.get("deterministic_verification")) and result.get("ok"):
                verified_mutations += 1
        metrics = {
            "evidence_count": len(evidence),
            "completed_steps": completed_steps,
            "running_steps": running_steps,
            "failed_steps": failed_steps,
            "verified_mutations": verified_mutations,
            "mutations": mutations,
            "distinct_targets": len(distinct_targets),
            "distinct_observations": len(distinct_observations),
            "repeated_observations": repeated_observations,
        }
        start = max(0, min(len(evidence), int(previous_evidence_count or 0)))
        recent = evidence[start:]
        return metrics, recent[-24:], step_state[-24:]

    def _maybe_extend_task_round_budget(
        self, job: PromptJob, task_rounds: int, current_limit: int, budget_state: dict, policy: dict[str, int]
    ) -> tuple[int, dict]:
        hard_limit = int(policy["hard_limit"])
        if current_limit >= hard_limit or bool(budget_state.get("denied")):
            return current_limit, budget_state
        next_review = int(budget_state.get("next_review") or policy["first_review"])
        if task_rounds < next_review:
            return current_limit, budget_state

        previous_metrics = dict(budget_state.get("metrics") or {})
        metrics, recent_evidence, step_state = self._task_progress_snapshot(
            job, previous_evidence_count=int(previous_metrics.get("evidence_count") or 0)
        )
        delta = {
            key: int(metrics.get(key) or 0) - int(previous_metrics.get(key) or 0)
            for key in ("evidence_count", "completed_steps", "verified_mutations", "mutations", "distinct_targets", "distinct_observations", "repeated_observations")
        }
        proposed_limit = min(hard_limit, current_limit + int(policy["extension"]))
        if self.n1_gatekeeper is None or not hasattr(self.n1_gatekeeper, "review_task_round_progress"):
            judgment = {"extend": False, "reason": "N1 progress review unavailable", "processing_failed": True}
        else:
            judgment = self.n1_gatekeeper.review_task_round_progress(
                task_id=job.task_id, step_id=job.step_id, task_rounds=task_rounds,
                current_limit=current_limit, proposed_limit=proposed_limit,
                recent_evidence=recent_evidence, step_state=step_state, progress_delta=delta,
            )
        budget_state = dict(budget_state or {})
        budget_state["reviews"] = int(budget_state.get("reviews") or 0) + 1
        budget_state["last_review_round"] = int(task_rounds)
        budget_state["last_review_reason"] = str(judgment.get("reason") or "")[:1600]
        budget_state["metrics"] = metrics
        if bool(judgment.get("extend")):
            current_limit = proposed_limit
            budget_state["granted_limit"] = current_limit
            budget_state["next_review"] = min(hard_limit, next_review + int(policy["extension"]))
            budget_state["denied"] = False
            logging.info(
                "N1 extended task round budget task=%s step=%s rounds=%s new_limit=%s next_review=%s reason=%s",
                job.task_id, job.step_id, task_rounds, current_limit, budget_state["next_review"], budget_state["last_review_reason"][:500],
            )
        else:
            budget_state["granted_limit"] = current_limit
            budget_state["denied"] = True
            logging.warning(
                "N1 declined task round extension task=%s step=%s rounds=%s limit=%s reason=%s",
                job.task_id, job.step_id, task_rounds, current_limit, budget_state["last_review_reason"][:500],
            )
        if hasattr(self.queue, "set_task_round_budget_state"):
            self.queue.set_task_round_budget_state(job.task_id, budget_state)
        return current_limit, budget_state

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
        file_policy = load_file_access_policy(root, capability="core")
        allowed_roots = sorted({str(p) for p in (*file_policy.read_roots, *file_policy.write_roots)})
        redis_cfg = self._runtime_config().get("redis", {})
        dq = self._runtime_config().get("deletion_queue", {})
        deletion_queue = RedisDeletionQueue(
            host=str(dq.get("host", redis_cfg.get("host", "127.0.0.1"))),
            port=int(dq.get("port", redis_cfg.get("port", 6379))),
            db=int(dq.get("db", 2)),
            stream=str(dq.get("stream", "norm:deletion:queue")),
            trash_root=str(dq.get("trash_root", path_cfg["state_root"] / "deletion-trash")),
            items_key=str(dq.get("items_key", "norm:trash:items")),
            batches_key=str(dq.get("batches_key", "norm:trash:batches")),
            cross_volume_move_max_bytes=int(dq.get("cross_volume_move_max_bytes", 268_435_456)),
        )
        self._slice_file_tools = FileToolExecutor(
            allowed_roots,
            backup_root=str(tools.get("backup_root", path_cfg["state_root"] / "file-backups")),
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
            invocation_header("n2", "n2", "completion_self_review") + "\n"
            "[NORM-INTERNAL COMPLETION REVIEW — NOT A NEW USER REQUEST]\n"
            "The user has not sent another message. Review the candidate for the "
            "original task, preserving the active voice profile throughout any visible reasoning.\n\n"
            "Original task/step context:\n"
            + original_prompt
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
        target_items = max(4, min(64, int(self._runtime_config().get("worker", {}).get("child_batch_target_items", 16))))
        batch_count = max(2, min(24, (count + target_items - 1) // target_items))
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
                "task_user_prompt": instruction if prompt_origin in {
                    "runtime_oversize_recovery_analysis", "runtime_oversize_recovery_unit"
                } else task_user_prompt,
                "expected_result_kind": (
                    "recovery_analysis" if prompt_origin == "runtime_oversize_recovery_analysis"
                    else "step_result" if prompt_origin == "runtime_oversize_recovery_unit"
                    else "final_candidate"),
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
        if isinstance(supplied, list) and (supplied or metadata.get("expected_result_kind") == "recovery_analysis"):
            return self._normalize_scope_items(supplied, field="recovery_scope_items")
        scope = self._scope_items_from_text(job.prompt)
        if not scope:
            scope = self._scope_items_from_text(str(metadata.get("step_name") or ""))
        # No numbered scope is still a valid recovery scope. Do not manufacture
        # item 1: doing so falsely turns ordinary unscoped work into an atomic
        # item and activates single-item recovery limits that the user never set.
        return scope

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
            self._store_output_overflow(job, "recovery-handoff-note", exc.partial)
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
                self._store_output_overflow_for_ids(child_id, "work", "recovery-child-note", exc.partial)
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
            f"CUMULATIVE PARENT CONTINUATION CHECKPOINT:\n{str(job.context or '')[-14000:]}\n\n"
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
        scope_example = parent_scope[:1] if parent_scope else []
        unit_limit_instruction = (
            "Because this parent is explicitly scoped to one numbered item, use at most 4 semantic subchecks. "
            if len(parent_scope) == 1
            else "Use 2-8 remaining_units when decomposition is warranted. "
        )
        instruction = (
            "Analyze the durable work from an oversized parent step and design a recovery decomposition. "
            "Do not redo the parent task. Identify what useful work is already complete, what remains unresolved, "
            "and split only the unresolved work into bounded non-overlapping units. "
            "Your FINAL response for this child MUST be the structured recovery record itself. "
            "Do not end with progress commentary, an intention to inspect more material, or a statement of what you will do next. "
            "Use the available tools first if needed, then finish by returning JSON only with this shape: "
            '{"useful_points":["..."],"completed_work":["..."],'
            f'"remaining_units":[{{"name":"...","instruction":"...","verify":"...","scope_items":{json.dumps(scope_example)}}}],'
            '"merge_requirements":["..."]}. '
            + unit_limit_instruction
            + "Units must be semantically meaningful, not arbitrary token ranges. "
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

    def _store_output_overflow_for_ids(self, task_id: str, step_id: str, stage: str, generated: str) -> str:
        """Preserve the actual generated text in Redis with readback verification."""
        from .output_overflow import OverflowStore
        overflow_cfg = self._runtime_config().get("output_overflow") or {}
        store = OverflowStore(self.queue.r, ttl_seconds=int(overflow_cfg.get("redis_ttl_seconds", 86400)))
        receipt = store.save(task_id, step_id, stage, generated)
        logging.warning("Preserved truncated output task=%s step=%s stage=%s redis=%s chars=%s",
                        task_id, step_id, stage, receipt.key, receipt.characters)
        return receipt.handoff

    def _store_output_overflow(self, job: PromptJob, stage: str, generated: str) -> str:
        """A bounded preview/Redis locator; does not count as a completed answer."""
        return self._store_output_overflow_for_ids(job.task_id, job.step_id, stage, generated)

    def _prepare_final_report(self, job: PromptJob, answer: str, *, forced: bool = False) -> str:
        """Archive a long final report; condense a Redis working copy at most 3 times.

        Does not approve/reject N2's answer; normal verification follows.
        """
        from .output_overflow import OverflowStore, write_full_report, report_link
        content = str(answer or "").strip()
        limit = max(1000, int((self._runtime_config().get("output_overflow") or {}).get("final_reply_max_chars", 16000)))
        if not content or (not forced and len(content) <= limit):
            return content
        root = Path(load_path_settings(self._runtime_root())["state_root"])
        path = write_full_report(root, job.task_id, job.step_id, content)
        store = OverflowStore(self.queue.r, ttl_seconds=int((self._runtime_config().get("output_overflow") or {}).get("redis_ttl_seconds", 86400)))
        receipt = store.save(job.task_id, job.step_id, "original-final-report", content)
        # Read the actual working copy back from Redis. Never discard the file.
        original = store.load(receipt.key)
        best = ""
        source = original
        for attempt in range(1, 4):
            prompt = (
                "Condense this COMPLETE, already-written user-facing report. Do not research, "
                "modify files, claim new work, invent results, or omit material caveats. "
                f"Target at most {limit} characters. This is condensation attempt {attempt}/3. "
                "Return only a coherent, self-contained Markdown summary, without code fences. "
                "The complete original will be linked separately.\n\n"
                "FULL SOURCE OR PRIOR COMPLETE CONDENSATION:\n" + source
            )
            try:
                candidate = self._generate_complete_text(prompt, think=False, num_predict=6000,
                    temperature=0.0, max_segments=1, job=job, stage=f"final-condense-{attempt}").strip()
            except ModelOutputTruncated as exc:
                # An incomplete generated fragment is not an acceptable user summary.
                logging.warning("Final condensation exceeded output allowance task=%s attempt=%s chars=%s",
                                job.task_id, attempt, len(exc.partial))
                continue
            except ModelContextExceeded:
                logging.warning("Final condensation context overrun task=%s attempt=%s", job.task_id, attempt)
                # Work on the shortest COMPLETE prior candidate, never silently
                # truncate the original report and pretend it is a full copy.
                if best:
                    source = best
                continue
            if not candidate:
                continue
            store.save(job.task_id, job.step_id, f"final-condense-{attempt}", candidate)
            if not best or len(candidate) < len(best):
                best = candidate
            if len(best) <= limit:
                break
            source = best
        # Even when 3 tries cannot meet the target, provide the shortest COMPLETE
        # attempt plus the untouched original report. The user's real work survives.
        if not best:
            best = "The complete report was preserved, but an abbreviated response could not be generated reliably."
        suffix = f"\n\nFull original report: {report_link(path)}"
        logging.warning("Archived long final report task=%s chars=%s condensed=%s original=%s",
                        job.task_id, len(content), len(best), str(path))
        return best + suffix

    def _generate_complete_text(self, prompt: str, *, think: bool, num_predict: int, temperature: float, max_segments: int = 4, system_context: str = "", job: PromptJob | None = None, stage: str = "text") -> str:
        pieces: list[str] = []
        current = str(prompt)
        for segment in range(1, max_segments + 1):
            try:
                # Only the real Ollama client has a per-request system-context API.
                # Other generator stubs/adapters retain their existing interface.
                kwargs = {"system_context": system_context} if system_context and isinstance(self.client, OllamaClient) else {}
                text = self.client.generate(current, think=think, num_predict=num_predict, temperature=temperature, **kwargs)
                pieces.append(text)
                return "".join(pieces).strip()
            except ModelOutputTruncated as exc:
                pieces.append(exc.partial)
                joined = "".join(pieces)
                logging.warning("Model text output hit length limit segment=%s/%s chars=%s eval_count=%s; continuing", segment, max_segments, len(joined), exc.eval_count)
                if job is not None:
                    self._store_output_overflow(job, f"{stage}-segment-{segment}", joined)
                if segment >= max_segments:
                    # Raise the COMPLETE accumulated generated portion, not just
                    # the last segment, so the caller can archive it safely.
                    raise ModelOutputTruncated(joined, exc.eval_count) from exc
                current = (
                    "Continue an incomplete response. Do not restart, summarize, or repeat already-completed material. "
                    "Continue exactly from the cutoff and finish every remaining requirement. Return only the continuation.\n\n"
                    f"ORIGINAL REQUEST/INSTRUCTIONS:\n{prompt}\n\nRESPONSE SO FAR:\n{joined}"
                )
        raise RuntimeError("unreachable text completion loop")

    def _yield_context_overflow(self, redis_id: str, job: PromptJob, name: str, started, exc: ModelContextExceeded) -> None:
        model_cfg = self._runtime_config().get("model_runtime", {}) or {}
        action = str(model_cfg.get("context_overflow_action", "split") or "split").strip().lower()
        if action != "split":
            raise exc
        outcome = {
            "yielded": True,
            "checkpoint": (
                "INPUT CONTEXT LIMIT CHECKPOINT\n"
                f"Original goal: {job.prompt}\n"
                f"Rejected request tokens: {exc.request_tokens or 'unknown'}\n"
                f"Available model context: {exc.context_size or 'unknown'}\n"
                "Do not retry the same monolithic request. Use Norm's oversize recovery to decompose the unresolved scope into smaller bounded child work, preserve completed work, and merge compact durable notes back into the parent."
            ),
            "reason": "input_context_limit",
            "step_rounds": 0,
            "task_rounds": self.queue.task_rounds(job.task_id),
            "evidence": [],
            "resume_metadata": {
                "context_overflow_request_tokens": int(exc.request_tokens or 0),
                "context_overflow_n_ctx": int(exc.context_size or 0),
            },
        }
        logging.warning(
            "Model input exceeded context; yielding to oversize recovery task=%s step=%s request_tokens=%s n_ctx=%s",
            job.task_id, job.step_id, exc.request_tokens, exc.context_size,
        )
        self._checkpoint_yield(redis_id, job, name, started, outcome)

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
        # N2 voice is a dedicated system instruction, not user-authored task text.
        voice_instructions = self._active_voice_instructions()
        prompt = self._prompt_with_context_injections(job, base_prompt)
        tools = self._worker_tools()
        if tools is not None and hasattr(tools, "set_task_context"):
            tools.set_task_context(job.task_id, job.step_id)
        metadata = job.metadata or {}
        if tools is None or not hasattr(self.client, "chat_with_tools"):
            try:
                answer = self._generate_complete_text(
                    prompt,
                    think=bool(metadata.get("think", False)),
                    num_predict=int(metadata.get("num_predict") or 16000),
                    temperature=float(metadata.get("temperature", 0.2)),
                    system_context=voice_instructions, job=job, stage="n2-no-tools",
                )
            except ModelOutputTruncated as exc:
                receipt = self._store_output_overflow(job, "n2-no-tools-output", exc.partial)
                self._checkpoint_yield(redis_id, job, name, started, {
                    "yielded": True, "checkpoint": "MODEL OUTPUT LIMIT: incomplete generation.\n" + receipt,
                    "reason": "output_length_checkpoint", "step_rounds": 0,
                    "task_rounds": self.queue.task_rounds(job.task_id), "evidence": [],
                })
                return None
            except ModelContextExceeded as exc:
                self._yield_context_overflow(redis_id, job, name, started, exc)
                return None
            if normalize_request_type(job.request_type) == "conclusion" or (job.metadata or {}).get("is_final_candidate"):
                answer = self._prepare_final_report(job, answer)
            return self._verify_and_correct_without_tools(job, prompt, answer, system_context=voice_instructions)

        historical_evidence = self.live.task_evidence(job.task_id) if hasattr(self.live, "task_evidence") else []
        try:
            outcome = self._run_tool_slice(job, base_prompt, tools)
        except ModelContextExceeded as exc:
            self._yield_context_overflow(redis_id, job, name, started, exc)
            return None
        prompt = self._prompt_with_context_injections(job, base_prompt, consume=False)
        local_evidence = list(outcome.get("evidence") or [])
        if outcome["yielded"]:
            self._checkpoint_yield(redis_id, job, name, started, outcome)
            return None
        answer = str(outcome["answer"])
        evidence = list(historical_evidence) + local_evidence
        if normalize_request_type(job.request_type) == "conclusion" or (job.metadata or {}).get("is_final_candidate"):
            answer = self._prepare_final_report(job, answer, forced=False)
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
        """Read only the one live Redis verification hash. PostgreSQL is never implicit."""
        cfg = self._validation_pool_config()
        if not bool(cfg.get("enabled", True)):
            return []
        limit = max(1, min(4096, int(cfg.get("emergency_row_cap", 512))))
        if not hasattr(self.live, "global_validation"):
            return []
        try:
            cleaned = [str(x).strip() for x in (subjects or []) if str(x).strip()]
            if cleaned:
                return [row for key in cleaned[:limit] if (row := self.live.global_validation(key))]
            if hasattr(self.live, "recent_global_validations"):
                return list(self.live.recent_global_validations(limit=limit) or [])[:limit]
        except Exception:
            logging.exception("Redis verification-pool read failed task=%s", getattr(job, "task_id", ""))
        return []

    def _validation_context(self, job: PromptJob) -> str:
        # Do not spray the whole pool into every model round.  The model asks Redis
        # for the intended target via verification_preflight and decides only after
        # seeing that result whether reuse/history/a fresh tool call is warranted.
        cfg = self._validation_pool_config()
        token_budget = max(1, int(cfg.get("context_token_budget", 2500)))
        char_budget = max(1, min(int(cfg.get("context_char_budget", 10000)), token_budget * 4))
        return (
            "LIVE REDIS VERIFICATION POOL: query it with verification_preflight before each information tool. "
            "Redis is the short-term record. PostgreSQL history is optional and is never consulted implicitly."
        )[:char_budget]

    @staticmethod
    def _validation_tool_schemas() -> list[dict]:
        return [
            {"type":"function","function":{
                "name":"verification_preflight",
                "description":"MANDATORY Redis lookup before an information-gathering tool. State the intended tool, stable target, and factual description. Redis returns same-target candidates first; decide what to do only after reading that result.",
                "parameters":{"type":"object","properties":{
                    "tool":{"type":"string","description":"The information tool you intend to use if a fresh check is needed."},
                    "target":{"type":"string","description":"Stable object/location being checked, e.g. an exact file/folder path, endpoint, table, or resource. Do not put procedural prose here."},
                    "description":{"type":"string","description":"What fact about the target is being verified. Wording may vary; existing same-target records are ranked semantically."}
                },"required":["tool","target","description"],"additionalProperties":False}
            }},
            {"type":"function","function":{
                "name":"verification_history",
                "description":"OPTIONAL PostgreSQL lookup for the current preflight target/fact. Call only if older evidence would materially affect the decision to reuse or recheck.",
                "parameters":{"type":"object","properties":{},"additionalProperties":False}
            }},
            {"type":"function","function":{
                "name":"verification_checkin",
                "description":"MANDATORY Redis check-in after a fresh information-tool result. Choose an existing record_id returned by preflight when it is the same fact; use 'new' only when none of those records describes the fact. Same value increments num_checks; changed value resets num_checks to 1.",
                "parameters":{"type":"object","properties":{
                    "record_id":{"type":"string","description":"Existing record_id returned by Redis preflight or optional PostgreSQL history, or literal 'new'."},
                    "value":{"type":"string","description":"Compact canonical factual value. Prefer stable JSON text for structured facts."}
                },"required":["record_id","value"],"additionalProperties":False}
            }},
        ]

    def _record_global_validations(self, job: PromptJob, items: list[dict]) -> list[dict]:
        """Redis check-in is mandatory; PostgreSQL receives only twice-daily migrations."""
        if not hasattr(self.live, "record_global_validations"):
            raise RuntimeError("mandatory Redis verification pool is unavailable")
        return self.live.record_global_validations(job.task_id, job.step_id, items)

    @staticmethod
    def _information_tool_requires_verification(name: str) -> bool:
        name = str(name or "")
        if name in {"verification_preflight", "verification_history", "verification_checkin"}:
            return False
        # Core file mutations are not evidence acquisition. Everything else, including
        # shell/tests and plugin calls, must explicitly pass through Redis verification.
        if name in {"make_directory", "write_file", "replace_text", "delete_file", "append_task_note"}:
            return False
        return True

    def _execute_validation_tool(self, job: PromptJob, name: str, arguments: dict, state: dict | None = None) -> dict:
        state = state if state is not None else {}
        cfg = self._validation_pool_config()
        if not bool(cfg.get("enabled", True)):
            return {"ok": False, "tool": name, "error": "verification pool is disabled"}

        if name == "verification_preflight":
            if state.get("pending_checkin"):
                return {"ok": False, "tool": name, "error": "verification_checkin is required for the previous evidence result first"}
            intended_tool = str(arguments.get("tool") or "").strip()
            target = str(arguments.get("target") or "").strip()
            description = str(arguments.get("description") or "").strip()
            if not intended_tool or not target or not description:
                return {"ok": False, "tool": name, "error": "tool, target, and description are required"}
            if not hasattr(self.live, "validation_candidates"):
                return {"ok": False, "tool": name, "error": "Redis validation pool does not support target lookup"}
            candidates = list(self.live.validation_candidates(
                tool=intended_tool, target=target, description=description, limit=12
            ) or [])
            recommended = ""
            if candidates:
                top_score = float(candidates[0].get("description_match") or 0.0)
                if top_score >= 0.72 or (len(candidates) == 1 and top_score >= 0.45):
                    recommended = str(candidates[0].get("record_id") or "")
            state["preflight"] = {
                "tool": intended_tool, "target": target, "description": description,
                "candidates": candidates, "recommended_record_id": recommended,
                "history_reviewed": False,
            }
            return {
                "ok": True, "tool": name, "storage_checked": "norm:validation:pool",
                "postgres_checked": False, "candidates": candidates,
                "recommended_record_id": recommended,
                "next": (
                    "Decide now: reuse a Redis candidate, optionally call verification_history, or run the intended information tool. "
                    "Do not invent a new record merely because your wording changed."
                ),
            }

        if name == "verification_history":
            preflight = state.get("preflight") or {}
            if not preflight:
                return {"ok": False, "tool": name, "error": "verification_preflight is required first"}
            if self.durable is None or not hasattr(self.durable, "validation_history"):
                history = {"storage": "postgres_history", "unavailable": True, "records": []}
            else:
                try:
                    history = self.durable.validation_history(
                        target=str(preflight.get("target") or ""),
                        description=str(preflight.get("description") or ""),
                        tool=str(preflight.get("tool") or ""),
                        limit=12,
                    )
                except Exception as exc:
                    logging.exception("Optional PostgreSQL verification history lookup failed target=%s", preflight.get("target"))
                    history = {"storage": "postgres_history", "unavailable": True, "records": [], "error": f"{type(exc).__name__}: {exc}"}
            preflight["history_reviewed"] = True
            preflight["history"] = history
            return {
                "ok": True, "tool": name, "history": history,
                "policy": "historical context only; decide for yourself whether its age/count changes the need for a fresh check",
            }

        if name == "verification_checkin":
            pending = state.get("pending_checkin") or {}
            preflight = state.get("preflight") or {}
            if not pending or not preflight:
                return {"ok": False, "tool": name, "error": "no pending information-tool result requires check-in"}
            if str(pending.get("tool") or "") != str(preflight.get("tool") or ""):
                return {"ok": False, "tool": name, "error": "information tool does not match the current verification preflight"}
            record_id = str(arguments.get("record_id") or "").strip()
            value = str(arguments.get("value") or "").strip()
            if not record_id or not value:
                return {"ok": False, "tool": name, "error": "record_id and value are required"}
            candidate_ids = {str(row.get("record_id") or "") for row in (preflight.get("candidates") or [])}
            history_rows = list(((preflight.get("history") or {}).get("records") or []))
            history_ids = {str(row.get("record_id") or "") for row in history_rows}
            allowed_ids = candidate_ids | history_ids
            if record_id != "new" and record_id not in allowed_ids:
                return {"ok": False, "tool": name, "error": "record_id must come from Redis preflight/PostgreSQL history, or be literal 'new'"}
            # If the model says 'new' despite a very strong Redis same-target semantic match,
            # refuse to manufacture wording-duplicate Redis records.
            if record_id == "new" and preflight.get("recommended_record_id"):
                return {
                    "ok": False, "tool": name,
                    "error": "preflight found a strong same-target match; reuse recommended_record_id unless this is genuinely a different fact",
                    "recommended_record_id": preflight.get("recommended_record_id"),
                }
            history_match = next((row for row in history_rows if str(row.get("record_id") or "") == record_id), None)
            item = {
                "record_id": record_id,
                "tool": str(pending.get("tool") or ""),
                "target": str(preflight.get("target") or ""),
                "description": str(preflight.get("description") or ""),
                "value": value,
            }
            if history_match and str(history_match.get("value") or "") != value:
                item["previous_value_hint"] = str(history_match.get("value") or "")
            recorded = self._record_global_validations(job, [item])
            state["pending_checkin"] = None
            state["preflight"] = None
            return {
                "ok": True, "tool": name, "storage": "norm:validation:pool", "recorded": recorded,
                "policy": "same value increments num_checks; changed value records previous_value, changed_at, and resets num_checks to 1",
            }

        return {"ok": False, "tool": name, "error": "unknown verification-pool tool"}

    @staticmethod
    def _tool_slice_initial_messages(job: PromptJob) -> list[dict]:
        """Keep the human's request as a genuine user-role message on every slice.

        A generated step is not a new request to plan the parent task. Its
        current executable instruction and verified progress are supplied by
        the *system* work_request; the user-role text remains the unchanged
        original goal/constraints for semantic grounding and final review.
        Ollama also requires an actual user message for its chat template.
        """
        metadata = job.metadata or {}
        request_type = normalize_request_type(job.request_type)
        user_request = str(metadata.get("original_user_prompt") or metadata.get("task_user_prompt") or "").strip()
        direct_user_turn = (
            request_type == "prompt"
            and str(metadata.get("prompt_origin") or "user_prompt")
            in {"user_prompt", "prompt_interface", "gui_prompt", "ssh_prompt"}
        )
        if not user_request and direct_user_turn:
            user_request = str(job.prompt or "").strip()
        if not user_request:
            # Old queued step metadata may have lost the separate field, while
            # the original task survives in its generated step text.
            old_step = str(job.prompt or "")
            marker = "AUTHORITATIVE PRIMARY TASK REQUEST:\n"
            end_marker = "\n\nNORM-GENERATED EXECUTION STEP:"
            if marker in old_step and end_marker in old_step.split(marker, 1)[1]:
                user_request = old_step.split(marker, 1)[1].split(end_marker, 1)[0].strip()
        if not user_request:
            raise RuntimeError(
                "Cannot execute an Ollama tool slice without the original user request. "
                "Restore original_user_prompt/task_user_prompt from durable task metadata; "
                "do not invent a replacement user message."
            )
        return [{"role": "system", "content": ""}, {"role": "user", "content": user_request}]

    @staticmethod
    def _tool_slice_execution_instructions(job: PromptJob) -> str:
        """Distinguish the already-reviewed PLAN from the active EXECUTION turn."""
        metadata = job.metadata or {}
        request_type = normalize_request_type(job.request_type)
        index = int(metadata.get("generated_step_index") or 0)
        total = int(metadata.get("generated_step_count") or 0)
        is_planned_step = str(metadata.get("prompt_origin") or "") in {
            "norm_generated_step", "runtime_child_subtask", "runtime_oversize_recovery_analysis",
            "runtime_oversize_recovery_unit", "runtime_recovery_attempt",
            "runtime_final_n2_repair",
        } or request_type in {"step", "task_step", "conclusion"}
        if is_planned_step:
            stage = f"Step {index}/{total}" if index and total else f"step_id={job.step_id}"
            recovery_directive = (
                "[RECOVERY TASK — INVENTORY FIRST]\nThe old task is not an unexecuted user request. "
                "Follow the approved recovery phase ONLY; verify existing artifacts and Redis/durable "
                "checkpoints before deciding anything needs redoing. Request archived evidence selectively.\n"
                if metadata.get("recovery_mode") else ""
            )
            return recovery_directive + (
                "[NORM EXECUTION PHASE — ACTIVE STEP, NOT TASK PLANNING]\n"
                f"Current execution target: {stage}.\n"
                "The original user request remains in the user role ONLY as reference "
                "for overall purpose, constraints, and success criteria. DO NOT execute "
                "that user-role text as a new task, nor replan/restart it.\n"
                "Planning and N1 plan verification have ALREADY occurred. Actively "
                "PROSECUTE the CURRENT work_request.instruction and its verification "
                "requirement, using the current continuation, task memory, and "
                "PostgreSQL-completed earlier steps as established progress. Use tools "
                "and produce concrete results for THIS step; do not merely describe "
                "how you would plan or perform it. Do not redo completed steps, "
                "regenerate the task plan, or silently do later steps. Reopen prior "
                "work only when new evidence specifically invalidates it or the "
                "active step explicitly requires it.\n"
                "If a targeted verifier/N1 correction is included in this invocation, "
                "resolve that correction within the current step rather than "
                "returning to the beginning. On a conclusion step, synthesize the "
                "verified prior results into the user-facing deliverable.\n"
            )
        return (
            "[NORM DIRECT EXECUTION PHASE]\n"
            "The user-role message is the actual operator request. Follow the "
            "current work_request's processing contract; execute rather than "
            "re-plan unless the current request explicitly calls for planning.\n"
        )

    def _run_tool_slice(self, job: PromptJob, prompt: str, tools) -> dict:
        import json

        policy = self._task_round_policy()
        step_limit = int(policy["step_limit"])
        task_rounds = self.queue.task_rounds(job.task_id)
        if hasattr(self.queue, "task_round_budget_state"):
            budget_state = self.queue.task_round_budget_state(
                job.task_id, base_limit=int(policy["base_limit"]), first_review=int(policy["first_review"])
            )
        else:
            budget_state = {"granted_limit": int(policy["base_limit"]), "next_review": int(policy["first_review"])}
        task_limit = min(int(policy["hard_limit"]), max(int(policy["base_limit"]), int(budget_state.get("granted_limit") or policy["base_limit"])))
        gate_instructions = (
            self.n1_gatekeeper.instructions()
            if self.n1_gatekeeper is not None
            else (
                "Tool requests execute directly because no N1 gatekeeper is configured. "
                "Do not call legacy verification_preflight/checkin tools."
            )
        )
        metadata = job.metadata or {}
        request_type = normalize_request_type(job.request_type)
        # Retain the original operator request in its genuine Ollama user role
        # during every execution slice. The system phase contract below—not
        # deleting the user's words—prevents internal steps from replanning.
        messages: list[dict] = self._tool_slice_initial_messages(job)
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
        output_budget = int(metadata.get("num_predict") or (74000 if long_form else 38000))
        chunk_manager = ChunkedEvidenceManager(self._runtime_config().get("context_budget") or {})
        restored_streams = chunk_manager.restore_streams((metadata.get("chunked_evidence_streams") or {}))
        if restored_streams:
            logging.info(
                "Restored chunked evidence stream cursor(s) task=%s step=%s streams=%s",
                job.task_id, job.step_id, restored_streams,
            )
        active_raw_stream_id = ""
        active_raw_piece = 0
        while step_rounds < step_limit and task_rounds < task_limit:
            task_limit, budget_state = self._maybe_extend_task_round_budget(
                job, task_rounds, task_limit, budget_state, policy
            )
            if task_rounds >= task_limit:
                break
            self._consume_context_injections(job)
            # Refresh the injection inside the system work context on every
            # round. A retired instruction cannot survive in old chat messages.
            live_injection = self._context_injection_block(
                (job.metadata or {}).get("applied_context_injections") or [])
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
            # N1 is transparent to user/N2 prose. Only the tool boundary is instrumented.
            system_sections = [
                tools.instructions(),
                gate_instructions,
                self._active_voice_instructions(),
                self._tool_slice_execution_instructions(job),
                "[NORM INTERNAL TASK/STEP INSTRUCTION — NOT USER-AUTHORED]\n"
                "The user-role message contains the operator's original task, "
                "preserved on each slice for purpose and constraints. The scoped "
                "internal work_request below defines WHAT TO EXECUTE NOW. "
                "Never attribute this internal step or its corrections to the user.\n"
                + prompt,
                live_injection,
            ]
            messages[0]["content"] = "\n\n".join(section for section in system_sections if section)
            native_schemas = tools.schemas()
            if self.n1_gatekeeper is not None:
                native_schemas = self.n1_gatekeeper.instrument_schemas(native_schemas)
            response = self.client.chat_with_tools(
                messages,
                native_schemas,
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
            headroom = self._context_headroom_state(response)
            thinking_index = self._persist_thinking_now(job, thinking, int(response.get("eval_count") or 0))
            turn_reset = ""
            if self.n1_gatekeeper is not None:
                turn_reset = self.n1_gatekeeper.observe_n2_turn(
                    task_id=job.task_id,
                    step_id=job.step_id,
                    content=content,
                    thinking=thinking,
                    calls=calls,
                )
            if content:
                last_content = content
            if done_reason == "length" and calls:
                # Truncated tool-call JSON is not an executable plan. Checkpoint
                # the actual generated payload; do not fire incomplete calls.
                partial_call_text = json.dumps({"content": content, "tool_calls": calls}, ensure_ascii=False)
                pointer = self._store_output_overflow(job, "n2-truncated-tool-proposal", partial_call_text)
                evidence.extend(self._verify_pending(job, pending_verification, tools))
                return {
                    "yielded": True,
                    "checkpoint": "OUTPUT LIMIT WITH UNEXECUTED TOOL PROPOSAL\n" + pointer,
                    "reason": "output_length_checkpoint",
                    "step_rounds": step_rounds, "task_rounds": task_rounds,
                    "evidence": evidence,
                }
            if not calls and turn_reset.startswith("The Bird says No."):
                # A sensitive-value probe can end as prose instead of another tool
                # call. Give N1's opaque redirect back to N2 rather than accepting
                # that dead-end as the step result. Do not explain the boundary.
                messages.append(redact({"role": "assistant", "content": content}))
                messages.append({"role": "system", "content": invocation_header("n1", "n2", "tool_intervention") + "\n[NORM-INTERNAL N1 INTERVENTION; NOT USER-AUTHORED]\n" + turn_reset})
                continue
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
                        checkpoint += "\n\n" + self._store_output_overflow(job, "n2-output-segment", current_segment)
                        job.metadata = dict(job.metadata or {}, output_length_seen=True)
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
                    thinking_ref = self._store_output_overflow(job, "n2-thinking-segment", thinking)
                    checkpoint = (
                        "DURABLE THINKING CHECKPOINT\n"
                        f"Original goal: {job.prompt}\n"
                        "The previous model slice reached its output budget before emitting usable answer text. "
                        "Its raw unfinished thinking WAS saved in PostgreSQL norm_runtime.task_thinking_segments "
                        f"(task_id={job.task_id}, step_id={job.step_id}, thinking_index={thinking_index}). "
                        "Resume from the compact working note below, not from the bulky raw thinking. Keep normal reasoning enabled and do not repeat already-explored work.\n\n"
                        f"COMPACT WORKING NOTE:\n{compact_thinking}\n\n{thinking_ref}"
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
                        "role": "system",
                        "content": "[NORM-INTERNAL RETRY] You returned no textual result. Using the task instructions and observed tool results above, return the required concise result for this step now. Do not repeat completed tool work unless verification is genuinely needed.",
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
            # Freeze the exact recent context before raw-chunk eviction. PostgreSQL
            # is the lossless record; the active messages list may be compacted next.
            lossless_checkpoint_messages = (
                json.loads(json.dumps(messages[-32:], ensure_ascii=False))
                if bool(headroom.get("checkpoint"))
                else []
            )
            n1_interventions: list[str] = [turn_reset] if turn_reset else []

            # A raw evidence chunk lives for exactly one N2 model turn.  Once N2
            # emits its next tool call(s), it has consumed that chunk.  Commit a
            # compact fact note (explicit `retain` wins; otherwise use N2's prose)
            # and replace the old raw tool payload with a provenance stub before
            # any new tool result can enter context.
            if active_raw_stream_id:
                active_stream = chunk_manager.get(active_raw_stream_id)
                retained_candidate = str(content or "").strip()
                for pending_call in calls:
                    pending_fn = pending_call.get("function") if isinstance(pending_call, dict) else None
                    if not isinstance(pending_fn, dict) or str(pending_fn.get("name") or "") != "read_file":
                        continue
                    pending_args = pending_fn.get("arguments", {})
                    if isinstance(pending_args, str):
                        try:
                            pending_args = json.loads(pending_args)
                        except json.JSONDecodeError:
                            pending_args = {}
                    if not isinstance(pending_args, dict):
                        continue
                    if str(pending_args.get("stream_id") or "") == active_raw_stream_id:
                        explicit_retain = str(pending_args.get("retain") or "").strip()
                        if explicit_retain:
                            retained_candidate = explicit_retain
                            break
                if active_stream is not None and active_raw_piece > 0:
                    retained = chunk_manager.retain(active_stream, active_raw_piece, retained_candidate)
                    chunk_manager.evict_tool_message(
                        messages, stream=active_stream, piece=active_raw_piece, retained=retained
                    )
                    for prior_item in reversed(evidence):
                        prior_result = prior_item.get("result") if isinstance(prior_item, dict) else None
                        prior_meta = prior_result.get("chunk_stream") if isinstance(prior_result, dict) else None
                        if not isinstance(prior_meta, dict):
                            continue
                        if (
                            str(prior_meta.get("stream_id") or "") == active_stream.stream_id
                            and int(prior_meta.get("piece") or 0) == active_raw_piece
                        ):
                            prior_item["result"] = chunk_manager.compact_result_for_active_evidence(
                                prior_result, retained
                            )
                            break
                    if retained:
                        retention_item = redact({
                            "tool": "chunk_retention",
                            "arguments": {
                                "stream_id": active_stream.stream_id,
                                "piece": active_raw_piece,
                            },
                            "result": {
                                "ok": True,
                                "path": active_stream.path,
                                "source_fingerprint": active_stream.source_fingerprint,
                                "chunk_stream": {
                                    "stream_id": active_stream.stream_id,
                                    "piece": active_raw_piece,
                                    "estimated_pieces": active_stream.estimated_pieces,
                                    "seam_before": chunk_manager.seam_before(active_raw_piece),
                                    "seam_after": chunk_manager.seam_after(active_raw_piece, active_stream.estimated_pieces),
                                },
                                "retained_facts": retained,
                            },
                            "deterministic_verification": False,
                        })
                        evidence.append(retention_item)
                        self._persist_evidence_now(job, [retention_item])
                active_raw_stream_id = ""
                active_raw_piece = 0

            if bool(headroom.get("checkpoint")):
                evidence.extend(self._verify_pending(job, pending_verification, tools))
                checkpoint, resume_metadata = self._distilled_continuation_checkpoint(
                    job,
                    prompt,
                    lossless_checkpoint_messages or messages,
                    reason="context_headroom_checkpoint",
                    headroom=headroom,
                    chunk_manager=chunk_manager,
                    step_rounds=step_rounds,
                    task_rounds=task_rounds,
                )
                logging.info(
                    "N2 context headroom reached checkpoint threshold task=%s step=%s remaining=%.3f%% prompt_tokens=%s generated_tokens=%s limit=%s",
                    job.task_id, job.step_id, float(headroom.get("remaining_fraction") or 0.0) * 100.0,
                    int(headroom.get("prompt_eval_count") or 0), int(headroom.get("eval_count") or 0),
                    int(headroom.get("context_limit_tokens") or 0),
                )
                return {
                    "yielded": True,
                    "checkpoint": checkpoint,
                    "reason": "context_headroom_checkpoint",
                    "step_rounds": step_rounds,
                    "task_rounds": task_rounds,
                    "evidence": evidence,
                    "resume_metadata": resume_metadata,
                }

            raw_chunk_delivered_this_turn = False
            for call in calls:
                function = call.get("function") if isinstance(call, dict) else None
                gate_meta: dict = {}
                chunk_stream = None
                chunk_piece: int | None = None
                chunk_budget = None
                chunk_candidate = False
                precomputed_result: dict | None = None
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
                    if isinstance(name, str) and isinstance(arguments, dict) and name == "read_file" and chunk_manager.enabled:
                        requested = dict(arguments)
                        stream_id = str(requested.pop("stream_id", "") or "").strip()
                        piece_raw = requested.pop("piece", None)
                        requested.pop("retain", None)
                        if raw_chunk_delivered_this_turn:
                            precomputed_result = {
                                "ok": False,
                                "tool": "read_file",
                                "error": "only one raw evidence chunk may be delivered per N2 turn",
                                **({"stream_id": stream_id} if stream_id else {}),
                            }
                        elif stream_id:
                            chunk_stream = chunk_manager.get(stream_id)
                            if chunk_stream is None:
                                precomputed_result = {
                                    "ok": False,
                                    "tool": "read_file",
                                    "error": "unknown or expired evidence stream_id; start a fresh read_file request",
                                    "stream_id": stream_id,
                                }
                            elif raw_chunk_delivered_this_turn:
                                precomputed_result = {
                                    "ok": False,
                                    "tool": "read_file",
                                    "error": "only one raw evidence chunk may be delivered per N2 turn",
                                    "stream_id": stream_id,
                                }
                            elif piece_raw is None:
                                precomputed_result = {
                                    "ok": False,
                                    "tool": "read_file",
                                    "error": "piece is required when stream_id is supplied",
                                    "stream_id": stream_id,
                                }
                            else:
                                try:
                                    chunk_piece = int(piece_raw)
                                    piece_args = chunk_manager.piece_arguments(chunk_stream, chunk_piece)
                                except (TypeError, ValueError) as exc:
                                    precomputed_result = {
                                        "ok": False,
                                        "tool": "read_file",
                                        "error": str(exc),
                                        "stream_id": stream_id,
                                    }
                                else:
                                    requested["path"] = chunk_stream.path
                                    requested.pop("start_line", None)
                                    requested.pop("end_line", None)
                                    requested.update(piece_args)
                                    requested["mode"] = "text"
                                    requested["_norm_chunked_evidence"] = True
                                    arguments = requested
                        elif piece_raw is not None:
                            precomputed_result = {
                                "ok": False,
                                "tool": "read_file",
                                "error": "stream_id is required when requesting a numbered evidence piece",
                            }
                        else:
                            archive_action = str(requested.get("archive_action") or "auto").strip().lower()
                            mode = str(requested.get("mode") or "text").strip().lower()
                            explicit_byte_cursor = "start_byte" in requested
                            explicit_line_range = any(key in requested for key in ("start_line", "end_line"))
                            explicit_range = explicit_byte_cursor or explicit_line_range
                            chunk_candidate = mode == "text" and archive_action in {"", "auto"} and not explicit_range
                            # A direct byte-cursor read is already a bounded evidence chunk even
                            # when N2 supplied start_byte itself. Mark it so N1 uses deterministic
                            # file/fingerprint metadata instead of asking the verification model
                            # to re-canonicalize the raw chunk.
                            if (
                                explicit_byte_cursor
                                and not explicit_line_range
                                and mode == "text"
                                and archive_action in {"", "auto"}
                            ):
                                requested["_norm_chunked_evidence"] = True
                                arguments = requested
                            if chunk_candidate and raw_chunk_delivered_this_turn:
                                precomputed_result = {
                                    "ok": False,
                                    "tool": "read_file",
                                    "error": "only one raw evidence chunk may be delivered per N2 turn",
                                }
                            elif chunk_candidate:
                                resolver = getattr(self.client, "effective_context_length", None)
                                context_limit = resolver() if callable(resolver) else getattr(self.client, "context_length", None)
                                context_limit = int(context_limit or 65536)
                                chunk_budget = chunk_manager.budget(
                                    context_limit_tokens=context_limit,
                                    output_reserve_tokens=output_budget,
                                    system_instructions=str(getattr(self.client, "system_instructions", "") or ""),
                                    messages=messages,
                                    tool_schemas=native_schemas,
                                    max_chunk_bytes=getattr(tools, "read_chunk_max_bytes", None),
                                )
                                requested_max = requested.get("max_bytes")
                                adaptive_max = int(chunk_budget.raw_evidence_target_bytes)
                                if requested_max is not None:
                                    try:
                                        adaptive_max = min(adaptive_max, max(1024, int(requested_max)))
                                    except (TypeError, ValueError):
                                        pass
                                requested["start_byte"] = 0
                                requested["max_bytes"] = adaptive_max
                                requested["mode"] = "text"
                                requested["_norm_chunked_evidence"] = True
                                arguments = requested

                    if precomputed_result is not None:
                        result = precomputed_result
                    elif not isinstance(name, str) or not isinstance(arguments, dict):
                        result = {"ok": False, "error": "invalid tool name or arguments"}
                    elif turn_reset and self.n1_gatekeeper is not None:
                        # N1 judged the entire N2 turn to be a reasoning rabbit hole.
                        # Satisfy the native tool-call protocol with a blocked result,
                        # invoke nothing, then inject the reset before the next N2 turn.
                        result = {
                            "ok": False,
                            "tool": name,
                            "n1_loop_blocked": True,
                            "error": "N1 stopped this repeated reasoning/tool turn before execution; continue from existing evidence and change approach.",
                        }
                        gate_meta = {"executed": False, "reasoning_loop_blocked": True}
                    elif name in {"verification_preflight", "verification_history", "verification_checkin"}:
                        # Legacy N2-managed verification is intentionally unavailable in
                        # checkpoint 1. N1 owns this boundary now.
                        result = {
                            "ok": False,
                            "tool": name,
                            "error": "legacy verification tools are N1-owned; request the actual information tool instead",
                        }
                    elif self.n1_gatekeeper is not None:
                        outcome = self.n1_gatekeeper.before_tool(
                            task_id=job.task_id,
                            step_id=job.step_id,
                            name=name,
                            arguments=arguments,
                        )
                        arguments = outcome.arguments
                        if outcome.execute:
                            # IMPORTANT: this object is passed to N2 unchanged. N1 records
                            # verification metadata out-of-band after execution.
                            result = tools.execute(name, arguments)
                            gate_meta = self.n1_gatekeeper.after_tool(
                                task_id=job.task_id,
                                step_id=job.step_id,
                                name=name,
                                outcome=outcome,
                                result=result,
                            )
                        else:
                            result = dict(outcome.result or {
                                "ok": False, "tool": name, "error": "N1 did not execute or supply a result"
                            })
                            gate_meta = {
                                "executed": False,
                                "target": outcome.target,
                                "need": outcome.need,
                                "note": outcome.note,
                            }
                        if outcome.reset_instruction:
                            n1_interventions.append(outcome.reset_instruction)
                    else:
                        result = tools.execute(name, arguments)

                    if (
                        name == "read_file"
                        and chunk_manager.enabled
                        and isinstance(result, dict)
                        and result.get("ok")
                        and str(result.get("mode") or "") == "text"
                    ):
                        if chunk_stream is not None and chunk_piece is not None:
                            observed_fingerprint = str(result.get("source_fingerprint") or "")
                            if observed_fingerprint and observed_fingerprint != chunk_stream.source_fingerprint:
                                result = {
                                    "ok": False,
                                    "tool": "read_file",
                                    "path": chunk_stream.path,
                                    "error": "source changed while evidence stream was active; restart with a fresh read_file request",
                                    "stream_id": chunk_stream.stream_id,
                                    "expected_source_fingerprint": chunk_stream.source_fingerprint,
                                    "observed_source_fingerprint": observed_fingerprint,
                                }
                                chunk_manager.streams.pop(chunk_stream.stream_id, None)
                                chunk_stream = None
                                chunk_piece = None
                            else:
                                result = chunk_manager.wrap_result(result, chunk_stream, chunk_piece)
                                raw_chunk_delivered_this_turn = True
                                active_raw_stream_id = chunk_stream.stream_id
                                active_raw_piece = chunk_piece
                        elif (
                            chunk_candidate
                            and chunk_budget is not None
                            and (
                                bool(result.get("truncated"))
                                or int(result.get("returned_bytes") or 0) >= chunk_manager.min_stream_bytes
                            )
                        ):
                            chunk_stream = chunk_manager.create_stream(
                                path=str(result.get("path") or arguments.get("path") or ""),
                                source_fingerprint=str(result.get("source_fingerprint") or ""),
                                source_size_bytes=int(result.get("source_size_bytes") or result.get("size") or 0),
                                chunk_bytes=max(1, int(arguments.get("max_bytes") or result.get("returned_bytes") or 1)),
                                budget=chunk_budget,
                            )
                            chunk_piece = 1
                            result = chunk_manager.wrap_result(result, chunk_stream, chunk_piece)
                            raw_chunk_delivered_this_turn = True
                            active_raw_stream_id = chunk_stream.stream_id
                            active_raw_piece = chunk_piece

                evidence_item = redact({"tool": str(name), "arguments": arguments, "result": result, "n1_gatekeeper": gate_meta})
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
            if n1_interventions:
                messages.append({
                    "role": "system",
                    "content": invocation_header("n1", "n2", "loop_reset") + "\n[NORM-INTERNAL N1 LOOP RESET]\n" + "\n".join(dict.fromkeys(n1_interventions)),
                })

        evidence.extend(self._verify_pending(job, pending_verification, tools))
        if task_rounds >= task_limit:
            hard_limit = int(policy["hard_limit"])
            boundary = "hard cap" if task_limit >= hard_limit else "N1-granted cap"
            # A manual resume must start from compact *relevant* state, not from a
            # raw recent-message tail. Reuse the same N1/PostgreSQL distillation
            # path as a 5% context-headroom yield so completed work and exact open
            # work survive while the spent model context does not.
            budget_checkpoint, budget_resume_metadata = self._distilled_continuation_checkpoint(
                job, prompt, messages,
                reason="task_round_budget",
                headroom={"remaining_fraction": 0.0, "prompt_eval_count": 0, "eval_count": 0, "context_limit_tokens": 0},
                chunk_manager=chunk_manager,
                step_rounds=step_rounds,
                task_rounds=task_rounds,
            )
            metadata_now = dict(job.metadata or {})
            metadata_now.update(budget_resume_metadata)
            metadata_now["last_yield_reason"] = "task_round_budget"
            job.metadata = metadata_now
            raise TaskRoundBudgetExceeded(
                f"task tool/reasoning round budget exhausted ({task_rounds}/{task_limit}; {boundary}; hard={hard_limit}); "
                "stopped instead of requeueing the same task indefinitely",
                checkpoint=budget_checkpoint, step_rounds=step_rounds, task_rounds=task_rounds,
            )
        reason = "step_round_limit"
        checkpoint, resume_metadata = self._distilled_continuation_checkpoint(
            job, prompt, messages, reason=reason,
            headroom={"remaining_fraction": 0.0, "prompt_eval_count": 0, "eval_count": 0, "context_limit_tokens": 0},
            chunk_manager=chunk_manager, step_rounds=step_rounds, task_rounds=task_rounds,
        )
        yield_count = int((job.metadata or {}).get("continuation_count", 0)) + 1
        # At the second ordinary step yield, ask N1 whether the unresolved work
        # merits existing sequential child-task decomposition rather than another
        # restart. This is advisory, bounded, and never fabricates children by default.
        if (yield_count >= 2 and yield_count % 2 == 0
                and not (job.metadata or {}).get("stalled_decomposition_attempted")
                and int((job.metadata or {}).get("stalled_decomposition_reviews", 0)) < 2
                and not (job.metadata or {}).get("oversize_recovery_stage")
                and (job.metadata or {}).get("allow_subtask", True) is not False):
            cfg = self._runtime_config().get("worker", {}) or {}
            depth = int((job.metadata or {}).get("subtask_depth", 0))
            if depth < max(1, int(cfg.get("max_subtask_depth", 4))):
                resume_metadata["stalled_decomposition_reviews"] = int((job.metadata or {}).get("stalled_decomposition_reviews", 0)) + 1
                if self.n1_gatekeeper is not None and hasattr(self.n1_gatekeeper, "review_stalled_step"):
                    try:
                        decision = self.n1_gatekeeper.review_stalled_step(
                            task_id=job.task_id, step_id=job.step_id,
                            original_request=str((job.metadata or {}).get("original_user_prompt") or job.prompt),
                            step_request=job.prompt, checkpoint=checkpoint,
                            round_count=task_rounds,
                        )
                        if decision.get("decompose") and self.durable is not None:
                            resume_metadata["stalled_decomposition_attempted"] = True
                            resume_metadata["oversize_recovery_stage"] = "analysis_pending"
                            resume_metadata["stalled_decomposition_reason"] = str(decision.get("reason") or "")[:1200]
                            checkpoint += "\n\nN1 DECOMPOSITION DECISION: Existing sequential child-task analysis is required for the unresolved work."
                        elif decision.get("next_action"):
                            checkpoint += "\n\nN1 NEXT-ACTION CORRECTION: " + str(decision["next_action"])[:1500]
                    except Exception:
                        logging.exception("N1 stalled-step review unavailable; retain ordinary continuation")
        return {
            "yielded": True, "checkpoint": checkpoint, "reason": reason,
            "step_rounds": step_rounds, "task_rounds": task_rounds,
            "evidence": evidence, "resume_metadata": resume_metadata,
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
                is_chunked = isinstance(result.get("chunk_stream"), dict)
                keys = (
                    "ok", "path", "paths", "sha256", "source_fingerprint", "file_mutation", "created", "error", "staged", "staged_path", "note_path",
                    "attempts", "answer", "text", "summary", "analysis", "observations", "data", "items",
                    "image_count", "output_dir", "analysis_json", "geometry_overlay", "horizontal_consensus", "profile",
                    "device", "width", "height", "reconstruction_similarity", "artifact_fraction", "resource_status", "storage_context",
                    "command", "cwd", "exit_code", "stdout", "stderr", "timed_out", "timeout_seconds", "duration_seconds",
                    "returned_bytes", "start_byte", "next_byte", "truncated", "chunk_stream", "context_budget",
                    "chunk_evicted", "retained_facts", "raw_chunk_in_durable_evidence"
                )
                if not is_chunked:
                    keys = (*keys, "content")
                for key in keys:
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
            "Use verdict=accept only with no issues and repair_scope=none. "
            "Optional fulfilled_injection_ids: list ONLY ids explicitly labelled retire_when_satisfied "
            "in the active user context whose entire requested change is NOW proven satisfied by "
            "the accepted answer AND observed evidence. Omit or return [] if uncertain. "
            "A completed step does not imply all injections are fulfilled. NEVER retire ongoing "
            "restrictions or instructions about later steps. Do not redo verified work.\n\n"
            f"Complete task context:\n{original_prompt}\n\nCandidate answer:\n{answer}\n\n"
            f"Observed evidence:\n{self._evidence_text(evidence)}"
        )

    @staticmethod
    def _correction_prompt(original_prompt: str, answer: str, issues: list[str], evidence: list[dict]) -> str:
        """Constrain a verifier-requested repair to documented defects, not style."""
        if not issues:
            raise VerifierProtocolError("cannot regenerate an answer without a verifier issue")
        return (
            invocation_header("n1" if any(str(issue).startswith("N1 requirement/scope review:") for issue in issues) else "system", "n2", "verified_correction") + "\n"
            "[NORM INTERNAL VERIFIED REPAIR — NOT A NEW USER REQUEST]\n"
            "The step verifier rejected the candidate for the specific issues below. "
            "Fix those issues only; retain correct work and the existing voice and brevity. "
            "Do not restart an accepted operation or claim an action without matching tool evidence. "
            "Return only the corrected candidate answer, not a discussion of the review.\n\n"
            "Original task context:\n" + str(original_prompt)
            + "\n\nOriginal candidate (preserve unaffected wording):\n" + str(answer)
            + "\n\nVerified issues requiring correction:\n"
            + json.dumps([str(issue) for issue in issues], ensure_ascii=False)
            + "\n\nObserved tool evidence:\n"
            + PromptWorker._evidence_text(evidence)
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
            "invoke_source": "system",
            "invoke_target": "n2",
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

    def _n1_step_alignment(
        self, job: PromptJob, answer: str, evidence: list[dict], *, verified_by_n2: bool = False,
    ) -> tuple[bool, list[str]]:
        """N1 checks scope; fail-open only for a previously N2-verified candidate."""
        gate = getattr(self, "n1_gatekeeper", None)
        if gate is None or not hasattr(gate, "judge_step_answer"):
            return True, []
        meta = dict(job.metadata or {})
        expected_kind = str(meta.get("expected_result_kind") or "")
        if expected_kind == "recovery_analysis":
            try:
                analysis = self._parse_recovery_analysis(
                    answer, parent_scope=self._recovery_scope_items(job))
                validate_recovery_analysis_child(
                    self._runtime_root(), task_id=job.task_id, step_id=job.step_id,
                    instruction=job.prompt, criterion=str(meta.get("verification") or ""),
                    original_prompt=str(meta.get("original_user_prompt") or ""),
                    parent_task_id=str(meta.get("parent_task_id") or ""),
                    analysis=analysis)
            except (ValueError, RuntimeError) as exc:
                return False, [f"Wrong recovery-analysis result contract: {exc}"]
        judgment = gate.judge_step_answer(
            original_request=str(meta.get("original_user_prompt") or ""),
            task_request=str(meta.get("task_user_prompt") or meta.get("original_user_prompt") or ""),
            step_prompt=str(job.prompt or ""),
            verification_requirement=(str(meta.get("verification") or "")
                + ("\nINTERNAL CHILD CONTRACT: expected recovery_analysis JSON; original root request is reference only. Judge the structured assessment, not the root deliverable."
                   if expected_kind == "recovery_analysis" else "")),
            candidate=str(answer),
            evidence=self._evidence_text(evidence),
        )
        if judgment.get("processing_failed"):
            if not verified_by_n2:
                raise VerifierProtocolError("N1 step alignment unavailable without prior N2 verification")
            # Archive the entire known review state in the existing lossless
            # PostgreSQL context-slice store BEFORE any retry model generation.
            # Keep the small task-step checkpoint as an index, not a second
            # truncated copy of the evidence.
            durable = getattr(self, "durable", None)
            if durable is None or not hasattr(durable, "record_context_slice") or not hasattr(durable, "context_slice"):
                raise VerifierProtocolError("N1 review retry requires the existing durable context-slice store")
            prior_progress = ""
            if hasattr(durable, "completed_step_context"):
                prior_progress = durable.completed_step_context(job.task_id, job.step_id, max_chars=None)
            archived_state = [
                {"role": "user", "invoke_source": "user", "kind": "original_request",
                 "content": str(meta.get("original_user_prompt") or "")},
                {"role": "system", "invoke_source": "system", "kind": "task_and_step",
                 "content": json.dumps({
                     "primary_task": str(meta.get("task_user_prompt") or meta.get("original_user_prompt") or ""),
                     "step_prompt": str(job.prompt or ""),
                     "verification_requirement": str(meta.get("verification") or ""),
                 }, ensure_ascii=False)},
                {"role": "assistant", "invoke_source": "n2", "kind": "verified_candidate", "content": str(answer)},
                {"role": "tool", "invoke_source": "system", "kind": "observed_evidence",
                 "content": json.dumps(evidence, ensure_ascii=False, default=str)},
                {"role": "system", "invoke_source": "system", "kind": "execution_progress",
                 "content": json.dumps({"working_context": job.context, "job_metadata": meta,
                                        "previous_steps": prior_progress}, ensure_ascii=False, default=str)},
            ]
            slice_index = durable.record_context_slice(
                job.task_id, job.step_id, archived_state, reason="n1_step_review_processing_retry"
            )
            stored = durable.context_slice(job.task_id, job.step_id, slice_index)
            if not isinstance(stored, dict) or stored.get("messages") != archived_state:
                raise VerifierProtocolError("N1 retry context-slice readback did not match the archived state")
            checkpoint = {
                "kind": "n1_step_review_processing_retry",
                "n2_structured_verifier": "accepted",
                "context_slice_index": slice_index,
                "n1_first_failure": str(judgment.get("reason") or "review unavailable"),
                "attempts": 1,
            }
            durable.checkpoint_step(StepResult(
                task_id=job.task_id, step_id=job.step_id,
                name=str((job.metadata or {}).get("step_name") or job.step_id),
                status=StepStatus.RUNNING, summary=json.dumps(checkpoint, ensure_ascii=False),
                verification="n1-step-review:processing-retry",
                started_at=_utc_now(), completed_at=_utc_now(),
            ))
            # Summarize the *read-back*, not an uncommitted in-memory candidate.
            # This is deterministic and bounded: a malfunctioning N1 need not
            # perform another expensive model generation just to summarize its
            # own failure. N1 performs the substantive judgment on the fresh call.
            source = stored["messages"]
            saved_request = source[0]["content"]
            saved_scope = json.loads(source[1]["content"])
            saved_candidate = source[2]["content"]
            saved_evidence = json.loads(source[3]["content"])
            progress = json.loads(source[4]["content"])
            tool_names = [str(item.get("tool") or "") for item in saved_evidence if isinstance(item, dict)]
            handoff = (
                "This is attempt 2 of the same N1 step review after a model-processing failure. "
                "Do not repeat N2's completed work. The N2 structured verifier already accepted this exact candidate; "
                "that does NOT constitute N1 approval. Independently compare the original request, step criteria, "
                "candidate and evidence supplied above. Return ACCEPT or specific actionable RETRY instructions.\n"
                f"First review failure: {checkpoint['n1_first_failure']}\n"
                f"Lossless PostgreSQL archive: task_context_slices task_id={job.task_id} "
                f"step_id={job.step_id} slice_index={slice_index}\n"
                f"Previous-step progress summary: {str(progress.get('previous_steps') or '')[:1600]}\n"
                f"Saved working context: {str(progress.get('working_context') or '')[:1600]}\n"
                f"Observed evidence records: {len(saved_evidence)}; tool names: {', '.join(tool_names[-18:])[:850]}\n"
                f"N2 candidate: archived in full, {len(saved_candidate)} characters, unchanged."
            )
            logging.warning("N1 step review failed; checkpointed verified candidate and retrying N1 only task=%s step=%s", job.task_id, job.step_id)
            judgment = gate.judge_step_answer(
                original_request=saved_request,
                task_request=str(saved_scope["primary_task"]),
                step_prompt=str(saved_scope["step_prompt"]),
                verification_requirement=str(saved_scope["verification_requirement"]),
                candidate=saved_candidate, evidence=self._evidence_text(saved_evidence), review_handoff=handoff,
            )
            if judgment.get("processing_failed"):
                meta["n1_step_review_status"] = "unavailable_after_2_attempts"
                job.metadata = meta
                if meta.get("recovery_mode"):
                    # A recovery audit/check plan MUST be approved by N1 before
                    # N2 can act on unverified claims of surviving or missing work.
                    raise VerifierProtocolError(
                        "Recovery stage requires independent N1 acceptance; "
                        "two N1 processing failures are not approval."
                    )
                logging.warning("N1 review failed twice; passing through independently N2-verified candidate task=%s step=%s (NOT an N1 acceptance)", job.task_id, job.step_id)
                return True, []
        if str(judgment.get("decision")) == "accept":
            return True, []
        correction = str(judgment.get("retry_instruction") or "").strip()
        if not correction:
            raise VerifierProtocolError("N1 rejected a step without actionable correction feedback")
        return False, ["N1 requirement/scope review: " + correction[:1800]]

    def _verify_and_correct_without_tools(
        self, job: PromptJob, original_prompt: str, answer: str, *, system_context: str = ""
    ) -> str:
        for cycle in range(1, 4):
            accepted, issues, report = self._request_verdict(original_prompt, answer, [])
            if accepted:
                accepted, issues = self._n1_step_alignment(job, answer, [], verified_by_n2=True)
            self._record_verifier_attempt(job, cycle, answer, report, accepted)
            if accepted:
                return answer
            corrected = self._generate_complete_text(
                self._correction_prompt(original_prompt, answer, issues, []),
                think=True,
                num_predict=12000,
                temperature=0.1,
                system_context=system_context,
                job=job, stage="no-tools-verifier-correction",
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
            if accepted:
                accepted, issues = self._n1_step_alignment(job, answer, evidence, verified_by_n2=True)
            self._record_verifier_attempt(job, cycle, answer, report, accepted)
            if accepted:
                self._retire_verified_injections(job, report, evidence)
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

    def _context_headroom_state(self, response: dict) -> dict[str, float | int | bool]:
        """Use Ollama's own token counters; never rescan/re-tokenize the prompt here."""
        cfg = self._runtime_config().get("context_budget", {}) or {}
        threshold = min(0.25, max(0.0, float(cfg.get("checkpoint_remaining_fraction", 0.05))))
        resolver = getattr(self.client, "effective_context_length", None)
        limit = resolver() if callable(resolver) else getattr(self.client, "context_length", None)
        limit = max(1, int(limit or 65536))
        prompt_tokens = max(0, int(response.get("prompt_eval_count") or 0))
        generated_tokens = max(0, int(response.get("eval_count") or 0))
        # eval_count includes hidden reasoning that is not replayed into the next
        # N2 prompt. Use Ollama's authoritative prompt count plus only the new
        # visible assistant payload as a cheap forward estimate; do not rescan the
        # existing context every turn.
        chars_per_token = max(1.0, float(cfg.get("estimated_chars_per_token", 3.5)))
        visible_payload = json.dumps({
            "content": str(response.get("content") or ""),
            "tool_calls": response.get("tool_calls") or [],
        }, ensure_ascii=False, separators=(",", ":"), default=str)
        visible_tokens = max(0, int((len(visible_payload) + chars_per_token - 1) // chars_per_token))
        used = prompt_tokens + visible_tokens
        remaining = max(0, limit - used)
        remaining_fraction = remaining / limit
        return {
            "context_limit_tokens": limit,
            "prompt_eval_count": prompt_tokens,
            "eval_count": generated_tokens,
            "visible_output_estimated_tokens": visible_tokens,
            "used_tokens": used,
            "remaining_tokens": remaining,
            "remaining_fraction": remaining_fraction,
            "threshold_fraction": threshold,
            "checkpoint": bool(prompt_tokens and remaining_fraction <= threshold),
        }

    def _distilled_continuation_checkpoint(
        self,
        job: PromptJob,
        prompt: str,
        archived_messages: list[dict],
        *,
        reason: str,
        headroom: dict,
        chunk_manager: ChunkedEvidenceManager,
        step_rounds: int,
        task_rounds: int,
    ) -> tuple[str, dict]:
        """Archive lossless recent context in PostgreSQL, then ask N1 for the applicable continuation."""
        cfg = self._runtime_config().get("context_budget", {}) or {}
        tail_count = max(1, min(32, int(cfg.get("checkpoint_archive_messages", 8))))
        # Save the complete slice losslessly, not merely its last few messages.
        # Only the bounded *model view* is reduced; PostgreSQL remains recoverable.
        full_slice = list(archived_messages)
        tail = full_slice[-tail_count:]
        archive_reference = ""
        slice_index = 0
        source_messages = tail
        if self.durable is not None and hasattr(self.durable, "record_context_slice"):
            slice_index = self.durable.record_context_slice(
                job.task_id,
                job.step_id,
                full_slice,
                reason=reason,
                prompt_eval_count=int(headroom.get("prompt_eval_count") or 0),
                eval_count=int(headroom.get("eval_count") or 0),
                context_limit_tokens=int(headroom.get("context_limit_tokens") or 0),
            )
            archive_reference = (
                f"norm_runtime.task_context_slices task_id={job.task_id} "
                f"step_id={job.step_id} slice_index={slice_index}"
            )
            if hasattr(self.durable, "context_slice"):
                archived = self.durable.context_slice(job.task_id, job.step_id, slice_index)
                if isinstance(archived, dict) and isinstance(archived.get("messages"), list):
                    source_messages = list(archived["messages"])[-tail_count:]

        retained = chunk_manager.retained_summary()
        checkpoint = ""
        if self.n1_gatekeeper is not None and hasattr(self.n1_gatekeeper, "distill_continuation_checkpoint"):
            try:
                checkpoint = self.n1_gatekeeper.distill_continuation_checkpoint(
                    original_goal=str((job.metadata or {}).get("task_user_prompt") or (job.metadata or {}).get("original_user_prompt") or job.prompt),
                    step_prompt=prompt,
                    archived_messages=source_messages,
                    retained_evidence=retained,
                    archive_reference=archive_reference,
                    prior_checkpoint=str(job.context or "")[:14000],
                ).strip()
            except Exception:
                logging.exception(
                    "N1 continuation distillation failed task=%s step=%s; falling back to bounded recent context",
                    job.task_id, job.step_id,
                )
        if not checkpoint:
            checkpoint = self._continuation_checkpoint(
                job, prompt, source_messages, reason, step_rounds, task_rounds
            )
            # A failed distillation must not erase the checkpoint from a prior
            # slice. Preserve it alongside the observed new material.
            if str(job.context or "").strip():
                checkpoint += "\n\nPRIOR VERIFIED CONTINUATION STATE:\n" + str(job.context)[-14000:]
        if retained and "Retained chunk evidence:" not in checkpoint:
            checkpoint += "\n\nRetained chunk evidence:\n" + retained
        resume_metadata = {
            "chunked_evidence_streams": chunk_manager.snapshot_streams(),
            "context_checkpoint_remaining_fraction": float(headroom.get("remaining_fraction") or 0.0),
            "compact_resume_context": True,
        }
        if slice_index:
            resume_metadata["postgres_context_slice_index"] = slice_index
        return checkpoint, resume_metadata

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
            f"CONTINUATION CHECKPOINT\nThis is an internal continuation of the same step, not a new user request.\nOriginal goal: {job.prompt}"
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
        if reason != "context_headroom_checkpoint":
            # A distilled checkpoint is valid for exactly its next resume. At a
            # later ordinary step-round/length yield we have a NEW checkpoint;
            # don't carry a stale compact-only instruction into that new slice.
            metadata.pop("compact_resume_context", None)
            metadata.pop("context_checkpoint_remaining_fraction", None)
        if reason in {"output_length_checkpoint", "reasoning_output_limit", "input_context_limit"} and metadata.get("allow_oversize_recovery", True) is not False:
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
        # Finite retries exhausted. Do not rewrite the task prompt using a
        # stripped-down model call: hand off to a *recovery*-kind task after SOS.
        count = self.queue.escalate_chain(job.chain_id, job.message_id)
        logging.error("Retries exhausted; recovery successor required task=%s step=%s escalated=%s",
                      job.task_id, job.step_id, count)
        return True

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
        memory_cfg = self._fresh_memory_config()
        maint_cfg = self._runtime_config().get("maintenance", {})
        if not bool(maint_cfg.get("weekly_cleanup_enabled", True)):
            return
        parked_state = self.durable.get_runtime_state("weekly_maintenance_parked")
        if isinstance(parked_state, dict):
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
        if isinstance(marker, dict) and str(marker.get("status") or "") in {"suppressed", "requires_attention"}:
            self.durable.set_runtime_state(
                "weekly_maintenance_parked",
                {
                    "status": str(marker.get("status") or "suppressed"),
                    "run_id": str(marker.get("run_id") or ""),
                    "mode": str(marker.get("mode") or "regular"),
                    "phase": str(marker.get("phase") or ""),
                    "reason": str(marker.get("error") or marker.get("reason") or ""),
                    "parked_at": str(marker.get("suppressed_at") or marker.get("failed_at") or _utc_now().isoformat()),
                },
            )
            return
        now = _utc_now()
        reductive_days = max(1, int(memory_cfg.get("scheduled_memory_interval_days", 7)))
        destructive_days = max(reductive_days, int(memory_cfg.get("destructive_memory_interval_days", 21)))
        legacy_last = self._runtime_state_time(self.durable.get_runtime_state("last_successful_cleanup_at"))
        last_reductive = self._runtime_state_time(self.durable.get_runtime_state("last_successful_reductive_cleanup_at")) or legacy_last
        last_destructive = self._runtime_state_time(self.durable.get_runtime_state("last_successful_destructive_cleanup_at")) or legacy_last
        reductive_due = last_reductive is None or last_reductive <= now - timedelta(days=reductive_days)
        destructive_due = last_destructive is None or last_destructive <= now - timedelta(days=destructive_days)
        if marker is None and not (reductive_due or destructive_due):
            return
        resumed = marker is not None
        if marker is None:
            # Independent clocks: weekly reductive maintenance, with a destructive
            # long-cycle pass every ~3 weeks. When both are due, destructive wins
            # and also satisfies that week's reductive maintenance.
            next_mode = "destructive" if destructive_due else "regular"
            marker = {
                "status": "cleanup_started", "run_id": str(uuid.uuid4()),
                "mode": next_mode,
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
        mode = str(marker.get("mode") or "regular")
        self._idle.clear()
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
            condense_client = self._maintenance_condense_client(memory_cfg)
            maintainer = DeepHistoryMaintainer(
                self.durable, condense_client, runtime_root=self._runtime_root(), config=memory_cfg,
                queue=self.queue, drain_event=self._drain, n1_gatekeeper=self.n1_gatekeeper,
            )
            marker["phase"] = "recovery_state_cleanup"
            client.set(active_key, json.dumps(marker, sort_keys=True))
            recovery_cleanup = maintainer.cleanup_recovery_state()
            marker["phase"] = "verification_history_cleanup"
            client.set(active_key, json.dumps(marker, sort_keys=True))
            verification_history_cleanup = (
                self.durable.purge_validation_history(
                    retention_seconds=max(3600, int(self._validation_pool_config().get("history_retention_seconds", 1209600)))
                )
                if hasattr(self.durable, "purge_validation_history") else {"skipped": "unsupported"}
            )
            destructive_mode = mode in {"full", "destructive"}
            marker["phase"] = "destructive_history" if destructive_mode else "background_memory"
            client.set(active_key, json.dumps(marker, sort_keys=True))
            if destructive_mode:
                result = maintainer.run(force=True, full=True, recent_only=False)
                if result is None:
                    result = {"status": "success", "work": "no_eligible_history"}
                elif str(result.get("status", "")) not in {"success", "already_completed_before_resume"}:
                    raise RuntimeError(f"full maintenance did not complete successfully: {result}")
            else:
                summary = maintainer.rebuild_background_snapshot(incremental=True)
                result = {
                    "status": "success",
                    "background_chars": len(summary or ""),
                    "n1_maintenance_judgments": (self.n1_gatekeeper.maintenance_judgment_stats() if self.n1_gatekeeper is not None and hasattr(self.n1_gatekeeper, "maintenance_judgment_stats") else {}),
                }
            completed = _utc_now()
            self.durable.set_runtime_state("last_successful_cleanup_at", completed.isoformat())
            self.durable.set_runtime_state("last_successful_scheduled_memory_mode", mode)
            if destructive_mode:
                self.durable.set_runtime_state("last_successful_destructive_cleanup_at", completed.isoformat())
                self.durable.set_runtime_state("last_successful_reductive_cleanup_at", completed.isoformat())
            else:
                self.durable.set_runtime_state("last_successful_reductive_cleanup_at", completed.isoformat())
            self.durable.record_maintenance_note(
                "weekly_cleanup", f"{mode.capitalize()} cleanup completed successfully.",
                details={"status": "success", "mode": mode, "run_id": run_id,
                         "resumed": resumed, "temp_cleanup": temp_cleanup, "trash_purge": trash_purge, "image_analysis_purge": purge,
                         "recovery_cleanup": recovery_cleanup, "verification_history_cleanup": verification_history_cleanup, "maintenance_result": result},
            )
            client.delete(active_key)
            self.durable.delete_runtime_state("weekly_maintenance_parked")
            self._maintenance_suppress_requested.clear()
            logging.info("Weekly maintenance completed run_id=%s mode=%s temp=%s trash=%s purge=%s result=%s", run_id, mode, temp_cleanup, trash_purge, purge, result)
        except ModelGenerationCancelled as exc:
            parked = self.durable.get_runtime_state("weekly_maintenance_parked")
            if self._maintenance_suppress_requested.is_set() or isinstance(parked, dict):
                suppressed = {
                    **marker,
                    "status": "suppressed",
                    "suppressed_at": _utc_now().isoformat(),
                    "reason": str((parked or {}).get("reason") or exc or "operator suppression"),
                    "auto_resume": False,
                }
                client.set(active_key, json.dumps(suppressed, sort_keys=True))
                logging.info("Weekly maintenance suppressed run_id=%s mode=%s phase=%s", run_id, mode, marker.get("phase"))
            else:
                failed = {**marker, "status": "failed", "failed_at": _utc_now().isoformat(), "error": str(exc)[:2000]}
                client.set(active_key, json.dumps(failed, sort_keys=True))
                logging.exception("Weekly maintenance generation cancelled unexpectedly run_id=%s mode=%s; marker retained for resume", run_id, mode)
        except ModelOutputTruncated as exc:
            failed_at = _utc_now().isoformat()
            reason = (
                "Scheduled maintenance exhausted the model output budget. "
                "The checkpoint was preserved, but automatic retry is disabled until explicitly resumed."
            )
            parked = {
                "status": "requires_attention",
                "run_id": run_id,
                "mode": mode,
                "phase": str(marker.get("phase") or ""),
                "parked_at": failed_at,
                "reason": reason,
                "error": f"{type(exc).__name__}: {exc}"[:2000],
            }
            self.durable.set_runtime_state("weekly_maintenance_parked", parked)
            failed = {
                **marker,
                "status": "requires_attention",
                "failed_at": failed_at,
                "failure_kind": "model_output_truncated",
                "error": parked["error"],
                "reason": reason,
                "auto_resume": False,
            }
            client.set(active_key, json.dumps(failed, sort_keys=True))
            try:
                self.durable.record_maintenance_note(
                    "weekly_cleanup",
                    "Scheduled memory maintenance was parked after exhausting its model output budget.",
                    details=parked,
                )
            except Exception:
                logging.exception("Could not record parked weekly-maintenance note")
            logging.exception("Weekly maintenance parked after output truncation run_id=%s mode=%s", run_id, mode)
        except Exception as exc:
            failed = {**marker, "status": "failed", "failed_at": _utc_now().isoformat(), "error": str(exc)[:2000]}
            try:
                client.set(active_key, json.dumps(failed, sort_keys=True))
            except Exception:
                logging.exception("Could not update weekly maintenance failure marker")
            logging.exception("Weekly maintenance failed run_id=%s mode=%s; marker retained for resume", run_id, mode)
        finally:
            self._idle.set()

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
