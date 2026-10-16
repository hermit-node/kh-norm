"""Build the Norm executable for Installer 1.6.12.

Called by the installer as:
  <venv-python> tools/build_norm.py --candidate <install-root>/core/norm.exe

Only the application launcher is frozen; dynamic plugins remain separate and
are validated/synchronized by the installer. Build cache is kept outside core/.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parent.parent
CORE = ROOT / "core"
ENTRYPOINT = CORE / "norm_main.py"


def build(candidate: Path, *, clean: bool = False, dry_run: bool = False) -> Path:
    candidate = candidate.expanduser().resolve()
    if not ENTRYPOINT.is_file():
        raise FileNotFoundError(f"Norm entrypoint not found: {ENTRYPOINT}")
    if candidate.suffix.lower() != ".exe":
        raise ValueError("--candidate must be a Windows .exe path")
    # Work/spec state is intentionally persistent and separate from managed source.
    cache = ROOT / "state" / "build-cache" / "pyinstaller"
    dist = cache / "dist"
    work = cache / "work"
    spec = cache / "spec"
    exe = dist / "norm.exe"
    cmd = [
        sys.executable, "-m", "PyInstaller", "--noconfirm", "--onefile", "--console",
        "--name", "norm", "--distpath", str(dist), "--workpath", str(work),
        "--specpath", str(spec), "--paths", str(CORE),
        # Plugins import these modules after the frozen launcher starts; explicitly
        # collect their native dependencies rather than relying on source scanning.
        "--collect-all", "cryptography", "--collect-all", "cffi",
        "--hidden-import", "_cffi_backend",
        str(ENTRYPOINT),
    ]
    if clean:
        cmd.insert(4, "--clean")
    print("Norm build command:", subprocess.list2cmdline(cmd), flush=True)
    if dry_run:
        return candidate
    if os.name != "nt":
        raise RuntimeError("Building norm.exe requires Windows and the pinned Python environment")
    dist.mkdir(parents=True, exist_ok=True)
    work.mkdir(parents=True, exist_ok=True)
    spec.mkdir(parents=True, exist_ok=True)
    subprocess.run(cmd, cwd=ROOT, check=True)
    if not exe.is_file() or not exe.stat().st_size:
        raise RuntimeError(f"PyInstaller did not create an executable: {exe}")
    candidate.parent.mkdir(parents=True, exist_ok=True)
    temp = candidate.with_name(candidate.name + ".new")
    try:
        shutil.copy2(exe, temp)
        if not temp.stat().st_size:
            raise RuntimeError("Compiled executable is empty")
        os.replace(temp, candidate)
    finally:
        temp.unlink(missing_ok=True)
    print(f"Built Norm executable: {candidate} ({candidate.stat().st_size} bytes)", flush=True)
    return candidate


def main() -> int:
    p = argparse.ArgumentParser(description="Build Norm executable for installer")
    p.add_argument("--candidate", type=Path, default=CORE / "norm.exe")
    p.add_argument("--clean", action="store_true", help="Discard reusable PyInstaller work cache")
    p.add_argument("--dry-run", action="store_true", help="Validate and show build command without compiling")
    a = p.parse_args()
    build(a.candidate, clean=a.clean, dry_run=a.dry_run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
