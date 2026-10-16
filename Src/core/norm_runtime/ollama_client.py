from __future__ import annotations

from .secret_redaction import redact

import base64
import json
import logging
import re
import time
import zlib
from pathlib import Path
from threading import Event, Lock
from typing import Callable
from urllib import error as urlerror
from urllib import request


class ModelGenerationCancelled(RuntimeError):
    pass


class ModelDegenerateOutput(RuntimeError):
    pass


class ModelContextExceeded(RuntimeError):
    def __init__(self, message: str, *, request_tokens: int = 0, context_size: int = 0, response_body: str = "") -> None:
        super().__init__(str(message or "model input exceeded available context"))
        self.request_tokens = int(request_tokens or 0)
        self.context_size = int(context_size or 0)
        self.response_body = str(response_body or "")


class ModelOutputTruncated(RuntimeError):
    def __init__(self, partial: str, eval_count: int = 0) -> None:
        super().__init__("model output stopped because output length limit was reached")
        self.partial = str(partial or "")
        self.eval_count = int(eval_count or 0)


class OllamaClient:
    _active_lock = Lock()
    _active: dict[int, tuple[Event, object | None]] = {}

    def __init__(
        self,
        base_url: str,
        model: str = "norm",
        timeout_seconds: float | None = 86400,
        activity_sink: Callable[[dict], None] | None = None,
        activity_source: str = "unknown",
        crash_sink: Callable[[str, str, list[dict[str, str]]], None] | None = None,
        context_length: int | None = None,
        model_context_overrides: dict[str, int] | None = None,
        system_instructions: str | list[str] | tuple[str, ...] | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout_seconds = timeout_seconds
        self.activity_sink = activity_sink
        self.activity_source = activity_source
        self.crash_sink = crash_sink
        self.context_length = max(1, int(context_length)) if context_length else None
        self.model_context_overrides = {
            str(k).casefold(): max(1, int(v))
            for k, v in dict(model_context_overrides or {}).items()
            if int(v) > 0
        }
        if isinstance(system_instructions, (list, tuple)):
            self.system_instructions = "\n".join(
                f"- {str(v).strip()}" for v in system_instructions if str(v).strip()
            )
        else:
            self.system_instructions = str(system_instructions or "").strip()
        self._display_pending = {}
        self._crash_task_id = ""
        self._crash_step_id = ""
        self._crash_pending: list[dict[str, str]] = []
        self._crash_pending_chars = 0
        self._crash_last_flush = time.monotonic()

    def _emit(self, event_type: str, **values) -> None:
        if event_type in {"thinking", "answer"}:
            # Publish each upstream Ollama stream fragment immediately.  Runtime display
            # cadence must not be coupled to newline boundaries, 1 KiB buffers, or any
            # memory/output batching limit.  The crash buffer remains independently
            # bounded and is not used as the operator display buffer.
            safe = redact(str(values.get("text") or ""))
            if safe:
                self._publish_safe_event(event_type, text=safe)
                self._buffer_crash(event_type, safe)
            return
        if event_type in {"model_end", "model_error", "model_start"}:
            self._display_pending.clear()
            self._flush_crash()
        self._publish_safe_event(event_type, **redact(values))

    def publish_durable_summary(self, summary: str) -> None:
        """Publish one completed end-of-task durable summary block to runtime activity."""
        text = str(summary or "").strip()
        if text:
            self._publish_safe_event("durable_summary", text=redact(text))

    def _publish_safe_event(self, event_type: str, **values) -> None:
        if self.activity_sink is None:
            return
        try:
            self.activity_sink(
                {
                    "channel": "ollama",
                    "type": event_type,
                    "source": self.activity_source,
                    "model": self.model,
                    **values,
                }
            )
        except Exception:
            pass

    @staticmethod
    def _guard_model_stream(recent: str, new_text: str) -> str:
        if not new_text:
            return recent
        tail = (recent + str(new_text))[-8192:]
        if len(tail) < 4096:
            return tail
        raw = tail.encode("utf-8", errors="replace")
        # Severe token loops compress to almost nothing. Natural prose/code, even when
        # repetitive, stays well above this threshold.
        compression_ratio = len(zlib.compress(raw, 1)) / max(1, len(raw))
        tokens = re.findall(r"[A-Za-z0-9_]+|[^\w\s]", tail.lower())
        dominant_ratio = 0.0
        unique_tokens = 0
        if len(tokens) >= 256:
            counts: dict[str, int] = {}
            for token in tokens:
                counts[token] = counts.get(token, 0) + 1
            unique_tokens = len(counts)
            dominant_ratio = max(counts.values(), default=0) / len(tokens)
        longest_run = max((len(part) for part in re.split(r"\s+", tail)), default=0)
        severe_compressed_loop = compression_ratio < 0.035 and (
            longest_run >= 1024 or (len(tokens) >= 512 and unique_tokens <= 6)
        )
        # A different failure mode repeats an entire coherent sentence/paragraph.
        # Compression and dominant-token checks can miss it because the repeated
        # block itself has normal vocabulary.  Detect exact normalized 32-token
        # phrases recurring four or more times in the recent stream tail.
        phrase_repeat_count = 0
        word_tokens = re.findall(r"[A-Za-z0-9_]+", tail.lower())
        if len(word_tokens) >= 160:
            phrase_counts: dict[tuple[str, ...], int] = {}
            width = 32
            for index in range(0, len(word_tokens) - width + 1):
                phrase = tuple(word_tokens[index:index + width])
                count = phrase_counts.get(phrase, 0) + 1
                phrase_counts[phrase] = count
                if count > phrase_repeat_count:
                    phrase_repeat_count = count
        repeated_phrase_loop = phrase_repeat_count >= 4
        if severe_compressed_loop or dominant_ratio > 0.72 or repeated_phrase_loop:
            raise ModelDegenerateOutput(
                "model stream became degenerate/repetitive "
                f"(compression={compression_ratio:.3f}, dominant_token={dominant_ratio:.3f}, "
                f"unique_tokens={unique_tokens}, longest_run={longest_run}, "
                f"repeated_phrase={phrase_repeat_count})"
            )
        return tail

    def set_crash_context(self, task_id: str, step_id: str) -> None:
        self._flush_crash()
        self._crash_task_id = str(task_id)
        self._crash_step_id = str(step_id)

    def clear_crash_context(self) -> None:
        self._flush_crash()
        self._crash_task_id = ""
        self._crash_step_id = ""

    def _buffer_crash(self, kind: str, text: str) -> None:
        if not text or self.crash_sink is None or not self._crash_task_id or not self._crash_step_id:
            return
        self._crash_pending.append({"kind": str(kind), "text": str(text)})
        self._crash_pending_chars += len(text)
        now = time.monotonic()
        if self._crash_pending_chars >= 4096 or now - self._crash_last_flush >= 0.5:
            self._flush_crash(now)

    def _flush_crash(self, now: float | None = None) -> None:
        if not self._crash_pending:
            self._crash_last_flush = time.monotonic() if now is None else now
            return
        chunks = self._crash_pending
        self._crash_pending = []
        self._crash_pending_chars = 0
        self._crash_last_flush = time.monotonic() if now is None else now
        if self.crash_sink is None or not self._crash_task_id or not self._crash_step_id:
            return
        try:
            self.crash_sink(self._crash_task_id, self._crash_step_id, chunks)
        except Exception:
            pass

    @classmethod
    def active_count(cls) -> int:
        with cls._active_lock:
            return len(cls._active)

    @classmethod
    def cancel_active(cls) -> int:
        with cls._active_lock:
            active = tuple(cls._active.values())
        for cancel_event, response in active:
            cancel_event.set()
            if response is not None:
                try:
                    response.close()
                except Exception:
                    pass
        return len(active)

    def _context_for_model(self, model: str | None = None) -> int | None:
        name = str(model or self.model or "").strip()
        folded = name.casefold()
        base = folded.split(":", 1)[0]
        for key in (folded, base):
            if key in self.model_context_overrides:
                return int(self.model_context_overrides[key])
        return self.context_length

    def effective_context_length(self, model: str | None = None) -> int | None:
        """Public runtime-owned context budget for the active/selected model."""
        return self._context_for_model(model)

    @staticmethod
    def _context_error_details(body: str) -> tuple[int, int, str] | None:
        try:
            payload = json.loads(str(body or ""))
        except Exception:
            payload = None
        candidate = payload
        if isinstance(payload, dict) and isinstance(payload.get("error"), str):
            try:
                candidate = json.loads(payload["error"])
            except Exception:
                candidate = payload
        if isinstance(candidate, dict) and isinstance(candidate.get("error"), dict):
            candidate = candidate["error"]
        if not isinstance(candidate, dict):
            return None
        kind = str(candidate.get("type") or "")
        message = str(candidate.get("message") or "")
        if kind != "exceed_context_size_error" and "exceeds the available context size" not in message:
            return None
        return (
            int(candidate.get("n_prompt_tokens") or 0),
            int(candidate.get("n_ctx") or 0),
            message or "request exceeds available context size",
        )

    @staticmethod
    def _http_request_summary(req: request.Request) -> dict[str, object]:
        """Return safe structural metadata for an Ollama HTTP request.

        Never include prompt/message/tool contents here: HTTP diagnostics may be
        persisted in operator logs.  The response body is retained separately
        because Ollama's error JSON is the authoritative reason for a 4xx/5xx.
        """
        raw = getattr(req, "data", None)
        summary: dict[str, object] = {"request_bytes": len(raw or b"")}
        if not raw:
            return summary
        try:
            payload = json.loads(raw.decode("utf-8", errors="replace"))
        except Exception:
            summary["request_json"] = False
            return summary
        summary["request_json"] = True
        if not isinstance(payload, dict):
            return summary
        for key in ("stream", "think", "keep_alive"):
            if key in payload:
                summary[key] = payload.get(key)
        messages = payload.get("messages")
        if isinstance(messages, list):
            summary["message_count"] = len(messages)
            summary["message_roles"] = [
                str(item.get("role") or "")
                for item in messages
                if isinstance(item, dict)
            ][:32]
        tools = payload.get("tools")
        if isinstance(tools, list):
            summary["tool_count"] = len(tools)
            names: list[str] = []
            for item in tools[:64]:
                if not isinstance(item, dict):
                    continue
                function = item.get("function")
                if isinstance(function, dict):
                    name = str(function.get("name") or "").strip()
                    if name:
                        names.append(name)
            summary["tool_names"] = names
        options = payload.get("options")
        if isinstance(options, dict):
            for key in ("num_predict", "temperature", "num_ctx"):
                if key in options:
                    summary[key] = options.get(key)
        return summary

    def _urlopen(self, req: request.Request, *, timeout: float | None, endpoint: str):
        """Open an Ollama request while preserving authoritative HTTP error text.

        urllib's HTTPError carries the response body, but the previous client let
        that exception escape unread, discarding Ollama's diagnostic JSON.  Read
        and redact it here, persist it through Norm's normal runtime logger, and
        include it in the raised error so recovery/troubleshooting receives it.
        """
        try:
            return request.urlopen(req, timeout=timeout)
        except urlerror.HTTPError as exc:
            try:
                raw_body = exc.read()
            except Exception as read_exc:
                body = f"<failed to read Ollama error body: {type(read_exc).__name__}: {read_exc}>"
            else:
                body = raw_body.decode("utf-8", errors="replace") if raw_body else "<empty response body>"
            safe_body = str(redact(body))
            truncated = False
            if len(safe_body) > 16384:
                safe_body = safe_body[:16384] + "...<truncated>"
                truncated = True
            summary = self._http_request_summary(req)
            content_type = ""
            try:
                content_type = str(exc.headers.get("Content-Type") or "") if exc.headers else ""
            except Exception:
                content_type = ""
            logging.error(
                "Ollama HTTP error endpoint=%s model=%s status=%s reason=%s content_type=%s request=%s response_body=%s truncated=%s",
                endpoint,
                self.model,
                exc.code,
                exc.reason,
                content_type,
                json.dumps(redact(summary), ensure_ascii=False, sort_keys=True, default=str),
                safe_body,
                truncated,
            )
            context_error = self._context_error_details(body)
            if context_error is not None:
                request_tokens, context_size, message = context_error
                raise ModelContextExceeded(
                    f"Ollama context limit exceeded endpoint={endpoint} model={self.model}: {message}",
                    request_tokens=request_tokens,
                    context_size=context_size,
                    response_body=safe_body,
                ) from exc
            raise RuntimeError(
                f"Ollama HTTP {exc.code} {exc.reason} endpoint={endpoint} model={self.model}; "
                f"response_body={safe_body}"
            ) from exc

    def list_models(self) -> list[str]:
        req = request.Request(f"{self.base_url}/api/tags", method="GET")
        timeout = 30.0 if self.timeout_seconds is None else min(30.0, float(self.timeout_seconds))
        with self._urlopen(req, timeout=timeout, endpoint="/api/tags") as response:
            payload = json.loads(response.read().decode("utf-8"))
        names = []
        for item in list(payload.get("models") or []):
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or item.get("model") or "").strip()
            if name and name not in names:
                names.append(name)
        return names

    def probe_model(self, model: str) -> str:
        candidate = OllamaClient(
            base_url=self.base_url,
            model=str(model),
            timeout_seconds=min(120.0, float(self.timeout_seconds or 120.0)),
            activity_sink=None,
            activity_source="model-switch-probe",
            context_length=self.context_length,
            model_context_overrides=self.model_context_overrides,
            system_instructions=self.system_instructions,
        )
        return candidate.generate(
            "Reply with exactly OK.",
            think=False,
            num_predict=16,
            temperature=0.0,
            emit_stream=False,
        ).strip()

    def generate(
        self,
        prompt: str,
        *,
        think: bool = False,
        num_predict: int | None = None,
        temperature: float = 0.2,
        response_format: str | dict | None = None,
        emit_stream: bool = True,
        timeout_seconds: float | None = None,
        system_context: str | None = None,
    ) -> str:
        options: dict[str, object] = {"temperature": temperature}
        if num_predict is not None:
            options["num_predict"] = num_predict
        context_length = self._context_for_model()
        if context_length is not None:
            options["num_ctx"] = int(context_length)
        body = {
            "model": self.model,
            "prompt": str(prompt),
            "stream": True,
            "think": think,
            "keep_alive": -1,
            "options": options,
        }
        request_system = "\n\n".join(
            item for item in (self.system_instructions, str(system_context or "").strip()) if item
        )
        if request_system:
            body["system"] = request_system
        if response_format is not None:
            body["format"] = response_format
        req = request.Request(
            f"{self.base_url}/api/generate",
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        response_parts: list[str] = []
        guard_tail = ""
        done_reason = ""
        eval_count = 0
        cancel_event = Event()
        call_id = id(cancel_event)
        with self._active_lock:
            self._active[call_id] = (cancel_event, None)
        if emit_stream:
            self._emit("model_start", thinking=think)
        try:
            call_timeout = self.timeout_seconds if timeout_seconds is None else float(timeout_seconds)
            with self._urlopen(req, timeout=call_timeout, endpoint="/api/generate") as response:
                with self._active_lock:
                    self._active[call_id] = (cancel_event, response)
                if cancel_event.is_set():
                    raise ModelGenerationCancelled("generation cancelled")
                for raw_line in response:
                    if cancel_event.is_set():
                        raise ModelGenerationCancelled("generation cancelled")
                    line = raw_line.decode("utf-8").strip()
                    if not line:
                        continue
                    data = json.loads(line)
                    if data.get("error"):
                        raise RuntimeError(data["error"])
                    if data.get("done"):
                        done_reason = str(data.get("done_reason") or "")
                        eval_count = int(data.get("eval_count") or 0)
                    thinking = str(data.get("thinking", ""))
                    answer = str(data.get("response", ""))
                    if thinking:
                        guard_tail = self._guard_model_stream(guard_tail, thinking)
                        if emit_stream:
                            self._emit("thinking", text=thinking)
                    if answer:
                        guard_tail = self._guard_model_stream(guard_tail, answer)
                        response_parts.append(answer)
                        if emit_stream:
                            self._emit("answer", text=answer)
                if cancel_event.is_set():
                    raise ModelGenerationCancelled("generation cancelled")
        except Exception as exc:
            if cancel_event.is_set():
                cancelled_exc = exc if isinstance(exc, ModelGenerationCancelled) else ModelGenerationCancelled("generation cancelled")
                if emit_stream:
                    self._emit("model_error", text=str(cancelled_exc), cancelled=True)
                raise cancelled_exc from None
            if emit_stream:
                self._emit("model_error", text=str(exc), cancelled=False)
            raise
        finally:
            self._flush_crash()
            with self._active_lock:
                self._active.pop(call_id, None)
        if emit_stream:
            self._emit("model_end")
        text = "".join(response_parts).strip()
        if done_reason == "length":
            raise ModelOutputTruncated(text, eval_count)
        return text

    def generate_complete_text(
        self, prompt: str, *, think: bool = False, num_predict: int = 16000,
        temperature: float = 0.2, max_segments: int = 4, response_so_far: str = "",
        on_partial=None,
    ) -> str:
        original = str(prompt)
        pieces: list[str] = [str(response_so_far)] if response_so_far else []
        current = original
        if pieces:
            current = (
                "Continue an incomplete response. Do not restart, summarize, or repeat already-completed material. "
                "Continue exactly from the cutoff and finish every remaining requirement. Return only the continuation.\n\n"
                f"ORIGINAL REQUEST/INSTRUCTIONS:\n{original}\n\nRESPONSE SO FAR:\n{''.join(pieces)}"
            )
        for segment in range(1, max(1, int(max_segments)) + 1):
            try:
                text = self.generate(current, think=think, num_predict=num_predict, temperature=temperature)
                pieces.append(text)
                joined = "".join(pieces).strip()
                if on_partial is not None:
                    on_partial(joined, True, segment)
                return joined
            except ModelOutputTruncated as exc:
                pieces.append(exc.partial)
                joined = "".join(pieces)
                if on_partial is not None:
                    on_partial(joined, False, segment)
                if segment >= max(1, int(max_segments)):
                    raise ModelOutputTruncated(joined, exc.eval_count)
                current = (
                    "Continue an incomplete response. Do not restart, summarize, or repeat already-completed material. "
                    "Continue exactly from the cutoff and finish every remaining requirement. Return only the continuation.\n\n"
                    f"ORIGINAL REQUEST/INSTRUCTIONS:\n{original}\n\nRESPONSE SO FAR:\n{joined}"
                )
        raise RuntimeError("unreachable complete-text loop")

    def chat_with_tools(
        self,
        messages: list[dict],
        tools: list[dict],
        *,
        think: bool = True,
        temperature: float = 0.2,
        num_predict: int | None = None,
    ) -> dict:
        options: dict[str, object] = {"temperature": temperature}
        if num_predict is not None:
            options["num_predict"] = int(num_predict)
        context_length = self._context_for_model()
        if context_length is not None:
            options["num_ctx"] = int(context_length)
        outbound_messages = list(messages)
        if self.system_instructions:
            if (
                not outbound_messages
                or str(outbound_messages[0].get("role") or "") != "system"
                or str(outbound_messages[0].get("content") or "") != self.system_instructions
            ):
                outbound_messages = [{"role": "system", "content": self.system_instructions}, *outbound_messages]
        body = {
            "model": self.model,
            "messages": outbound_messages,
            "tools": tools,
            "stream": True,
            "think": think,
            "keep_alive": -1,
            "options": options,
        }
        req = request.Request(
            f"{self.base_url}/api/chat",
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        content_parts: list[str] = []
        thinking_parts: list[str] = []
        guard_tail = ""
        tool_calls: list[dict] = []
        seen_calls: set[str] = set()
        done_reason = ""
        eval_count = 0
        prompt_eval_count = 0
        cancel_event = Event()
        call_id = id(cancel_event)
        with self._active_lock:
            self._active[call_id] = (cancel_event, None)
        self._emit("model_start", thinking=think, tool_mode=True)
        try:
            with self._urlopen(req, timeout=self.timeout_seconds, endpoint="/api/chat") as response:
                with self._active_lock:
                    self._active[call_id] = (cancel_event, response)
                for raw_line in response:
                    if cancel_event.is_set():
                        raise ModelGenerationCancelled("generation cancelled")
                    line = raw_line.decode("utf-8").strip()
                    if not line:
                        continue
                    data = json.loads(line)
                    if data.get("error"):
                        raise RuntimeError(data["error"])
                    if data.get("done"):
                        done_reason = str(data.get("done_reason") or "")
                        eval_count = int(data.get("eval_count") or 0)
                        prompt_eval_count = int(data.get("prompt_eval_count") or 0)
                    message = data.get("message") or {}
                    thinking = str(message.get("thinking", ""))
                    content = str(message.get("content", ""))
                    if thinking:
                        guard_tail = self._guard_model_stream(guard_tail, thinking)
                        thinking_parts.append(thinking)
                        self._emit("thinking", text=thinking)
                    if content:
                        guard_tail = self._guard_model_stream(guard_tail, content)
                        content_parts.append(content)
                        self._emit("answer", text=content)
                    for call in message.get("tool_calls") or []:
                        marker = json.dumps(call, sort_keys=True, ensure_ascii=False)
                        if marker not in seen_calls:
                            seen_calls.add(marker)
                            tool_calls.append(call)
                            safe_marker = json.dumps(redact(call), sort_keys=True, ensure_ascii=False)
                            self._emit("tool_call", text=safe_marker)
                            self._buffer_crash("tool_call", safe_marker)
                if cancel_event.is_set():
                    raise ModelGenerationCancelled("generation cancelled")
        except Exception as exc:
            if cancel_event.is_set():
                cancelled_exc = exc if isinstance(exc, ModelGenerationCancelled) else ModelGenerationCancelled("generation cancelled")
                self._emit("model_error", text=str(cancelled_exc), cancelled=True)
                raise cancelled_exc from None
            self._emit("model_error", text=str(exc), cancelled=False)
            raise
        finally:
            self._flush_crash()
            with self._active_lock:
                self._active.pop(call_id, None)
        self._emit("model_end")
        return {
            "content": "".join(content_parts).strip(),
            "thinking": "".join(thinking_parts),
            "tool_calls": tool_calls,
            "done_reason": done_reason,
            "eval_count": eval_count,
            "prompt_eval_count": prompt_eval_count,
        }

    def vision_result(
        self,
        prompt: str,
        image_paths: list[str],
        *,
        think: bool = True,
        temperature: float = 0.1,
        num_predict: int | None = None,
    ) -> dict[str, Any]:
        images: list[str] = []
        for raw in image_paths:
            path = Path(raw).resolve()
            if not path.is_file():
                raise FileNotFoundError(path)
            images.append(base64.b64encode(path.read_bytes()).decode("ascii"))
        options: dict[str, Any] = {"temperature": temperature}
        if num_predict is not None:
            options["num_predict"] = max(1, int(num_predict))
        body = {
            "model": self.model,
            "messages": [{"role": "user", "content": str(prompt), "images": images}],
            "stream": True,
            "think": think,
            "keep_alive": -1,
            "options": options,
        }
        req = request.Request(
            f"{self.base_url}/api/chat",
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        parts: list[str] = []
        guard_tail = ""
        done_reason = ""
        eval_count = 0
        cancel_event = Event()
        call_id = id(cancel_event)
        with self._active_lock:
            self._active[call_id] = (cancel_event, None)
        self._emit("model_start", thinking=think, vision=True, images=len(images))
        try:
            with self._urlopen(req, timeout=self.timeout_seconds, endpoint="/api/chat") as response:
                with self._active_lock:
                    self._active[call_id] = (cancel_event, response)
                for raw_line in response:
                    if cancel_event.is_set():
                        raise ModelGenerationCancelled("generation cancelled")
                    line = raw_line.decode("utf-8").strip()
                    if not line:
                        continue
                    data = json.loads(line)
                    if data.get("error"):
                        raise RuntimeError(data["error"])
                    if data.get("done"):
                        done_reason = str(data.get("done_reason") or "")
                        try:
                            eval_count = int(data.get("eval_count") or 0)
                        except (TypeError, ValueError):
                            eval_count = 0
                    message = data.get("message") or {}
                    thinking_text = str(message.get("thinking", ""))
                    content = str(message.get("content", ""))
                    if thinking_text:
                        guard_tail = self._guard_model_stream(guard_tail, thinking_text)
                        self._emit("thinking", text=thinking_text)
                    if content:
                        guard_tail = self._guard_model_stream(guard_tail, content)
                        parts.append(content)
                        self._emit("answer", text=content)
                if cancel_event.is_set():
                    raise ModelGenerationCancelled("generation cancelled")
        except Exception as exc:
            if cancel_event.is_set():
                cancelled_exc = exc if isinstance(exc, ModelGenerationCancelled) else ModelGenerationCancelled("generation cancelled")
                self._emit("model_error", text=str(cancelled_exc), cancelled=True)
                raise cancelled_exc from None
            self._emit("model_error", text=str(exc), cancelled=False)
            raise
        finally:
            self._flush_crash()
            with self._active_lock:
                self._active.pop(call_id, None)
        self._emit("model_end")
        return {
            "content": "".join(parts).strip(),
            "done_reason": done_reason,
            "eval_count": eval_count,
        }

    def vision(
        self,
        prompt: str,
        image_paths: list[str],
        *,
        think: bool = True,
        temperature: float = 0.1,
        num_predict: int | None = None,
    ) -> str:
        return str(
            self.vision_result(
                prompt,
                image_paths,
                think=think,
                temperature=temperature,
                num_predict=num_predict,
            ).get("content") or ""
        ).strip()

    @staticmethod
    def parse_json(text: str) -> dict:
        cleaned = text.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.split("\n", 1)[-1]
            if cleaned.endswith("```"):
                cleaned = cleaned[:-3]
        try:
            value = json.loads(cleaned)
        except json.JSONDecodeError:
            start = cleaned.find("{")
            end = cleaned.rfind("}")
            if start < 0 or end <= start:
                raise ValueError(f"Model did not return JSON: {text[:300]}")
            value = json.loads(cleaned[start:end + 1])
        if not isinstance(value, dict):
            raise ValueError("Expected a JSON object from model")
        return value
