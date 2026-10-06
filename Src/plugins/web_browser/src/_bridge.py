from __future__ import annotations

import configparser
import json
import os
import subprocess
from pathlib import Path


def _runtime_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _venv_python() -> Path:
    root = _runtime_root()
    parser = configparser.ConfigParser(interpolation=None)
    parser.read(root / "config" / "settings.ini", encoding="utf-8-sig")
    rel = parser.get("environment", "venv_path", fallback=".venv").strip() or ".venv"
    venv = root / rel
    candidate = venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    if not candidate.is_file():
        raise RuntimeError(f"Norm virtual-environment Python is missing: {candidate}")
    return candidate


def call(action: str, payload: dict, timeout_seconds: int = 180) -> dict:
    root = _runtime_root()
    plugin_src = Path(__file__).resolve().parent
    env = os.environ.copy()
    prior = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = str(plugin_src) + (os.pathsep + prior if prior else "")
    proc = subprocess.run(
        [str(_venv_python()), "-m", "web_runtime.worker", action],
        input=json.dumps(payload, ensure_ascii=False),
        text=True,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        cwd=str(root),
        env=env,
        timeout=max(10, min(int(timeout_seconds), 900)),
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "web worker failed").strip()
        raise RuntimeError(detail[-6000:])
    try:
        result = json.loads(proc.stdout)
    except Exception as exc:
        raise RuntimeError(f"web worker returned invalid JSON: {proc.stdout[-2000:]}") from exc
    if not isinstance(result, dict):
        raise RuntimeError("web worker returned a non-object result")
    if not result.get("ok", False):
        raise RuntimeError(str(result.get("error") or "web worker failed"))
    value = result.get("result")
    return value if isinstance(value, dict) else {"result": value}
