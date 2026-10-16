"""Read-only, cross-layer operator queue inventory for /queue-full.

Do not confuse an empty GUI ingress stream with no runnable/recoverable Norm work.
No status changes, resume operations or queue mutations occur here.
"""
from __future__ import annotations

from typing import Any


def collect_queue_full(*, prompt_queue=None, durable=None, maintenance: dict | None = None) -> dict[str, Any]:
    items: dict[str, dict[str, Any]] = {}
    errors: list[str] = []

    def record(task_id: str, *, location: str, state: str, title: str = "", detail: dict | None = None) -> None:
        task_id = str(task_id or "").strip()
        if not task_id:
            return
        row = items.setdefault(task_id, {"task_id": task_id, "title": "", "state": "", "locations": [], "details": []})
        if title and not row["title"]:
            row["title"] = str(title)[:240]
        row["locations"].append(location)
        if not row["state"] or state in {"running", "in-flight", "suppressed", "failed-resumable"}:
            row["state"] = state
        if detail:
            row["details"].append(detail)

    if prompt_queue is None:
        errors.append("Redis worker queue not initialized")
    else:
        pending: set[str] = set()
        try:
            for row in prompt_queue.r.xpending_range(prompt_queue.stream, prompt_queue.group, "-", "+", 10000):
                pending.add(str(row.get("message_id") or ""))
        except Exception as exc:
            errors.append(f"Redis pending state: {type(exc).__name__}: {exc}")
        for name, stream in (
            ("worker:work", prompt_queue.stream), ("worker:parked-retry", prompt_queue.retry_stream),
            ("worker:escalation", prompt_queue.escalation_stream), ("worker:dead-letter", prompt_queue.dead_letter_stream),
        ):
            try:
                for entry_id, fields in prompt_queue.r.xrange(stream, min="-", max="+"):
                    task_id = fields.get("task_id")
                    state = "in-flight" if name == "worker:work" and str(entry_id) in pending else name.partition(":")[2]
                    record(task_id, location=name, state=state,
                           detail={"step_id": str(fields.get("step_id") or ""),
                                   "stream_id": str(entry_id), "attempt": str(fields.get("attempt") or "0")})
            except Exception as exc:
                errors.append(f"{name}: {type(exc).__name__}: {exc}")

    if durable is None:
        errors.append("PostgreSQL durable task state not initialized")
    else:
        for location, method, state in (
            ("postgres:running", "running_tasks", "running"),
            ("postgres:failed", "recent_failed_tasks", "failed"),
            ("postgres:suppressed", "suppressed_tasks", "suppressed"),
        ):
            try:
                records = list(getattr(durable, method)(limit=10000))
                if len(records) == 10000:
                    errors.append(f"{location}: reached 10000-row snapshot limit; more records may exist")
                for task in records:
                    task_id = str(task.get("task_id") or "")
                    is_resumable = bool(task.get("resumable")) if state == "failed" else True
                    row_state = "failed-resumable" if state == "failed" and is_resumable else ("failed-not-resumable" if state == "failed" else state)
                    record(task_id, location=location, state=row_state,
                           title=str(task.get("title") or ""),
                           detail={"updated_at": str(task.get("updated_at") or task.get("failed_at") or ""),
                                   "resumable": is_resumable, "status": str(task.get("status") or state)})
            except Exception as exc:
                errors.append(f"{location}: {type(exc).__name__}: {exc}")
    rows = list(items.values())
    for row in rows:
        row["locations"] = sorted(set(row["locations"]))
        row["step_count"] = sum(1 for detail in row["details"] if "step_id" in detail)
    rows.sort(key=lambda x: (x["state"] not in {"running", "in-flight", "work", "retry"}, x["task_id"]))
    return {
        "status": "partial" if errors else "ok", "count": len(rows), "tasks": rows,
        "errors": errors, "maintenance": maintenance or {},
        "scope": "worker Redis streams + PostgreSQL running/failed/suppressed; GUI ingress and uncertain-delivery are joined by /queue-full",
    }
