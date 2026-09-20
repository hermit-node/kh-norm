from __future__ import annotations

import hashlib
import shutil
import time
import uuid
from pathlib import Path
from typing import Any

import redis


class RedisDeletionQueue:
    def __init__(self, *, host: str, port: int, db: int, stream: str, trash_root: str) -> None:
        self.redis = redis.Redis(host=host, port=int(port), db=int(db), decode_responses=True)
        self.stream = str(stream)
        self.trash_root = Path(trash_root).resolve()
        self.trash_root.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(131_072), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def stage_file(self, source: Path, *, expected_sha256: str, reason: str) -> dict[str, Any]:
        source = source.resolve()
        if not source.is_file():
            raise FileNotFoundError(source)
        actual = self._sha256(source)
        if expected_sha256.lower() != actual:
            raise ValueError("expected_sha256 does not match the current file")
        if not reason or not reason.strip():
            raise ValueError("deletion reason must not be blank")
        token = uuid.uuid4().hex
        stamp = time.strftime("%Y%m%d-%H%M%S")
        folder = self.trash_root / f"{stamp}-{token[:12]}"
        folder.mkdir(parents=True, exist_ok=False)
        trash_path = folder / source.name
        shutil.move(str(source), str(trash_path))
        try:
            redis_id = self.redis.xadd(
                self.stream,
                {
                    "deletion_id": token,
                    "original_path": str(source),
                    "trash_path": str(trash_path),
                    "sha256": actual,
                    "reason": reason.strip()[:4000],
                    "queued_at": str(time.time()),
                },
            )
        except Exception:
            try:
                source.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(trash_path), str(source))
                folder.rmdir()
            finally:
                raise
        return {
            "deletion_id": token,
            "redis_id": redis_id,
            "original_path": str(source),
            "trash_path": str(trash_path),
            "sha256": actual,
            "reason": reason.strip(),
        }

    def purge_all(self) -> dict[str, int]:
        purged = 0
        held = 0
        entries = self.redis.xrange(self.stream, min="-", max="+")
        for redis_id, fields in entries:
            trash_path = Path(fields.get("trash_path", "")).resolve(strict=False)
            try:
                if not (trash_path == self.trash_root or trash_path.is_relative_to(self.trash_root)):
                    raise PermissionError(f"trash path escaped deletion root: {trash_path}")
                if trash_path.exists():
                    if not trash_path.is_file():
                        raise IsADirectoryError(trash_path)
                    expected = str(fields.get("sha256") or "")
                    if expected and self._sha256(trash_path) != expected:
                        raise ValueError(f"trash hash changed: {trash_path}")
                    trash_path.unlink()
                try:
                    trash_path.parent.rmdir()
                except OSError:
                    pass
                self.redis.xdel(self.stream, redis_id)
                purged += 1
            except Exception:
                held += 1
        if self.redis.xlen(self.stream) == 0:
            self.redis.delete(self.stream)
        return {"purged": purged, "held": held}

    def flush_db(self) -> None:
        self.redis.flushdb()

    def pending(self) -> int:
        try:
            return int(self.redis.xlen(self.stream))
        except Exception:
            return 0
