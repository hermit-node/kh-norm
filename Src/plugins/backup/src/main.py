from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


def _call(mode: str, label: str = "", validate_only: bool = False) -> dict:
    runtime_root = Path(__file__).resolve().parents[3]
    python_exe = runtime_root / ".venv" / "Scripts" / "python.exe"
    if not python_exe.is_file():
        python_exe = Path(sys.executable)
    helper = runtime_root / "tools" / "norm_backup.py"
    cmd = [str(python_exe), str(helper), "--mode", mode, "--json"]
    if validate_only:
        cmd.append("--validate-only")
    if str(label or "").strip():
        cmd.extend(["--label", str(label).strip()])
    proc = subprocess.run(cmd, cwd=str(runtime_root), capture_output=True, text=True, encoding="utf-8", errors="replace", check=False)
    if proc.returncode != 0:
        raise RuntimeError(f"Norm {mode} backup failed ({proc.returncode}): {(proc.stderr or proc.stdout).strip()}")
    lines = [line.strip() for line in proc.stdout.splitlines() if line.strip()]
    if not lines:
        raise RuntimeError("Norm backup returned no result")
    return json.loads(lines[-1])


def create_backup(label: str = "", full: bool = False) -> dict:
    """Create an installer-compatible source backup, or a sensitive full-state backup when full=True."""
    return _call("full" if full else "source", label=label)


def create_full_backup(label: str = "") -> dict:
    """Create a sensitive full-state installer backup including secrets, .ssh, workspace, and PostgreSQL."""
    return _call("full", label=label)


def validate_backup_setup(full: bool = True) -> dict:
    """Validate source/full backup prerequisites without creating a final ZIP."""
    return _call("full" if full else "source", validate_only=True)
