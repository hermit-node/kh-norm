from __future__ import annotations

import configparser
import json
import os
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATE_DIR = ROOT / "state"
DISPLAY_FILE = STATE_DIR / "norm_gui_final_answer.txt"
FALLBACK_LOG = STATE_DIR / "norm_gui_history_fallback.jsonl"
SETTINGS_FILE = ROOT / "config" / "settings.ini"


def atomic_write_utf8(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, path)
    finally:
        if tmp_path.exists():
            tmp_path.unlink(missing_ok=True)

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
    cfg = configparser.ConfigParser()
    if not SETTINGS_FILE.is_file():
        raise RuntimeError(f"Missing settings file: {SETTINGS_FILE}")
    cfg.read(SETTINGS_FILE, encoding="utf-8")
    host = tailscale_ipv4()
    norm_port = cfg.getint("ports", "norm_http")
    activity_port = cfg.getint("ports", "activity")
    return {
        "host": host,
        "chat": f"http://{host}:{norm_port}/api/chat",
        "chat_health": f"http://{host}:{norm_port}/health",
        "activity": f"http://{host}:{activity_port}",
        "activity_health": f"http://{host}:{activity_port}/health",
        "events": f"http://{host}:{activity_port}/events",
        "busy": f"http://{host}:{activity_port}/status/busy",
        "shutdown": f"http://{host}:{activity_port}/control/shutdown-norm",
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


def start_norm_detached() -> None:
    exe = ROOT / "app" / "norm.exe"
    if not exe.is_file():
        raise RuntimeError(f"Norm executable not found: {exe}")
    flags = 0
    if hasattr(subprocess, "DETACHED_PROCESS"):
        flags |= subprocess.DETACHED_PROCESS
    if hasattr(subprocess, "CREATE_NEW_PROCESS_GROUP"):
        flags |= subprocess.CREATE_NEW_PROCESS_GROUP
    subprocess.Popen(
        [str(exe)],
        cwd=str(ROOT),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=flags,
    )
