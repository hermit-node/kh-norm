from __future__ import annotations

import asyncio
import logging
from typing import Any

from aiohttp import web

from .aiohttp_host import AiohttpThreadServer
from .conversation_service import ConversationService

MAX_BODY_BYTES = 1_048_576


async def _json_body(request: web.Request, *, required: bool) -> dict[str, Any]:
    if request.content_length is not None and request.content_length > MAX_BODY_BYTES:
        raise web.HTTPRequestEntityTooLarge(max_size=MAX_BODY_BYTES, actual_size=request.content_length)
    if not request.can_read_body:
        if required:
            raise web.HTTPBadRequest(text='{"error":"request body required"}', content_type="application/json")
        return {}
    try:
        payload = await request.json()
    except Exception as exc:
        raise web.HTTPBadRequest(text='{"error":"invalid JSON"}', content_type="application/json") from exc
    if not isinstance(payload, dict):
        raise web.HTTPBadRequest(text='{"error":"invalid JSON"}', content_type="application/json")
    return payload


def _app_factory(service: ConversationService):
    def factory(server: AiohttpThreadServer) -> web.Application:
        app = web.Application(client_max_size=MAX_BODY_BYTES)

        async def health(_request: web.Request) -> web.Response:
            return web.json_response({"status": "ok"})

        async def list_threads(request: web.Request) -> web.Response:
            project_id = str(request.query.get("project_id") or "default")
            try:
                limit = int(request.query.get("limit", "50"))
            except (TypeError, ValueError):
                limit = 50
            try:
                threads = await asyncio.to_thread(service.list_threads, project_id=project_id, limit=limit)
            except Exception:
                logging.exception("Thread list failed")
                return web.json_response({"error": "thread list failed"}, status=500)
            return web.json_response({"project_id": project_id, "threads": threads})

        async def new_thread(request: web.Request) -> web.Response:
            try:
                payload = await _json_body(request, required=False)
            except web.HTTPException as exc:
                return web.json_response({"error": exc.reason or "invalid request"}, status=exc.status)
            project_id = payload.get("project_id", "default")
            title = payload.get("title")
            if not isinstance(project_id, str) or not project_id.strip():
                return web.json_response({"error": "project_id must be a non-empty string"}, status=400)
            if title is not None and not isinstance(title, str):
                return web.json_response({"error": "title must be a string or null"}, status=400)
            try:
                result = await asyncio.to_thread(service.create_named_thread, project_id=project_id, title=title)
            except Exception:
                logging.exception("Thread creation failed")
                return web.json_response({"error": "thread creation failed"}, status=500)
            return web.json_response(result)

        async def chat(request: web.Request) -> web.Response:
            if not server.accepting_requests:
                return web.json_response({"error": "Norm is shutting down"}, status=503)
            try:
                payload = await _json_body(request, required=True)
            except web.HTTPRequestEntityTooLarge:
                return web.json_response({"error": "request body too large"}, status=413)
            except web.HTTPException as exc:
                return web.json_response({"error": "request body required" if not request.can_read_body else "invalid JSON"}, status=400)

            message = payload.get("message")
            if not isinstance(message, str) or not message.strip():
                return web.json_response({"error": "message is required"}, status=400)
            project_id = payload.get("project_id", "default")
            if not isinstance(project_id, str) or not project_id:
                return web.json_response({"error": "project_id must be a non-empty string"}, status=400)
            thread_id = payload.get("thread_id")
            if thread_id is not None and (not isinstance(thread_id, str) or not thread_id):
                return web.json_response({"error": "thread_id must be a non-empty string or null"}, status=400)
            source_prompt_id = payload.get("prompt_id")
            if source_prompt_id is not None and not isinstance(source_prompt_id, str):
                return web.json_response({"error": "prompt_id must be a string or null"}, status=400)

            logging.info("Chat request project=%s explicit_thread=%s prompt_id=%s", project_id, bool(thread_id), str(source_prompt_id or "")[:12])
            server.begin_request()
            try:
                try:
                    result = await asyncio.to_thread(
                        service.chat, message, project_id=project_id, thread_id=thread_id,
                        source_prompt_id=str(source_prompt_id or ""),
                    )
                except ValueError as exc:
                    logging.warning("Bad chat request: %s", exc)
                    return web.json_response({"error": str(exc)}, status=400)
                except Exception:
                    logging.exception("Chat request failed")
                    return web.json_response({"error": "internal server error"}, status=500)
            finally:
                server.end_request()
            return web.json_response(result)

        app.router.add_get("/health", health)
        app.router.add_get("/api/threads", list_threads)
        app.router.add_post("/api/threads/new", new_thread)
        app.router.add_post("/api/chat", chat)
        return app
    return factory


def start_chat_api(service: ConversationService, host: str, port: int):
    server = AiohttpThreadServer(
        host=host,
        port=int(port),
        app_factory=_app_factory(service),
        name="norm-chat-api",
    )
    result = server.start()
    logging.info("Norm aiohttp chat API listening on http://%s:%s", host, port)
    return result


def serve_chat_api(service: ConversationService, host: str, port: int) -> None:
    server, thread = start_chat_api(service, host, port)
    try:
        thread.join()
    finally:
        server.shutdown()
        server.server_close()
