"""prompt_queue.py - Redis Streams queue for prompt jobs with chain management."""

from __future__ import annotations

import json
import logging
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
    original_user_prompt: str = ''

    def __post_init__(self) -> None:
        """Normalize original user intent from legacy metadata without inventing it."""
        if not isinstance(self.metadata, dict):
            raise ValueError("PromptJob.metadata must be a dictionary")
        stored = str(self.metadata.get("original_user_prompt") or "")
        explicit = str(self.original_user_prompt or "")
        if explicit and stored and explicit != stored:
            raise ValueError("PromptJob original user prompt disagrees with metadata")
        self.original_user_prompt = explicit or stored
        if self.original_user_prompt:
            self.metadata["original_user_prompt"] = self.original_user_prompt


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
        durable=None,
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
        self.durable = durable

    def _task_suppressed(self, task_id: str) -> bool:
        """Return True if the durable log marks this task's tree as suppressed."""
        if self.durable is None:
            return False
        try:
            return bool(self.durable.is_task_tree_suppressed(task_id))
        except Exception:
            return False

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
        # The first-class field and the legacy metadata view must agree on
        # every Redis handoff, even if another worker changed metadata in-place.
        metadata_prompt = str((job.metadata or {}).get("original_user_prompt") or "")
        explicit_prompt = str(job.original_user_prompt or "")
        if metadata_prompt and explicit_prompt and metadata_prompt != explicit_prompt:
            raise ValueError("Conflicting original user prompts at Redis boundary")
        resolved_prompt = metadata_prompt or explicit_prompt
        if resolved_prompt:
            job.original_user_prompt = resolved_prompt
            job.metadata["original_user_prompt"] = resolved_prompt
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

    def ack_preserve(self, message_id: str) -> None:
        """Ack a message (remove from PEL) without deleting it from the stream.

        The entry remains in Redis for inspection and persistence until
        explicit /flush-queue or Redis shutdown removes it.
        """
        self.r.xack(self.stream, self.group, message_id)

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
                if self._task_suppressed(job.task_id):
                    return 0
                self.r.xadd(self.stream, self._job_to_fields(job))
                self.r.xdel(self.retry_stream, msg_id)
                return 1
        else:
                # Non-empty chain_id: restore all entries with that chain_id, sorted by chain_index
                entries = chain_map[oldest_chain_id]
                entries.sort(key=lambda x: x[1].chain_index)
                restored = 0
                for msg_id, job in entries:
                    if self._task_suppressed(job.task_id):
                        continue
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

    def _task_round_budget_key(self, task_id: str) -> str:
        return f"{self.stream}:task-round-budget:{task_id}"

    def task_rounds(self, task_id: str) -> int:
        value = self.r.get(self._task_round_key(task_id))
        return int(value or 0)

    def add_task_rounds(self, task_id: str, rounds: int = 1) -> int:
        if rounds < 0:
            raise ValueError("rounds must be non-negative")
        return int(self.r.incrby(self._task_round_key(task_id), int(rounds)))

    def task_round_budget_state(
        self, task_id: str, *, base_limit: int = 128, first_review: int = 96
    ) -> dict[str, Any]:
        raw = self.r.get(self._task_round_budget_key(task_id))
        state: dict[str, Any] = {}
        if raw:
            try:
                parsed = json.loads(raw)
                if isinstance(parsed, dict):
                    state = parsed
            except (TypeError, ValueError, json.JSONDecodeError):
                state = {}
        state.setdefault("granted_limit", int(base_limit))
        state.setdefault("next_review", int(first_review))
        state.setdefault("reviews", 0)
        state.setdefault("denied", False)
        state.setdefault("last_review_round", 0)
        return state

    def set_task_round_budget_state(self, task_id: str, state: dict[str, Any]) -> None:
        self.r.set(self._task_round_budget_key(task_id), json.dumps(dict(state or {}), ensure_ascii=False, sort_keys=True))

    def reset_task_round_budget(self, task_id: str) -> None:
        self.r.delete(self._task_round_budget_key(task_id))

    def reset_task_rounds(self, task_id: str) -> None:
        self.r.delete(self._task_round_key(task_id))
        self.reset_task_round_budget(task_id)

    def _async_delayed_key(self) -> str:
        return f"{self.stream}:celery-delayed"

    def _approval_wait_key(self) -> str:
        return self.stream + ":awaiting-user-approval"

    def park_for_user_approval(self, redis_id: str, job: PromptJob, checkpoint: str) -> None:
        """Atomically park the active step and later chain steps until user context arrives."""
        if not checkpoint.strip():
            raise ValueError("Approval checkpoint required")
        metadata = dict(job.metadata or {})
        metadata["last_yield_reason"] = "awaiting_user_approval"
        job.metadata = metadata
        job.context = checkpoint.strip()
        entries = [(redis_id, job)]
        if job.chain_id:
            for msg_id, fields in self.r.xrange(self.stream, min="-", max="+"):
                if msg_id == redis_id:
                    continue
                other = self._fields_to_job(msg_id, fields)
                if other.chain_id == job.chain_id and other.chain_index > job.chain_index:
                    entries.append((msg_id, other))
        pipe = self.r.pipeline(transaction=True)
        for msg_id, item in entries:
            pipe.zadd(self._approval_wait_key(), {
                json.dumps(self._job_to_fields(item), sort_keys=True, ensure_ascii=False): 0
            })
            pipe.xack(self.stream, self.group, msg_id)
            pipe.xdel(self.stream, msg_id)
        pipe.execute()

    def resume_approval_on_injection(self, task_ids: list[str]) -> int:
        """Wake only the waiting steps in the addressed task tree; never all tasks."""
        import redis as redis_module
        selected = {str(value) for value in task_ids if str(value)}
        if not selected:
            return 0
        key = self._approval_wait_key()
        for _ in range(4):
            with self.r.pipeline() as pipe:
                try:
                    pipe.watch(key)
                    rows = []
                    for raw in pipe.zrange(key, 0, -1):
                        fields = json.loads(raw)
                        if str(fields.get("task_id") or "") not in selected:
                            continue
                        if self._task_suppressed(fields["task_id"]):
                            continue
                        rows.append((raw, fields))
                    if not rows:
                        pipe.unwatch()
                        return 0
                    pipe.multi()
                    for _, fields in rows:
                        pipe.xadd(self.stream, fields)
                    pipe.zrem(key, *(raw for raw, _ in rows))
                    pipe.execute()
                    return len(rows)
                except redis_module.WatchError:
                    continue
        raise RuntimeError("Approval wake conflicted with concurrent queue updates")

    def yield_async_job(
        self, redis_id: str, job: PromptJob, checkpoint: str,
        *, next_check_at: float, step_rounds: int, task_rounds: int,
    ) -> None:
        """Park ONLY an N1-approved pending async step until its Redis due time.

        This does not change ordinary yield_job, retries, or N1/N2 work limits.
        The chain tail is parked with the step to avoid taking subsequent steps
        while a tool answer is outstanding. A single transaction moves them.
        """
        if not checkpoint.strip() or next_check_at <= 0:
            raise ValueError("Async checkpoint and next check time required")
        meta = dict(job.metadata or {})
        meta["last_yield_reason"] = "n1_async_pending"
        meta["last_step_rounds"] = int(step_rounds)
        meta["last_task_rounds"] = int(task_rounds)
        job.metadata = meta
        job.context = checkpoint.strip()
        job.created_at = time.time()
        entries = [(redis_id, job)]
        if job.chain_id:
            for msg_id, fields in self.r.xrange(self.stream, min="-", max="+"):
                if msg_id == redis_id:
                    continue
                queued = self._fields_to_job(msg_id, fields)
                if queued.chain_id == job.chain_id and queued.chain_index > job.chain_index:
                    entries.append((msg_id, queued))
        entries.sort(key=lambda entry: entry[1].chain_index)
        from redis.exceptions import RedisError
        pipe = self.r.pipeline(transaction=True)
        for msg_id, pending in entries:
            pipe.zadd(self._async_delayed_key(), {
                json.dumps(self._job_to_fields(pending), ensure_ascii=False, sort_keys=True):
                float(next_check_at)
            })
            pipe.xack(self.stream, self.group, msg_id)
            pipe.xdel(self.stream, msg_id)
        pipe.execute()

    def promote_due_async(self, *, now: float | None = None, limit: int = 80) -> int:
        """Atomically put due asynchronous continuations back into the work queue."""
        import redis as redis_module
        now = time.time() if now is None else float(now)
        key = self._async_delayed_key()
        for _ in range(3):
            with self.r.pipeline() as pipe:
                try:
                    pipe.watch(key)
                    members = pipe.zrangebyscore(key, "-inf", now, start=0, num=limit)
                    if not members:
                        pipe.unwatch()
                        return 0
                    rows = []
                    for raw in members:
                        fields = json.loads(raw)
                        if not isinstance(fields, dict):
                            raise RuntimeError("Invalid delayed async job payload")
                        job = self._fields_to_job("queued", fields)
                        if self._task_suppressed(job.task_id):
                            continue
                        if self.durable and self.durable.task_status(job.task_id) in {
                            "passed_to", "completed", "failed", "suppressed", "cancelled"
                        }:
                            continue
                        rows.append(fields)
                    pipe.multi()
                    for fields in rows:
                        pipe.xadd(self.stream, fields)
                    pipe.zrem(key, *members)
                    pipe.execute()
                    return len(rows)
                except redis_module.WatchError:
                    continue
        return 0

    def async_delayed_count(self) -> int:
        return int(self.r.zcard(self._async_delayed_key()))

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
        # Redis XRANGE rejects COUNT 0. Queue inspection is always bounded here;
        # callers wanting a full GUI snapshot use the dedicated unlimited path.
        count = max(1, int(count))
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
        for prefix in (f"{self.stream}:task-rounds:", f"{self.stream}:task-round-budget:"):
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
        # Historical versions deliberately kept acknowledged suppressed rows in Redis.
        # Ignore those stale rows when selecting the next operator-visible task.
        for stream in (self.stream, self.retry_stream, self.escalation_stream):
            for _, fields in self.r.xrange(stream, min="-", max="+"):
                task_id = str(fields.get("task_id") or "").strip()
                if task_id and not self._task_suppressed(task_id):
                    return task_id
        return None

    def awaiting_approval(self, task_ids: list[str] | None = None) -> list[dict]:
        """Read parked approval requests without disturbing their queue state."""
        requested = {str(t) for t in task_ids} if task_ids is not None else None
        found = []
        for raw in self.r.zrange(self._approval_wait_key(), 0, -1):
            try:
                fields = json.loads(raw)
                tid = str(fields.get("task_id") or "")
                if requested is not None and tid not in requested:
                    continue
                metadata = json.loads(fields.get("metadata") or "{}")
                request = metadata.get("approval_request") or {}
                if isinstance(request, dict) and request.get("question"):
                    found.append({"task_id": tid, "step_id": fields.get("step_id"),
                                  "request": request,
                                  "injection_floor": int(metadata.get("approval_injection_floor") or 0)})
            except (TypeError, ValueError):
                continue
        return found

    def snapshot_task_jobs(self, task_id: str) -> dict:
        jobs = []
        for stream, label in ((self.stream, "work"), (self.retry_stream, "retry"), (self.escalation_stream, "escalation")):
            for redis_id, fields in self.r.xrange(stream, min="-", max="+"):
                if str(fields.get("task_id") or "") != task_id:
                    continue
                job = self._fields_to_job(redis_id, fields)
                jobs.append({"stream": label, "redis_id": str(redis_id), "job": asdict(job)})
        # Async continuations are parked in a Redis sorted set, NOT any stream.
        # Snapshot them or /resume-task has no executable state after suppression.
        for payload, score in self.r.zrange(self._async_delayed_key(), 0, -1, withscores=True):
            try:
                fields = json.loads(payload)
                if str(fields.get("task_id") or "") != task_id:
                    continue
                job = self._fields_to_job("celery-delayed", fields)
                jobs.append({"stream": "celery-delayed", "due_at": float(score),
                             "job": asdict(job)})
            except (ValueError, TypeError, KeyError):
                continue
        # Permission-waiting steps also live outside the Redis work streams.
        for payload in self.r.zrange(self._approval_wait_key(), 0, -1):
            try:
                fields = json.loads(payload)
                if str(fields.get("task_id") or "") == task_id:
                    jobs.append({"stream": "approval-wait", "job":
                                 asdict(self._fields_to_job("approval-wait", fields))})
            except (ValueError, TypeError, KeyError):
                continue
        # Keep pending N1 dispatch evidence with the PostgreSQL-bound suppression
        # snapshot. Redis async-link receipts normally expire after two hours;
        # an operator may resume the task days later. Do not rerun such calls.
        celery_receipts = []
        for raw_key in (self.r.scan_iter(match=f"norm:async-link:{task_id}:*", count=50)
                        if hasattr(self.r, "scan_iter") else ()):
            try:
                saved = self.r.get(raw_key)
                pending = json.loads(saved) if saved else []
                if not isinstance(pending, list) or not pending:
                    continue
                marked = []
                for item in pending:
                    if isinstance(item, dict):
                        receipt = dict(item)
                        receipt["suppressed_while_pending"] = True
                        marked.append(receipt)
                if marked:
                    key = raw_key.decode("utf-8") if isinstance(raw_key, bytes) else str(raw_key)
                    celery_receipts.append({"key": key, "pending": marked})
            except (ValueError, TypeError, UnicodeDecodeError):
                logging.exception("Skipping unreadable Celery suppression receipt task=%s", task_id)
        return {"jobs": jobs, "celery_receipts": celery_receipts,
                "captured_at": time.time()}

    def restore_task_jobs(self, payload: dict) -> list[str]:
        """Restore a captured suppression snapshot without changing stream semantics.

        Legacy payloads contain ``jobs`` directly. Tree-aware v2 payloads contain
        per-task queue snapshots under ``tasks``. A single Redis transaction restores
        all entries so a failed resume cannot leave only part of a task tree queued.
        """
        payload = dict(payload or {})
        raw: list[dict] = []
        celery_receipts = []
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
                celery_receipts.extend(item for item in list(queue_payload.get("celery_receipts") or []) if isinstance(item, dict))
        else:
            raw = [item for item in list(payload.get("jobs") or []) if isinstance(item, dict)]
            celery_receipts = [item for item in list(payload.get("celery_receipts") or []) if isinstance(item, dict)]

        destinations = {
            "work": self.stream,
            "retry": self.retry_stream,
            "escalation": self.escalation_stream,
            "celery-delayed": self._async_delayed_key(),
            "approval-wait": self._approval_wait_key(),
        }
        restored: list[tuple[str, PromptJob, float]] = []
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
            due_at = float(item.get("due_at") or 0)
            if label == "celery-delayed" and due_at <= 0:
                raise ValueError("async suppression snapshot missing Celery due_at")
            restored.append((label, PromptJob(**data), due_at))

        if not restored:
            return []
        pipe = self.r.pipeline(transaction=True)
        # The restored receipt is an N1 observation, not an authorization to
        # execute. Status distinguishes a suppressed in-flight call from one
        # that actually completed. Pair Redis receipt recovery with queue restore.
        receipt_writes = 0
        for receipt in celery_receipts:
            key = str(receipt.get("key") or "")
            rows = receipt.get("pending")
            if key.startswith("norm:async-link:") and isinstance(rows, list):
                pipe.set(key, json.dumps(rows, ensure_ascii=False), ex=7200)
                receipt_writes += 1
        for label, job, due_at in restored:
            if label == "approval-wait":
                pipe.zadd(destinations[label], {
                    json.dumps(self._job_to_fields(job), ensure_ascii=False, sort_keys=True): 0
                })
            elif label == "celery-delayed":
                # Keep the original due time: if already elapsed it will be
                # promoted by the standard worker loop on the next pass.
                pipe.zadd(destinations[label], {
                    json.dumps(self._job_to_fields(job), ensure_ascii=False, sort_keys=True): due_at
                })
            else:
                pipe.xadd(destinations[label], self._job_to_fields(job))
        result = pipe.execute()
        return [str(value) for value in result[receipt_writes:]]

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
            if self._task_suppressed(job.task_id):
                # Resume state is already durable in PostgreSQL; remove stale live
                # queue copies left by older versions so they cannot shadow new work.
                self.ack(msg_id)
                continue
            job.created_at = time.time()
            self.r.xack(self.stream, self.group, msg_id)
            self.r.xdel(self.stream, msg_id)
            self.enqueue(job)
            moved += 1
        return moved

    def cleanup_task(self, task_id: str) -> dict[str, int]:
        counts = {"work": 0, "retry": 0, "escalation": 0, "dead": 0, "round_key": 0, "budget_key": 0, "async_delayed": 0, "approval_wait": 0}
        approval_key = self._approval_wait_key()
        for raw in self.r.zrange(approval_key, 0, -1):
            try:
                if str(json.loads(raw).get("task_id") or "") == task_id:
                    counts["approval_wait"] += int(self.r.zrem(approval_key, raw))
            except (TypeError, ValueError):
                continue
        delayed_key = self._async_delayed_key()
        for raw in self.r.zrange(delayed_key, 0, -1):
            try:
                fields = json.loads(raw)
                if str(fields.get("task_id") or "") == task_id:
                    counts["async_delayed"] += int(self.r.zrem(delayed_key, raw))
            except (ValueError, TypeError):
                continue
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
        counts["budget_key"] = int(self.r.delete(self._task_round_budget_key(task_id)))
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
            original_user_prompt=fields.get("original_user_prompt", ""),
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
