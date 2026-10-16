from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ContextBudget:
    context_limit_tokens: int
    estimated_fixed_input_tokens: int
    output_reserve_tokens: int
    safety_reserve_tokens: int
    usable_input_tokens: int
    raw_evidence_target_tokens: int
    raw_evidence_target_bytes: int
    compact_state_max_tokens: int

    def as_dict(self) -> dict[str, int]:
        return {
            "context_limit_tokens": self.context_limit_tokens,
            "estimated_fixed_input_tokens": self.estimated_fixed_input_tokens,
            "output_reserve_tokens": self.output_reserve_tokens,
            "safety_reserve_tokens": self.safety_reserve_tokens,
            "usable_input_tokens": self.usable_input_tokens,
            "raw_evidence_target_tokens": self.raw_evidence_target_tokens,
            "raw_evidence_target_bytes": self.raw_evidence_target_bytes,
            "compact_state_max_tokens": self.compact_state_max_tokens,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "ContextBudget":
        return cls(
            context_limit_tokens=max(1, int(value.get("context_limit_tokens") or 1)),
            estimated_fixed_input_tokens=max(0, int(value.get("estimated_fixed_input_tokens") or 0)),
            output_reserve_tokens=max(0, int(value.get("output_reserve_tokens") or 0)),
            safety_reserve_tokens=max(0, int(value.get("safety_reserve_tokens") or 0)),
            usable_input_tokens=max(0, int(value.get("usable_input_tokens") or 0)),
            raw_evidence_target_tokens=max(0, int(value.get("raw_evidence_target_tokens") or 0)),
            raw_evidence_target_bytes=max(1, int(value.get("raw_evidence_target_bytes") or 1)),
            compact_state_max_tokens=max(1, int(value.get("compact_state_max_tokens") or 1)),
        )


@dataclass
class EvidenceStream:
    stream_id: str
    path: str
    source_fingerprint: str
    source_size_bytes: int
    chunk_bytes: int
    estimated_pieces: int
    budget: ContextBudget
    base_byte: int = 0
    retained_by_piece: dict[int, str] = field(default_factory=dict)
    last_delivered_piece: int = 0


class ChunkedEvidenceManager:
    """Bound one large raw text payload to one N2 turn at a time.

    The manager deliberately keeps source addressing in bytes instead of model
    tokens.  Model/context changes may alter the chunk size for a *new* stream,
    while an active stream keeps one stable byte map so piece numbers and seam
    markers remain deterministic.
    """

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        cfg = dict(config or {})
        self.enabled = bool(cfg.get("enabled", True))
        self.raw_fraction = min(0.80, max(0.01, float(cfg.get("raw_evidence_fraction", 0.20))))
        self.safety_fraction = min(0.40, max(0.0, float(cfg.get("safety_fraction", 0.08))))
        self.compact_fraction = min(0.80, max(0.01, float(cfg.get("compact_state_max_fraction", 0.30))))
        self.chars_per_token = max(1.0, float(cfg.get("estimated_chars_per_token", 3.5)))
        self.text_bytes_per_token = max(1.0, float(cfg.get("estimated_text_bytes_per_token", 3.0)))
        self.min_chunk_tokens = max(128, int(cfg.get("min_raw_evidence_tokens", 1024)))
        self.max_chunk_tokens = max(self.min_chunk_tokens, int(cfg.get("max_raw_evidence_tokens", 32768)))
        self.min_safety_tokens = max(0, int(cfg.get("min_safety_tokens", 2048)))
        self.max_retained_chars_per_piece = max(256, int(cfg.get("max_retained_chars_per_piece", 5000)))
        self.min_stream_bytes = max(1024, int(cfg.get("min_stream_bytes", 16384)))
        # This is an invariant, not a tuning hint.  Values >1 are ignored.
        self.max_raw_items_per_turn = 1
        self.streams: dict[str, EvidenceStream] = {}

    def estimate_tokens(self, value: Any) -> int:
        if value is None:
            return 0
        if not isinstance(value, str):
            value = json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)
        if not value:
            return 0
        return max(1, int(math.ceil(len(value) / self.chars_per_token)))

    def budget(
        self,
        *,
        context_limit_tokens: int,
        output_reserve_tokens: int,
        system_instructions: str,
        messages: list[dict[str, Any]],
        tool_schemas: list[dict[str, Any]],
        max_chunk_bytes: int | None = None,
    ) -> ContextBudget:
        context_limit = max(1, int(context_limit_tokens))
        output_reserve = max(1, int(output_reserve_tokens))
        fixed_payload = {
            "system": str(system_instructions or ""),
            "messages": messages,
            "tools": tool_schemas,
        }
        fixed_tokens = self.estimate_tokens(fixed_payload)
        safety = max(self.min_safety_tokens, int(math.ceil(context_limit * self.safety_fraction)))
        usable = max(0, context_limit - output_reserve - safety - fixed_tokens)
        if usable <= 0:
            target_tokens = 0
        else:
            target_tokens = int(math.floor(usable * self.raw_fraction))
            target_tokens = max(min(self.min_chunk_tokens, usable), target_tokens)
            target_tokens = min(target_tokens, self.max_chunk_tokens, usable)
        target_bytes = max(1024, int(target_tokens * self.text_bytes_per_token)) if target_tokens else 1024
        if max_chunk_bytes is not None:
            target_bytes = min(target_bytes, max(1024, int(max_chunk_bytes)))
        compact_max = max(256, int(math.floor(context_limit * self.compact_fraction)))
        return ContextBudget(
            context_limit_tokens=context_limit,
            estimated_fixed_input_tokens=fixed_tokens,
            output_reserve_tokens=output_reserve,
            safety_reserve_tokens=safety,
            usable_input_tokens=usable,
            raw_evidence_target_tokens=target_tokens,
            raw_evidence_target_bytes=target_bytes,
            compact_state_max_tokens=compact_max,
        )

    @staticmethod
    def seam_before(piece: int) -> str:
        piece = max(1, int(piece))
        return f"{{{piece - 1}:{piece}}}"

    @staticmethod
    def seam_after(piece: int, estimated_pieces: int) -> str:
        piece = max(1, int(piece))
        total = max(piece, int(estimated_pieces))
        return f"{{{piece}:0}}" if piece >= total else f"{{{piece}:{piece + 1}}}"

    @staticmethod
    def _stream_id(path: str, source_fingerprint: str, chunk_bytes: int, base_byte: int = 0) -> str:
        material = f"{path}\0{source_fingerprint}\0{int(chunk_bytes)}\0{int(base_byte)}".encode(
            "utf-8", errors="surrogatepass"
        )
        return "evidence-" + hashlib.sha256(material).hexdigest()[:20]

    def create_stream(
        self,
        *,
        path: str,
        source_fingerprint: str,
        source_size_bytes: int,
        chunk_bytes: int,
        budget: ContextBudget,
        base_byte: int = 0,
    ) -> EvidenceStream:
        size = max(0, int(source_size_bytes))
        width = max(1, int(chunk_bytes))
        base = min(size, max(0, int(base_byte))) if size else max(0, int(base_byte))
        remaining = max(0, size - base)
        total = max(1, int(math.ceil(remaining / width))) if remaining else 1
        stream_id = self._stream_id(path, source_fingerprint, width, base)
        stream = EvidenceStream(
            stream_id=stream_id,
            path=str(path),
            source_fingerprint=str(source_fingerprint),
            source_size_bytes=size,
            chunk_bytes=width,
            estimated_pieces=total,
            budget=budget,
            base_byte=base,
        )
        self.streams[stream_id] = stream
        return stream

    def get(self, stream_id: str) -> EvidenceStream | None:
        return self.streams.get(str(stream_id or "").strip())

    def snapshot_streams(self) -> dict[str, dict[str, Any]]:
        """Return the tiny stream cursor state safe to keep in existing job metadata."""
        return {stream_id: self.stream_state(stream) for stream_id, stream in self.streams.items()}

    def restore_streams(self, states: dict[str, Any] | None) -> int:
        """Restore stream cursors after the existing worker checkpoint requeues a step."""
        restored = 0
        for raw in dict(states or {}).values():
            if not isinstance(raw, dict):
                continue
            try:
                self.restore_stream(raw)
            except (TypeError, ValueError):
                continue
            restored += 1
        return restored

    def retained_summary(self) -> str:
        """Compact source-addressed facts suitable for an existing continuation checkpoint."""
        lines: list[str] = []
        for stream in self.streams.values():
            if not stream.retained_by_piece:
                continue
            lines.append(
                f"stream={stream.stream_id} path={stream.path} fingerprint={stream.source_fingerprint} "
                f"pieces={stream.estimated_pieces} chunk_bytes={stream.chunk_bytes}"
            )
            for piece in sorted(stream.retained_by_piece):
                lines.append(
                    f"piece {piece} {self.seam_before(piece)}..{self.seam_after(piece, stream.estimated_pieces)}: "
                    f"{stream.retained_by_piece[piece]}"
                )
        return "\n".join(lines)

    def piece_arguments(self, stream: EvidenceStream, piece: int) -> dict[str, int]:
        index = int(piece)
        if index < 1 or index > stream.estimated_pieces:
            raise ValueError(f"piece must be between 1 and {stream.estimated_pieces}")
        return {
            "start_byte": stream.base_byte + (index - 1) * stream.chunk_bytes,
            "max_bytes": stream.chunk_bytes,
        }

    def stream_state(self, stream: EvidenceStream) -> dict[str, Any]:
        return {
            "stream_id": stream.stream_id,
            "path": stream.path,
            "source_fingerprint": stream.source_fingerprint,
            "source_size_bytes": stream.source_size_bytes,
            "chunk_bytes": stream.chunk_bytes,
            "estimated_pieces": stream.estimated_pieces,
            "base_byte": stream.base_byte,
            "budget": stream.budget.as_dict(),
            "retained_by_piece": {str(k): v for k, v in stream.retained_by_piece.items()},
            "last_delivered_piece": stream.last_delivered_piece,
        }

    def restore_stream(self, state: dict[str, Any]) -> EvidenceStream:
        budget_raw = dict(state.get("budget") or {})
        budget = ContextBudget(
            context_limit_tokens=int(budget_raw.get("context_limit_tokens") or 1),
            estimated_fixed_input_tokens=int(budget_raw.get("estimated_fixed_input_tokens") or 0),
            output_reserve_tokens=int(budget_raw.get("output_reserve_tokens") or 1),
            safety_reserve_tokens=int(budget_raw.get("safety_reserve_tokens") or 0),
            usable_input_tokens=int(budget_raw.get("usable_input_tokens") or 0),
            raw_evidence_target_tokens=int(budget_raw.get("raw_evidence_target_tokens") or 0),
            raw_evidence_target_bytes=int(budget_raw.get("raw_evidence_target_bytes") or 1024),
            compact_state_max_tokens=int(budget_raw.get("compact_state_max_tokens") or 256),
        )
        stream = EvidenceStream(
            stream_id=str(state.get("stream_id") or ""),
            path=str(state.get("path") or ""),
            source_fingerprint=str(state.get("source_fingerprint") or ""),
            source_size_bytes=max(0, int(state.get("source_size_bytes") or 0)),
            chunk_bytes=max(1, int(state.get("chunk_bytes") or 1)),
            estimated_pieces=max(1, int(state.get("estimated_pieces") or 1)),
            budget=budget,
            base_byte=max(0, int(state.get("base_byte") or 0)),
            retained_by_piece={
                int(k): str(v)
                for k, v in dict(state.get("retained_by_piece") or {}).items()
                if str(k).isdigit() and str(v).strip()
            },
            last_delivered_piece=max(0, int(state.get("last_delivered_piece") or 0)),
        )
        expected_stream_id = self._stream_id(
            stream.path, stream.source_fingerprint, stream.chunk_bytes, stream.base_byte
        )
        if stream.stream_id and stream.stream_id != expected_stream_id:
            raise ValueError("evidence stream state does not match its source fingerprint/chunk map")
        stream.stream_id = expected_stream_id
        self.streams[stream.stream_id] = stream
        return stream

    def retain(self, stream: EvidenceStream, piece: int, text: str) -> str:
        clean = " ".join(str(text or "").strip().split())
        # Reserve the compact-state partition across the whole stream.  If a file
        # has many pieces, each piece gets a proportionally smaller fact allowance
        # so retained notes cannot silently consume the raw-evidence slot.
        stream_chars = int(stream.budget.compact_state_max_tokens * self.chars_per_token)
        fair_share = max(256, stream_chars // max(1, stream.estimated_pieces))
        limit = min(self.max_retained_chars_per_piece, fair_share)
        if len(clean) > limit:
            clean = clean[:limit].rstrip() + "…"
        if clean:
            stream.retained_by_piece[int(piece)] = clean
        return clean

    def wrap_result(self, result: dict[str, Any], stream: EvidenceStream, piece: int) -> dict[str, Any]:
        wrapped = dict(result)
        index = int(piece)
        stream.last_delivered_piece = index
        before = self.seam_before(index)
        after = self.seam_after(index, stream.estimated_pieces)
        content = str(wrapped.get("content") or "")
        wrapped["content"] = before + "\n" + content + "\n" + after
        wrapped["chunk_stream"] = {
            "stream_id": stream.stream_id,
            "piece": index,
            "estimated_pieces": stream.estimated_pieces,
            "chunk_bytes": stream.chunk_bytes,
            "seam_before": before,
            "seam_after": after,
            "source_fingerprint": stream.source_fingerprint,
            "source_size_bytes": stream.source_size_bytes,
            "stream_base_byte": stream.base_byte,
            "byte_start": stream.base_byte + (index - 1) * stream.chunk_bytes,
            "byte_end_exclusive": min(
                stream.base_byte + index * stream.chunk_bytes, stream.source_size_bytes
            ),
            "policy": "one raw evidence piece per N2 turn; retain compact facts before requesting one next piece",
        }
        wrapped["context_budget"] = stream.budget.as_dict()
        return wrapped

    def evict_tool_message(
        self,
        messages: list[dict[str, Any]],
        *,
        stream: EvidenceStream,
        piece: int,
        retained: str,
    ) -> bool:
        """Replace the already-consumed raw tool payload with a small provenance stub."""
        for message in reversed(messages):
            if str(message.get("role") or "") != "tool":
                continue
            raw = message.get("content")
            if not isinstance(raw, str):
                continue
            try:
                payload = json.loads(raw)
            except Exception:
                continue
            meta = payload.get("chunk_stream") if isinstance(payload, dict) else None
            if not isinstance(meta, dict):
                continue
            if str(meta.get("stream_id") or "") != stream.stream_id or int(meta.get("piece") or 0) != int(piece):
                continue
            stub = {
                "ok": bool(payload.get("ok", True)),
                "tool": str(payload.get("tool") or "read_file"),
                "path": str(payload.get("path") or stream.path),
                "sha256": payload.get("sha256"),
                "source_fingerprint": stream.source_fingerprint,
                "chunk_evicted": True,
                "chunk_stream": {
                    "stream_id": stream.stream_id,
                    "piece": int(piece),
                    "estimated_pieces": stream.estimated_pieces,
                    "seam_before": self.seam_before(piece),
                    "seam_after": self.seam_after(piece, stream.estimated_pieces),
                },
                "retained_facts": retained,
                "note": "Raw chunk was evicted after N2 consumed it; durable tool evidence retains the original payload.",
            }
            message["content"] = json.dumps(stub, ensure_ascii=False)
            return True
        return False

    def compact_result_for_active_evidence(self, result: dict[str, Any], retained: str = "") -> dict[str, Any]:
        """Drop raw text from an in-memory evidence copy while preserving provenance."""
        compact = dict(result)
        if isinstance(compact.get("chunk_stream"), dict):
            compact.pop("content", None)
            compact.pop("base64", None)
            compact["raw_chunk_in_durable_evidence"] = True
            if retained:
                compact["retained_facts"] = retained
        return compact
