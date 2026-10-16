from __future__ import annotations

import hashlib
import json
import os
import shutil
import time
import uuid
from pathlib import Path
from typing import Any, Iterable

import redis


class RedisDeletionQueue:
    """Flat, batch-oriented soft-delete trash with Redis live metadata.

    Physical payloads live directly under ``trash_root``. Redis is the hot index;
    ``trash-index.json`` is a batched recovery checkpoint, never a per-file journal.
    """

    def __init__(
        self,
        *,
        host: str,
        port: int,
        db: int,
        stream: str,
        trash_root: str,
        items_key: str = "norm:trash:items",
        batches_key: str = "norm:trash:batches",
        cross_volume_move_max_bytes: int = 268_435_456,
    ) -> None:
        self.redis = redis.Redis(host=host, port=int(port), db=int(db), decode_responses=True)
        self.legacy_stream = str(stream)
        self.items_key = str(items_key)
        self.batches_key = str(batches_key)
        self.trash_root = Path(trash_root).resolve()
        self.trash_root.mkdir(parents=True, exist_ok=True)
        self.index_path = self.trash_root / "trash-index.json"
        self.cross_volume_move_max_bytes = max(0, int(cross_volume_move_max_bytes))

    @staticmethod
    def _same_volume(a: Path, b: Path) -> bool:
        # Windows drive/UNC roots are meaningful. On POSIX test hosts, Path.drive is
        # blank for both and os.stat().st_dev gives the useful answer when possible.
        if a.drive or b.drive:
            return a.drive.casefold() == b.drive.casefold()
        try:
            a_dev = a.stat().st_dev
            probe = b if b.exists() else b.parent
            probe.mkdir(parents=True, exist_ok=True)
            return a_dev == probe.stat().st_dev
        except OSError:
            return True

    @staticmethod
    def _safe_directory_inventory(source: Path, *, fingerprint: bool = False) -> tuple[dict[str, tuple[int, str]], set[str], int]:
        """Exact recursive inventory. Refuse links/reparse points: they can escape root."""
        paths: dict[str, tuple[int, str]] = {}
        dirs: set[str] = set()
        total = 0
        for parent, children, names in os.walk(source, followlinks=False):
            base = Path(parent)
            relbase = base.relative_to(source)
            for name in children:
                child = base / name
                if child.is_symlink() or getattr(child, "is_junction", lambda: False)():
                    raise PermissionError(f"linked subdirectory is not safe to move: {child}")
                dirs.add((relbase / name).as_posix())
            for name in names:
                file = base / name
                if file.is_symlink() or not file.is_file():
                    raise PermissionError(f"non-regular file in directory: {file}")
                size = file.stat().st_size
                total += size
                digest = ""
                if fingerprint:
                    h = hashlib.sha256()
                    with file.open("rb") as fh:
                        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
                            h.update(chunk)
                    digest = h.hexdigest()
                paths[(relbase / name).as_posix()] = (size, digest)
        return paths, dirs, total

    def _move_verified(self, source: Path, target: Path) -> None:
        """Move a file or directory; verify a cross-volume copy before removing source."""
        source = source.resolve(strict=True)
        target = target.resolve(strict=False)
        if source.is_symlink() or getattr(source, "is_junction", lambda: False)():
            raise PermissionError(f"refusing linked path: {source}")
        if target.exists():
            raise FileExistsError(f"trash target already exists: {target}")
        target.parent.mkdir(parents=True, exist_ok=True)
        if self._same_volume(source, target):
            os.replace(source, target)
            return
        tmp = target.with_name(f".{target.name}.copy-{os.getpid()}-{time.time_ns()}")
        try:
            if source.is_dir():
                before_files, before_dirs, size = self._safe_directory_inventory(source, fingerprint=True)
                if self.cross_volume_move_max_bytes and size > self.cross_volume_move_max_bytes:
                    raise ValueError(f"cross-volume directory exceeds configured move limit: {size}")
                shutil.copytree(source, tmp, symlinks=False)
                after_files, after_dirs, after_size = self._safe_directory_inventory(tmp, fingerprint=True)
                if before_files != after_files or before_dirs != after_dirs or size != after_size:
                    raise IOError(f"cross-volume directory copy mismatch: {source}")
                os.replace(tmp, target)
                shutil.rmtree(source)
            else:
                shutil.copy2(source, tmp)
                if int(tmp.stat().st_size) != int(source.stat().st_size):
                    raise IOError(f"cross-volume copy size mismatch: {source} -> {target}")
                src_hash = hashlib.sha256(source.read_bytes()).digest()
                dst_hash = hashlib.sha256(tmp.read_bytes()).digest()
                if src_hash != dst_hash:
                    raise IOError(f"cross-volume copy content mismatch: {source}")
                os.replace(tmp, target)
                source.unlink()
        except Exception:
            if tmp.is_dir():
                shutil.rmtree(tmp)
            else:
                tmp.unlink(missing_ok=True)
            raise

    def _source_kind_size(self, source: Path) -> tuple[str, int]:
        if source.is_symlink() or getattr(source, "is_junction", lambda: False)():
            raise PermissionError(f"refusing linked source: {source}")
        if source.is_file():
            return "file", int(source.stat().st_size)
        if source.is_dir():
            _, _, size = self._safe_directory_inventory(source)
            return "directory", size
        raise FileNotFoundError(f"not a regular file or directory: {source}")

    def _guard_source(self, source: Path) -> None:
        if source == self.trash_root or source.is_relative_to(self.trash_root) or self.trash_root.is_relative_to(source):
            raise PermissionError(f"cannot soft-delete the trash or its ancestor/descendant: {source}")
        if source == Path(source.anchor):
            raise PermissionError(f"cannot soft-delete a filesystem root: {source}")

    def _atomic_checkpoint(self) -> dict[str, Any]:
        raw = self.redis.hgetall(self.items_key)
        items: dict[str, dict[str, Any]] = {}
        for deletion_id, payload in raw.items():
            try:
                record = json.loads(payload)
            except Exception:
                continue
            if isinstance(record, dict):
                items[str(deletion_id)] = record
        data = {
            "schema": 1,
            "updated_at": time.time(),
            "items": items,
        }
        tmp = self.index_path.with_name(f".{self.index_path.name}.tmp-{os.getpid()}-{time.time_ns()}")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
        os.replace(tmp, self.index_path)
        return {"checkpoint": str(self.index_path), "items": len(items)}

    def _snapshot_items(self) -> dict[str, dict[str, Any]]:
        if not self.index_path.is_file():
            return {}
        try:
            data = json.loads(self.index_path.read_text(encoding="utf-8-sig"))
        except Exception:
            return {}
        items = data.get("items") if isinstance(data, dict) else None
        return {str(k): dict(v) for k, v in dict(items or {}).items() if isinstance(v, dict)}

    def _trash_path(self, record: dict[str, Any]) -> Path:
        name = str(record.get("trash_name") or "").strip()
        if not name:
            raise ValueError("trash record is missing trash_name")
        path = (self.trash_root / name).resolve(strict=False)
        if path.parent != self.trash_root:
            raise PermissionError(f"trash path escaped deletion root: {path}")
        return path

    def _unique_trash_name(self, deletion_id: str, source: Path) -> str:
        safe_name = source.name.replace("/", "_").replace("\\", "_") or "unnamed"
        return f"{deletion_id[:12]}__{safe_name}"

    def _migrate_legacy_stream(self) -> int:
        """Convert pre-flat stream entries when present. Best-effort and idempotent."""
        try:
            entries = self.redis.xrange(self.legacy_stream, min="-", max="+")
        except Exception:
            return 0
        migrated = 0
        for redis_id, fields in entries:
            deletion_id = str(fields.get("deletion_id") or uuid.uuid4().hex)
            if self.redis.hexists(self.items_key, deletion_id):
                self.redis.xdel(self.legacy_stream, redis_id)
                continue
            old_path = Path(str(fields.get("trash_path") or "")).resolve(strict=False)
            if not old_path.is_file():
                self.redis.xdel(self.legacy_stream, redis_id)
                continue
            name = self._unique_trash_name(deletion_id, old_path)
            target = self.trash_root / name
            if target.exists():
                name = f"{deletion_id}__{old_path.name}"
                target = self.trash_root / name
            self._move_verified(old_path, target)
            try:
                old_path.parent.rmdir()
            except OSError:
                pass
            record = {
                "deletion_id": deletion_id,
                "trash_name": name,
                "original_path": str(fields.get("original_path") or ""),
                "sha256": str(fields.get("sha256") or ""),
                "reason": str(fields.get("reason") or "legacy deletion queue migration"),
                "queued_at": float(fields.get("queued_at") or time.time()),
                "task_id": "",
                "batch_id": "legacy",
                "size_bytes": int(target.stat().st_size),
                "state": "committed",
            }
            self.redis.hset(self.items_key, deletion_id, json.dumps(record, ensure_ascii=False, sort_keys=True))
            self.redis.xdel(self.legacy_stream, redis_id)
            migrated += 1
        if migrated and self.redis.xlen(self.legacy_stream) == 0:
            self.redis.delete(self.legacy_stream)
        return migrated

    def stage_batch(
        self,
        items: Iterable[dict[str, Any]],
        *,
        task_id: str = "",
        batch_reason: str = "task cleanup",
        checkpoint: bool = True,
    ) -> dict[str, Any]:
        batch_id = uuid.uuid4().hex
        staged: list[dict[str, Any]] = []
        skipped: list[dict[str, Any]] = []
        started = time.time()
        self.redis.hset(self.batches_key, batch_id, json.dumps({
            "batch_id": batch_id, "task_id": str(task_id), "state": "moving",
            "started_at": started, "reason": str(batch_reason)[:4000],
        }, sort_keys=True))
        try:
            for raw in items:
                requested = Path(str(raw.get("path") or ""))
                if requested.is_symlink() or getattr(requested, "is_junction", lambda: False)():
                    raise PermissionError(f"linked source is not eligible for trash: {requested}")
                source = requested.resolve()
                self._guard_source(source)
                if not (source.is_file() or source.is_dir()):
                    skipped.append({"path": str(source), "reason": "missing_or_unsupported"})
                    continue
                kind, size = self._source_kind_size(source)
                if (
                    self.cross_volume_move_max_bytes
                    and size > self.cross_volume_move_max_bytes
                    and not self._same_volume(source, self.trash_root)
                ):
                    skipped.append({
                        "path": str(source), "reason": "cross_volume_soft_delete_too_large",
                        "size_bytes": size, "limit_bytes": self.cross_volume_move_max_bytes,
                    })
                    continue
                deletion_id = uuid.uuid4().hex
                trash_name = self._unique_trash_name(deletion_id, source)
                target = self.trash_root / trash_name
                self._move_verified(source, target)
                record = {
                    "deletion_id": deletion_id,
                    "trash_name": trash_name,
                    "original_path": str(source),
                    "reason": str(raw.get("reason") or batch_reason).strip()[:4000],
                    "queued_at": time.time(),
                    "task_id": str(raw.get("task_id") or task_id),
                    "batch_id": batch_id,
                    "size_bytes": size,
                    "kind": kind,
                    "reproduce_from": raw.get("reproduce_from"),
                    "reproducibility": raw.get("reproducibility"),
                    "state": "committed",
                }
                try:
                    self.redis.hset(self.items_key, deletion_id, json.dumps(record, ensure_ascii=False, sort_keys=True))
                except Exception:
                    source.parent.mkdir(parents=True, exist_ok=True)
                    self._move_verified(target, source)
                    raise
                staged.append(record)
            batch_record = {
                "batch_id": batch_id, "task_id": str(task_id), "state": "committed",
                "started_at": started, "completed_at": time.time(),
                "reason": str(batch_reason)[:4000], "staged": len(staged), "skipped": len(skipped),
            }
            self.redis.hset(self.batches_key, batch_id, json.dumps(batch_record, sort_keys=True))
            cp = self._atomic_checkpoint() if checkpoint else None
            return {"batch_id": batch_id, "staged": staged, "skipped": skipped, "checkpoint": cp}
        except Exception:
            self.redis.hset(self.batches_key, batch_id, json.dumps({
                "batch_id": batch_id, "task_id": str(task_id), "state": "failed",
                "started_at": started, "failed_at": time.time(), "reason": str(batch_reason)[:4000],
                "staged": len(staged), "skipped": len(skipped),
            }, sort_keys=True))
            # Checkpoint whatever was successfully committed before the failure.
            if checkpoint:
                self._atomic_checkpoint()
            raise

    def stage_file(self, source: Path, *, reason: str, task_id: str = "") -> dict[str, Any]:
        """Backward-compatible entrypoint; now accepts a file or a whole folder."""
        source = Path(source)
        if source.is_symlink() or getattr(source, "is_junction", lambda: False)():
            raise PermissionError(f"linked source is not eligible for trash: {source}")
        source = source.resolve()
        result = self.stage_batch([{
            "path": str(source), "reason": reason, "task_id": task_id,
        }], task_id=task_id, batch_reason=reason)
        if not result["staged"]:
            detail = result["skipped"][0] if result["skipped"] else {"reason": "not_staged"}
            raise RuntimeError(f"soft delete was not staged: {detail}")
        return result["staged"][0] | {"batch_id": result["batch_id"], "checkpoint": result.get("checkpoint")}

    def stage_path(self, source: Path, *, reason: str, task_id: str = "") -> dict[str, Any]:
        """Stage a file or complete directory as a single reversible record."""
        return self.stage_file(source, reason=reason, task_id=task_id)

    def list_items(self) -> list[dict[str, Any]]:
        # A read should never omit orphaned physical folders. Recover them
        # as visible, held records; never invent an original location.
        self.reconcile(checkpoint=False)
        records: list[dict[str, Any]] = []
        for deletion_id, payload in self.redis.hgetall(self.items_key).items():
            try:
                item = json.loads(payload)
            except Exception:
                continue
            if isinstance(item, dict):
                item["deletion_id"] = str(item.get("deletion_id") or deletion_id)
                records.append(item)
        return sorted(records, key=lambda item: float(item.get("queued_at") or 0.0))

    def restore(self, deletion_ids: Iterable[str] | str) -> dict[str, Any]:
        if isinstance(deletion_ids, str):
            ids = [deletion_ids]
        else:
            ids = [str(v) for v in deletion_ids]
        if any(value.lower() == "all" for value in ids):
            ids = [item["deletion_id"] for item in self.list_items()]
        restored: list[dict[str, Any]] = []
        conflicts: list[dict[str, Any]] = []
        missing: list[str] = []
        for deletion_id in ids:
            payload = self.redis.hget(self.items_key, deletion_id)
            if not payload:
                missing.append(deletion_id)
                continue
            try:
                record = json.loads(payload)
            except Exception:
                missing.append(deletion_id)
                continue
            original = str(record.get("original_path") or "").strip()
            if not original:
                conflicts.append({"deletion_id": deletion_id, "reason": "original_path_unknown"})
                continue
            source = Path(original).resolve(strict=False)
            trash_path = self._trash_path(record)
            if source.exists():
                conflicts.append({"deletion_id": deletion_id, "path": str(source), "reason": "destination_exists"})
                continue
            if not (trash_path.is_file() or trash_path.is_dir()):
                conflicts.append({"deletion_id": deletion_id, "path": str(source), "reason": "trash_payload_missing"})
                continue
            source.parent.mkdir(parents=True, exist_ok=True)
            self._move_verified(trash_path, source)
            self.redis.hdel(self.items_key, deletion_id)
            restored.append({"deletion_id": deletion_id, "path": str(source)})
        cp = self._atomic_checkpoint()
        return {"restored": restored, "conflicts": conflicts, "missing": missing, "checkpoint": cp}

    def reconcile(self, *, checkpoint: bool = True) -> dict[str, Any]:
        migrated = self._migrate_legacy_stream()
        snapshot = self._snapshot_items()
        redis_items = self.redis.hgetall(self.items_key)
        records: dict[str, dict[str, Any]] = {}
        for deletion_id, payload in redis_items.items():
            try:
                record = json.loads(payload)
            except Exception:
                continue
            if isinstance(record, dict):
                records[str(deletion_id)] = record
        for deletion_id, record in snapshot.items():
            records.setdefault(deletion_id, record)

        actual = {p.name: p for p in self.trash_root.iterdir()
                  if (p.is_file() or p.is_dir()) and p.name != self.index_path.name
                  and not p.name.startswith(".")}
        stale: list[str] = []
        recovered = 0
        known_names: set[str] = set()
        for deletion_id, record in list(records.items()):
            try:
                trash_path = self._trash_path(record)
            except Exception:
                stale.append(deletion_id)
                continue
            known_names.add(trash_path.name)
            if not (trash_path.is_file() or trash_path.is_dir()):
                stale.append(deletion_id)
                continue
            self.redis.hset(self.items_key, deletion_id, json.dumps(record, ensure_ascii=False, sort_keys=True))
        if stale:
            self.redis.hdel(self.items_key, *stale)
        for name, path in actual.items():
            if name in known_names:
                continue
            deletion_id = name.split("__", 1)[0] if "__" in name else uuid.uuid4().hex[:12]
            while self.redis.hexists(self.items_key, deletion_id):
                deletion_id = uuid.uuid4().hex
            record = {
                "deletion_id": deletion_id,
                "trash_name": name,
                "original_path": "",
                "sha256": "",
                "reason": "recovered orphaned trash payload; original path unknown",
                "queued_at": path.stat().st_mtime,
                "task_id": "",
                "batch_id": "reconcile",
                "size_bytes": self._source_kind_size(path)[1],
                "kind": "directory" if path.is_dir() else "file",
                "state": "orphaned",
            }
            self.redis.hset(self.items_key, deletion_id, json.dumps(record, ensure_ascii=False, sort_keys=True))
            recovered += 1
        # A newly observed orphan must appear in BOTH Redis and the JSON
        # checkpoint, even when discovered by a normal inventory request.
        cp = self._atomic_checkpoint() if (checkpoint or migrated or stale or recovered) else None
        return {"migrated": migrated, "stale_removed": len(stale), "orphans_recovered": recovered, "checkpoint": cp}

    def purge_all(self) -> dict[str, int]:
        self.reconcile(checkpoint=False)
        purged = 0
        held = 0
        for deletion_id, payload in list(self.redis.hgetall(self.items_key).items()):
            try:
                record = json.loads(payload)
                trash_path = self._trash_path(record)
                if record.get("state") == "orphaned" or not record.get("original_path"):
                    # Unknown-original historical folders must not disappear
                    # in a bulk purge. Explicit recovery/adoption comes first.
                    raise PermissionError("orphaned trash requires recovery before purge")
                if trash_path.exists():
                    if trash_path.is_dir():
                        self._safe_directory_inventory(trash_path)
                        shutil.rmtree(trash_path)
                    elif trash_path.is_file():
                        trash_path.unlink()
                    else:
                        raise OSError(f"unsupported trash payload: {trash_path}")
                self.redis.hdel(self.items_key, deletion_id)
                purged += 1
            except Exception:
                held += 1
        self._atomic_checkpoint()
        return {"purged": purged, "held": held}

    def flush_db(self) -> None:
        # Never FLUSHDB: DB2 may acquire unrelated coordination state. Delete only
        # keys owned by this subsystem.
        keys = [self.items_key, self.batches_key, self.legacy_stream]
        self.redis.delete(*keys)

    def pending(self) -> int:
        try:
            return int(self.redis.hlen(self.items_key))
        except Exception:
            return 0
