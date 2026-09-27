from __future__ import annotations

from typing import Any


def full_context_status() -> dict[str, Any]:
    return {"context_state": "full", "message": "full context", "unresponsive_connections": []}


def impaired_context_status(connection: str, reason: str) -> dict[str, Any]:
    return {
        "context_state": "impaired",
        "message": "generating with impaired context",
        "unresponsive_connections": [
            {"connection": str(connection), "reason": str(reason)}
        ],
    }


def merge_resource_status(*statuses: dict[str, Any] | None) -> dict[str, Any]:
    failures: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for status in statuses:
        if not isinstance(status, dict):
            continue
        for item in status.get("unresponsive_connections") or []:
            if not isinstance(item, dict):
                continue
            connection = str(item.get("connection") or "unknown")
            reason = str(item.get("reason") or "unresponsive")
            key = (connection, reason)
            if key not in seen:
                seen.add(key)
                failures.append({"connection": connection, "reason": reason})
    return {"context_state": "impaired" if failures else "full", "message": "generating with impaired context" if failures else "full context", "unresponsive_connections": failures}
