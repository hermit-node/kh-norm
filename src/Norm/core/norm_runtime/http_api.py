from __future__ import annotations

import ipaddress
import json
import logging
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

from .conversation_service import ConversationService

MAX_BODY_BYTES = 1_048_576


class NormHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, server_address: tuple[str, int], service: ConversationService) -> None:
        super().__init__(server_address, NormRequestHandler)
        self.service = service
        self.accepting_requests = True
        self._active_requests = 0
        self._active_requests_lock = threading.Lock()

    @property
    def active_request_count(self) -> int:
        with self._active_requests_lock:
            return self._active_requests

    def begin_request(self) -> None:
        with self._active_requests_lock:
            self._active_requests += 1

    def end_request(self) -> None:
        with self._active_requests_lock:
            self._active_requests = max(0, self._active_requests - 1)


class NormRequestHandler(BaseHTTPRequestHandler):
    server: NormHTTPServer

    def log_message(self, format: str, *args: Any) -> None:
        logging.info("HTTP %s - %s", self.client_address[0], format % args)

    def _send_json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/health":
            self._send_json(200, {"status": "ok"})
            return
        if parsed.path == "/api/threads":
            query = parse_qs(parsed.query)
            project_id = str((query.get("project_id") or ["default"])[0] or "default")
            try:
                limit = int((query.get("limit") or ["50"])[0])
            except (TypeError, ValueError):
                limit = 50
            try:
                threads = self.server.service.list_threads(project_id=project_id, limit=limit)
            except Exception:
                logging.exception("Thread list failed")
                self._send_json(500, {"error": "thread list failed"})
                return
            self._send_json(200, {"project_id": project_id, "threads": threads})
            return
        self._send_json(404, {"error": "not found"})

    def do_POST(self) -> None:
        if self.path == "/api/threads/new":
            length_header = self.headers.get("Content-Length")
            try:
                length = int(length_header or 0)
            except (TypeError, ValueError):
                length = 0
            payload: dict = {}
            if length > 0:
                if length > MAX_BODY_BYTES:
                    self._send_json(413, {"error": "request body too large"})
                    return
                try:
                    payload = json.loads(self.rfile.read(length).decode("utf-8"))
                except Exception:
                    self._send_json(400, {"error": "invalid JSON"})
                    return
                if not isinstance(payload, dict):
                    self._send_json(400, {"error": "invalid JSON"})
                    return
            project_id = payload.get("project_id", "default")
            title = payload.get("title")
            if not isinstance(project_id, str) or not project_id.strip():
                self._send_json(400, {"error": "project_id must be a non-empty string"})
                return
            if title is not None and not isinstance(title, str):
                self._send_json(400, {"error": "title must be a string or null"})
                return
            try:
                result = self.server.service.create_named_thread(project_id=project_id, title=title)
            except Exception:
                logging.exception("Thread creation failed")
                self._send_json(500, {"error": "thread creation failed"})
                return
            self._send_json(200, result)
            return
        if self.path != "/api/chat":
            self._send_json(404, {"error": "not found"})
            return
        if not self.server.accepting_requests:
            self._send_json(503, {"error": "Norm is shutting down"})
            return

        length_header = self.headers.get("Content-Length")
        try:
            length = int(length_header)
        except (TypeError, ValueError):
            self._send_json(400, {"error": "request body required"})
            return
        if length <= 0:
            self._send_json(400, {"error": "request body required"})
            return
        if length > MAX_BODY_BYTES:
            self._send_json(413, {"error": "request body too large"})
            return

        raw = self.rfile.read(length)
        try:
            text = raw.decode("utf-8")
            payload = json.loads(text)
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._send_json(400, {"error": "invalid JSON"})
            return

        if not isinstance(payload, dict):
            self._send_json(400, {"error": "invalid JSON"})
            return

        message = payload.get("message")
        if not isinstance(message, str) or not message.strip():
            self._send_json(400, {"error": "message is required"})
            return

        project_id = payload.get("project_id", "default")
        if not isinstance(project_id, str) or not project_id:
            self._send_json(400, {"error": "project_id must be a non-empty string"})
            return

        thread_id = payload.get("thread_id")
        if thread_id is not None and (not isinstance(thread_id, str) or not thread_id):
            self._send_json(400, {"error": "thread_id must be a non-empty string or null"})
            return

        logging.info("Chat request project=%s explicit_thread=%s", project_id, bool(thread_id))

        self.server.begin_request()
        try:
            try:
                result = self.server.service.chat(message, project_id=project_id, thread_id=thread_id)
            except ValueError as exc:
                logging.warning("Bad chat request: %s", exc)
                self._send_json(400, {"error": str(exc)})
                return
            except Exception:
                logging.exception("Chat request failed")
                self._send_json(500, {"error": "internal server error"})
                return
        finally:
            self.server.end_request()

        self._send_json(200, result)


def start_chat_api(service: ConversationService, host: str, port: int):
    try:
        ip = ipaddress.ip_address(host)
    except ValueError as exc:
        raise ValueError("service bind host must be a literal loopback or Tailscale IP") from exc
    if not (ip.is_loopback or ip in ipaddress.ip_network("100.64.0.0/10")):
        raise ValueError("service bind host must remain loopback or Tailscale-only")
    server = NormHTTPServer((host, int(port)), service)
    thread = threading.Thread(
        target=server.serve_forever,
        kwargs={"poll_interval": 0.5},
        name="norm-chat-api",
        daemon=True,
    )
    thread.start()
    logging.info("Norm chat API listening on http://%s:%s", host, port)
    return server, thread


def serve_chat_api(service: ConversationService, host: str, port: int) -> None:
    server, thread = start_chat_api(service, host, port)
    try:
        thread.join()
    finally:
        server.shutdown()
        server.server_close()