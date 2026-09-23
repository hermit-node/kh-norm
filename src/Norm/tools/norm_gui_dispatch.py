from __future__ import annotations

import json
import os
import socket
import threading
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
        self.consumer = f"{socket.gethostname()}-{os.getpid()}-{uuid.uuid4().hex[:8]}"
        self.stop_event = threading.Event()
        self.activity_wakeup = threading.Event()
        self.redis.ping()
        try:
            self.redis.xgroup_create(self.stream, self.group, id="0", mkstream=True)
        except redis.ResponseError as exc:
            if "BUSYGROUP" not in str(exc):
                raise

    @staticmethod
    def _now_iso() -> str:
        return datetime.now(timezone.utc).isoformat()

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
        self.activity_wakeup.set()
        return str(entry_id), prompt_id

    def enqueue_thread_reset(self) -> str:
        entry_id = self.redis.xadd(
            self.stream,
            {"kind": "thread_reset", "enqueued_at": self._now_iso()},
            maxlen=5000,
            approximate=True,
        )
        self.activity_wakeup.set()
        return str(entry_id)

    def queue_stats(self) -> tuple[int, int]:
        queued = 0
        try:
            for info in self.redis.xinfo_groups(self.stream):
                if str(info.get("name")) == self.group:
                    queued = int(info.get("pending") or 0) + int(info.get("lag") or 0)
                    break
        except Exception:
            pass
        return queued, int(self.redis.hlen(self.uncertain_key))

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
            pending = self.redis.xpending_range(self.stream, self.group, "-", "+", 1000)
        except Exception:
            return
        for item in pending:
            entry_id = str(item.get("message_id") or "")
            owner = str(item.get("consumer") or "")
            if not entry_id or owner == self.consumer:
                continue
            dispatching = self.redis.hget(self.dispatching_key, entry_id)
            if dispatching:
                self.redis.hset(self.uncertain_key, entry_id, dispatching)
                self.redis.hdel(self.dispatching_key, entry_id)
                self.redis.xack(self.stream, self.group, entry_id)
                self.redis.xdel(self.stream, entry_id)
            else:
                self.redis.xclaim(self.stream, self.group, self.consumer, 0, [entry_id])
    def _next_entry(self):
        rows = self.redis.xreadgroup(self.group, self.consumer, {self.stream: "0"}, count=1)
        if not rows or not rows[0][1]:
            rows = self.redis.xreadgroup(
                self.group, self.consumer, {self.stream: ">"}, count=1, block=5000
            )
        if not rows or not rows[0][1]:
            return None
        return rows[0][1][0]

    def _busy(self) -> bool:
        try:
            with request.urlopen(self.ep["busy"], timeout=2) as response:
                payload = json.loads(response.read().decode("utf-8"))
            return bool(payload.get("busy"))
        except Exception:
            return True

    def _wait_until_idle(self) -> bool:
        while not self.stop_event.is_set():
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
        record = {
            "prompt_id": str(fields.get("prompt_id") or ""),
            "message": str(fields.get("message") or ""),
            "enqueued_at": str(fields.get("enqueued_at") or ""),
            "failed_at": self._now_iso(),
            "error": f"{type(error).__name__}: {error}",
        }
        self.redis.hset(self.uncertain_key, entry_id, json.dumps(record, ensure_ascii=False))
        self._finish_entry(entry_id)

    def _dispatch_prompt(self, entry_id: str, fields: dict) -> None:
        if not self._wait_until_idle():
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
        try:
            with request.urlopen(req, timeout=None) as response:
                result = json.loads(response.read().decode("utf-8"))
            reply = str(result.get("reply", ""))
            returned = result.get("primary_thread_id") or result.get("thread_id")
            if isinstance(returned, str) and returned:
                self.redis.set(self.thread_key, returned)
            self.redis.set(self.last_answer_key, reply)
            self.publish_reply(reply, source="completed")
            if self.on_result is not None:
                self.on_result(str(fields.get("message") or ""), reply, result)
            self._finish_entry(entry_id)
        except Exception as exc:
            self._mark_uncertain(entry_id, fields, exc)
    def _dispatcher_loop(self) -> None:
        self._claim_abandoned()
        while not self.stop_event.is_set():
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
