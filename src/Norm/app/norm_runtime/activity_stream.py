from __future__ import annotations

import ipaddress
import json
import logging
import queue
import threading
import time
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable


class ActivityHub:
    def __init__(self, history_size: int = 250) -> None:
        self._history = deque(maxlen=history_size)
        self._subscribers: set[queue.Queue] = set()
        self._lock = threading.Lock()

    def publish(self, event: dict) -> None:
        item = dict(event)
        item.setdefault("timestamp", time.time())
        with self._lock:
            self._history.append(item)
            subscribers = tuple(self._subscribers)
        for subscriber in subscribers:
            try:
                subscriber.put_nowait(item)
            except queue.Full:
                try:
                    subscriber.get_nowait()
                    subscriber.put_nowait(item)
                except (queue.Empty, queue.Full):
                    pass

    def subscribe(self) -> queue.Queue:
        subscriber: queue.Queue = queue.Queue(maxsize=1000)
        with self._lock:
            history = tuple(self._history)
            self._subscribers.add(subscriber)
        for item in history:
            try:
                subscriber.put_nowait(item)
            except queue.Full:
                break
        return subscriber

    def unsubscribe(self, subscriber: queue.Queue) -> None:
        with self._lock:
            self._subscribers.discard(subscriber)


class ActivityLogHandler(logging.Handler):
    def __init__(self, hub: ActivityHub) -> None:
        super().__init__(logging.INFO)
        self.hub = hub
        self.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.hub.publish(
                {"channel": "norm", "type": "log", "text": self.format(record)}
            )
        except Exception:
            self.handleError(record)


class ActivityHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(
        self,
        address: tuple[str, int],
        hub: ActivityHub,
        cancel_ollama: Callable[[], int],
        shutdown_ollama: Callable[[], dict] | None = None,
        shutdown_norm: Callable[[bool], dict] | None = None,
        stop_all: Callable[[bool], dict] | None = None,
        suppress_task: Callable[[str | None, str], dict] | None = None,
        flush_suppressed: Callable[[], dict] | None = None,
        busy_status: Callable[[], dict] | None = None,
        context_status: Callable[[], dict] | None = None,
    ) -> None:
        super().__init__(address, ActivityRequestHandler)
        self.hub = hub
        self.cancel_ollama = cancel_ollama
        self.shutdown_ollama = shutdown_ollama
        self.shutdown_norm = shutdown_norm
        self.stop_all = stop_all
        self.suppress_task = suppress_task
        self.flush_suppressed = flush_suppressed
        self.busy_status = busy_status
        self.context_status = context_status


class ActivityRequestHandler(BaseHTTPRequestHandler):
    server: ActivityHTTPServer

    def log_message(self, format: str, *args) -> None:
        logging.info("Activity HTTP %s - %s", self.client_address[0], format % args)

    def _send_json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_text(self, status: int, text: str, content_type: str = "text/plain; charset=utf-8") -> None:
        body = text.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        if self.path == "/health":
            self._send_json(200, {"status": "ok"})
            return
        if self.path == "/status/busy":
            if self.server.busy_status is None:
                self._send_json(503, {"error": "busy status unavailable"})
                return
            try:
                self._send_json(200, self.server.busy_status())
            except Exception:
                logging.exception("Busy status probe failed")
                self._send_json(500, {"error": "busy status probe failed"})
            return
        if self.path == "/status-context":
            if self.server.context_status is None:
                self._send_json(503, {"error": "context status unavailable"})
                return
            try:
                payload = self.server.context_status()
                text = str(payload.get("handoff_markdown", ""))
                pointer = payload.get("handoff_pointer")
                if isinstance(pointer, dict) and pointer.get("path"):
                    pointer_path = Path(str(pointer["path"]))
                    if pointer_path.is_file():
                        text = pointer_path.read_text(encoding="utf-8-sig", errors="replace")
                self._send_text(200, text, "text/markdown; charset=utf-8")
            except Exception:
                logging.exception("Full context snapshot failed")
                self._send_json(500, {"error": "full context snapshot failed"})
            return
        if self.path in {"/status/context", "/status/context.md"}:
            if self.server.context_status is None:
                self._send_json(503, {"error": "context status unavailable"})
                return
            try:
                payload = self.server.context_status()
                if self.path.endswith(".md"):
                    self._send_text(200, str(payload.get("handoff_markdown", "")), "text/markdown; charset=utf-8")
                else:
                    self._send_json(200, payload)
            except Exception:
                logging.exception("Context snapshot failed")
                self._send_json(500, {"error": "context snapshot failed"})
            return
        if self.path != "/events":
            self._send_json(404, {"error": "not found"})
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        subscriber = self.server.hub.subscribe()
        try:
            while True:
                try:
                    event = subscriber.get(timeout=15)
                    payload = json.dumps(event, ensure_ascii=False)
                    data = f"data: {payload}\n\n".encode("utf-8")
                except queue.Empty:
                    data = b": keepalive\n\n"
                self.wfile.write(data)
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            self.server.hub.unsubscribe(subscriber)

    def _read_json_body(self) -> dict:
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if length <= 0:
            return {}
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except Exception:
            return {}

    def do_POST(self) -> None:
        payload = self._read_json_body()
        if self.path == "/control/cancel-ollama":
            count = self.server.cancel_ollama()
            self._send_json(200, {"status": "ok", "cancelled": count})
            return
        if self.path == "/control/shutdown-ollama":
            if self.server.shutdown_ollama is None:
                self._send_json(503, {"error": "Ollama shutdown control unavailable"})
                return
            result = self.server.shutdown_ollama()
            self._send_json(200 if result.get("status") == "ok" else 409, result)
            return
        if self.path in {"/control/shutdown-norm", "/control/shutdown-norm-now"}:
            if self.server.shutdown_norm is None:
                self._send_json(503, {"error": "shutdown control unavailable"})
                return
            result = self.server.shutdown_norm(self.path.endswith("-now"))
            self._send_json(200, result)
            return
        if self.path == "/control/suppress-task":
            if self.server.suppress_task is None:
                self._send_json(503, {"error": "suppress-task control unavailable"})
                return
            result = self.server.suppress_task(payload.get("task_id"), str(payload.get("reason") or ""))
            self._send_json(200 if result.get("status") == "ok" else 409, result)
            return
        if self.path == "/control/flush-suppressed":
            if self.server.flush_suppressed is None:
                self._send_json(503, {"error": "flush-suppressed control unavailable"})
                return
            self._send_json(200, self.server.flush_suppressed())
            return
        if self.path in {"/control/stop-all", "/control/stop-all-now"}:
            if self.server.stop_all is None:
                self._send_json(503, {"error": "stop-all control unavailable"})
                return
            result = self.server.stop_all(self.path.endswith("-now"))
            self._send_json(200, result)
            return
        self._send_json(404, {"error": "not found"})


def start_activity_server(
    hub: ActivityHub,
    *,
    host: str,
    port: int,
    cancel_ollama: Callable[[], int],
    shutdown_ollama: Callable[[], dict] | None = None,
    shutdown_norm: Callable[[bool], dict] | None = None,
    stop_all: Callable[[bool], dict] | None = None,
    suppress_task: Callable[[str | None, str], dict] | None = None,
    flush_suppressed: Callable[[], dict] | None = None,
    busy_status: Callable[[], dict] | None = None,
    context_status: Callable[[], dict] | None = None,
):
    try:
        ip = ipaddress.ip_address(host)
    except ValueError as exc:
        raise ValueError("service bind host must be a literal loopback or Tailscale IP") from exc
    if not (ip.is_loopback or ip in ipaddress.ip_network("100.64.0.0/10")):
        raise ValueError("service bind host must remain loopback or Tailscale-only")
    server = ActivityHTTPServer(
        (host, port), hub, cancel_ollama, shutdown_ollama, shutdown_norm, stop_all, suppress_task, flush_suppressed, busy_status, context_status
    )
    thread = threading.Thread(
        target=server.serve_forever,
        name="norm-activity-api",
        daemon=True,
    )
    thread.start()
    return server, thread
