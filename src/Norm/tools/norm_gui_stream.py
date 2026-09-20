from __future__ import annotations

import json
import sys
import os
import signal
import ctypes
import time
from urllib import request

from norm_gui_common import endpoints, norm_process_running


def render(event: dict) -> None:
    channel = str(event.get("channel", "norm"))
    kind = str(event.get("type", ""))
    text = str(event.get("text", ""))
    source = str(event.get("source", ""))
    if channel == "norm":
        print(f"[norm] {text}", flush=True)
    elif kind == "model_start":
        thinking = "on" if event.get("thinking") else "off"
        print(f"\n--- Ollama {source or 'unknown'} / thinking {thinking} ---", flush=True)
    elif kind == "thinking":
        print(text, end="", flush=True)
    elif kind == "answer":
        print(text, end="", flush=True)
    elif kind == "tool_call":
        print(f"\n[tool] {text}", flush=True)
    elif kind == "model_end":
        print("\n--- Ollama complete ---", flush=True)
    elif kind == "model_error":
        print(f"\n[ollama error] {text}", flush=True)


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
    try:
        ep = endpoints()
    except Exception as exc:
        print(f"Cannot initialize runtime stream: {exc}")
        return 1
    print("=== Norm Runtime Stream ===")
    print(f"Listening: {ep['events']}")
    print("Ctrl+C here only closes this stream window.\n")
    startup_deadline = time.monotonic() + 90
    seen_runtime = False
    while True:
        if not norm_process_running():
            if seen_runtime:
                print("\nNorm stopped; closing runtime stream.", flush=True)
                return 0
            if time.monotonic() >= startup_deadline:
                print("\nNorm did not start; closing runtime stream.", flush=True)
                return 0
            time.sleep(0.25)
            continue
        try:
            with request.urlopen(ep["events"], timeout=None) as response:
                seen_runtime = True
                for raw in response:
                    line = raw.decode("utf-8", errors="replace").strip()
                    if not line.startswith("data: "):
                        continue
                    render(json.loads(line[6:]))
        except KeyboardInterrupt:
            print("\nRuntime stream closed.")
            return 0
        except Exception as exc:
            if seen_runtime:
                time.sleep(0.25)
                if not norm_process_running():
                    print("\nNorm stopped; closing runtime stream.", flush=True)
                    return 0
            print(f"\n[stream reconnect] {exc}", flush=True)
            time.sleep(1)


if __name__ == "__main__":
    try:
        code = main()
    except KeyboardInterrupt:
        print("\nRuntime stream closed.")
        code = 0
    raise SystemExit(code)
