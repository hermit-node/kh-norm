from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PYTHON = ROOT / ".venv" / "Scripts" / "python.exe"
HOST = ROOT / "tools" / "operator_console_host.py"
APP = ROOT / "core"
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))

from norm_runtime.settings import load_path_settings

CONSOLES = (
    ("Norm Runtime", ROOT / "tools" / "norm_gui_stream.py"),
    ("Norm Replies", ROOT / "tools" / "norm_gui_reply.py"),
    ("Norm Prompt", ROOT / "tools" / "norm_gui_prompt.py"),
)


def _state_paths() -> tuple[Path, Path]:
    state_root = load_path_settings(ROOT)["state_root"]
    return state_root / "operator-console-shutdown.json", state_root / "operator-consoles.json"


def _launch(title: str, script: Path) -> subprocess.Popen:
    for required in (PYTHON, HOST, script):
        if not required.is_file():
            raise RuntimeError(f"Required operator-console file not found: {required}")

    flags = 0
    if hasattr(subprocess, "CREATE_NEW_CONSOLE"):
        flags |= subprocess.CREATE_NEW_CONSOLE
    if hasattr(subprocess, "CREATE_NEW_PROCESS_GROUP"):
        flags |= subprocess.CREATE_NEW_PROCESS_GROUP
    return subprocess.Popen(
        [str(PYTHON), "-u", str(HOST), title, str(script)],
        cwd=str(ROOT),
        creationflags=flags,
    )


def main() -> int:
    signal, registry = _state_paths()
    signal.parent.mkdir(parents=True, exist_ok=True)
    signal.unlink(missing_ok=True)

    failures: list[str] = []
    launched: list[dict] = []
    for title, script in CONSOLES:
        try:
            proc = _launch(title, script)
            launched.append({"title": title, "pid": proc.pid, "helper": str(script)})
        except Exception as exc:
            failures.append(f"{title}: {exc}")

    registry.write_text(
        json.dumps(
            {
                "schema": 1,
                "started_at": datetime.now(timezone.utc).isoformat(),
                "consoles": launched,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    if failures:
        print("Could not launch all Norm operator consoles:")
        for failure in failures:
            print(f"  - {failure}")
        return 1
    print("Opened persistent Norm Prompt, Norm Runtime, and Norm Replies consoles.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
