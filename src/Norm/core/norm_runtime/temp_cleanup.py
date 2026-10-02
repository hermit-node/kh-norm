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


def _remove_empty_tree(path: Path, root: Path) -> int:
    root = root.resolve()
    resolved = path.resolve()
    if resolved == root or root not in resolved.parents:
        raise ValueError(f"refusing to remove path outside temp root: {path}")
    removed = 0
    for directory in sorted((p for p in resolved.rglob("*") if p.is_dir() and not p.is_symlink()), key=lambda p: len(p.parts), reverse=True):
        try:
            directory.rmdir()
            removed += 1
        except OSError:
            pass
    try:
        resolved.rmdir()
        removed += 1
    except OSError:
        pass
    return removed


def cleanup_task_temp(
    temp_root: str | Path,
    task_id: str,
    durable: Any,
    *,
    retention_root: str | Path | None = None,
    deletion_queue: Any | None = None,
) -> dict[str, Any]:
    """Soft-delete verified terminal task temp after retaining its compact manifest.

    No file in a task directory is permanently removed here.  If reversible trash is
    unavailable or declines an item (for example a huge cross-volume move), the file is
    preserved in place and reported.
    """
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
            return {"removed": False, "task_id": str(task_id), "path": str(target), "reason": "retention_manifest_failed"}
    files = [p for p in target.rglob("*") if p.is_file() and not p.is_symlink()]
    if files and deletion_queue is None:
        return {
            "removed": False, "task_id": str(task_id), "path": str(target),
            "reason": "reversible_trash_unavailable", "files_preserved": len(files),
            "retained_manifest": str(retained_manifest) if retained_manifest else None,
        }
    staged = []
    skipped = []
    if files:
        result = deletion_queue.stage_batch(
            [{
                "path": str(item),
                "reason": "verified terminal task temp is reproducible from retained task state",
                "task_id": str(task_id),
                "reproduce_from": str(retained_manifest or "durable PostgreSQL task evidence"),
                "reproducibility": "easy",
            } for item in files],
            task_id=str(task_id),
            batch_reason="verified terminal task temp cleanup",
            checkpoint=True,
        )
        staged = list(result.get("staged") or [])
        skipped = list(result.get("skipped") or [])
    _remove_empty_tree(target, root)
    remaining = [p for p in target.rglob("*") if p.is_file()] if target.exists() else []
    return {
        "removed": not target.exists(), "task_id": str(task_id), "path": str(target),
        "files_soft_deleted": len(staged), "files_preserved": len(remaining),
        "skipped": skipped,
        "retained_manifest": str(retained_manifest) if retained_manifest else None,
    }


def cleanup_temp_root(
    temp_root: str | Path,
    *,
    durable: Any | None = None,
    max_age_hours: int = 72,
    recovery_max_age_hours: int = 168,
    retention_root: str | Path | None = None,
    deletion_queue: Any | None = None,
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
            outcome = cleanup_task_temp(root, child.name, durable, retention_root=retention_root, deletion_queue=deletion_queue)
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
            candidates = [item] if item.is_file() else [p for p in item.rglob("*") if p.is_file() and not p.is_symlink()]
            if candidates and deletion_queue is None:
                result["preserved"].append({"path": str(item), "reason": "reversible_trash_unavailable"})
                continue
            stage = {"staged": [], "skipped": []}
            if candidates:
                stage = deletion_queue.stage_batch(
                    [{"path": str(p), "reason": "aged Norm temp output", "reproducibility": "easy"} for p in candidates],
                    batch_reason="aged temp cleanup", checkpoint=True,
                )
            if item.is_dir():
                _remove_empty_tree(item, root)
            result["aged_items_removed"] += 1 if not item.exists() else 0
            result["files_removed"] += len(stage.get("staged") or [])
            result["bytes_removed"] += sum(int(x.get("size_bytes") or 0) for x in stage.get("staged") or [])
            if stage.get("skipped") or item.exists():
                result["preserved"].append({"path": str(item), "reason": "trash_stage_partial", "skipped": stage.get("skipped") or []})
        except Exception as exc:
            logging.warning("Temp cleanup could not soft-delete %s: %s", item, exc)
            result["preserved"].append({"path": str(item), "reason": f"soft_delete_failed:{type(exc).__name__}"})
            continue

    return result
