from __future__ import annotations

import ctypes
import json
import os
import signal
import sys
import time
from urllib import request

from rich.console import Console
from rich.rule import Rule
from rich.text import Text

from norm_gui_common import acquire_windows_mutex, endpoints, norm_process_running

_WINDOWS_CTRL_HANDLER = None
_NOISE = (
    '"GET /health HTTP/1.1" 200',
    '"GET /status/busy HTTP/1.1" 200',
    '"GET /events HTTP/1.1" 200',
)


def _console_interrupt(_signum, _frame) -> None:
    os._exit(0)


def _install_console_signal_handlers() -> None:
    global _WINDOWS_CTRL_HANDLER
    if sys.platform == "win32":
        handler_type = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_uint)

        @handler_type
        def _windows_handler(ctrl_type):
            if ctrl_type in (0, 1):
                ctypes.windll.kernel32.ExitProcess(0)
                return True
            return False

        _WINDOWS_CTRL_HANDLER = _windows_handler
        if not ctypes.windll.kernel32.SetConsoleCtrlHandler(_WINDOWS_CTRL_HANDLER, True):
            raise OSError("SetConsoleCtrlHandler failed")
        return
    signal.signal(signal.SIGINT, _console_interrupt)


def _render(console: Console, event: dict) -> None:
    channel = str(event.get("channel", "norm"))
    kind = str(event.get("type", ""))
    text = str(event.get("text", ""))
    source = str(event.get("source", ""))

    if channel == "norm":
        if not text or any(marker in text for marker in _NOISE):
            return
        style = "dim white"
        upper = text.upper()
        if " ERROR " in upper or upper.startswith("ERROR"):
            style = "bold red"
        elif " WARNING " in upper or upper.startswith("WARNING"):
            style = "yellow"
        if getattr(console, "_norm_model_line_open", False):
            console.print()
            console._norm_model_line_open = False
        console.print(Text(text, style=style), soft_wrap=True)
        return

    if kind == "model_start":
        console._norm_model_line_open = False
        thinking = "on" if event.get("thinking") else "off"
        console.print()
        console.print(Rule(f"Ollama · {source or 'unknown'} · thinking {thinking}", style="cyan"))
    elif kind == "thinking":
        console.print(Text(text, style="dim cyan"), end="", soft_wrap=True)
        if text:
            console._norm_model_line_open = not text.endswith("\n")
    elif kind == "answer":
        console.print(Text(text, style="green"), end="", soft_wrap=True)
        if text:
            console._norm_model_line_open = not text.endswith("\n")
    elif kind == "tool_call":
        console._norm_model_line_open = False
        console.print()
        console.print(Text(f"Tool call: {text}", style="bold yellow"), soft_wrap=True)
    elif kind == "model_end":
        console._norm_model_line_open = False
        console.print()
        console.print(Rule("Ollama complete", style="dim"))
    elif kind == "model_error":
        console._norm_model_line_open = False
        console.print()
        console.print(Text(f"Ollama error: {text}", style="bold red"), soft_wrap=True)


def main() -> int:
    if not acquire_windows_mutex("NormGuiStream"):
        print("Norm Runtime is already running in another process. Close the old operator console/process, then run Run-Norm.bat again.")
        return 0
    _install_console_signal_handlers()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    try:
        ep = endpoints()
    except Exception as exc:
        print(f"Cannot initialize runtime stream: {exc}")
        return 1

    console = Console(highlight=False)
    console.print(Rule("Norm Runtime", style="bold cyan"))
    console.print("Live Norm/Ollama activity. Ctrl+C closes only this window.", style="dim")

    was_connected = False
    warned = False
    seen_norm = norm_process_running()
    startup_deadline = time.monotonic() + 60
    while True:
        running = norm_process_running()
        seen_norm = seen_norm or running
        if not running and (seen_norm or time.monotonic() >= startup_deadline):
            return 0
        try:
            with request.urlopen(ep["events"], timeout=None) as response:
                if was_connected:
                    console.print("Activity stream reconnected.", style="green")
                was_connected = True
                warned = False
                for raw in response:
                    line = raw.decode("utf-8", errors="replace").strip()
                    if not line.startswith("data: "):
                        continue
                    try:
                        event = json.loads(line[6:])
                    except json.JSONDecodeError:
                        continue
                    _render(console, event)
        except KeyboardInterrupt:
            return 0
        except Exception as exc:
            if not warned:
                console.print(f"Activity stream unavailable; waiting to reconnect: {exc}", style="yellow")
                warned = True
            time.sleep(1)


if __name__ == "__main__":
    raise SystemExit(main())
