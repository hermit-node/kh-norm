from __future__ import annotations

import hashlib
import json
import os
import shutil
import time
import zipfile
from pathlib import Path
from typing import Any

_GIB = 1024 ** 3
_MIB = 1024 ** 2

DEFAULTS = {
    "per_pass_process_bytes": 3 * _GIB,
    "task_storage_limit_bytes": 54 * _GIB,
    "text_processing_buffer_bytes": 24 * _MIB,
    "tool_return_bytes": 384 * 1024,
    "human_output_bytes": 384 * 1024,
    "internal_note_chunk_bytes": 5 * _MIB,
    "image_processing_buffer_bytes": 96 * _MIB,
    "media_processing_buffer_bytes": 256 * _MIB,
}


class TaskStorageLimitReached(RuntimeError):
    def __init__(self, message: str, *, checkpoint_zip: str, used_bytes: int, limit_bytes: int) -> None:
        super().__init__(message)
        self.checkpoint_zip = checkpoint_zip
        self.used_bytes = int(used_bytes)
        self.limit_bytes = int(limit_bytes)


def _utc_stamp() -> str:
    import datetime as _dt
    return _dt.datetime.now(_dt.timezone.utc).isoformat()


def _sha256(path: Path, block: int = 4 * _MIB) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(block), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _atomic_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp-{os.getpid()}-{time.time_ns()}")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(tmp, path)


def _tree_bytes(root: Path) -> int:
    total = 0
    if not root.exists():
        return 0
    for item in root.rglob("*"):
        try:
            if item.is_file() and not item.is_symlink():
                total += item.stat().st_size
        except OSError:
            pass
    return total


class TaskStorageManager:
    """Task-local storage/accounting for large-source work.

    Source files are references and do not count toward managed task storage. Norm-owned
    task material lives under temp/tasks/<task_id> and is capped independently.
    """

    def __init__(self, temp_root: str | Path, workspace_root: str | Path, config: dict[str, Any] | None = None) -> None:
        cfg = dict(DEFAULTS)
        cfg.update({k: int(v) for k, v in dict(config or {}).items() if k in cfg and v is not None})
        self.temp_root = Path(temp_root).expanduser().resolve()
        self.workspace_root = Path(workspace_root).expanduser().resolve()
        self.config = cfg
        self.task_id: str | None = None
        self.step_id: str | None = None

    def bind(self, task_id: str | None, step_id: str | None = None) -> None:
        self.task_id = str(task_id or "").strip() or None
        self.step_id = str(step_id or "").strip() or None
        if self.task_id:
            self.task_root.mkdir(parents=True, exist_ok=True)
            self._ensure_manifest()

    @property
    def task_root(self) -> Path:
        if not self.task_id:
            raise RuntimeError("task storage is not bound to a task")
        return self.temp_root / "tasks" / self.task_id

    @property
    def manifest_path(self) -> Path:
        return self.task_root / "task-storage.json"

    def _ensure_manifest(self) -> dict[str, Any]:
        if self.manifest_path.exists():
            try:
                data = json.loads(self.manifest_path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    return data
            except Exception:
                pass
        data = {
            "schema": 1,
            "task_id": self.task_id,
            "created_at": _utc_stamp(),
            "updated_at": _utc_stamp(),
            "limits": dict(self.config),
            "pass_processed_bytes": 0,
            "lifetime_processed_bytes": 0,
            "sources": [],
            "assets": [],
            "notes": [],
        }
        _atomic_json(self.manifest_path, data)
        return data

    def _load(self) -> dict[str, Any]:
        return self._ensure_manifest()

    def _save(self, data: dict[str, Any]) -> None:
        data["updated_at"] = _utc_stamp()
        _atomic_json(self.manifest_path, data)

    def usage_bytes(self) -> int:
        return _tree_bytes(self.task_root) if self.task_id else 0

    def capacity(self) -> dict[str, Any]:
        used = self.usage_bytes()
        limit = int(self.config["task_storage_limit_bytes"])
        return {"used_bytes": used, "limit_bytes": limit, "remaining_bytes": max(0, limit - used), "full": used >= limit}

    def ensure_capacity(self, additional_bytes: int = 0) -> None:
        used = self.usage_bytes()
        limit = int(self.config["task_storage_limit_bytes"])
        if used + max(0, int(additional_bytes)) > limit:
            checkpoint = self.create_checkpoint_zip(reason="task_storage_limit")
            raise TaskStorageLimitReached(
                f"task managed-storage ceiling reached ({used} + {additional_bytes} > {limit} bytes); checkpoint preserved",
                checkpoint_zip=str(checkpoint), used_bytes=used, limit_bytes=limit,
            )

    def register_source(self, path: str | Path, *, sha256: str | None = None) -> dict[str, Any]:
        if not self.task_id:
            return {}
        src = Path(path).resolve()
        stat = src.stat()
        data = self._load()
        key = str(src)
        found = next((x for x in data.get("sources", []) if x.get("path") == key), None)
        item = {
            "path": key,
            "size_bytes": int(stat.st_size),
            "mtime_ns": int(stat.st_mtime_ns),
            "last_seen_at": _utc_stamp(),
        }
        if sha256:
            item["sha256"] = sha256
        if found is None:
            data.setdefault("sources", []).append(item)
        else:
            found.update(item)
        self._save(data)
        return item

    def add_processed_bytes(self, count: int) -> dict[str, Any]:
        if not self.task_id:
            return {"park_required": False}
        count = max(0, int(count))
        data = self._load()
        data["pass_processed_bytes"] = int(data.get("pass_processed_bytes", 0)) + count
        data["lifetime_processed_bytes"] = int(data.get("lifetime_processed_bytes", 0)) + count
        limit = int(self.config["per_pass_process_bytes"])
        park = data["pass_processed_bytes"] >= limit
        checkpoint = None
        self._save(data)
        if park:
            checkpoint = self.create_checkpoint_zip(reason="per_pass_processing_limit")
        return {
            "pass_processed_bytes": data["pass_processed_bytes"],
            "lifetime_processed_bytes": data["lifetime_processed_bytes"],
            "pass_limit_bytes": limit,
            "park_required": park,
            "checkpoint_zip": str(checkpoint) if checkpoint else None,
        }

    def reset_pass(self) -> None:
        if not self.task_id:
            return
        data = self._load()
        data["pass_processed_bytes"] = 0
        self._save(data)

    def register_asset(
        self,
        path: str | Path,
        *,
        role: str,
        source: str | Path | None = None,
        reproducible: bool = False,
        recipe: str | None = None,
        retention: str = "ephemeral",
    ) -> dict[str, Any]:
        if not self.task_id:
            return {}
        target = Path(path).resolve()
        try:
            size = target.stat().st_size if target.is_file() else _tree_bytes(target)
        except OSError:
            size = 0
        item = {
            "path": str(target),
            "role": str(role),
            "source": str(Path(source).resolve()) if source else None,
            "size_bytes": int(size),
            "reproducible": bool(reproducible),
            "recipe": recipe,
            "retention": retention,
            "created_at": _utc_stamp(),
            "step_id": self.step_id,
        }
        data = self._load()
        data.setdefault("assets", []).append(item)
        self._save(data)
        return item

    def append_note(self, text: str, *, category: str = "notes") -> Path:
        if not self.task_id:
            raise RuntimeError("task storage is not bound to a task")
        raw = str(text)
        note_limit = int(self.config["internal_note_chunk_bytes"])
        notes_dir = self.task_root / "notes"
        notes_dir.mkdir(parents=True, exist_ok=True)
        index = 1
        while True:
            path = notes_dir / f"{category}-{index:04d}.md"
            current = path.stat().st_size if path.exists() else 0
            encoded = raw.encode("utf-8")
            if current and current + len(encoded) + 2 > note_limit:
                index += 1
                continue
            self.ensure_capacity(len(encoded) + 2)
            with path.open("a", encoding="utf-8", newline="\n") as handle:
                if current:
                    handle.write("\n\n")
                handle.write(raw)
            data = self._load()
            data.setdefault("notes", []).append({"path": str(path), "category": category, "bytes": len(encoded), "created_at": _utc_stamp()})
            self._save(data)
            return path

    def create_checkpoint_zip(self, *, reason: str) -> Path:
        if not self.task_id:
            raise RuntimeError("task storage is not bound to a task")
        recovery = self.temp_root / "recovery" / "task-checkpoints"
        recovery.mkdir(parents=True, exist_ok=True)
        path = recovery / f"{self.task_id}-checkpoint.zip"
        manifest = self._load()
        manifest["checkpoint_reason"] = reason
        manifest["checkpoint_created_at"] = _utc_stamp()
        self._save(manifest)
        tmp = path.with_name(f".{path.name}.tmp-{time.time_ns()}")
        with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
            if self.manifest_path.exists():
                zf.write(self.manifest_path, "task-storage.json")
            notes = self.task_root / "notes"
            if notes.exists():
                for item in notes.rglob("*"):
                    if item.is_file():
                        zf.write(item, str(Path("notes") / item.relative_to(notes)))
        os.replace(tmp, path)
        return path


def retain_task_manifest(task_root: str | Path, retention_root: str | Path) -> Path | None:
    """Keep compact lineage plus durable note chunks before disposable task bytes go away.

    Reproducible derivatives are intentionally *not* copied. Internal Markdown notes are
    considered useful derived knowledge and survive under workspace/.norm-task-retention.
    """
    root = Path(task_root).resolve()
    manifest = root / "task-storage.json"
    if not manifest.is_file():
        return None
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    task_id = str(data.get("task_id") or root.name)
    out_root = Path(retention_root).expanduser().resolve() / task_id
    out_root.mkdir(parents=True, exist_ok=True)

    kept_notes: list[dict[str, Any]] = []
    notes_root = root / "notes"
    if notes_root.exists():
        target_notes = out_root / "notes"
        target_notes.mkdir(parents=True, exist_ok=True)
        for item in sorted(notes_root.rglob("*")):
            if not item.is_file():
                continue
            rel = item.relative_to(notes_root)
            dst = target_notes / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(item, dst)
            try:
                size = dst.stat().st_size
            except OSError:
                size = 0
            kept_notes.append({"path": str(dst), "bytes": int(size)})

    compact = {
        "schema": 1,
        "task_id": task_id,
        "compacted_at": _utc_stamp(),
        "lifetime_processed_bytes": data.get("lifetime_processed_bytes", 0),
        "sources": data.get("sources", []),
        # Keep provenance/recipes for deleted derivatives without retaining their bytes.
        "asset_lineage": data.get("assets", []),
        "retained_notes": kept_notes,
        "cleanup_policy": {
            "reproducible_assets_retained": False,
            "internal_notes_retained": True,
            "source_files_copied": False,
        },
    }
    out = out_root / "manifest.json"
    _atomic_json(out, compact)
    return out

