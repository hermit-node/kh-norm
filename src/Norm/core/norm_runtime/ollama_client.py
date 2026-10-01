from __future__ import annotations

from .secret_redaction import redact

import base64
import json
import re
import time
import zlib
from pathlib import Path
from threading import Event, Lock
from typing import Callable
from urllib import request


class ModelGenerationCancelled(RuntimeError):
    pass


class ModelDegenerateOutput(RuntimeError):
    pass


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
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout_seconds = timeout_seconds
        self.activity_sink = activity_sink
        self.activity_source = activity_source
        self.crash_sink = crash_sink
        self._display_pending = {}
        self._crash_task_id = ""
        self._crash_step_id = ""
        self._crash_pending: list[dict[str, str]] = []
        self._crash_pending_chars = 0
        self._crash_last_flush = time.monotonic()

    def _emit(self, event_type: str, **values) -> None:
        if event_type in {"thinking", "answer"}:
            pending = self._display_pending.get(event_type, "") + str(values.get("text") or "")
            boundary = pending.rfind("\n") + 1
            # Ollama can occasionally emit a very long token loop with no newline. The
            # old console buffered that entire run until cancellation, making Norm look
            # frozen and then dumping thousands of characters at once. Flush bounded
            # partial lines so activity remains visible and cancellation stays clean.
            if boundary == 0 and len(pending) >= 1024:
                boundary = (len(pending) // 1024) * 1024
            self._display_pending[event_type] = pending[boundary:]
            if boundary:
                safe = redact(pending[:boundary])
                self._publish_safe_event(event_type, text=safe)
                self._buffer_crash(event_type, safe)
            return
        if event_type in {"model_end", "model_error", "model_start"}:
            for kind, pending in self._display_pending.items():
                if pending:
                    safe = redact(pending)
                    self._publish_safe_event(kind, text=safe)
                    self._buffer_crash(kind, safe)
            self._display_pending.clear()
            self._flush_crash()
        self._publish_safe_event(event_type, **redact(values))

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
        if severe_compressed_loop or dominant_ratio > 0.72:
            raise ModelDegenerateOutput(
                "model stream became degenerate/repetitive "
                f"(compression={compression_ratio:.3f}, dominant_token={dominant_ratio:.3f}, "
                f"unique_tokens={unique_tokens}, longest_run={longest_run})"
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

    def generate(
        self,
        prompt: str,
        *,
        think: bool = False,
        num_predict: int | None = None,
        temperature: float = 0.2,
        response_format: str | dict | None = None,
    ) -> str:
        options: dict[str, object] = {"temperature": temperature}
        if num_predict is not None:
            options["num_predict"] = num_predict
        body = {
            "model": self.model,
            "prompt": str(prompt),
            "stream": True,
            "think": think,
            "keep_alive": -1,
            "options": options,
        }
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
        self._emit("model_start", thinking=think)
        try:
            with request.urlopen(req, timeout=self.timeout_seconds) as response:
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
                        self._emit("thinking", text=thinking)
                    if answer:
                        guard_tail = self._guard_model_stream(guard_tail, answer)
                        response_parts.append(answer)
                        self._emit("answer", text=answer)
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
        body = {
            "model": self.model,
            "messages": messages,
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
        cancel_event = Event()
        call_id = id(cancel_event)
        with self._active_lock:
            self._active[call_id] = (cancel_event, None)
        self._emit("model_start", thinking=think, tool_mode=True)
        try:
            with request.urlopen(req, timeout=self.timeout_seconds) as response:
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
        }

    def vision(
        self,
        prompt: str,
        image_paths: list[str],
        *,
        think: bool = True,
        temperature: float = 0.1,
    ) -> str:
        images: list[str] = []
        for raw in image_paths:
            path = Path(raw).resolve()
            if not path.is_file():
                raise FileNotFoundError(path)
            images.append(base64.b64encode(path.read_bytes()).decode("ascii"))
        body = {
            "model": self.model,
            "messages": [{"role": "user", "content": str(prompt), "images": images}],
            "stream": True,
            "think": think,
            "keep_alive": -1,
            "options": {"temperature": temperature},
        }
        req = request.Request(
            f"{self.base_url}/api/chat",
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        parts: list[str] = []
        guard_tail = ""
        cancel_event = Event()
        call_id = id(cancel_event)
        with self._active_lock:
            self._active[call_id] = (cancel_event, None)
        self._emit("model_start", thinking=think, vision=True, images=len(images))
        try:
            with request.urlopen(req, timeout=self.timeout_seconds) as response:
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
        return "".join(parts).strip()

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
