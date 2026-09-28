from __future__ import annotations

import asyncio
from collections import deque
import json
import logging
from pathlib import Path
import queue
import threading
import time
from typing import Callable

from aiohttp import web

from .aiohttp_host import AiohttpThreadServer
from .secret_redaction import RedactingFormatter, redact


class ActivityHub:
    def __init__(self, history_size: int = 250) -> None:
        self._history = deque(maxlen=history_size)
        self._subscribers: set[queue.Queue] = set()
        self._lock = threading.Lock()

    def publish(self, event: dict) -> None:
        item = redact(dict(event))
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
        self.setFormatter(RedactingFormatter("%(asctime)s %(levelname)s %(message)s"))

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.hub.publish({"channel": "norm", "type": "log", "text": self.format(record)})
        except Exception:
            self.handleError(record)


async def _call(callback: Callable, *args):
    return await asyncio.to_thread(callback, *args)


def _activity_app(
    hub: ActivityHub,
    *,
    cancel_ollama: Callable[[], int],
    shutdown_ollama: Callable[[], dict] | None,
    shutdown_norm: Callable[[bool], dict] | None,
    stop_all: Callable[[bool], dict] | None,
    suppress_task: Callable[[str | None, str], dict] | None,
    flush_suppressed: Callable[[], dict] | None,
    busy_status: Callable[[], dict] | None,
    context_status: Callable[[], dict] | None,
    inject_context: Callable | None,
    health_status: Callable[[], dict] | None,
):
    def factory(_server: AiohttpThreadServer) -> web.Application:
        app = web.Application(client_max_size=1_048_576)

        async def health(_request: web.Request) -> web.Response:
            if health_status is None:
                return web.json_response({"status": "ok"})
            try:
                return web.json_response(await _call(health_status))
            except Exception:
                logging.exception("Activity health probe computation failed")
                return web.json_response({"status": "error", "error": "activity health probe failed"}, status=500)

        async def busy(_request: web.Request) -> web.Response:
            if busy_status is None:
                return web.json_response({"error": "busy status unavailable"}, status=503)
            try:
                return web.json_response(await _call(busy_status))
            except Exception:
                logging.exception("Busy status computation failed")
                return web.json_response({"error": "busy status probe failed"}, status=500)

        async def context(request: web.Request) -> web.Response:
            if context_status is None:
                return web.json_response({"error": "context status unavailable"}, status=503)
            try:
                payload = await _call(context_status)
                if request.path.endswith(".md"):
                    return web.Response(text=str(payload.get("handoff_markdown", "")), content_type="text/markdown")
                return web.json_response(payload)
            except Exception:
                logging.exception("Context snapshot computation failed")
                return web.json_response({"error": "context snapshot failed"}, status=500)

        async def status_context(_request: web.Request) -> web.Response:
            if context_status is None:
                return web.json_response({"error": "context status unavailable"}, status=503)
            try:
                payload = await _call(context_status)
                text = str(payload.get("handoff_markdown", ""))
                pointer = payload.get("handoff_pointer")
                if isinstance(pointer, dict) and pointer.get("path"):
                    pointer_path = Path(str(pointer["path"]))
                    if pointer_path.is_file():
                        text = await asyncio.to_thread(
                            pointer_path.read_text, encoding="utf-8-sig", errors="replace"
                        )
                return web.Response(text=text, content_type="text/markdown")
            except Exception:
                logging.exception("Full context snapshot computation failed")
                return web.json_response({"error": "full context snapshot failed"}, status=500)

        async def events(request: web.Request) -> web.StreamResponse:
            response = web.StreamResponse(
                status=200,
                headers={
                    "Content-Type": "text/event-stream; charset=utf-8",
                    "Cache-Control": "no-cache",
                    "Connection": "keep-alive",
                    "X-Accel-Buffering": "no",
                },
            )
            await response.prepare(request)
            subscriber = hub.subscribe()
            last_write = time.monotonic()
            try:
                while True:
                    event = None
                    try:
                        event = subscriber.get_nowait()
                    except queue.Empty:
                        pass
                    if event is not None:
                        payload = json.dumps(event, ensure_ascii=False)
                        data = f"data: {payload}\n\n".encode("utf-8")
                    elif time.monotonic() - last_write >= 15:
                        data = b": keepalive\n\n"
                    else:
                        await asyncio.sleep(0.1)
                        continue
                    try:
                        await response.write(data)
                        last_write = time.monotonic()
                    except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError, asyncio.CancelledError):
                        break
                    except OSError as exc:
                        if getattr(exc, "winerror", None) in {10053, 10054}:
                            break
                        raise
            finally:
                hub.unsubscribe(subscriber)
            return response

        async def body(request: web.Request) -> dict:
            if not request.can_read_body:
                return {}
            try:
                payload = await request.json()
            except Exception:
                return {}
            return payload if isinstance(payload, dict) else {}

        async def control(request: web.Request) -> web.Response:
            payload = await body(request)
            path = request.path
            try:
                if path == "/control/inject-context":
                    if inject_context is None:
                        return web.json_response({"error": "Context injection unavailable"}, status=503)
                    try:
                        result = await _call(
                            inject_context,
                            payload.get("task_id"),
                            payload.get("content"),
                            payload.get("request_id"),
                        )
                    except ValueError as exc:
                        return web.json_response({"error": str(exc)}, status=400)
                    return web.json_response(result)
                if path == "/control/cancel-ollama":
                    return web.json_response({"status": "ok", "cancelled": await _call(cancel_ollama)})
                if path == "/control/shutdown-ollama":
                    if shutdown_ollama is None:
                        return web.json_response({"error": "Ollama shutdown control unavailable"}, status=503)
                    result = await _call(shutdown_ollama)
                    return web.json_response(result, status=200 if result.get("status") == "ok" else 409)
                if path in {"/control/shutdown-norm", "/control/shutdown-norm-now"}:
                    if shutdown_norm is None:
                        return web.json_response({"error": "shutdown control unavailable"}, status=503)
                    return web.json_response(await _call(shutdown_norm, path.endswith("-now")))
                if path == "/control/suppress-task":
                    if suppress_task is None:
                        return web.json_response({"error": "suppress-task control unavailable"}, status=503)
                    result = await _call(
                        suppress_task, payload.get("task_id"), str(payload.get("reason") or "")
                    )
                    return web.json_response(result, status=200 if result.get("status") == "ok" else 409)
                if path == "/control/flush-suppressed":
                    if flush_suppressed is None:
                        return web.json_response({"error": "flush-suppressed control unavailable"}, status=503)
                    return web.json_response(await _call(flush_suppressed))
                if path in {"/control/stop-all", "/control/stop-all-now"}:
                    if stop_all is None:
                        return web.json_response({"error": "stop-all control unavailable"}, status=503)
                    return web.json_response(await _call(stop_all, path.endswith("-now")))
            except Exception:
                logging.exception("Activity control operation failed path=%s", path)
                return web.json_response({"error": "control operation failed"}, status=503)
            return web.json_response({"error": "not found"}, status=404)

        app.router.add_get("/health", health)
        app.router.add_get("/status/busy", busy)
        app.router.add_get("/status-context", status_context)
        app.router.add_get("/status/context", context)
        app.router.add_get("/status/context.md", context)
        app.router.add_get("/events", events)
        for route in (
            "/control/inject-context",
            "/control/cancel-ollama",
            "/control/shutdown-ollama",
            "/control/shutdown-norm",
            "/control/shutdown-norm-now",
            "/control/suppress-task",
            "/control/flush-suppressed",
            "/control/stop-all",
            "/control/stop-all-now",
        ):
            app.router.add_post(route, control)
        return app
    return factory


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
    inject_context: Callable | None = None,
    health_status: Callable[[], dict] | None = None,
):
    server = AiohttpThreadServer(
        host=host,
        port=int(port),
        app_factory=_activity_app(
            hub,
            cancel_ollama=cancel_ollama,
            shutdown_ollama=shutdown_ollama,
            shutdown_norm=shutdown_norm,
            stop_all=stop_all,
            suppress_task=suppress_task,
            flush_suppressed=flush_suppressed,
            busy_status=busy_status,
            context_status=context_status,
            inject_context=inject_context,
            health_status=health_status,
        ),
        name="norm-activity-api",
    )
    result = server.start()
    logging.info("Norm aiohttp activity/control API listening on http://%s:%s", host, port)
    return result
