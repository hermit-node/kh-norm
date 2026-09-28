from __future__ import annotations

import ctypes
import subprocess
import sys
from pathlib import Path


def _set_title(title: str) -> None:
    if sys.platform == "win32":
        ctypes.windll.kernel32.SetConsoleTitleW(str(title))


def main() -> int:
    if len(sys.argv) != 3:
        print("Usage: operator_console_host.py <title> <helper.py>")
        return 2
    title = sys.argv[1]
    helper = Path(sys.argv[2]).resolve()
    _set_title(title)
    if not helper.is_file():
        print(f"{title}: helper not found: {helper}")
        input("Press Enter to close this console...")
        return 2

    rc = subprocess.call([sys.executable, "-u", str(helper)], cwd=str(helper.parents[1]))
    print()
    print(f"{title} helper exited with code {rc}.")
    print("This console is being kept open so the startup/exit message is visible.")
    try:
        input("Press Enter to close this console...")
    except (EOFError, KeyboardInterrupt):
        pass
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
