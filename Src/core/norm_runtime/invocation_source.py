"""Trusted invocation provenance for Norm internal N1/N2 exchanges.

The source is assigned by the caller and must not be inferred from quoted text.
Never put this field into Ollama's message-role objects; use the JSON payload or
content envelope, preserving only genuine operator turns as user-role messages.
"""
from __future__ import annotations
import json

USER_PROMPT_ORIGINS = frozenset({"user_prompt", "prompt_interface", "gui_prompt", "ssh_prompt"})
INTERNAL_SOURCES = frozenset({"n1", "n2", "system"})


def work_invoke_source(request_type: str, prompt_origin: str, metadata: dict | None = None) -> str:
    """Only the known user-ingress boundary may produce invoke_source=user."""
    if request_type == "prompt" and prompt_origin in USER_PROMPT_ORIGINS:
        return "user"
    provided = str((metadata or {}).get("invoke_source") or "").strip().lower()
    if provided in INTERNAL_SOURCES:
        return provided
    # These runtime work items are authored by N2's planner; they are NOT fresh user turns.
    if prompt_origin in {"norm_generated_step", "runtime_child_subtask"}:
        return "n2"
    return "system"


def invocation_header(source: str, target: str, kind: str) -> str:
    """Machine-readable instruction attribution, separate from payload and user request."""
    if source not in {"user", *INTERNAL_SOURCES}:
        raise ValueError("invalid invoke_source")
    if target not in {"n1", "n2", "system"}:
        raise ValueError("invalid invocation target")
    return json.dumps({"invoke_source": source, "invoke_target": target, "kind": kind}, ensure_ascii=False)
