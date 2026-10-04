from __future__ import annotations

from pathlib import Path

from norm_runtime.file_access_policy import authorize_path, load_file_access_policy


def _root() -> Path:
    return Path(__file__).resolve().parents[3]


def _queue():
    from runtime_bootstrap import build_deletion_queue
    return build_deletion_queue(_root())


def _allowed(path: str) -> Path:
    policy = load_file_access_policy(_root())
    return authorize_path(path, policy.write_roots, access="write", hardlock=policy.enforce_write_directories)


def soft_delete(path: str, reason: str) -> dict:
    """Move one allowed file into Norm's reversible trash."""
    target = _allowed(path)
    return _queue().stage_file(target, reason=reason)


def soft_delete_batch(paths: list[str], reason: str) -> dict:
    """Move several allowed files into trash as one checkpointed cleanup batch."""
    if not paths:
        raise ValueError("paths must not be empty")
    items = []
    for raw in paths:
        target = _allowed(raw)
        if not target.is_file():
            raise FileNotFoundError(target)
        items.append({"path": str(target), "reason": reason})
    return _queue().stage_batch(items, batch_reason=reason, checkpoint=True)


def delete_list() -> dict:
    """List current reversible trash items."""
    items = _queue().list_items()
    return {"count": len(items), "items": items}


def restore_delete(deletion_id: str) -> dict:
    """Restore one deletion id, or pass 'all' to restore every non-conflicting item."""
    return _queue().restore(deletion_id)


def delete_files() -> dict:
    """Permanently purge every currently staged trash payload."""
    return _queue().purge_all()


def reconcile_trash() -> dict:
    """Reconcile Redis/JSON metadata with physical trash contents and checkpoint once."""
    return _queue().reconcile(checkpoint=True)
