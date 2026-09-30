from __future__ import annotations

import logging
import re
import shutil
import time
from pathlib import Path
from typing import Any

from .task_storage import retain_task_manifest

_TASK_LINE = re.compile(r"^### Task\s+([^\s/]+)", re.MULTILINE)
_TERMINAL = {"completed", "failed", "cancelled"}


def _age_hours(path: Path, now: float) -> float:
    try:
        return max(0.0, (now - path.stat().st_mtime) / 3600.0)
    except OSError:
        return 0.0


def _safe_remove(path: Path, root: Path) -> tuple[int, int]:
    root = root.resolve()
    resolved = path.resolve()
    if resolved == root or root not in resolved.parents:
        raise ValueError(f"refusing to remove path outside temp root: {path}")
    files = 0
    total = 0
    if path.is_dir() and not path.is_symlink():
        for item in path.rglob("*"):
            if item.is_file():
                files += 1
                try:
                    total += item.stat().st_size
                except OSError:
                    pass
        shutil.rmtree(path, ignore_errors=False)
    elif path.exists() or path.is_symlink():
        files = 1
        try:
            total = path.stat().st_size
        except OSError:
            pass
        path.unlink(missing_ok=True)
    return files, total


def _sos_task_ids(path: Path) -> set[str]:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return set()
    return {m.group(1).strip() for m in _TASK_LINE.finditer(text) if m.group(1).strip()}


def _task_is_safely_terminal(durable: Any, task_id: str) -> bool:
    try:
        status = durable.task_status(task_id)
    except Exception:
        return False
    if status not in _TERMINAL:
        return False
    try:
        return bool(durable.terminal_summary_verified(task_id))
    except Exception:
        return False


def cleanup_task_temp(temp_root: str | Path, task_id: str, durable: Any, *, retention_root: str | Path | None = None) -> dict[str, Any]:
    """Remove temp/tasks/<task_id> only after durable terminal verification."""
    root = Path(temp_root).resolve()
    target = root / "tasks" / str(task_id)
    if not target.exists():
        return {"removed": False, "task_id": str(task_id), "path": str(target)}
    if not _task_is_safely_terminal(durable, str(task_id)):
        return {"removed": False, "task_id": str(task_id), "path": str(target), "reason": "task_not_verified_terminal"}
    retained_manifest = None
    if retention_root is not None:
        try:
            retained_manifest = retain_task_manifest(target, retention_root)
        except Exception:
            logging.exception("Could not retain compact task-storage manifest before cleanup task=%s", task_id)
    files, size = _safe_remove(target, root)
    return {
        "removed": True, "task_id": str(task_id), "path": str(target),
        "files_removed": files, "bytes_removed": size,
        "retained_manifest": str(retained_manifest) if retained_manifest else None,
    }


def cleanup_temp_root(
    temp_root: str | Path,
    *,
    durable: Any | None = None,
    max_age_hours: int = 72,
    recovery_max_age_hours: int = 168,
    retention_root: str | Path | None = None,
) -> dict[str, Any]:
    """Safely clean Norm's external disposable temp tree.

    Rules:
    - temp/tasks/<task_id> is removed only when that task is durably terminal and verified.
    - temp/recovery/SOS*.md is removed only when every referenced task is durably terminal,
      and the file is older than recovery_max_age_hours. If no task IDs can be recovered,
      it is retained rather than guessed disposable.
    - other first-level temp content is age-based and removed after max_age_hours.
    - the temp root itself is never removed.
    """
    root = Path(temp_root).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    (root / "tasks").mkdir(exist_ok=True)
    (root / "recovery").mkdir(exist_ok=True)
    (root / "scratch").mkdir(exist_ok=True)
    now = time.time()
    result: dict[str, Any] = {
        "path": str(root), "files_removed": 0, "bytes_removed": 0,
        "task_dirs_removed": 0, "recovery_files_removed": 0, "aged_items_removed": 0,
        "preserved": [],
    }

    tasks_dir = root / "tasks"
    if durable is not None:
        for child in list(tasks_dir.iterdir()):
            if not child.is_dir():
                continue
            outcome = cleanup_task_temp(root, child.name, durable, retention_root=retention_root)
            if outcome.get("removed"):
                result["task_dirs_removed"] += 1
                result["files_removed"] += int(outcome.get("files_removed", 0))
                result["bytes_removed"] += int(outcome.get("bytes_removed", 0))
            else:
                result["preserved"].append({"path": str(child), "reason": outcome.get("reason", "active_or_unverified_task")})

    recovery_dir = root / "recovery"
    for item in list(recovery_dir.iterdir()):
        if not item.is_file():
            continue
        age = _age_hours(item, now)
        if age < max(1, recovery_max_age_hours):
            continue
        ids = _sos_task_ids(item) if item.name.lower().startswith("sos") else set()
        if item.name.lower().startswith("sos"):
            if durable is None or not ids or not all(_task_is_safely_terminal(durable, task_id) for task_id in ids):
                result["preserved"].append({"path": str(item), "reason": "recovery_tasks_not_verified_terminal"})
                continue
        files, size = _safe_remove(item, root)
        result["recovery_files_removed"] += 1
        result["files_removed"] += files
        result["bytes_removed"] += size

    reserved = {"tasks", "recovery"}
    for item in list(root.iterdir()):
        if item.name in reserved:
            continue
        if _age_hours(item, now) < max(1, max_age_hours):
            continue
        try:
            files, size = _safe_remove(item, root)
        except Exception as exc:
            logging.warning("Temp cleanup could not remove %s: %s", item, exc)
            result["preserved"].append({"path": str(item), "reason": f"remove_failed:{type(exc).__name__}"})
            continue
        result["aged_items_removed"] += 1
        result["files_removed"] += files
        result["bytes_removed"] += size

    return result
