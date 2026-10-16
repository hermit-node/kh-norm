#!/usr/bin/env python3
"""Create role-specific Windows Terminal profiles without altering settings.json.

Used by NormWorkspace-Freeform-v3 for explicit profiled startup. Does NOT
start or stop the Norm service. Font changes apply to newly launched terminals.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import uuid

ROLES = (
    ("runtime", "Norm Runtime", "Norm Workspace Runtime", "norm_gui_stream.py", "Cascadia Mono", 13),
    ("replies", "Norm Replies", "Norm Workspace Replies", "norm_gui_reply.py", "Consolas", 14),
    ("prompt", "Norm Prompt", "Norm Workspace Prompt", "norm_gui_prompt.py", "Cascadia Code", 16),
)
FONT_WEIGHTS = {
    "thin", "extra-light", "light", "semi-light", "normal", "medium",
    "semi-bold", "bold", "extra-bold", "black", "extra-black",
}
UUID_ROOT = uuid.UUID("378a8d56-6872-4148-a112-c356e5b5a3e0")


def font_spec(settings: dict, key: str, default_face: str, default_size: int) -> dict:
    face = settings.get(f"{key}_font_face", default_face)
    size = settings.get(f"{key}_font_size", default_size)
    weight = settings.get(f"{key}_font_weight", "normal")
    if not isinstance(face, str) or not 1 <= len(face) <= 100 or any(ord(c) < 32 for c in face):
        raise ValueError(f"Invalid {key}_font_face")
    if type(size) is not int or not 6 <= size <= 48:
        raise ValueError(f"Invalid {key}_font_size: expected 6..48 points")
    if not isinstance(weight, str) or weight not in FONT_WEIGHTS:
        raise ValueError(f"Invalid {key}_font_weight")
    return {"face": face, "size": size, "weight": weight}


def fragment(settings: dict) -> dict:
    profiles = []
    for key, title, name, helper, face, size in ROLES:
        profiles.append({
            "name": name,
            "guid": "{" + str(uuid.uuid5(UUID_ROOT, name)) + "}",
            "commandline": "cmd.exe",
            "tabTitle": title,
            "font": font_spec(settings, key, face, size),
        })
    return {"profiles": profiles}


def fragment_path() -> Path:
    local = os.environ.get("LOCALAPPDATA")
    if not local:
        raise RuntimeError("LOCALAPPDATA not defined: Windows Terminal profile directory unavailable")
    return Path(local) / "Microsoft" / "Windows Terminal" / "Fragments" / "NormWorkspace" / "roles.json"


def write_fragment(settings: dict, target: Path | None = None) -> Path:
    # Own only our fragment; do not edit Windows Terminal's settings.json.
    out = target or fragment_path()
    body = json.dumps(fragment(settings), indent=2, ensure_ascii=False) + "\n"
    out.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".norm-profiles-", suffix=".tmp", dir=str(out.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(body)
        os.replace(temporary, out)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return out


def running_hosts() -> list[dict]:
    cmd = (
        "$ErrorActionPreference='Stop'; "
        "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
        "Where-Object { $_.CommandLine -match 'operator_console_host\\.py|norm_gui_prompt\\.py' } | "
        "Select-Object ProcessId, CommandLine | ConvertTo-Json -Compress"
    )
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-Command", cmd],
        capture_output=True, text=True, timeout=30, check=True,
    )
    if not result.stdout.strip():
        return []
    parsed = json.loads(result.stdout)
    return parsed if isinstance(parsed, list) else [parsed]


def live_role_pids() -> dict[str, int]:
    result = {}
    for record in running_hosts():
        line = str(record.get("CommandLine") or "").lower()
        if "operator_console_host.py" not in line:
            continue
        for key, title, *_ in ROLES:
            if f'"{title.lower()}"' in line:
                result[key] = int(record["ProcessId"])
    return result


def register_roles(root: Path, pids: dict[str, int]) -> Path:
    # Match the registry schema normally written by start_operator_consoles.py.
    sys.path.insert(0, str(root / "core"))
    from norm_runtime.settings import load_path_settings
    state = load_path_settings(root)["state_root"]
    state.mkdir(parents=True, exist_ok=True)
    path = state / "operator-consoles.json"
    rows = []
    for key, title, profile, helper, face, size in ROLES:
        rows.append({"title": title, "pid": pids[key], "helper": str(root / "tools" / helper)})
    body = {
        "schema": 1,
        "started_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "consoles": rows,
    }
    temporary = path.with_suffix(".json.new")
    temporary.write_text(json.dumps(body, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)
    return path


def launch_roles(root: Path, settings: dict) -> None:
    python = root / ".venv" / "Scripts" / "python.exe"
    host = root / "tools" / "operator_console_host.py"
    if not python.is_file() or not host.is_file():
        raise FileNotFoundError("Norm Python environment/operator_console_host.py is missing")
    for _, _, _, helper, _, _ in ROLES:
        if not (root / "tools" / helper).is_file():
            raise FileNotFoundError(root / "tools" / helper)
    if not shutil.which("wt.exe"):
        raise RuntimeError("Windows Terminal (wt.exe) is unavailable")
    already = running_hosts()
    if already:
        raise RuntimeError(
            f"Refusing to launch duplicate operator sessions: {len(already)} "
            "operator host/helper process(es) are already running. Attach existing windows instead."
        )
    fragment_file = write_fragment(settings)
    print("Windows Terminal role profiles:", fragment_file, flush=True)
    # Mirror the existing start_operator_consoles.py shutdown-signal initialization.
    sys.path.insert(0, str(root / "core"))
    from norm_runtime.settings import load_path_settings
    signal = load_path_settings(root)["state_root"] / "operator-console-shutdown.json"
    signal.unlink(missing_ok=True)
    for key, title, profile, helper, _, _ in ROLES:
        command = [
            "wt.exe", "-w", "new", "new-tab",
            "-p", profile, "--title", title, "-d", str(root),
            str(python), "-u", str(host), title, str(root / "tools" / helper),
        ]
        subprocess.Popen(command, cwd=root, creationflags=subprocess.CREATE_NO_WINDOW)
        print("Launched", title, "with", profile, flush=True)
        time.sleep(0.4)
    deadline = time.monotonic() + 25
    while time.monotonic() < deadline:
        pids = live_role_pids()
        if len(pids) == 3:
            registered = register_roles(root, pids)
            print("Operator console registry:", registered, flush=True)
            return
        time.sleep(1)
    raise RuntimeError(
        "Windows Terminal launch didn't produce all three operator host processes. "
        "Inspect Windows Terminal/profile errors and do not launch a second set automatically."
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--root", type=Path, default=Path(r"C:\Norm"))
    parser.add_argument("--prepare-only", action="store_true")
    parser.add_argument("--launch", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--target-fragment", type=Path, help="For isolated tests only")
    args = parser.parse_args()
    settings = json.loads(args.config.read_text(encoding="utf-8-sig"))
    if args.dry_run:
        print(json.dumps(fragment(settings), indent=2, ensure_ascii=False))
    elif args.launch:
        if args.target_fragment:
            parser.error("--target-fragment cannot be combined with --launch")
        launch_roles(args.root.resolve(), settings)
    else:
        path = write_fragment(settings, args.target_fragment)
        print(path)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print("ERROR:", exc, file=sys.stderr)
        raise SystemExit(1)
