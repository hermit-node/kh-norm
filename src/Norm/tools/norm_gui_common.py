from __future__ import annotations

import configparser
import ctypes
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP_DIR = ROOT / "core"
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))
from norm_runtime.settings import load_ports
from runtime_bootstrap import load_config as load_runtime_config
STATE_DIR = ROOT / "state"
FALLBACK_LOG = STATE_DIR / "norm_gui_history_fallback.jsonl"
SETTINGS_FILE = ROOT / "config" / "settings.ini"
_MUTEX_HANDLES = []

def acquire_windows_mutex(name: str) -> bool:
    if os.name != "nt":
        return True
    handle = ctypes.windll.kernel32.CreateMutexW(None, False, name)
    if not handle:
        return False
    if ctypes.windll.kernel32.GetLastError() == 183:
        ctypes.windll.kernel32.CloseHandle(handle)
        return False
    _MUTEX_HANDLES.append(handle)
    return True


def append_fallback_jsonl(record: dict) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    with FALLBACK_LOG.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def tailscale_ipv4() -> str:
    proc = subprocess.run(
        ["tailscale", "ip", "-4"],
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    )
    for line in proc.stdout.splitlines():
        value = line.strip()
        if value:
            return value
    raise RuntimeError(
        "Tailscale IPv4 is unavailable. Norm is configured fail-closed on the tailnet."
    )


def endpoints() -> dict[str, str]:
    if not SETTINGS_FILE.is_file():
        raise RuntimeError(f"Missing settings file: {SETTINGS_FILE}")
    ports = load_ports(ROOT)
    host = tailscale_ipv4()
    norm_port = int(ports["norm_http"])
    activity_port = int(ports["activity"])
    return {
        "host": host,
        "chat": f"http://{host}:{norm_port}/api/chat",
        "chat_health": f"http://{host}:{norm_port}/health",
        "activity": f"http://{host}:{activity_port}",
        "activity_health": f"http://{host}:{activity_port}/health",
        "events": f"http://{host}:{activity_port}/events",
        "busy": f"http://{host}:{activity_port}/status/busy",
        "shutdown": f"http://{host}:{activity_port}/control/shutdown-norm",
        "shutdown_now": f"http://{host}:{activity_port}/control/shutdown-norm-now",
        "stop_all": f"http://{host}:{activity_port}/control/stop-all",
        "stop_all_now": f"http://{host}:{activity_port}/control/stop-all-now",
        "suppress_task": f"http://{host}:{activity_port}/control/suppress-task",
        "inject_context": f"http://{host}:{activity_port}/control/inject-context",
        "flush_suppressed": f"http://{host}:{activity_port}/control/flush-suppressed",
    }


def norm_process_running() -> bool:
    proc = subprocess.run(
        ["tasklist", "/FI", "IMAGENAME eq norm.exe", "/FO", "CSV", "/NH"],
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    )
    return '"norm.exe"' in proc.stdout.lower()


def start_norm_detached() -> subprocess.Popen:
    exe = ROOT / "core" / "norm.exe"
    if not exe.is_file():
        raise RuntimeError(f"Norm executable not found: {exe}")
    flags = 0
    if hasattr(subprocess, "DETACHED_PROCESS"):
        flags |= subprocess.DETACHED_PROCESS
    if hasattr(subprocess, "CREATE_NEW_PROCESS_GROUP"):
        flags |= subprocess.CREATE_NEW_PROCESS_GROUP
    return subprocess.Popen(
        [str(exe), "--service"],
        cwd=str(ROOT),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=flags,
    )
