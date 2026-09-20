from __future__ import annotations

import subprocess
import time
from pathlib import Path
from typing import Any

from .resource_status import full_context_status, impaired_context_status, merge_resource_status


class StorageContext:
    """Lazy primary/backup storage selection and targeted availability checks."""

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        cfg = config or {}
        self.primary_name = str(cfg.get("primary_name") or "primary")
        self.backup_name = str(cfg.get("backup_name") or "backup")
        self.primary_root = Path(str(cfg.get("primary_root") or "")).absolute() if cfg.get("primary_root") else None
        self.backup_root = Path(str(cfg.get("backup_root") or "")).absolute() if cfg.get("backup_root") else None
        self.probe_timeout_seconds = max(0.25, float(cfg.get("probe_timeout_seconds", 2.0)))
        self.cache_seconds = max(1.0, float(cfg.get("status_cache_seconds", 30.0)))
        self._source_cache: dict[str, tuple[float, bool, str | None]] = {}

    @staticmethod
    def _is_unc(path: Path | None) -> bool:
        return bool(path and str(path).startswith("\\\\"))

    def _source(self, source: str) -> tuple[str, Path | None]:
        key = str(source).strip().lower()
        if key in {"primary", self.primary_name.lower(), "ca8d", "ca8d_smb"}:
            return self.primary_name, self.primary_root
        if key in {"backup", self.backup_name.lower(), "khzz", "khzz_docs", "local"}:
            return self.backup_name, self.backup_root
        raise ValueError(f"unknown storage source: {source}")

    def _probe(self, path: Path | None) -> tuple[bool, str | None]:
        if path is None:
            return False, "not configured"
        if self._is_unc(path):
            command = ["cmd.exe", "/d", "/c", f'if exist "{path}\\NUL" (exit /b 0) else (exit /b 1)']
            try:
                completed = subprocess.run(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                           timeout=self.probe_timeout_seconds, check=False)
                if completed.returncode == 0:
                    return True, None
                return False, f"SMB path unavailable or access denied (exit {completed.returncode})"
            except subprocess.TimeoutExpired:
                return False, f"SMB probe timed out after {self.probe_timeout_seconds:g}s"
            except OSError as exc:
                return False, f"SMB probe failed: {type(exc).__name__}: {exc}"
        try:
            return (True, None) if path.is_dir() else (False, "directory is unavailable")
        except OSError as exc:
            return False, f"probe failed: {type(exc).__name__}: {exc}"

    def check_source(self, source: str, *, force: bool = False) -> dict[str, Any]:
        name, path = self._source(source)
        now = time.monotonic()
        cached = self._source_cache.get(name)
        if not force and cached and now - cached[0] < self.cache_seconds:
            ok, error = cached[1], cached[2]
        else:
            ok, error = self._probe(path)
            self._source_cache[name] = (now, ok, error)
        return {"name": name, "path": str(path) if path else None, "checked": True, "responsive": ok, "error": error}

    def _unchecked(self, source: str) -> dict[str, Any]:
        name, path = self._source(source)
        return {"name": name, "path": str(path) if path else None, "checked": False, "responsive": None, "error": None}

    def select_root(self) -> tuple[Path | None, dict[str, Any]]:
        primary = self.check_source("primary")
        if primary["responsive"]:
            backup = self._unchecked("backup")
            return self.primary_root, self._selection_record(primary, backup, "primary")
        backup = self.check_source("backup")
        if backup["responsive"]:
            return self.backup_root, self._selection_record(primary, backup, "backup")
        return None, self._selection_record(primary, backup, "none")

    def _selection_record(self, primary: dict[str, Any], backup: dict[str, Any], active_role: str) -> dict[str, Any]:
        active = primary if active_role == "primary" else backup if active_role == "backup" else None
        if active_role == "primary":
            note = f"{self.primary_name} selected; backup was not queried because it was unnecessary."
            degraded = False
            context_status = full_context_status()
        elif active_role == "backup":
            note = f"{self.primary_name} is unresponsive; continued using {self.backup_name}."
            degraded = True
            context_status = impaired_context_status(self.primary_name, primary.get("error") or "unresponsive")
        else:
            note = "Primary and backup local storage are unresponsive; local-file storage is unavailable."
            degraded = True
            context_status = merge_resource_status(
                impaired_context_status(self.primary_name, primary.get("error") or "unresponsive"),
                impaired_context_status(self.backup_name, backup.get("error") or "unresponsive"),
            )
        return {
            "kind": "storage_context", "primary": primary, "backup": backup,
            "active_role": active_role, "active_name": active.get("name") if active else None,
            "active_root": active.get("path") if active else None, "degraded": degraded, "note": note,
            "context_status": context_status,
        }

    def context_for_source(self, source: str, *, force: bool = False) -> dict[str, Any]:
        name, _ = self._source(source)
        checked = self.check_source(source, force=force)
        other_source = "backup" if name == self.primary_name else "primary"
        other = self._unchecked(other_source)
        primary = checked if name == self.primary_name else other
        backup = checked if name == self.backup_name else other
        if checked["responsive"]:
            context_status = full_context_status()
            note = f"{name} responded; unrelated storage was not queried."
        else:
            context_status = impaired_context_status(name, checked.get("error") or "unresponsive")
            note = f"{name} is unresponsive; generating with impaired context."
        return {
            "kind": "storage_context", "primary": primary, "backup": backup,
            "active_role": "primary" if name == self.primary_name and checked["responsive"] else "backup" if name == self.backup_name and checked["responsive"] else "none",
            "active_name": name if checked["responsive"] else None,
            "active_root": checked.get("path") if checked["responsive"] else None,
            "degraded": not bool(checked["responsive"]), "note": note,
            "context_status": context_status,
        }

    def status(self, *, force: bool = False) -> dict[str, Any]:
        primary = self.check_source("primary", force=force)
        backup = self.check_source("backup", force=force)
        if primary["responsive"]:
            return self._selection_record(primary, backup, "primary")
        if backup["responsive"]:
            return self._selection_record(primary, backup, "backup")
        return self._selection_record(primary, backup, "none")

    def root_for_write(self) -> Path | None:
        root, _ = self.select_root()
        return root

    def context_record(self, *, force: bool = False) -> dict[str, Any]:
        return self.status(force=force)
