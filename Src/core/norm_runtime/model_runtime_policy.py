from __future__ import annotations

from typing import Any


def _instruction_list(value: Any) -> list[str]:
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, (list, tuple)):
        return []
    out: list[str] = []
    seen: set[str] = set()
    for item in value:
        text = str(item or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        out.append(text)
    return out


def agent_persistent_instructions(config: dict, agent: str) -> list[str]:
    """Return the runtime-owned persistent instructions for one agent role.

    0.54.0e stores role-specific instruction sets under ``agents.n1`` and
    ``agents.n2``.  The legacy top-level ``persistent_instructions`` list is only
    a compatibility fallback for installations upgraded from an older runtime.
    """
    agents = dict((config or {}).get("agents", {}) or {})
    agent_cfg = dict(agents.get(str(agent), {}) or {})
    explicit = _instruction_list(agent_cfg.get("persistent_instructions"))
    if explicit:
        return explicit
    return _instruction_list((config or {}).get("persistent_instructions"))


def agent_model_client_kwargs(config: dict, agent: str) -> dict[str, Any]:
    """Build model policy that stays attached when the selected Ollama model changes."""
    config = dict(config or {})
    runtime = dict(config.get("model_runtime", {}) or {})
    agents = dict(config.get("agents", {}) or {})
    agent_cfg = dict(agents.get(str(agent), {}) or {})

    context_length = agent_cfg.get("context_length", runtime.get("default_context_length", 65536))
    overrides = dict(runtime.get("model_context_overrides", {}) or {})
    overrides.update(dict(agent_cfg.get("model_context_overrides", {}) or {}))

    return {
        "context_length": max(1, int(context_length)),
        "model_context_overrides": overrides,
        "system_instructions": agent_persistent_instructions(config, agent),
    }

def effective_context_length(config: dict, agent: str, model: str) -> int:
    policy = agent_model_client_kwargs(config, agent)
    overrides = {str(k).casefold(): int(v) for k, v in dict(policy.get("model_context_overrides", {}) or {}).items()}
    folded = str(model or "").strip().casefold()
    base = folded.split(":", 1)[0]
    for key in (folded, base):
        if key in overrides:
            return max(1, int(overrides[key]))
    return max(1, int(policy.get("context_length") or 65536))

