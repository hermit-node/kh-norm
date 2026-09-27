from __future__ import annotations

import json
import logging
import os
import socket
import threading
import time
import uuid
from datetime import datetime, timezone
from urllib import request

import redis

logger = logging.getLogger(__name__)


class GuiPromptDispatcher:
    def __init__(self, endpoints: dict[str, str], config: dict, on_result=None, *, source_name: str = "console") -> None:
        self.ep = endpoints
        self.on_result = on_result
        self.source_name = str(source_name or "console")
        self.redis = redis.Redis(
            host=config.get("host", "127.0.0.1"),
            port=int(config.get("port", 6379)),
            db=int(config.get("db", 3)),
            decode_responses=True,
            encoding_errors="strict",
            socket_connect_timeout=5,
            socket_keepalive=True,
            health_check_interval=30,
        )
        self.stream = str(config.get("console_ingress_stream", "norm:gui:ingress"))
        self.group = str(config.get("console_ingress_group", "norm-gui-dispatchers"))
        self.thread_key = str(config.get("console_thread_key", "norm:gui:thread-id"))
        self.last_submission_key = str(config.get("console_last_submission_key", "norm:gui:last-submission"))
        self.last_answer_key = str(config.get("console_last_answer_key", "norm:gui:last-answer"))
        self.reply_stream = str(config.get("console_reply_stream", "norm:gui:replies"))
        self.dispatching_key = str(config.get("console_dispatching_key", "norm:gui:dispatching"))
        self.uncertain_key = str(config.get("console_uncertain_key", "norm:gui:uncertain"))
        self.resume_key = str(config.get("console_resume_key", "norm:gui:resume-request"))
        self.suppressed_prompt_ids_key = str(
            config.get("console_suppressed_prompt_ids_key", "norm:gui:suppressed-prompt-ids")
        )
        self.uncertain_continue_seconds = max(0.0, float(config.get("uncertain_continue_seconds", 10)))
        self.consumer = f"{socket.gethostname()}-{os.getpid()}-{uuid.uuid4().hex[:8]}"
        self.stop_event = threading.Event()
        self.activity_wakeup = threading.Event()
        self._activity_lock = threading.Lock()
        self._last_activity = time.monotonic()
        self._last_busy_probe = time.monotonic()
        self._probed_activity = -1.0
        self.pause_dispatch = threading.Event()
        self.dispatch_active = threading.Event()
        self._admin_lock = threading.RLock()
        self.redis.ping()
        self._ensure_group()

    @staticmethod
    def _now_iso() -> str:
        return datetime.now(timezone.utc).isoformat()

    def _ensure_group(self, start_id: str = "0") -> None:
        """Create the consumer group if it does not already exist.

        start_id controls where the group begins reading:
          "0"  — from the beginning (initial setup, rotation)
          "$"  — from the tail (NOGROUP recovery: only new entries)
        """
        try:
            self.redis.xgroup_create(self.stream, self.group, id=start_id, mkstream=True)
        except redis.ResponseError as exc:
            if "BUSYGROUP" not in str(exc):
                raise

    def seed_history(self, submission: str | None, answer: str | None, thread_id: str | None) -> None:
        if submission and not self.redis.exists(self.last_submission_key):
            self.redis.set(self.last_submission_key, submission)
        if answer and not self.redis.exists(self.last_answer_key):
            self.redis.set(self.last_answer_key, answer)
        if thread_id and not self.redis.exists(self.thread_key):
            self.redis.set(self.thread_key, thread_id)

    def last_submission(self) -> str | None:
        return self.redis.get(self.last_submission_key) or None

    def last_answer(self) -> str | None:
        return self.redis.get(self.last_answer_key) or None

    def publish_reply(self, text: str, source: str = "completed") -> str:
        return str(self.redis.xadd(
            self.reply_stream,
            {"text": str(text), "source": str(source), "at": self._now_iso()},
            maxlen=1000,
            approximate=True,
        ))

    def enqueue_prompt(self, message: str) -> tuple[str, str]:
        if message.lstrip().startswith("/"):
            raise ValueError("Slash commands must be handled by the prompt console; nothing queued.")
        prompt_id = str(uuid.uuid4())
        self.redis.set(self.last_submission_key, message)
        entry_id = self.redis.xadd(
            self.stream,
            {
                "kind": "prompt",
                "prompt_id": prompt_id,
                "message": message,
                "project_id": "default",
                "source": self.source_name,
                "enqueued_at": self._now_iso(),
            },
            maxlen=5000,
            approximate=True,
        )
        self._ensure_group()
        self.activity_wakeup.set()
        return str(entry_id), prompt_id

    def enqueue_thread_reset(self) -> str:
        entry_id = self.redis.xadd(
            self.stream,
            {"kind": "thread_reset", "source": self.source_name, "enqueued_at": self._now_iso()},
            maxlen=5000,
            approximate=True,
        )
        self._ensure_group()
        self.activity_wakeup.set()
        return str(entry_id)

    def enqueue_thread_switch(self, thread_id: str, title: str = "") -> str:
        thread_id = str(thread_id or "").strip()
        if not thread_id:
            raise ValueError("thread_id is required")
        entry_id = self.redis.xadd(
            self.stream,
            {
                "kind": "thread_switch",
                "source": self.source_name,
                "thread_id": thread_id,
                "title": str(title or ""),
                "enqueued_at": self._now_iso(),
            },
            maxlen=5000,
            approximate=True,
        )
        self._ensure_group()
        self.activity_wakeup.set()
        return str(entry_id)

    def queue_stats(self) -> tuple[int | None, int]:
        """Return (queued, uncertain).

        queued is an int when the queue depth is known (from group statistics,
        or from the XLEN fallback), or None when the depth is genuinely unknown
        (group stats unavailable AND the stream key cannot be read). A known 0
        means the queue is confirmed empty; None means the count could not be
        determined and must not be reported as empty.
        """
        queued: int | None = None
        # Primary: group statistics (pending + lag) for our consumer group.
        try:
            self._ensure_group()
            for info in self.redis.xinfo_groups(self.stream):
                if str(info.get("name")) == self.group:
                    queued = int(info.get("pending") or 0) + int(info.get("lag") or 0)
                    break
        except Exception:
            queued = None  # group stats unavailable; fall through to XLEN
        # Fallback: XLEN on the stream key. Only a confirmed 0 (or absent key)
        # means "empty"; a read failure means "unknown", never "empty".
        if queued is None:
            try:
                queued = int(self.redis.xlen(self.stream))
            except Exception:
                queued = None  # stream key unreadable -> unknown, NOT empty
        try:
            uncertain = int(self.redis.hlen(self.uncertain_key))
        except Exception:
            uncertain = 0
        return queued, uncertain

    def uncertain_snapshot(self) -> list[dict]:
        """Read uncertain submissions without consuming, retrying, or modifying them."""
        rows = self.redis.hgetall(self.uncertain_key)
        blocked = set(self.redis.smembers(self.suppressed_prompt_ids_key))
        snapshot = []
        for entry_id, raw in sorted(rows.items()):
            try:
                record = json.loads(raw)
                if not isinstance(record, dict):
                    raise ValueError("expected a JSON object")
            except (ValueError, TypeError) as exc:
                snapshot.append({"entry_id": entry_id, "state": "unreadable", "message": "", "error": str(exc)})
                continue
            prompt_id = str(record.get("prompt_id") or "")
            suppressed = bool(record.get("suppressed")) or prompt_id in blocked
            auto_retry = record.get("auto_retry") is True and not suppressed
            snapshot.append({
                "entry_id": entry_id,
                "prompt_id": prompt_id,
                "state": "suppressed" if suppressed else "retry-enabled" if auto_retry else "parked",
                "auto_retry": auto_retry,
                "suppressed": suppressed,
                "message": str(record.get("message") or ""),
                "enqueued_at": record.get("enqueued_at"),
                "failed_at": record.get("failed_at"),
                "retry_count": record.get("retry_count", 0),
                "reason": str(record.get("reason") or ""),
                "error": str(record.get("error") or ""),
            })
        return snapshot

    def queue_snapshot(self, limit: int | None = 1000) -> list[dict]:
        """Return a stable, human-facing snapshot of the GUI ingress queue."""
        options = {} if limit is None else {"count": max(1, int(limit))}
        rows = self.redis.xrange(self.stream, min="-", max="+", **options)
        dispatching = set(str(k) for k in self.redis.hkeys(self.dispatching_key))
        snapshot: list[dict] = []
        for index, (raw_id, raw_fields) in enumerate(rows):
            entry_id = raw_id.decode("utf-8") if isinstance(raw_id, bytes) else str(raw_id)
            fields = {
                (k.decode("utf-8") if isinstance(k, bytes) else str(k)):
                (v.decode("utf-8") if isinstance(v, bytes) else v)
                for k, v in dict(raw_fields).items()
            }
            prompt_id = str(fields.get("prompt_id") or "")
            if self._prompt_retry_suppressed(prompt_id):
                state = "suppressed"
            elif entry_id in dispatching:
                state = "dispatching"
            else:
                state = "queued"
            snapshot.append({
                "index": index,
                "entry_id": entry_id,
                "state": state,
                "prompt_id": prompt_id,
                "kind": str(fields.get("kind") or "prompt"),
                "message": str(fields.get("message") or ""),
                "enqueued_at": str(fields.get("enqueued_at") or ""),
            })
        return snapshot

    def _prompt_retry_suppressed(self, prompt_id: str) -> bool:
        """Return True when this GUI prompt was explicitly suppressed."""
        prompt_id = str(prompt_id or "").strip()
        if not prompt_id:
            return False
        return bool(self.redis.sismember(self.suppressed_prompt_ids_key, prompt_id))

    def suppress_active_retry(self) -> dict:
        """Prevent the currently dispatched GUI prompt from being resurrected.

        Norm's PostgreSQL task status remains the authoritative worker-side gate.
        This second gate covers the GUI's independent uncertain/HTTP retry layer.
        """
        prompt_ids: set[str] = set()

        for raw in self.redis.hgetall(self.dispatching_key).values():
            try:
                record = json.loads(raw)
            except Exception:
                continue
            prompt_id = str(record.get("prompt_id") or "").strip()
            if prompt_id:
                prompt_ids.add(prompt_id)

        if prompt_ids:
            self.redis.sadd(self.suppressed_prompt_ids_key, *sorted(prompt_ids))

        uncertain_disabled = 0
        for uncertain_id, raw in self.redis.hgetall(self.uncertain_key).items():
            try:
                record = json.loads(raw)
            except Exception:
                continue
            prompt_id = str(record.get("prompt_id") or "").strip()
            if prompt_id not in prompt_ids:
                continue
            if bool(record.get("auto_retry")):
                uncertain_disabled += 1
            record["auto_retry"] = False
            record["suppressed"] = True
            self.redis.hset(
                self.uncertain_key,
                uncertain_id,
                json.dumps(record, ensure_ascii=False),
            )

        return {
            "suppressed_prompt_ids": sorted(prompt_ids),
            "uncertain_disabled": uncertain_disabled,
        }

    def active_prompt_ids(self) -> list[str]:
        """Capture prompt IDs whose HTTP dispatch is currently active."""
        prompt_ids: set[str] = set()

        for raw in self.redis.hgetall(self.dispatching_key).values():
            try:
                record = json.loads(raw)
            except Exception:
                continue

            prompt_id = str(record.get("prompt_id") or "").strip()
            if prompt_id:
                prompt_ids.add(prompt_id)

        return sorted(prompt_ids)

    def suppress_prompt_retries(self, prompt_ids) -> dict:
        """Durably disable GUI uncertain recovery for these submissions."""
        ids = sorted(
            {
                str(value or "").strip()
                for value in prompt_ids
                if str(value or "").strip()
            }
        )

        if ids:
            self.redis.sadd(
                self.suppressed_prompt_ids_key,
                *ids,
            )

        disabled = 0

        for uncertain_id, raw in self.redis.hgetall(
            self.uncertain_key
        ).items():
            try:
                record = json.loads(raw)
            except Exception:
                continue

            prompt_id = str(record.get("prompt_id") or "").strip()

            if prompt_id not in ids:
                continue

            if bool(record.get("auto_retry")):
                disabled += 1

            record["auto_retry"] = False
            record["suppressed"] = True

            self.redis.hset(
                self.uncertain_key,
                uncertain_id,
                json.dumps(record, ensure_ascii=False),
            )

        return {
            "suppressed_prompt_ids": ids,
            "uncertain_disabled": disabled,
        }

    def _normalize_fields(self, raw_fields: dict) -> dict[str, str]:
        return {
            (k.decode("utf-8") if isinstance(k, bytes) else str(k)):
            (v.decode("utf-8") if isinstance(v, bytes) else str(v))
            for k, v in dict(raw_fields).items()
        }

    def _requeue_uncertain_record(self, uncertain_id: str, record: dict) -> str | None:
        # Suppressed GUI prompt IDs are a hard retry gate. The uncertain queue
        # must never recreate a new Norm task behind PostgreSQL suppression.
        if self._prompt_retry_suppressed(str(record.get("prompt_id") or "")):
            parked = dict(record)
            parked["auto_retry"] = False
            parked["suppressed"] = True
            self.redis.hset(
                self.uncertain_key,
                uncertain_id,
                json.dumps(parked, ensure_ascii=False),
            )
            return None
        if not bool(record.get("auto_retry")):
            return None
        fields = dict(record.get("fields") or {})
        if not fields:
            fields = {
                "kind": "prompt",
                "prompt_id": str(record.get("prompt_id") or ""),
                "message": str(record.get("message") or ""),
                "project_id": str(record.get("project_id") or "default"),
                "enqueued_at": str(record.get("enqueued_at") or self._now_iso()),
            }
        fields = {str(k): str(v) for k, v in fields.items()}
        fields["retry_count"] = str(int(record.get("retry_count") or 0) + 1)
        fields["retried_from"] = str(uncertain_id)
        new_id = str(self.redis.xadd(self.stream, fields, maxlen=5000, approximate=True))
        self._ensure_group()
        self.redis.hdel(self.uncertain_key, uncertain_id)
        self.activity_wakeup.set()
        return new_id

    def _schedule_uncertain_retry(self, uncertain_id: str, record: dict) -> None:
        retry_after = float(record.get("retry_after_epoch") or (time.time() + self.uncertain_continue_seconds))
        delay = max(0.0, retry_after - time.time())

        def _retry() -> None:
            if self.stop_event.is_set():
                return
            raw = self.redis.hget(self.uncertain_key, uncertain_id)
            if not raw:
                return
            try:
                latest = json.loads(raw)
            except Exception:
                return
            if not bool(latest.get("auto_retry")):
                return
            self._requeue_uncertain_record(uncertain_id, latest)

        timer = threading.Timer(delay, _retry)
        timer.daemon = True
        timer.start()

    def _recover_due_uncertain(self) -> None:
        for uncertain_id, raw in self.redis.hgetall(self.uncertain_key).items():
            try:
                record = json.loads(raw)
            except Exception:
                continue
            if not bool(record.get("auto_retry")):
                continue
            self._schedule_uncertain_retry(str(uncertain_id), record)

    def _defer_entry(self, entry_id: str, fields: dict, reason: str, error: Exception | None = None) -> None:
        retry_count = int(fields.get("retry_count") or 0)
        record = {
            "prompt_id": str(fields.get("prompt_id") or ""),
            "message": str(fields.get("message") or ""),
            "project_id": str(fields.get("project_id") or "default"),
            "enqueued_at": str(fields.get("enqueued_at") or ""),
            "failed_at": self._now_iso(),
            "reason": str(reason),
            "error": "" if error is None else f"{type(error).__name__}: {error}",
            "fields": {str(k): str(v) for k, v in fields.items()},
            "retry_count": retry_count,
            "auto_retry": True,
            "retry_after_epoch": time.time() + self.uncertain_continue_seconds,
        }
        # A request suppressed while HTTP was active stays parked rather than
        # becoming eligible for the GUI's delayed automatic retry.
        if self._prompt_retry_suppressed(str(record.get("prompt_id") or "")):
            record["auto_retry"] = False
            record["suppressed"] = True
        self.redis.hset(self.uncertain_key, entry_id, json.dumps(record, ensure_ascii=False))
        self._finish_entry(entry_id)
        self._schedule_uncertain_retry(entry_id, record)

    def _rotate_queue(self, index: int | None) -> dict:
        """Atomically rotate the ingress stream without losing prompt payloads.

        Queue indexes are snapshot-only human conveniences. Redis stream IDs remain durable
        identities for the current generation of the queue, while prompt_id remains stable
        across a manual rotation.
        """
        rows = self.redis.xrange(self.stream, min="-", max="+")
        if not rows:
            return {"status": "ok", "rotated": False, "count": 0, "start_index": 0}
        entries = [(str(raw_id), self._normalize_fields(raw_fields)) for raw_id, raw_fields in rows]
        count = len(entries)
        dispatching_ids = set(str(k) for k in self.redis.hkeys(self.dispatching_key))
        if index is None:
            pivot = 0
            if dispatching_ids:
                while pivot < count and entries[pivot][0] in dispatching_ids:
                    pivot += 1
                if pivot >= count:
                    pivot = 0
        else:
            pivot = int(index)
            if pivot < 0 or pivot >= count:
                raise IndexError(f"queue index {pivot} is out of range 0..{count - 1}")
        ordered = entries[pivot:] + entries[:pivot]
        # Filter out entries whose prompt_id is in the suppressed set.
        # Suppressed prompts must not become runnable again via rotation.
        actionable = [
            (old_id, fields) for old_id, fields in ordered
            if not self._prompt_retry_suppressed(str(fields.get("prompt_id") or ""))
        ]
        suppressed_count = len(ordered) - len(actionable)
        if not actionable:
            return {
                "status": "ok",
                "rotated": False,
                "count": 0,
                "start_index": 0,
                "suppressed_skipped": suppressed_count,
                "reason": "all entries are suppressed; nothing to rotate",
            }
        temp_stream = f"{self.stream}:resume:{uuid.uuid4().hex}"
        try:
            for old_id, fields in actionable:
                copied = dict(fields)
                copied["resumed_from_entry"] = old_id
                copied["resume_count"] = str(int(copied.get("resume_count") or 0) + 1)
                self.redis.xadd(temp_stream, copied, maxlen=5000, approximate=True)
            # RENAME replaces the old ingress stream atomically. Its old consumer group/PEL
            # disappears with it; the new group starts at 0 and sees every rotated entry.
            self.redis.rename(temp_stream, self.stream)
            self.redis.delete(self.dispatching_key)
            self._ensure_group()
            self.activity_wakeup.set()
            return {
                "status": "ok",
                "rotated": True,
                "count": len(actionable),
                "start_index": pivot,
                "suppressed_skipped": suppressed_count,
                "first_prompt_id": str(actionable[0][1].get("prompt_id") or ""),
                "first_message": str(actionable[0][1].get("message") or ""),
            }
        finally:
            if self.redis.exists(temp_stream):
                self.redis.delete(temp_stream)

    def resume_queue(self, index: int | None = None) -> dict:
        """Resume/rotate the queue now if safe, otherwise persist the request.

        A synchronous /api/chat call can legitimately run for a long time. We never spawn a
        second concurrent dispatcher over a still-live call. If one is active, remember the
        requested rotation and apply it immediately after that call returns (or after restart).
        """
        requested = "next" if index is None else str(int(index))
        self.pause_dispatch.set()
        self.activity_wakeup.set()
        deadline = time.monotonic() + 1.5
        while self.dispatch_active.is_set() and time.monotonic() < deadline:
            time.sleep(0.05)
        if self.dispatch_active.is_set():
            self.redis.set(self.resume_key, requested)
            self.pause_dispatch.clear()
            self.activity_wakeup.set()
            return {"status": "scheduled", "requested": requested, "reason": "active dispatch still owns the current HTTP request"}
        with self._admin_lock:
            result = self._rotate_queue(index)
            self.redis.delete(self.resume_key)
        self.pause_dispatch.clear()
        self.activity_wakeup.set()
        return result

    def _apply_pending_resume(self) -> None:
        raw = self.redis.get(self.resume_key)
        if raw is None:
            return
        text = raw.decode("utf-8") if isinstance(raw, bytes) else str(raw)
        index = None if text == "next" else int(text)
        with self._admin_lock:
            self._rotate_queue(index)
            self.redis.delete(self.resume_key)

    def _activity_listener(self) -> None:
        while not self.stop_event.is_set():
            try:
                with request.urlopen(self.ep["events"], timeout=None) as response:
                    for raw in response:
                        if self.stop_event.is_set():
                            return
                        line = raw.decode("utf-8", errors="replace")
                        if line.startswith("data: "):
                            # The busy endpoint logs its own GET; do not let that
                            # response trigger a fresh quiet check indefinitely.
                            event = json.loads(line[6:])
                            text = str(event.get("text") or "")
                            if event.get("type") == "log" and "Activity HTTP " in text and "/status/busy" in text:
                                continue
                            with self._activity_lock:
                                self._last_activity = time.monotonic()
                            self.activity_wakeup.set()
            except Exception:
                if not self.stop_event.is_set():
                    self.stop_event.wait(1)

    def _claim_abandoned(self) -> None:
        try:
            self._ensure_group()
            pending = self.redis.xpending_range(self.stream, self.group, "-", "+", 1000)
        except Exception:
            return
        for item in pending:
            entry_id = str(item.get("message_id") or "")
            owner = str(item.get("consumer") or "")
            if not entry_id or owner == self.consumer:
                continue
            dispatching = self.redis.hget(self.dispatching_key, entry_id)
            rows = self.redis.xrange(self.stream, min=entry_id, max=entry_id, count=1)
            fields = self._normalize_fields(rows[0][1]) if rows else {}
            if dispatching:
                if not fields:
                    try:
                        prior = json.loads(dispatching)
                    except Exception:
                        prior = {}
                    fields = {
                        "kind": "prompt",
                        "prompt_id": str(prior.get("prompt_id") or ""),
                        "message": str(prior.get("message") or ""),
                        "project_id": "default",
                        "enqueued_at": str(prior.get("enqueued_at") or self._now_iso()),
                    }
                self._defer_entry(entry_id, fields, "abandoned dispatch from a previous GUI process")
            else:
                self.redis.xclaim(self.stream, self.group, self.consumer, 0, [entry_id])

    def _next_entry(self):
        for attempt in range(2):
            try:
                rows = self.redis.xreadgroup(self.group, self.consumer, {self.stream: "0"}, count=1)
                if not rows or not rows[0][1]:
                    rows = self.redis.xreadgroup(
                        self.group, self.consumer, {self.stream: ">"}, count=1, block=5000
                    )
                if not rows or not rows[0][1]:
                    return None
                return rows[0][1][0]
            except redis.ResponseError as exc:
                if attempt == 0 and "NOGROUP" in str(exc):
                    logger.warning("NOGROUP detected on stream %s; re-creating group %s at tail", self.stream, self.group)
                    # Use "$" so only NEW entries are delivered; historical
                    # (already-acknowledged or suppressed) entries are NOT
                    # re-dispatched as "new" work.
                    self._ensure_group(start_id="$")
                    continue
                raise
        return None

    def _busy_probe_due(self) -> bool:
        """One probe per quiet transition, plus a 30-second watchdog."""
        now = time.monotonic()
        with self._activity_lock:
            quiet = now - self._last_activity >= 1.0
            new_quiet = quiet and self._last_activity > self._probed_activity
            fallback = now - self._last_busy_probe >= 30.0
            if not (new_quiet or fallback):
                return False
            self._last_busy_probe = now
            if quiet:
                self._probed_activity = self._last_activity
            return True

    def _busy_payload(self) -> dict | None:
        try:
            with request.urlopen(self.ep["busy"], timeout=2) as response:
                payload = json.loads(response.read().decode("utf-8"))
            return payload if isinstance(payload, dict) else None
        except Exception:
            return None

    def _busy(self) -> bool:
        payload = self._busy_payload()
        return True if payload is None else bool(payload.get("busy"))

    def _wait_until_idle(self) -> bool:
        while not self.stop_event.is_set():
            if self.pause_dispatch.is_set():
                return False
            self.activity_wakeup.clear()
            if self._busy_probe_due() and not self._busy():
                return True
            # Activity wakes the scheduler only to postpone the quiet check.
            self.activity_wakeup.wait(0.1)
        return False

    def _finish_entry(self, entry_id: str) -> None:
        self.redis.hdel(self.dispatching_key, entry_id)
        self.redis.xack(self.stream, self.group, entry_id)
        self.redis.xdel(self.stream, entry_id)

    def _mark_uncertain(self, entry_id: str, fields: dict, error: Exception) -> None:
        self._defer_entry(entry_id, fields, "dispatch outcome became uncertain", error=error)
        # Give the runtime a small recovery window, then continue with the next queue item.
        # The uncertain item is re-enqueued at the tail, so it is retried after the rest
        # of the current queue pass rather than wedging the entire dispatcher.
        self.stop_event.wait(self.uncertain_continue_seconds)

    def _api_base(self) -> str:
        chat_url = str(self.ep.get("chat") or "")
        suffix = "/api/chat"
        return chat_url[:-len(suffix)] if chat_url.endswith(suffix) else chat_url.rsplit("/", 1)[0]

    def _ensure_current_thread(self, project_id: str, message: str) -> str:
        """Return the canonical console thread, creating one when none is selected.

        Console/SSH prompt submission is explicit-thread work.  This prevents the
        generic semantic router from turning an accepted console prompt into a
        pre-task clarification response.  /new and /thread-resume still change
        the selected thread through the same ordered DB3 ingress stream.
        """
        current = self.redis.get(self.thread_key)
        if isinstance(current, bytes):
            current = current.decode("utf-8")
        current = str(current or "").strip()
        if current:
            return current
        title = str(message or "").strip()[:80] or "New thread"
        req = request.Request(
            f"{self._api_base()}/api/threads/new",
            data=json.dumps({"project_id": project_id, "title": title}, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json; charset=utf-8"},
            method="POST",
        )
        with request.urlopen(req, timeout=10) as response:
            created = json.loads(response.read().decode("utf-8"))
        thread_id = str(created.get("thread_id") or "").strip() if isinstance(created, dict) else ""
        if not thread_id:
            raise RuntimeError("thread creation returned no thread_id")
        self.redis.set(self.thread_key, thread_id)
        logger.info(
            "Prompt ingress created canonical thread source=%s project=%s thread=%s",
            self.source_name, project_id, thread_id,
        )
        return thread_id

    def _park_protocol_violation(self, entry_id: str, fields: dict, reason: str, result: object = None) -> None:
        record = {
            "prompt_id": str(fields.get("prompt_id") or ""),
            "message": str(fields.get("message") or ""),
            "project_id": str(fields.get("project_id") or "default"),
            "source": str(fields.get("source") or self.source_name),
            "enqueued_at": str(fields.get("enqueued_at") or ""),
            "failed_at": self._now_iso(),
            "reason": str(reason),
            "error": "",
            "fields": {str(k): str(v) for k, v in fields.items()},
            "retry_count": int(fields.get("retry_count") or 0),
            "auto_retry": False,
            "protocol_violation": True,
        }
        if isinstance(result, dict):
            record["result_keys"] = sorted(str(k) for k in result.keys())
        self.redis.hset(self.uncertain_key, entry_id, json.dumps(record, ensure_ascii=False))
        self._finish_entry(entry_id)

    def _dispatch_prompt(self, entry_id: str, fields: dict) -> None:
        prompt_id = str(fields.get("prompt_id") or "")
        if self._prompt_retry_suppressed(prompt_id):
            self._defer_entry(entry_id, fields, "prompt suppressed by operator")
            return
        self.dispatch_active.set()
        with self._activity_lock:
            self._last_activity = time.monotonic()
            self._last_busy_probe = time.monotonic()
            self._probed_activity = -1.0
        if not self._wait_until_idle():
            self.dispatch_active.clear()
            return
        dispatch_state = {
            "prompt_id": str(fields.get("prompt_id") or ""),
            "message": str(fields.get("message") or ""),
            "enqueued_at": str(fields.get("enqueued_at") or ""),
            "dispatch_started_at": self._now_iso(),
        }
        self.redis.hset(self.dispatching_key, entry_id, json.dumps(dispatch_state, ensure_ascii=False))
        message = str(fields.get("message") or "")
        project_id = str(fields.get("project_id") or "default")
        thread_id = self._ensure_current_thread(project_id, message)
        payload = {
            "message": message,
            "project_id": project_id,
            "thread_id": thread_id,
        }
        logger.info(
            "Prompt ingress dispatch started source=%s prompt_id=%s entry_id=%s project=%s thread=%s",
            str(fields.get("source") or self.source_name), prompt_id, entry_id, project_id, thread_id,
        )
        req = request.Request(
            self.ep["chat"],
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json; charset=utf-8"},
            method="POST",
        )
        outcome: dict[str, object] = {}
        finished = threading.Event()

        def _send_chat() -> None:
            try:
                with request.urlopen(req, timeout=None) as response:
                    outcome["result"] = json.loads(response.read().decode("utf-8"))
            except Exception as exc:
                outcome["error"] = exc
            finally:
                finished.set()

        threading.Thread(target=_send_chat, name=f"norm-gui-http-{entry_id}", daemon=True).start()
        idle_since: float | None = None
        monitor_interval = min(0.5, max(0.05, self.uncertain_continue_seconds / 4 if self.uncertain_continue_seconds else 0.05))
        try:
            while not finished.wait(monitor_interval):
                if self.stop_event.is_set():
                    return
                with self._activity_lock:
                    if idle_since is not None and self._last_activity > idle_since:
                        idle_since = None
                if not self._busy_probe_due():
                    continue
                status = self._busy_payload()
                if status is None:
                    idle_since = None
                    continue
                signals = status.get("signals") or {}
                active_requests = int(signals.get("active_chat_requests") or 0) if isinstance(signals, dict) else 0
                runtime_idle = not bool(status.get("busy")) and active_requests == 0
                if self.pause_dispatch.is_set() and runtime_idle:
                    self._defer_entry(entry_id, fields, "operator resumed queue while the HTTP dispatch had no live runtime request")
                    return
                if runtime_idle:
                    if idle_since is None:
                        idle_since = time.monotonic()
                    elif time.monotonic() - idle_since >= self.uncertain_continue_seconds:
                        self._defer_entry(entry_id, fields, "HTTP dispatch remained open while Norm reported idle; treating it as lost in limbo")
                        return
                else:
                    idle_since = None

            error = outcome.get("error")
            if isinstance(error, Exception):
                self._mark_uncertain(entry_id, fields, error)
                return
            result = outcome.get("result")
            if not isinstance(result, dict):
                self._mark_uncertain(entry_id, fields, RuntimeError("chat dispatch returned no JSON object"))
                return
            task_id = str(result.get("task_id") or "").strip()
            if not task_id:
                reason = "chat returned HTTP 200 without a durable task_id"
                logger.error(
                    "Prompt ingress protocol violation source=%s prompt_id=%s entry_id=%s: %s",
                    str(fields.get("source") or self.source_name), prompt_id, entry_id, reason,
                )
                self._park_protocol_violation(entry_id, fields, reason, result=result)
                return
            reply = str(result.get("reply", ""))
            returned = result.get("primary_thread_id") or result.get("thread_id")
            if isinstance(returned, str) and returned:
                self.redis.set(self.thread_key, returned)
            self.redis.set(self.last_answer_key, reply)
            self.publish_reply(reply, source="completed")
            if self.on_result is not None:
                self.on_result(str(fields.get("message") or ""), reply, result)
            self._finish_entry(entry_id)
            logger.info(
                "Prompt ingress dispatch completed source=%s prompt_id=%s entry_id=%s task=%s",
                str(fields.get("source") or self.source_name), prompt_id, entry_id, task_id,
            )
        finally:
            self.dispatch_active.clear()

    def _dispatcher_loop(self) -> None:
        self._claim_abandoned()
        self._recover_due_uncertain()
        while not self.stop_event.is_set():
            if self.pause_dispatch.is_set():
                self.activity_wakeup.clear()
                self.activity_wakeup.wait(0.1)
                continue
            try:
                self._apply_pending_resume()
            except Exception:
                self.stop_event.wait(1)
                continue
            try:
                item = self._next_entry()
            except Exception:
                if not self.stop_event.is_set():
                    self.stop_event.wait(1)
                continue
            if item is None:
                continue
            raw_entry_id, raw_fields = item[0], dict(item[1])
            entry_id = raw_entry_id.decode("utf-8") if isinstance(raw_entry_id, bytes) else str(raw_entry_id)
            fields = {
                (key.decode("utf-8") if isinstance(key, bytes) else str(key)):
                (value.decode("utf-8") if isinstance(value, bytes) else value)
                for key, value in raw_fields.items()
            }
            if fields.get("kind") == "thread_reset":
                self.redis.delete(self.thread_key)
                self._finish_entry(entry_id)
                continue
            if fields.get("kind") == "thread_switch":
                thread_id = str(fields.get("thread_id") or "").strip()
                if thread_id:
                    self.redis.set(self.thread_key, thread_id)
                else:
                    self.redis.delete(self.thread_key)
                self._finish_entry(entry_id)
                continue
            prompt_id = str(fields.get("prompt_id") or "")
            if self._prompt_retry_suppressed(prompt_id):
                self._defer_entry(entry_id, fields, "prompt suppressed by operator")
                continue
            self._dispatch_prompt(entry_id, fields)

    def start(self) -> None:
        threading.Thread(target=self._activity_listener, name="norm-gui-activity", daemon=True).start()
        threading.Thread(target=self._dispatcher_loop, name="norm-gui-dispatch", daemon=True).start()

    def stop(self) -> None:
        self.stop_event.set()
        self.activity_wakeup.set()
