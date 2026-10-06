from __future__ import annotations

import copy
import difflib
import hashlib
import json
import logging
import ntpath
import posixpath
import re
from collections import OrderedDict
from dataclasses import dataclass
from threading import Lock
from typing import Any

from .ollama_client import OllamaClient
from .secret_redaction import redact


_CACHEABLE_CORE_TOOLS = {
    "list_directory",
    "read_file",
    "check_connection",
    "analyze_image",
    "vision_image",
}

# Plugin calls default to non-cacheable because skipping an unknown plugin can skip a
# side effect. Only known read/inspection functions participate in Redis answer reuse.
_CACHEABLE_PLUGIN_FUNCTIONS = {
    "read_text", "read_lines", "read_bytes", "archive_manifest", "archive_member",
    "archive_member_sha256", "archive_compare_directory",
    "postgres_pool", "postgres_query",
    "vision_parse",
    "delete_list",
    "pair_info", "pair_stats", "key_pair_stats", "recover_key",
    "extract_text", "extract_base64", "envelope_info",
    "profile_summary", "list_profiles", "render_voice_prompt", "retrieve_context",
    "compose_context", "active_profile", "validate_backup_setup",
}


@dataclass
class GateOutcome:
    execute: bool
    arguments: dict[str, Any]
    result: dict[str, Any] | None = None
    target: str = ""
    need: str = ""
    candidates: list[dict] | None = None
    note: str = ""
    reset_instruction: str = ""
    signature: str = ""


class N1Gatekeeper:
    """N1 boundary around N2 tool use.

    N2 is allowed to reason freely and propose native tools. N1 owns whether an
    information tool actually executes, whether a live Redis observation already
    answers the request, and the automatic Redis check-in after fresh evidence.

    Fresh executor results are never rewritten before they are returned to N2.
    """

    def __init__(
        self,
        client: OllamaClient,
        live,
        durable=None,
        *,
        enabled: bool = True,
        candidate_limit: int = 12,
        semantic_match_floor: float = 0.45,
        loop_repeat_threshold: int = 3,
        reasoning_loop_threshold: int = 3,
        reasoning_similarity_floor: float = 0.78,
        raw_result_cache_entries: int = 128,
        turn_history_entries: int = 256,
        request_state_entries: int = 4096,
    ) -> None:
        self.client = client
        self.live = live
        self.durable = durable
        self.enabled = bool(enabled)
        self.candidate_limit = max(1, min(64, int(candidate_limit)))
        self.semantic_match_floor = max(0.0, min(1.0, float(semantic_match_floor)))
        self.loop_repeat_threshold = max(2, int(loop_repeat_threshold))
        self.reasoning_loop_threshold = max(3, int(reasoning_loop_threshold))
        self.reasoning_similarity_floor = max(0.50, min(0.98, float(reasoning_similarity_floor)))
        self.raw_result_cache_entries = max(8, int(raw_result_cache_entries))
        self.turn_history_entries = max(32, int(turn_history_entries))
        self.request_state_entries = max(256, int(request_state_entries))
        self._lock = Lock()
        self._request_counts: OrderedDict[str, int] = OrderedDict()
        self._raw_results: OrderedDict[str, dict[str, Any]] = OrderedDict()
        self._loop_reset_emitted: OrderedDict[str, None] = OrderedDict()
        self._turn_history: OrderedDict[str, list[dict[str, Any]]] = OrderedDict()
        self._turn_loop_judgments: OrderedDict[str, None] = OrderedDict()

    @staticmethod
    def information_tool(name: str) -> bool:
        name = str(name or "")
        if name in _CACHEABLE_CORE_TOOLS:
            return True
        if name.startswith("plugin_"):
            function_name = name.rsplit("__", 1)[-1]
            return function_name in _CACHEABLE_PLUGIN_FUNCTIONS
        return False

    def forward_user(self, message: str) -> str:
        """Checkpoint-1 user ingress hook. N1 observes the boundary but does not rewrite text."""
        return message

    def forward_to_user(self, message: str) -> str:
        """Checkpoint-1 egress hook. N1 passes N2's user-facing text through unchanged."""
        return message

    def instructions(self) -> str:
        return (
            "N1 TOOL BOUNDARY:\n"
            "- You are N2, the working/reasoning agent. Propose normal native tools when you need them.\n"
            "- For every information-gathering tool proposal, include n1_need (the fact/information you need) and n1_target (the stable object/resource being checked).\n"
            "- Do NOT call verification_preflight, verification_history, or verification_checkin. N1 owns Redis validation, reuse/recheck decisions, and check-in.\n"
            "- N1 may satisfy your request from an existing verified observation instead of invoking the proposed tool. Treat that returned value as the tool answer and continue.\n"
            "- When N1 does invoke a tool, you receive the executor's raw result unchanged.\n"
            "- If N1 issues a loop-reset instruction, stop repeating the same reasoning/tool path and continue from the evidence already supplied."
        )

    def instrument_schemas(self, schemas: list[dict]) -> list[dict]:
        """Add N1 request metadata to information-tool schemas without changing executors."""
        result = copy.deepcopy(list(schemas or []))
        for schema in result:
            function = schema.get("function") if isinstance(schema, dict) else None
            if not isinstance(function, dict):
                continue
            name = str(function.get("name") or "")
            if not name or not self.information_tool(name):
                continue
            parameters = function.get("parameters")
            if not isinstance(parameters, dict):
                continue
            properties = parameters.setdefault("properties", {})
            if not isinstance(properties, dict):
                continue
            properties.setdefault(
                "n1_need",
                {
                    "type": "string",
                    "description": "The specific fact or information this tool proposal is intended to obtain. N1 uses this to detect reusable verified evidence.",
                },
            )
            properties.setdefault(
                "n1_target",
                {
                    "type": "string",
                    "description": "Stable object/resource being checked (exact path, endpoint, table/resource, connection target, etc.), not procedural prose.",
                },
            )
            required = parameters.setdefault("required", [])
            if isinstance(required, list):
                for key in ("n1_need", "n1_target"):
                    if key not in required:
                        required.append(key)
        return result

    @staticmethod
    def _request_signature(task_id: str, step_id: str, name: str, arguments: dict[str, Any]) -> str:
        payload = json.dumps(
            {"task_id": task_id, "step_id": step_id, "tool": name, "arguments": arguments},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
        return hashlib.sha256(payload.encode("utf-8", errors="replace")).hexdigest()

    @staticmethod
    def _fallback_target(name: str, arguments: dict[str, Any]) -> str:
        for key in ("path", "compare_path", "target", "url", "endpoint", "table", "resource", "host"):
            value = arguments.get(key)
            if value is not None and str(value).strip():
                return str(value).strip()
        paths = arguments.get("paths")
        if isinstance(paths, list) and paths:
            return " | ".join(str(item) for item in paths[:6])
        compact = json.dumps(redact(arguments), ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
        return f"{name}:{compact[:1500]}"

    @staticmethod
    def _fallback_need(name: str, arguments: dict[str, Any]) -> str:
        if name == "read_file":
            return "requested file/archive contents or metadata"
        if name == "list_directory":
            return "requested directory contents"
        if name == "run_command":
            return "observed result of the requested command"
        if name == "check_connection":
            return "current connection health/status"
        return f"information requested through {name}"

    def _clean_request(self, name: str, arguments: dict[str, Any]) -> tuple[dict[str, Any], str, str]:
        clean = dict(arguments or {})
        need = str(clean.pop("n1_need", "") or "").strip()
        target = str(clean.pop("n1_target", "") or "").strip()
        if not target:
            target = self._fallback_target(name, clean)
        if not need:
            need = self._fallback_need(name, clean)
        return clean, target, need

    def _remember_request(self, signature: str) -> int:
        with self._lock:
            count = int(self._request_counts.get(signature, 0)) + 1
            self._request_counts[signature] = count
            self._request_counts.move_to_end(signature)
            while len(self._request_counts) > self.request_state_entries:
                expired, _ = self._request_counts.popitem(last=False)
                self._loop_reset_emitted.pop(expired, None)
            return count

    def _cached_raw(self, signature: str) -> dict[str, Any] | None:
        with self._lock:
            value = self._raw_results.get(signature)
            if value is None:
                return None
            self._raw_results.move_to_end(signature)
            return copy.deepcopy(value.get("result") if isinstance(value, dict) else value)

    def _store_raw(self, signature: str, target: str, result: dict[str, Any]) -> None:
        with self._lock:
            self._raw_results[signature] = {"target": str(target or ""), "result": copy.deepcopy(result)}
            self._raw_results.move_to_end(signature)
            while len(self._raw_results) > self.raw_result_cache_entries:
                self._raw_results.popitem(last=False)

    def _normalized_target_key(self, target: str) -> str:
        normalizer = getattr(self.live, "_normalize_validation_target", None)
        if callable(normalizer):
            try:
                return str(normalizer(target) or "")
            except Exception:
                pass
        return " ".join(str(target or "").strip().split()).casefold()

    def _invalidate_local_target(self, target: str) -> int:
        normalized = self._normalized_target_key(target)
        if not normalized:
            return 0
        removed = 0
        with self._lock:
            for signature, item in list(self._raw_results.items()):
                item_target = self._normalized_target_key(str(item.get("target") or "")) if isinstance(item, dict) else ""
                if item_target == normalized:
                    self._raw_results.pop(signature, None)
                    removed += 1
        return removed

    @staticmethod
    def _parent_target(target: str) -> str:
        text = str(target or "").strip()
        if not text:
            return ""
        if re.match(r"^[A-Za-z]:[\\/]", text) or text.startswith("\\\\"):
            parent = ntpath.dirname(ntpath.normpath(text))
        elif "/" in text:
            parent = posixpath.dirname(posixpath.normpath(text))
        else:
            parent = ""
        return parent if parent and parent != text else ""

    def _invalidate_after_mutation(self, *targets: str) -> dict[str, int]:
        unique: list[str] = []
        seen: set[str] = set()
        for raw in targets:
            target = str(raw or "").strip()
            if not target:
                continue
            for candidate in (target, self._parent_target(target)):
                key = self._normalized_target_key(candidate)
                if candidate and key and key not in seen:
                    seen.add(key)
                    unique.append(candidate)
        local = 0
        redis_count = 0
        for target in unique:
            local += self._invalidate_local_target(target)
            if hasattr(self.live, "invalidate_validation_target"):
                try:
                    redis_count += int(self.live.invalidate_validation_target(target) or 0)
                except Exception:
                    logging.exception("N1 Redis target invalidation failed target=%s", target)
        return {"local": local, "redis": redis_count, "targets": len(unique)}

    def _loop_reset(self, *, signature: str, count: int, name: str, target: str, need: str) -> str:
        if count < self.loop_repeat_threshold:
            return ""
        with self._lock:
            if signature in self._loop_reset_emitted:
                return ""
            self._loop_reset_emitted[signature] = None
            self._loop_reset_emitted.move_to_end(signature)
            while len(self._loop_reset_emitted) > self.request_state_entries:
                self._loop_reset_emitted.popitem(last=False)
        fallback = (
            "N1 detected a repeated tool/reasoning loop. The requested evidence has already been supplied. "
            "Do not request the same tool for the same fact again in this step; use the existing evidence, change approach if needed, and continue toward the step objective."
        )
        if not self.enabled:
            return fallback
        prompt = (
            "You are N1, Norm's gatekeeper. N2 has proposed the exact same tool request repeatedly even though N1 already has the result. "
            "Return strict JSON with one short field named instruction. The instruction must stop the rabbit hole without changing the user's goal: tell N2 to use the existing evidence, abandon the repeated path, and continue productively.\n\n"
            f"repeat_count={count}\ntool={name}\ntarget={target}\nneeded_fact={need}"
        )
        schema = {
            "type": "object",
            "properties": {"instruction": {"type": "string"}},
            "required": ["instruction"],
            "additionalProperties": False,
        }
        try:
            raw = self.client.generate(
                prompt,
                think=True,
                temperature=0.0,
                num_predict=220,
                response_format=schema,
                emit_stream=False,
            )
            instruction = str(OllamaClient.parse_json(raw).get("instruction") or "").strip()
            return instruction or fallback
        except Exception:
            logging.exception("N1 loop-reset generation failed")
            return fallback

    @staticmethod
    def _normalized_turn_text(content: str, thinking: str) -> str:
        # Keep enough of both ends to detect "start over and rediscover the same thing"
        # loops without retaining unbounded model scratch text in process memory.
        combined = (str(thinking or "") + "\n" + str(content or "")).strip()
        if len(combined) > 16000:
            combined = combined[:8000] + "\n...\n" + combined[-8000:]
        combined = combined.casefold()
        combined = re.sub(r"\b[0-9a-f]{12,}\b", "<id>", combined)
        combined = re.sub(r"\b\d+(?:\.\d+)?\b", "<n>", combined)
        return " ".join(re.findall(r"[a-z0-9_./\\:+-]+", combined))[:16000]

    @staticmethod
    def _compact_calls(calls: list[dict] | None) -> list[dict[str, Any]]:
        compact: list[dict[str, Any]] = []
        for call in list(calls or [])[:12]:
            function = call.get("function") if isinstance(call, dict) else None
            if isinstance(function, dict):
                name = str(function.get("name") or "")
                arguments = function.get("arguments", {})
            elif isinstance(call, dict):
                name = str(call.get("name") or "")
                arguments = call.get("arguments", {})
            else:
                continue
            if isinstance(arguments, str):
                try:
                    arguments = json.loads(arguments)
                except Exception:
                    arguments = {"raw": arguments[:2000]}
            if not isinstance(arguments, dict):
                arguments = {}
            clean = dict(arguments)
            clean.pop("n1_need", None)
            clean.pop("n1_target", None)
            compact.append({"tool": name, "arguments": redact(clean)})
        return compact

    def _record_turn(self, key: str, turn: dict[str, Any]) -> list[dict[str, Any]]:
        with self._lock:
            history = list(self._turn_history.get(key) or [])
            history.append(turn)
            history = history[-8:]
            self._turn_history[key] = history
            self._turn_history.move_to_end(key)
            while len(self._turn_history) > self.turn_history_entries:
                self._turn_history.popitem(last=False)
            return copy.deepcopy(history)

    def _turn_loop_suspected(self, history: list[dict[str, Any]]) -> bool:
        if len(history) < self.reasoning_loop_threshold:
            return False
        recent = history[-self.reasoning_loop_threshold:]
        current = str(recent[-1].get("normalized") or "")
        if not current:
            return False
        similarities = []
        for item in recent[:-1]:
            prior = str(item.get("normalized") or "")
            if not prior:
                return False
            similarities.append(difflib.SequenceMatcher(None, current, prior).ratio())
        if similarities and min(similarities) >= self.reasoning_similarity_floor:
            return True

        # A tool rabbit hole can rephrase the prose while repeatedly circling the same
        # tool family. Let N1 judge these instead of relying on exact string equality.
        call_sets = [tuple(str(c.get("tool") or "") for c in item.get("calls", [])) for item in recent]
        if all(call_sets) and len(set(call_sets)) == 1:
            return True
        return False

    def _judge_turn_loop(self, *, task_id: str, step_id: str, history: list[dict[str, Any]]) -> str:
        recent = history[-self.reasoning_loop_threshold:]
        cluster_payload = json.dumps(
            [{"normalized": item.get("normalized"), "calls": item.get("calls")} for item in recent],
            ensure_ascii=False, sort_keys=True, default=str,
        )
        cluster_id = hashlib.sha256((task_id + "\n" + step_id + "\n" + cluster_payload).encode("utf-8", errors="replace")).hexdigest()
        with self._lock:
            if cluster_id in self._turn_loop_judgments:
                return ""
            self._turn_loop_judgments[cluster_id] = None
            self._turn_loop_judgments.move_to_end(cluster_id)
            while len(self._turn_loop_judgments) > self.request_state_entries:
                self._turn_loop_judgments.popitem(last=False)
        fallback = (
            "N1 detected that the current reasoning path is repeating without material progress. "
            "Stop this path, reuse the evidence already obtained, restate the unresolved subproblem once, and continue with a materially different next action."
        )
        if not self.enabled:
            return fallback
        payload = []
        for index, item in enumerate(recent, 1):
            payload.append({
                "turn": index,
                "thinking_excerpt": str(item.get("thinking") or "")[-3500:],
                "content_excerpt": str(item.get("content") or "")[-2500:],
                "tool_requests": item.get("calls") or [],
            })
        prompt = (
            "You are N1, Norm's gatekeeper, passively watching N2's work. Determine whether these consecutive N2 turns are a genuine rabbit hole: substantially the same reasoning/question/tool path repeated without material new evidence or progress. "
            "Do NOT flag legitimate iterative work merely because it uses the same tool family with different targets/chunks, and do NOT change the user's goal. Return strict JSON only with loop (boolean), reason (short), and instruction (short). If loop=false, instruction must be blank. If loop=true, tell N2 what repeated path to abandon and to continue from already-established evidence.\n\n"
            + json.dumps({"task_id": task_id, "step_id": step_id, "turns": payload}, ensure_ascii=False, default=str)
        )
        schema = {
            "type": "object",
            "properties": {
                "loop": {"type": "boolean"},
                "reason": {"type": "string"},
                "instruction": {"type": "string"},
            },
            "required": ["loop", "reason", "instruction"],
            "additionalProperties": False,
        }
        try:
            raw = self.client.generate(
                prompt,
                think=True,
                temperature=0.0,
                num_predict=420,
                response_format=schema,
                emit_stream=False,
            )
            parsed = OllamaClient.parse_json(raw)
            if bool(parsed.get("loop")):
                return str(parsed.get("instruction") or "").strip() or fallback
            return ""
        except Exception:
            logging.exception("N1 reasoning-loop judgment failed; leaving N2 uninterrupted")
            return ""

    def observe_n2_turn(
        self,
        *,
        task_id: str,
        step_id: str,
        content: str = "",
        thinking: str = "",
        calls: list[dict] | None = None,
    ) -> str:
        """Passively observe an N2 turn and return a reset instruction only for a judged loop.

        This never rewrites N2 output. The caller may inject the returned instruction
        before the next N2 turn; if no instruction is returned, N1 is transparent.
        """
        normalized = self._normalized_turn_text(content, thinking)
        turn = {
            "normalized": normalized,
            "content": str(content or ""),
            "thinking": str(thinking or ""),
            "calls": self._compact_calls(calls),
        }
        key = f"{task_id}\n{step_id}"
        history = self._record_turn(key, turn)
        if not self._turn_loop_suspected(history):
            return ""
        return self._judge_turn_loop(task_id=task_id, step_id=step_id, history=history)

    def _decide_reuse(
        self,
        *,
        name: str,
        arguments: dict[str, Any],
        target: str,
        need: str,
        candidates: list[dict],
    ) -> tuple[str, str, str]:
        """Return (decision, record_id, reason). Fail-open to fresh execution."""
        if not candidates:
            return "execute", "", "no live Redis candidate"
        prompt = (
            "You are N1, Norm's tool gatekeeper. N2 wants information and proposed a tool. Decide whether the existing LIVE Redis verification record already answers the requested fact or whether the tool should execute again. "
            "The records shown are hot Redis observations, not archival PostgreSQL history. Favor reuse when the same target/fact is already established, especially after repeated checks. Execute when the requested fact is materially different, the requested scope/arguments differ, or the fact is intrinsically volatile enough that a fresh observation is justified. "
            "Do not invent facts. Return strict JSON only: decision ('reuse' or 'execute'), record_id (required for reuse, blank for execute), reason (one short sentence).\n\n"
            + json.dumps(
                {
                    "proposed_tool": name,
                    "arguments": redact(arguments),
                    "target": target,
                    "needed_fact": need,
                    "redis_candidates": candidates,
                },
                ensure_ascii=False,
                sort_keys=True,
                default=str,
            )
        )
        schema = {
            "type": "object",
            "properties": {
                "decision": {"type": "string", "enum": ["reuse", "execute"]},
                "record_id": {"type": "string"},
                "reason": {"type": "string"},
            },
            "required": ["decision", "record_id", "reason"],
            "additionalProperties": False,
        }
        try:
            raw = self.client.generate(
                prompt,
                think=True,
                temperature=0.0,
                num_predict=420,
                response_format=schema,
                emit_stream=False,
            )
            parsed = OllamaClient.parse_json(raw)
            decision = str(parsed.get("decision") or "execute").strip().lower()
            record_id = str(parsed.get("record_id") or "").strip()
            reason = str(parsed.get("reason") or "").strip()
            allowed = {str(row.get("record_id") or "") for row in candidates}
            if decision == "reuse" and record_id in allowed:
                return "reuse", record_id, reason or "N1 selected an existing live verified observation."
            return "execute", "", reason or "N1 requested a fresh observation."
        except Exception:
            logging.exception("N1 Redis reuse decision failed; allowing fresh tool execution")
            return "execute", "", "N1 decision failed; fresh execution allowed"

    def before_tool(self, *, task_id: str, step_id: str, name: str, arguments: dict[str, Any]) -> GateOutcome:
        clean, target, need = self._clean_request(name, arguments)
        signature = self._request_signature(task_id, step_id, name, clean)
        count = self._remember_request(signature)
        reset = self._loop_reset(signature=signature, count=count, name=name, target=target, need=need)

        if not self.enabled:
            return GateOutcome(True, clean, target=target, need=need, signature=signature, reset_instruction=reset)
        if not self.information_tool(name):
            if count >= self.loop_repeat_threshold:
                return GateOutcome(
                    False,
                    clean,
                    result={
                        "ok": False,
                        "tool": name,
                        "n1_loop_blocked": True,
                        "error": "N1 blocked a repeated identical tool request at the loop threshold; use existing results or change approach.",
                    },
                    target=target,
                    need=need,
                    note="repeated identical non-cacheable tool request blocked by N1",
                    reset_instruction=reset,
                    signature=signature,
                )
            return GateOutcome(True, clean, target=target, need=need, signature=signature, reset_instruction=reset)

        # Exact same request inside this runtime/task/step: preserve and return the
        # original raw executor response without reinvoking the tool.
        raw_cached = self._cached_raw(signature)
        if raw_cached is not None:
            return GateOutcome(
                False,
                clean,
                result=raw_cached,
                target=target,
                need=need,
                note="N1 reused the prior raw result for the identical request; tool not reinvoked.",
                reset_instruction=reset,
                signature=signature,
            )

        try:
            candidates = list(
                self.live.validation_candidates(
                    tool=name,
                    target=target,
                    description=need,
                    limit=self.candidate_limit,
                )
                or []
            )
        except Exception:
            logging.exception("N1 Redis validation lookup failed; allowing fresh tool execution")
            candidates = []

        useful = [row for row in candidates if float(row.get("description_match") or 0.0) >= self.semantic_match_floor]
        decision, record_id, reason = self._decide_reuse(
            name=name,
            arguments=clean,
            target=target,
            need=need,
            candidates=useful,
        )
        if decision == "reuse":
            candidate = next((row for row in useful if str(row.get("record_id") or "") == record_id), None)
            if candidate is not None:
                result = {
                    "ok": True,
                    "tool": name,
                    "n1_reused": True,
                    "source": "redis_validation_pool",
                    "target": candidate.get("target"),
                    "description": candidate.get("description"),
                    "value": candidate.get("value"),
                    "num_checks": candidate.get("num_checks"),
                    "previous_value": candidate.get("previous_value"),
                    "changed_at": candidate.get("changed_at"),
                }
                return GateOutcome(
                    False,
                    clean,
                    result=result,
                    target=target,
                    need=need,
                    candidates=useful,
                    note=reason,
                    reset_instruction=reset,
                    signature=signature,
                )

        return GateOutcome(
            True,
            clean,
            target=target,
            need=need,
            candidates=useful,
            note=reason,
            reset_instruction=reset,
            signature=signature,
        )

    def _canonical_value(self, *, name: str, target: str, need: str, result: dict[str, Any]) -> str:
        prompt = (
            "You are N1, Norm's verification gatekeeper. A fresh information tool just returned the raw executor result below. Extract ONLY a compact canonical value that answers the stated needed fact. "
            "The value must be supported by the raw result, must not add interpretation not present in the evidence, and should be stable across repeated equivalent observations. For structured facts, return compact JSON text inside the value string. Return strict JSON with one field named value.\n\n"
            + json.dumps(
                {
                    "tool": name,
                    "target": target,
                    "needed_fact": need,
                    "raw_result": redact(result),
                },
                ensure_ascii=False,
                sort_keys=True,
                default=str,
            )[:180000]
        )
        schema = {
            "type": "object",
            "properties": {"value": {"type": "string"}},
            "required": ["value"],
            "additionalProperties": False,
        }
        raw = self.client.generate(
            prompt,
            think=True,
            temperature=0.0,
            num_predict=700,
            response_format=schema,
            emit_stream=False,
        )
        return str(OllamaClient.parse_json(raw).get("value") or "").strip()

    def after_tool(
        self,
        *,
        task_id: str,
        step_id: str,
        name: str,
        outcome: GateOutcome,
        result: dict[str, Any],
    ) -> dict[str, Any]:
        """Record a fresh result. Return gate metadata; never modify result."""
        if outcome.signature and self.information_tool(name):
            self._store_raw(outcome.signature, outcome.target, result)
        meta: dict[str, Any] = {
            "executed": True,
            "target": outcome.target,
            "need": outcome.need,
            "recorded": False,
        }
        if not self.enabled or not result.get("ok"):
            return meta
        if not self.information_tool(name):
            mutation_targets = [outcome.target]
            for key in ("path", "original_path", "source_path", "destination_path", "trash_path"):
                value = result.get(key)
                if value:
                    mutation_targets.append(str(value))
            paths = result.get("paths")
            if isinstance(paths, list):
                mutation_targets.extend(str(value) for value in paths if value)
            meta["invalidated"] = self._invalidate_after_mutation(*mutation_targets)
            # Arbitrary commands/plugins may mutate state outside the obvious target.
            # Their exact repeated call is still loop-guarded, but they are never put
            # in the Redis answer-reuse pool.
            return meta
        try:
            value = self._canonical_value(name=name, target=outcome.target, need=outcome.need, result=result)
            if not value:
                return meta
            candidates = list(outcome.candidates or [])
            best = candidates[0] if candidates else None
            record_id = "new"
            if best is not None and float(best.get("description_match") or 0.0) >= self.semantic_match_floor:
                record_id = str(best.get("record_id") or "new") or "new"
            recorded = self.live.record_global_validations(
                task_id,
                step_id,
                [
                    {
                        "record_id": record_id,
                        "tool": name,
                        "target": outcome.target,
                        "description": outcome.need,
                        "value": value,
                    }
                ],
            )
            meta.update({"recorded": True, "record_id": record_id, "value": value, "redis": recorded})
        except Exception:
            # Evidence delivery to N2 is more important than poisoning/stalling the
            # task because N1's semantic check-in failed. The raw result still flows.
            logging.exception("N1 verification check-in failed tool=%s target=%s", name, outcome.target)
        return meta
