"""prompt_queue.py - Redis Streams queue for prompt jobs with chain management."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional, Tuple

import redis


REQUEST_TYPES = frozenset({
    'prompt', 'internal_instruction', 'internal_direction', 'straightforward_direction',
    'simple_task', 'information_retrieval', 'task_step', 'append', 'deferred_append_plan',
    'step', 'task', 'maintenance', 'conclusion', 'final_verification',
})


def normalize_request_type(value: str | None) -> str:
    raw = str(value or 'step').strip().lower().replace('-', '_').replace(' ', '_')
    aliases = {
        'internal_instructions': 'internal_instruction',
        'straightforward_directions': 'straightforward_direction',
        'simple': 'simple_task',
        'retrieval': 'information_retrieval',
        'info_retrieval': 'information_retrieval',
        'taskstep': 'task_step',
    }
    raw = aliases.get(raw, raw)
    if raw not in REQUEST_TYPES:
        raise ValueError(f'unsupported request_type: {value!r}')
    return raw


@dataclass
class PromptJob:
    message_id: str
    task_id: str
    step_id: str
    prompt: str
    project_id: str
    context: str
    attempt: int
    created_at: float
    metadata: Dict[str, Any]
    chain_id: str
    previous_prompt_id: str
    next_prompt_id: str
    chain_index: int
    recovery_attempted: bool
    request_type: str = 'step'
    task_uuid: str = ''
    node_id: str = ''
    chain_uuid: str = ''
    previous_node_id: str = ''
    next_node_id: str = ''


class RedisPromptQueue:
    def __init__(
        self,
        redis_client: redis.Redis,
        stream: str,
        group: str,
        retry_stream: str,
        escalation_stream: str,
        dead_letter_stream: str,
        consumer: str = "default",
        stale_ms: int = 60_000,
        max_attempts: int = 3,
    ) -> None:
        self.r = redis_client
        self.stream = stream
        self.group = group
        self.retry_stream = retry_stream
        self.escalation_stream = escalation_stream
        self.dead_letter_stream = dead_letter_stream
        self.consumer = consumer
        self.stale_ms = stale_ms
        self.max_attempts = max_attempts

    # ------------------------------------------------------------------
    # Group management
    # ------------------------------------------------------------------
    def ensure_group(self) -> None:
        try:
            self.r.xgroup_create(self.stream, self.group, id="0", mkstream=True)
        except redis.ResponseError as e:
            if "BUSYGROUP" not in str(e):
                raise

    # ------------------------------------------------------------------
    # Enqueue
    # ------------------------------------------------------------------
    def _job_to_fields(self, job: PromptJob) -> Dict[str, str]:
        d = asdict(job)
        d['request_type'] = normalize_request_type(job.request_type)
        d["metadata"] = json.dumps(d["metadata"])
        return {k: str(v) for k, v in d.items()}

    def enqueue(self, job: PromptJob) -> str:
        if not job.prompt or not job.prompt.strip():
            raise ValueError("prompt must not be blank")
        fields = self._job_to_fields(job)
        msg_id = self.r.xadd(self.stream, fields)
        return msg_id

    def contains_message_id(self, message_id: str) -> bool:
        target = str(message_id or "")
        if not target:
            return False
        for stream in (self.stream, self.retry_stream, self.escalation_stream, self.dead_letter_stream):
            for _, fields in self.r.xrange(stream, min="-", max="+"):
                if str(fields.get("message_id") or "") == target:
                    return True
        return False

    def enqueue_chain(self, jobs: List[PromptJob]) -> List[str]:
        if not jobs:
            return []
        chain_ids = {job.chain_uuid or job.chain_id for job in jobs}
        if len(chain_ids) != 1 or not next(iter(chain_ids)):
            raise ValueError("all chained jobs must share one non-empty UUID chain identity")
        node_ids = [job.node_id or job.message_id for job in jobs]
        if any(not node_id for node_id in node_ids) or len(set(node_ids)) != len(node_ids):
            raise ValueError("every chained job requires a unique non-empty UUID node identity")
        for i, job in enumerate(jobs):
            job.chain_uuid = next(iter(chain_ids))
            job.chain_id = job.chain_uuid
            job.node_id = node_ids[i]
            job.message_id = job.node_id
            job.previous_node_id = node_ids[i - 1] if i > 0 else ""
            job.next_node_id = node_ids[i + 1] if i < len(jobs) - 1 else ""
            job.previous_prompt_id = job.previous_node_id
            job.next_prompt_id = job.next_node_id
            job.chain_index = i
        pipe = self.r.pipeline(transaction=True)
        for job in jobs:
            pipe.xadd(self.stream, self._job_to_fields(job))
        redis_ids = list(pipe.execute())
        try:
            for i, (redis_id, expected) in enumerate(zip(redis_ids, jobs)):
                entries = self.r.xrange(self.stream, min=redis_id, max=redis_id)
                if len(entries) != 1:
                    raise RuntimeError(f"chain verification could not read node {expected.message_id}")
                _, fields = entries[0]
                observed = self._fields_to_job(redis_id, fields)
                expected_prev = jobs[i - 1].node_id if i > 0 else ""
                expected_next = jobs[i + 1].node_id if i < len(jobs) - 1 else ""
                if observed.chain_uuid != expected.chain_uuid or observed.node_id != expected.node_id or observed.chain_index != i or observed.previous_node_id != expected_prev or observed.next_node_id != expected_next or observed.request_type != normalize_request_type(expected.request_type):
                    raise RuntimeError(f"Redis chain-link verification failed at {expected.message_id}")
        except Exception:
            if redis_ids:
                self.r.xdel(self.stream, *redis_ids)
            raise
        return redis_ids

    def replace_claimed_with_chain(self, redis_id: str, jobs: List[PromptJob]) -> List[str]:
        """Atomically replace one claimed control job with a verified execution chain."""
        if not jobs:
            raise ValueError("replacement chain must not be empty")
        chain_ids = {job.chain_uuid or job.chain_id for job in jobs}
        if len(chain_ids) != 1 or not next(iter(chain_ids)):
            raise ValueError("all replacement jobs must share one non-empty UUID chain identity")
        node_ids = [job.node_id or job.message_id for job in jobs]
        if any(not node_id for node_id in node_ids) or len(set(node_ids)) != len(node_ids):
            raise ValueError("every replacement job requires a unique non-empty UUID node identity")
        for i, job in enumerate(jobs):
            job.chain_uuid = next(iter(chain_ids))
            job.chain_id = job.chain_uuid
            job.node_id = node_ids[i]
            job.message_id = job.node_id
            job.previous_node_id = node_ids[i - 1] if i > 0 else ""
            job.next_node_id = node_ids[i + 1] if i < len(jobs) - 1 else ""
            job.previous_prompt_id = job.previous_node_id
            job.next_prompt_id = job.next_node_id
            job.chain_index = i
        pipe = self.r.pipeline(transaction=True)
        for job in jobs:
            pipe.xadd(self.stream, self._job_to_fields(job))
        pipe.xack(self.stream, self.group, redis_id)
        pipe.xdel(self.stream, redis_id)
        results = list(pipe.execute())
        redis_ids = results[:len(jobs)]
        return redis_ids

    # ------------------------------------------------------------------
    # Read / Ack
    # ------------------------------------------------------------------
    def read_one(self, block_ms: int = 0) -> Optional[Tuple[str, PromptJob]]:
        resp = self.r.xreadgroup(
            self.group,
            self.consumer,
            {self.stream: ">"},
            count=1,
            block=block_ms,
        )
        if not resp:
            return None
        stream_name, entries = resp[0]
        if not entries:
            return None
        msg_id, raw_fields = entries[0]
        job = self._fields_to_job(msg_id, raw_fields)
        return msg_id, job

    def ack(self, message_id: str) -> None:
        self.r.xack(self.stream, self.group, message_id)
        self.r.xdel(self.stream, message_id)

    # ------------------------------------------------------------------
    # Stale reclaim
    # ------------------------------------------------------------------
    def reclaim_stale(self) -> int:
        # Get pending entries older than stale_ms
        pending = self.r.xpending_range(
            self.stream, self.group, min="-", max="+", count=100
        )
        reclaimed = 0
        now = time.time()
        for entry in pending:
            idle_ms = entry.get("time_since_delivered", 0)
            if idle_ms >= self.stale_ms:
                msg_id = entry["message_id"]
                # Re-add to stream head
                self.r.xack(self.stream, self.group, msg_id)
                # Re-enqueue by reading the entry
                entries = self.r.xrange(self.stream, min=msg_id, max=msg_id)
                if entries:
                    _, fields = entries[0]
                    job = self._fields_to_job(msg_id, fields)
                    job.attempt += 1
                    self.r.xdel(self.stream, msg_id)
                    self.enqueue(job)
                reclaimed += 1
        return reclaimed

    # ------------------------------------------------------------------
    # Stats
    # ------------------------------------------------------------------
    def stats(self) -> Dict[str, Any]:
        length = self.r.xlen(self.stream)
        pending = self.r.xpending(self.stream, self.group)
        return {
            "stream_length": length,
            "pending_count": pending.get("pending", 0),
            "group": self.group,
            "stream": self.stream,
        }

    # ------------------------------------------------------------------
    # Park chain
    # ------------------------------------------------------------------
    def park_chain(self, chain_id: str, failed_message_id: str) -> int:
        if not chain_id:
            entries = self.r.xrange(self.stream, min=failed_message_id, max=failed_message_id)
            if not entries:
                return 0
            _, fields = entries[0]
            job = self._fields_to_job(failed_message_id, fields)
            job.attempt += 1
            self.r.xadd(self.retry_stream, self._job_to_fields(job))
            self.r.xack(self.stream, self.group, failed_message_id)
            self.r.xdel(self.stream, failed_message_id)
            return 1

        entries = self.r.xrange(self.stream, min=failed_message_id, max=failed_message_id)
        if not entries:
            return 0
        _, failed_fields = entries[0]
        failed_job = self._fields_to_job(failed_message_id, failed_fields)
        failed_index = failed_job.chain_index

        all_entries = self.r.xrange(self.stream, min="-", max="+")
        parked = 0
        for msg_id, fields in all_entries:
            job = self._fields_to_job(msg_id, fields)
            if job.chain_id != chain_id:
                continue
            if job.chain_index >= failed_index:
                if msg_id == failed_message_id:
                    job.attempt += 1
                self.r.xadd(self.retry_stream, self._job_to_fields(job))
                self.r.xack(self.stream, self.group, msg_id)
                self.r.xdel(self.stream, msg_id)
                parked += 1
        return parked

    def park_chain_for_dependencies(self, redis_id: str, job: PromptJob) -> int:
        """Park this step and all later nodes in its chain without counting a retry attempt."""
        entries: list[tuple[str, PromptJob]] = [(redis_id, job)]
        if job.chain_id:
            for msg_id, fields in self.r.xrange(self.stream, min="-", max="+"):
                if msg_id == redis_id:
                    continue
                queued = self._fields_to_job(msg_id, fields)
                if queued.chain_id == job.chain_id and queued.chain_index > job.chain_index:
                    entries.append((msg_id, queued))
        entries.sort(key=lambda item: item[1].chain_index)
        pipe = self.r.pipeline(transaction=True)
        for _, queued in entries:
            pipe.xadd(self.retry_stream, self._job_to_fields(queued))
        ids = [msg_id for msg_id, _ in entries]
        if ids:
            pipe.xack(self.stream, self.group, *ids)
            pipe.xdel(self.stream, *ids)
        pipe.execute()
        return len(entries)

    def park_chain_for_child(self, redis_id: str, job: PromptJob, child_task_id: str) -> int:
        child_task_id = str(child_task_id or "").strip()
        if not child_task_id:
            raise ValueError("child_task_id must not be blank")
        metadata = dict(job.metadata or {})
        metadata["waiting_child_task_id"] = child_task_id
        metadata["waiting_child_since"] = time.time()
        job.metadata = metadata
        entries: list[tuple[str, PromptJob]] = [(redis_id, job)]
        if job.chain_id:
            for msg_id, fields in self.r.xrange(self.stream, min="-", max="+"):
                if msg_id == redis_id:
                    continue
                queued = self._fields_to_job(msg_id, fields)
                if queued.chain_id == job.chain_id and queued.chain_index > job.chain_index:
                    entries.append((msg_id, queued))
        entries.sort(key=lambda item: item[1].chain_index)
        pipe = self.r.pipeline(transaction=True)
        for _, queued in entries:
            pipe.xadd(self.retry_stream, self._job_to_fields(queued))
        ids = [msg_id for msg_id, _ in entries]
        if ids:
            pipe.xack(self.stream, self.group, *ids)
            pipe.xdel(self.stream, *ids)
        pipe.execute()
        return len(entries)

    def restore_parked_if_idle(self) -> int:
        """Restore oldest parked chain only when normal work and pending are empty."""
        normal_len = self.r.xlen(self.stream)
        pending = self.r.xpending(self.stream, self.group)
        pending_count = pending.get("pending", 0)
        if normal_len > 0 or pending_count > 0:
                return 0

        # Find oldest parked chain
        retry_entries = self.r.xrange(self.retry_stream, min="-", max="+")
        if not retry_entries:
                return 0

        # Group by chain_id, find oldest
        chain_map: Dict[str, List[Tuple[str, PromptJob]]] = {}
        for msg_id, fields in retry_entries:
                job = self._fields_to_job(msg_id, fields)
                chain_map.setdefault(job.chain_id, []).append((msg_id, job))

        if not chain_map:
                return 0

        # Oldest chain = first one encountered (streams are ordered)
        oldest_chain_id = next(iter(chain_map))

        if not oldest_chain_id:
                # Empty chain_id: restore only the oldest entry
                oldest_entry = chain_map[oldest_chain_id][0]
                msg_id, job = oldest_entry
                self.r.xadd(self.stream, self._job_to_fields(job))
                self.r.xdel(self.retry_stream, msg_id)
                return 1
        else:
                # Non-empty chain_id: restore all entries with that chain_id, sorted by chain_index
                entries = chain_map[oldest_chain_id]
                entries.sort(key=lambda x: x[1].chain_index)
                restored = 0
                for msg_id, job in entries:
                    self.r.xadd(self.stream, self._job_to_fields(job))
                    self.r.xdel(self.retry_stream, msg_id)
                    restored += 1
                return restored

    def revise_retry(
        self,
        chain_id: str,
        message_id: str,
        revised_prompt: str,
        *,
        recovery_context: str = "",
    ) -> int:
        """Revise exactly the failed parked job for one recovery attempt."""
        if not revised_prompt or not revised_prompt.strip():
            raise ValueError("revised_prompt must not be blank")
        revised = 0
        for retry_id, fields in self.r.xrange(self.retry_stream, min="-", max="+"):
            job = self._fields_to_job(retry_id, fields)
            if chain_id:
                matches = job.chain_id == chain_id and job.message_id == message_id
            else:
                matches = not job.chain_id and job.message_id == message_id
            if not matches:
                continue
            job.prompt = revised_prompt.strip()
            if recovery_context.strip():
                job.context = (
                    f"{job.context}\n\nRecovery context:\n{recovery_context.strip()}"
                    if job.context.strip()
                    else f"Recovery context:\n{recovery_context.strip()}"
                )
            job.recovery_attempted = True
            self.r.xadd(self.retry_stream, self._job_to_fields(job))
            self.r.xdel(self.retry_stream, retry_id)
            revised += 1
        return revised

    def escalate_chain(self, chain_id: str, message_id: Optional[str] = None) -> int:
        """Move matched retry entries to escalation stream."""
        retry_entries = self.r.xrange(self.retry_stream, min="-", max="+")
        escalated = 0
        for msg_id, fields in retry_entries:
            job = self._fields_to_job(msg_id, fields)
            if chain_id:
                if job.chain_id != chain_id:
                    continue
            else:
                if not message_id or job.chain_id or job.message_id != message_id:
                    continue
            self.r.xadd(self.escalation_stream, self._job_to_fields(job))
            self.r.xdel(self.retry_stream, msg_id)
            escalated += 1
        return escalated

    def _task_round_key(self, task_id: str) -> str:
        return f"{self.stream}:task-rounds:{task_id}"

    def task_rounds(self, task_id: str) -> int:
        value = self.r.get(self._task_round_key(task_id))
        return int(value or 0)

    def add_task_rounds(self, task_id: str, rounds: int = 1) -> int:
        if rounds < 0:
            raise ValueError("rounds must be non-negative")
        return int(self.r.incrby(self._task_round_key(task_id), int(rounds)))

    def reset_task_rounds(self, task_id: str) -> None:
        self.r.delete(self._task_round_key(task_id))

    def yield_job(self, redis_id: str, job: PromptJob, checkpoint: str, *, reason: str, step_rounds: int, task_rounds: int) -> str:
        """Yield current node and its chain tail behind unrelated work, without failure."""
        if not checkpoint or not checkpoint.strip():
            raise ValueError("checkpoint must not be blank")
        metadata = dict(job.metadata or {})
        metadata["continuation_count"] = int(metadata.get("continuation_count", 0)) + 1
        metadata["last_yield_reason"] = str(reason)
        metadata["last_step_rounds"] = int(step_rounds)
        metadata["last_task_rounds"] = int(task_rounds)
        job.metadata = metadata
        job.context = checkpoint.strip()
        job.created_at = time.time()

        tail = []
        if job.chain_id:
            for msg_id, fields in self.r.xrange(self.stream, min="-", max="+"):
                if msg_id == redis_id:
                    continue
                queued = self._fields_to_job(msg_id, fields)
                if queued.chain_id == job.chain_id and queued.chain_index > job.chain_index:
                    tail.append((msg_id, queued))
            tail.sort(key=lambda item: item[1].chain_index)

        # Replace the claimed job and its chain tail atomically. This matters for
        # stop-all-now: a process exit between deleting the claimed entry and
        # re-enqueueing it would otherwise leave PostgreSQL marked running with
        # no recoverable Redis work.
        pipe = self.r.pipeline(transaction=True)
        pipe.xadd(self.stream, self._job_to_fields(job))
        pipe.xack(self.stream, self.group, redis_id)
        pipe.xdel(self.stream, redis_id)
        for msg_id, queued in tail:
            pipe.xack(self.stream, self.group, msg_id)
            pipe.xdel(self.stream, msg_id)
            pipe.xadd(self.stream, self._job_to_fields(queued))
        results = list(pipe.execute())
        return str(results[0])

    # ------------------------------------------------------------------
    # Dead letter
    # ------------------------------------------------------------------
    def dead_letter(self, message_id: str) -> None:
        """Move a single message to the dead letter stream."""
        entries = self.r.xrange(self.stream, min=message_id, max=message_id)
        if not entries:
            return
        _, fields = entries[0]
        job = self._fields_to_job(message_id, fields)
        self.r.xadd(self.dead_letter_stream, self._job_to_fields(job))
        self.r.xack(self.stream, self.group, message_id)
        self.r.xdel(self.stream, message_id)

    # ------------------------------------------------------------------
    # Inspect
    # ------------------------------------------------------------------
    def inspect_queue(self, count: int = 10) -> Dict[str, List[Dict[str, Any]]]:
        work_entries = self.r.xrange(self.stream, min="-", max="+", count=count)
        retry_entries = self.r.xrange(self.retry_stream, min="-", max="+", count=count)
        work_result = []
        for msg_id, fields in work_entries:
            job = self._fields_to_job(msg_id, fields)
            work_result.append(asdict(job))
        retry_result = []
        for msg_id, fields in retry_entries:
            job = self._fields_to_job(msg_id, fields)
            retry_result.append(asdict(job))
        return {"work": work_result, "retry": retry_result}

    def task_ids(self) -> set[str]:
        ids: set[str] = set()
        for stream in (self.stream, self.retry_stream, self.escalation_stream, self.dead_letter_stream):
            for _, fields in self.r.xrange(stream, min="-", max="+"):
                task_id = str(fields.get("task_id") or "").strip()
                if task_id:
                    ids.add(task_id)
        prefix = f"{self.stream}:task-rounds:"
        for key in self.r.scan_iter(match=prefix + "*"):
            text = key.decode() if isinstance(key, bytes) else str(key)
            task_id = text[len(prefix):]
            if task_id:
                ids.add(task_id)
        return ids

    def task_locations(self, task_id: str) -> dict[str, int]:
        counts = {"work": 0, "retry": 0, "escalation": 0, "dead": 0, "pending": 0}
        streams = ((self.stream, "work"), (self.retry_stream, "retry"),
                   (self.escalation_stream, "escalation"), (self.dead_letter_stream, "dead"))
        for stream, label in streams:
            counts[label] = sum(1 for _, fields in self.r.xrange(stream, min="-", max="+")
                                if str(fields.get("task_id") or "") == task_id)
        pending = self.r.xpending_range(self.stream, self.group, min="-", max="+", count=1000)
        pending_ids = {entry["message_id"] for entry in pending}
        counts["pending"] = sum(1 for msg_id, fields in self.r.xrange(self.stream, min="-", max="+")
                                if msg_id in pending_ids and str(fields.get("task_id") or "") == task_id)
        return counts

    def oldest_task_id(self) -> str | None:
        for stream in (self.stream, self.retry_stream, self.escalation_stream):
            for _, fields in self.r.xrange(stream, min="-", max="+", count=1):
                task_id = str(fields.get("task_id") or "").strip()
                if task_id:
                    return task_id
        return None

    def snapshot_task_jobs(self, task_id: str) -> dict:
        jobs = []
        for stream, label in ((self.stream, "work"), (self.retry_stream, "retry"), (self.escalation_stream, "escalation")):
            for redis_id, fields in self.r.xrange(stream, min="-", max="+"):
                if str(fields.get("task_id") or "") != task_id:
                    continue
                job = self._fields_to_job(redis_id, fields)
                jobs.append({"stream": label, "redis_id": str(redis_id), "job": asdict(job)})
        return {"jobs": jobs, "captured_at": time.time()}

    def restore_task_jobs(self, payload: dict) -> list[str]:
        """Restore a captured suppression snapshot without changing stream semantics.

        Legacy payloads contain ``jobs`` directly. Tree-aware v2 payloads contain
        per-task queue snapshots under ``tasks``. A single Redis transaction restores
        all entries so a failed resume cannot leave only part of a task tree queued.
        """
        payload = dict(payload or {})
        raw: list[dict] = []
        task_items = payload.get("tasks")
        if isinstance(task_items, list):
            # Deepest tasks first. If both a child and its waiting parent were parked
            # in retry, this lets the child become runnable before the parent chain.
            ordered = sorted(
                (item for item in task_items if isinstance(item, dict)),
                key=lambda item: int(item.get("task_depth") or 0),
                reverse=True,
            )
            for task_item in ordered:
                queue_payload = task_item.get("queue") if isinstance(task_item.get("queue"), dict) else {}
                raw.extend(item for item in list(queue_payload.get("jobs") or []) if isinstance(item, dict))
        else:
            raw = [item for item in list(payload.get("jobs") or []) if isinstance(item, dict)]

        destinations = {
            "work": self.stream,
            "retry": self.retry_stream,
            "escalation": self.escalation_stream,
        }
        restored: list[tuple[str, PromptJob]] = []
        seen: set[str] = set()
        for item in raw:
            data = dict(item.get("job") or {})
            message_id = str(data.get("message_id") or "")
            if not message_id or message_id in seen:
                continue
            seen.add(message_id)
            if self.contains_message_id(message_id):
                continue
            label = str(item.get("stream") or "work").strip().lower()
            if label not in destinations:
                raise ValueError(f"unsupported suppression snapshot stream: {label!r}")
            restored.append((label, PromptJob(**data)))

        if not restored:
            return []
        pipe = self.r.pipeline(transaction=True)
        for label, job in restored:
            pipe.xadd(destinations[label], self._job_to_fields(job))
        return [str(value) for value in pipe.execute()]

    def requeue_pending_on_startup(self) -> int:
        pending = self.r.xpending_range(self.stream, self.group, min="-", max="+", count=1000)
        moved = 0
        for entry in pending:
            msg_id = entry["message_id"]
            rows = self.r.xrange(self.stream, min=msg_id, max=msg_id)
            if not rows:
                self.r.xack(self.stream, self.group, msg_id)
                continue
            _, fields = rows[0]
            job = self._fields_to_job(msg_id, fields)
            job.created_at = time.time()
            self.r.xack(self.stream, self.group, msg_id)
            self.r.xdel(self.stream, msg_id)
            self.enqueue(job)
            moved += 1
        return moved

    def cleanup_task(self, task_id: str) -> dict[str, int]:
        counts = {"work": 0, "retry": 0, "escalation": 0, "dead": 0, "round_key": 0}
        streams = ((self.stream, "work"), (self.retry_stream, "retry"),
                   (self.escalation_stream, "escalation"), (self.dead_letter_stream, "dead"))
        for stream, label in streams:
            matched = [msg_id for msg_id, fields in self.r.xrange(stream, min="-", max="+")
                       if str(fields.get("task_id") or "") == task_id]
            if matched:
                if stream == self.stream:
                    self.r.xack(self.stream, self.group, *matched)
                counts[label] += int(self.r.xdel(stream, *matched))
        counts["round_key"] = int(self.r.delete(self._task_round_key(task_id)))
        return counts

    # ------------------------------------------------------------------
    # Delete blank artifacts
    # ------------------------------------------------------------------
    def delete_blank_artifacts(self) -> int:
        """Remove entries with blank prompts from the stream."""
        deleted = 0
        # Work stream
        entries = self.r.xrange(self.stream, min="-", max="+")
        for msg_id, fields in entries:
            prompt = fields.get("prompt", "")
            if not prompt or not prompt.strip():
                self.r.xack(self.stream, self.group, msg_id)
                self.r.xdel(self.stream, msg_id)
                deleted += 1
        # Retry stream
        retry_entries = self.r.xrange(self.retry_stream, min="-", max="+")
        for msg_id, fields in retry_entries:
            prompt = fields.get("prompt", "")
            if not prompt or not prompt.strip():
                self.r.xdel(self.retry_stream, msg_id)
                deleted += 1
        return deleted

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _fields_to_job(self, message_id: str, fields: Dict[str, str]) -> PromptJob:
        metadata = json.loads(fields.get("metadata", "{}"))
        return PromptJob(
            message_id=fields.get("message_id", message_id),
            task_id=fields.get("task_id", ""),
            step_id=fields.get("step_id", ""),
            prompt=fields.get("prompt", ""),
            project_id=fields.get("project_id", ""),
            context=fields.get("context", ""),
            attempt=int(fields.get("attempt", 0)),
            created_at=float(fields.get("created_at", 0.0)),
            metadata=metadata,
            chain_id=fields.get("chain_id", ""),
            previous_prompt_id=fields.get("previous_prompt_id", ""),
            next_prompt_id=fields.get("next_prompt_id", ""),
            chain_index=int(fields.get("chain_index", 0)),
            recovery_attempted=fields.get("recovery_attempted", "False") == "True",
            request_type=normalize_request_type(fields.get('request_type', 'step')),
            task_uuid=fields.get("task_uuid", ""),
            node_id=fields.get("node_id", fields.get("message_id", message_id)),
            chain_uuid=fields.get("chain_uuid", fields.get("chain_id", "")),
            previous_node_id=fields.get("previous_node_id", fields.get("previous_prompt_id", "")),
            next_node_id=fields.get("next_node_id", fields.get("next_prompt_id", "")),
        )
