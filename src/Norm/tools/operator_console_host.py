from __future__ import annotations

import ctypes
import json
import subprocess
import sys
import time
from pathlib import Path


def _set_title(title: str) -> None:
    if sys.platform == "win32":
        ctypes.windll.kernel32.SetConsoleTitleW(str(title))


def _shutdown_signal(root: Path) -> Path:
    app = root / "core"
    if str(app) not in sys.path:
        sys.path.insert(0, str(app))
    try:
        from norm_runtime.settings import load_path_settings

        return load_path_settings(root)["state_root"] / "operator-console-shutdown.json"
    except Exception:
        return root / "state" / "operator-console-shutdown.json"


def _signal_delay(path: Path, title: str) -> float:
    default = 10.0 if title.casefold() == "norm prompt" else 5.0
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        key = "prompt_delay_seconds" if title.casefold() == "norm prompt" else "other_delay_seconds"
        return max(0.0, float(data.get(key, default)))
    except Exception:
        return default


def _terminate_helper(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    if sys.platform == "win32":
        subprocess.run(
            ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    else:
        proc.terminate()
    try:
        proc.wait(timeout=2)
    except subprocess.TimeoutExpired:
        proc.kill()


def _interruptible_close(delay: float) -> None:
    delay = max(0.0, float(delay))
    if delay <= 0:
        return
    deadline = time.monotonic() + delay
    try:
        if sys.platform == "win32":
            import msvcrt

            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                print(
                    f"\rClosing automatically in {max(1, int(remaining + 0.999))}s; press Enter or Ctrl+C to close now... ",
                    end="",
                    flush=True,
                )
                end = min(deadline, time.monotonic() + 0.1)
                while time.monotonic() < end:
                    if msvcrt.kbhit():
                        char = msvcrt.getwch()
                        if char in {"\r", "\n", "\x03"}:
                            print()
                            return
                    time.sleep(0.02)
            print()
            return
        time.sleep(delay)
    except KeyboardInterrupt:
        print()


def main() -> int:
    if len(sys.argv) != 3:
        print("Usage: operator_console_host.py <title> <helper.py>")
        return 2
    title = sys.argv[1]
    helper = Path(sys.argv[2]).resolve()
    root = helper.parents[1]
    signal = _shutdown_signal(root)
    _set_title(title)
    if not helper.is_file():
        print(f"{title}: helper not found: {helper}")
        try:
            input("Press Enter to close this console...")
        except (EOFError, KeyboardInterrupt):
            pass
        return 2

    proc = subprocess.Popen([sys.executable, "-u", str(helper)], cwd=str(root))
    shutdown_seen = False
    is_prompt = title.casefold() == "norm prompt"
    while proc.poll() is None:
        if signal.is_file():
            shutdown_seen = True
            # Norm Prompt owns the emergency-stop helper, so allow it to finish
            # printing/verifying the SOS result. Runtime/Replies can stop now.
            if not is_prompt:
                _terminate_helper(proc)
                break
        time.sleep(0.1)

    rc = proc.wait()
    print()
    print(f"{title} helper exited with code {rc}.")
    if shutdown_seen or signal.is_file():
        delay = _signal_delay(signal, title)
        _interruptible_close(delay)
        return rc

    print("This console is being kept open so the startup/exit message is visible.")
    try:
        input("Press Enter to close this console...")
    except (EOFError, KeyboardInterrupt):
        pass
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
