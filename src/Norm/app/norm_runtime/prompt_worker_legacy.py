from __future__ import annotations

import logging
import socket
import redis
from datetime import datetime, timezone
from threading import Event, Thread
from time import monotonic

from .models import StepResult, StepStatus
from .ollama_client import OllamaClient
from .prompt_queue import PromptJob, RedisPromptQueue


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


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
    ) -> None:
        self.queue = queue
        self.client = client
        self.live = live
        self.durable = durable
        self.heartbeat_seconds = max(1, int(heartbeat_seconds))
        self.claim_idle_ms = max(1000, int(claim_idle_seconds) * 1000)
        self.poll_ms = max(100, int(poll_ms))
        self.consumer = consumer or f"{socket.gethostname()}-{id(self):x}"
        self._stop = Event()
        self._thread: Thread | None = None
        self._blank_claims = 0

    def start(self) -> Thread:
        if self._thread and self._thread.is_alive():
            return self._thread
        self._stop.clear()
        self._thread = Thread(target=self.run_forever, name="norm-prompt-worker", daemon=True)
        self._thread.start()
        return self._thread

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=timeout)

    def run_forever(self) -> None:
        logging.info("Prompt worker started consumer=%s", self.consumer)
        while not self._stop.is_set():
            try:
                self.queue.reclaim_stale()
                try:
                    claimed = self.queue.read_one(block_ms=self.poll_ms)
                except redis.TimeoutError:
                    claimed = None
                if claimed is None:
                    restored = self.queue.restore_parked_if_idle()
                    if restored:
                        logging.info("Restored parked prompts count=%s", restored)
                    continue
                redis_id, job = claimed
                if not job.prompt or not job.prompt.strip():
                    self._handle_blank(redis_id)
                    continue
                self._blank_claims = 0
                self._process(redis_id, job)
            except Exception:
                logging.exception("Prompt worker loop error")
                self._stop.wait(2)
        logging.info("Prompt worker stopped consumer=%s", self.consumer)

    def _process(self, redis_id: str, job: PromptJob) -> None:
        metadata = job.metadata or {}
        name = str(metadata.get("step_name") or job.step_id)
        if self.durable:
            try:
                ready = self.durable.dependencies_satisfied(job.task_id, job.step_id)
            except Exception as exc:
                error = f"{type(exc).__name__}: {exc}"
                self.queue.park_chain(job.chain_id, redis_id)
                logging.exception("Prompt dependency check failed task=%s step=%s", job.task_id, job.step_id)
                return
            if not ready:
                self.queue.ack(redis_id)
                self.queue.enqueue(job)
                logging.info("Prompt deferred for dependencies task=%s step=%s", job.task_id, job.step_id)
                self._stop.wait(0.25)
                return
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
        try:
            prompt = job.prompt
            if job.context.strip():
                prompt = f"{prompt}\n\nContext from prior completed work:\n{job.context}"
            answer = self.client.generate(
                prompt,
                think=bool(metadata.get("think", False)),
                num_predict=metadata.get("num_predict"),
                temperature=float(metadata.get("temperature", 0.2)),
            )
            result = StepResult(
                task_id=job.task_id,
                step_id=job.step_id,
                name=name,
                status=StepStatus.COMPLETED,
                summary=answer,
                verification=str(metadata.get("verification") or ""),
                started_at=started,
                completed_at=_utc_now(),
            )
            task_finished = False
            if self.durable:
                self.durable.checkpoint_step(result)
                task_finished = self.durable.all_steps_completed(job.task_id)
                if task_finished:
                    self.durable.save_summary(job.task_id, result.summary, final=True)
            self.live.step_completed(
                job.task_id,
                job.step_id,
                result.summary,
                result.verification,
            )
            if task_finished:
                self.live.finish(job.task_id, result.summary)
            self.queue.ack(redis_id)
            logging.info("Prompt completed task=%s step=%s", job.task_id, job.step_id)
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            result = StepResult(
                task_id=job.task_id,
                step_id=job.step_id,
                name=name,
                status=StepStatus.FAILED,
                summary="Step failed",
                error=error,
                started_at=started,
                completed_at=_utc_now(),
            )
            if self.durable:
                try:
                    self.durable.checkpoint_step(result)
                except Exception:
                    logging.exception(
                        "Failed to checkpoint prompt failure task=%s step=%s",
                        job.task_id,
                        job.step_id,
                    )
            try:
                self.live.step_failed(job.task_id, job.step_id, error)
            finally:
                self._recover_or_escalate(redis_id, job, error)
            logging.exception("Prompt failed task=%s step=%s", job.task_id, job.step_id)
        finally:
            heartbeat_stop.set()
            heartbeat.join(timeout=1)

    def _recover_or_escalate(self, redis_id: str, job: PromptJob, error: str) -> None:
        parked = self.queue.park_chain(job.chain_id, redis_id)
        if not parked:
            logging.error("Failed prompt was not parked task=%s step=%s", job.task_id, job.step_id)
            return
        next_attempt = job.attempt + 1
        if next_attempt < self.queue.max_attempts:
            logging.info(
                "Prompt parked for retry task=%s step=%s attempt=%s/%s",
                job.task_id,
                job.step_id,
                next_attempt,
                self.queue.max_attempts,
            )
            return
        if job.recovery_attempted:
            count = self.queue.escalate_chain(job.chain_id, job.message_id)
            logging.error("Recovery failed; escalated prompts count=%s", count)
            return
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
        else:
            logging.info("Norm revised parked prompt for one recovery attempt")

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
