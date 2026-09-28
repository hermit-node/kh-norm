from __future__ import annotations

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PYTHON = ROOT / ".venv" / "Scripts" / "python.exe"
HOST = ROOT / "tools" / "operator_console_host.py"

CONSOLES = (
    ("Norm Runtime", ROOT / "tools" / "norm_gui_stream.py"),
    ("Norm Replies", ROOT / "tools" / "norm_gui_reply.py"),
    ("Norm Prompt", ROOT / "tools" / "norm_gui_prompt.py"),
)


def _launch(title: str, script: Path) -> None:
    for required in (PYTHON, HOST, script):
        if not required.is_file():
            raise RuntimeError(f"Required operator-console file not found: {required}")
    flags = 0
    if hasattr(subprocess, "CREATE_NEW_CONSOLE"):
        flags |= subprocess.CREATE_NEW_CONSOLE
    if hasattr(subprocess, "CREATE_NEW_PROCESS_GROUP"):
        flags |= subprocess.CREATE_NEW_PROCESS_GROUP
    subprocess.Popen(
        [str(PYTHON), "-u", str(HOST), title, str(script)],
        cwd=str(ROOT),
        creationflags=flags,
    )


def main() -> int:
    failures: list[str] = []
    for title, script in CONSOLES:
        try:
            _launch(title, script)
        except Exception as exc:
            failures.append(f"{title}: {exc}")
    if failures:
        print("Could not launch all Norm operator consoles:")
        for failure in failures:
            print(f"  - {failure}")
        return 1
    print("Opened Norm Prompt, Norm Runtime, and Norm Replies consoles.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
