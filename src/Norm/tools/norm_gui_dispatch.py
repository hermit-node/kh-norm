from __future__ import annotations

import json
import os
import socket
import threading
import time
import uuid
from datetime import datetime, timezone
from urllib import request

import redis


class GuiPromptDispatcher:
    def __init__(self, endpoints: dict[str, str], config: dict, on_result=None) -> None:
        self.ep = endpoints
        self.on_result = on_result
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
        self.uncertain_continue_seconds = max(0.0, float(config.get("uncertain_continue_seconds", 10)))
        self.consumer = f"{socket.gethostname()}-{os.getpid()}-{uuid.uuid4().hex[:8]}"
        self.stop_event = threading.Event()
        self.activity_wakeup = threading.Event()
        self.pause_dispatch = threading.Event()
        self.dispatch_active = threading.Event()
        self._admin_lock = threading.RLock()
        self.redis.ping()
        self._ensure_group()

    @staticmethod
    def _now_iso() -> str:
        return datetime.now(timezone.utc).isoformat()

    def _ensure_group(self) -> None:
        try:
            self.redis.xgroup_create(self.stream, self.group, id="0", mkstream=True)
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
        prompt_id = str(uuid.uuid4())
        self.redis.set(self.last_submission_key, message)
        entry_id = self.redis.xadd(
            self.stream,
            {
                "kind": "prompt",
                "prompt_id": prompt_id,
                "message": message,
                "project_id": "default",
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
            {"kind": "thread_reset", "enqueued_at": self._now_iso()},
            maxlen=5000,
            approximate=True,
        )
        self._ensure_group()
        self.activity_wakeup.set()
        return str(entry_id)

    def queue_stats(self) -> tuple[int, int]:
        queued = 0
        try:
            self._ensure_group()
            for info in self.redis.xinfo_groups(self.stream):
                if str(info.get("name")) == self.group:
                    queued = int(info.get("pending") or 0) + int(info.get("lag") or 0)
                    break
        except Exception:
            try:
                queued = int(self.redis.xlen(self.stream))
            except Exception:
                pass
        return queued, int(self.redis.hlen(self.uncertain_key))

    def queue_snapshot(self, limit: int = 1000) -> list[dict]:
        """Return a stable, human-facing snapshot of the GUI ingress queue."""
        rows = self.redis.xrange(self.stream, min="-", max="+", count=max(1, int(limit)))
        dispatching = set(str(k) for k in self.redis.hkeys(self.dispatching_key))
        snapshot: list[dict] = []
        for index, (raw_id, raw_fields) in enumerate(rows):
            entry_id = raw_id.decode("utf-8") if isinstance(raw_id, bytes) else str(raw_id)
            fields = {
                (k.decode("utf-8") if isinstance(k, bytes) else str(k)):
                (v.decode("utf-8") if isinstance(v, bytes) else v)
                for k, v in dict(raw_fields).items()
            }
            state = "dispatching" if entry_id in dispatching else "queued"
            snapshot.append({
                "index": index,
                "entry_id": entry_id,
                "state": state,
                "prompt_id": str(fields.get("prompt_id") or ""),
                "kind": str(fields.get("kind") or "prompt"),
                "message": str(fields.get("message") or ""),
                "enqueued_at": str(fields.get("enqueued_at") or ""),
            })
        return snapshot

    def _normalize_fields(self, raw_fields: dict) -> dict[str, str]:
        return {
            (k.decode("utf-8") if isinstance(k, bytes) else str(k)):
            (v.decode("utf-8") if isinstance(v, bytes) else str(v))
            for k, v in dict(raw_fields).items()
        }

    def _requeue_uncertain_record(self, uncertain_id: str, record: dict) -> str | None:
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
        temp_stream = f"{self.stream}:resume:{uuid.uuid4().hex}"
        try:
            for old_id, fields in ordered:
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
                "count": count,
                "start_index": pivot,
                "first_prompt_id": str(ordered[0][1].get("prompt_id") or ""),
                "first_message": str(ordered[0][1].get("message") or ""),
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
                        if raw.decode("utf-8", errors="replace").startswith("data: "):
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
                    self._ensure_group()
                    continue
                raise
        return None

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
            if not self._busy():
                return True
            self.activity_wakeup.wait(300)
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

    def _dispatch_prompt(self, entry_id: str, fields: dict) -> None:
        self.dispatch_active.set()
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
        payload = {
            "message": str(fields.get("message") or ""),
            "project_id": str(fields.get("project_id") or "default"),
        }
        thread_id = self.redis.get(self.thread_key)
        if isinstance(thread_id, bytes):
            thread_id = thread_id.decode("utf-8")
        if thread_id:
            payload["thread_id"] = thread_id
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
            reply = str(result.get("reply", ""))
            returned = result.get("primary_thread_id") or result.get("thread_id")
            if isinstance(returned, str) and returned:
                self.redis.set(self.thread_key, returned)
            self.redis.set(self.last_answer_key, reply)
            self.publish_reply(reply, source="completed")
            if self.on_result is not None:
                self.on_result(str(fields.get("message") or ""), reply, result)
            self._finish_entry(entry_id)
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
            self._dispatch_prompt(entry_id, fields)

    def start(self) -> None:
        threading.Thread(target=self._activity_listener, name="norm-gui-activity", daemon=True).start()
        threading.Thread(target=self._dispatcher_loop, name="norm-gui-dispatch", daemon=True).start()

    def stop(self) -> None:
        self.stop_event.set()
        self.activity_wakeup.set()

