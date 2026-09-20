from __future__ import annotations

import os
import sys
import signal
import ctypes
import time
from datetime import datetime

from norm_gui_common import DISPLAY_FILE, norm_process_running


def claim_display_file():
    if not DISPLAY_FILE.is_file():
        return None
    claimed = DISPLAY_FILE.with_name(
        f"{DISPLAY_FILE.stem}.displaying-{os.getpid()}{DISPLAY_FILE.suffix}"
    )
    try:
        os.replace(DISPLAY_FILE, claimed)
        return claimed
    except FileNotFoundError:
        return None


_WINDOWS_CTRL_HANDLER = None


def _console_interrupt(_signum, _frame) -> None:
    # Non-Windows/fallback path. These viewers own no durable state.
    os._exit(0)


def _install_console_signal_handlers() -> None:
    global _WINDOWS_CTRL_HANDLER
    if sys.platform == "win32":
        handler_type = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_uint)
        @handler_type
        def _windows_handler(ctrl_type):
            if ctrl_type in (0, 1):  # CTRL_C_EVENT / CTRL_BREAK_EVENT
                ctypes.windll.kernel32.ExitProcess(0)
                return True
            return False
        _WINDOWS_CTRL_HANDLER = _windows_handler
        if not ctypes.windll.kernel32.SetConsoleCtrlHandler(_WINDOWS_CTRL_HANDLER, True):
            raise OSError("SetConsoleCtrlHandler failed")
        return
    signal.signal(signal.SIGINT, _console_interrupt)


def main() -> int:
    _install_console_signal_handlers()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    print("=== Norm Replies ===")
    print("Completed answers are displayed only after the full UTF-8 handoff file is written.\n")
    startup_deadline = time.monotonic() + 90
    seen_runtime = False
    running = False
    next_process_check = 0.0
    while True:
        now = time.monotonic()
        if now >= next_process_check:
            running = norm_process_running()
            next_process_check = now + 1.0
            if running:
                seen_runtime = True
            elif seen_runtime:
                print("\nNorm stopped; closing reply window.")
                return 0
            elif now >= startup_deadline:
                print("\nNorm did not start; closing reply window.")
                return 0
        try:
            claimed = claim_display_file()
            if claimed is not None:
                try:
                    text = claimed.read_text(encoding="utf-8")
                    stamp = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
                    sys.stdout.write(f"\n{'=' * 78}\n[{stamp}] NORM\n{text}")
                    sys.stdout.flush()
                finally:
                    claimed.unlink(missing_ok=True)
            time.sleep(0.2)
        except KeyboardInterrupt:
            print("\nReply window closed.")
            return 0
        except Exception as exc:
            print(f"\n[reply viewer error] {exc}")
            time.sleep(1)


if __name__ == "__main__":
    try:
        code = main()
    except KeyboardInterrupt:
        print("\nReply window closed.")
        code = 0
    raise SystemExit(code)
