from __future__ import annotations

from . import _engine


def status() -> dict:
    """Check whether the configured Codex CLI reasoning lane is available."""
    return _engine.status()


def reason(
    question: str,
    context: str = "",
    mode: str = "analysis",
) -> dict:
    """Ask Codex/ChatGPT for read-only reasoning; Norm remains responsible for every action."""
    return _engine.reason(question=question, context=context, mode=mode)
