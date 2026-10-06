from __future__ import annotations

import ctypes
import os
import signal
import sys
import time
from datetime import datetime

import redis
from rich.console import Console
from rich.markdown import Markdown
from rich.rule import Rule
from rich.text import Text

from norm_gui_common import ROOT, acquire_windows_mutex, load_runtime_config

_WINDOWS_CTRL_HANDLER = None


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


def _queue_config() -> dict:
    cfg = load_runtime_config(ROOT)
    return dict(cfg.get("console_queue", {}))


def _redis_client(cfg: dict) -> redis.Redis:
    return redis.Redis(
        host=cfg.get("host", "127.0.0.1"),
        port=int(cfg.get("port", 6379)),
        db=int(cfg.get("db", 3)),
        decode_responses=False,
        socket_connect_timeout=5,
        socket_keepalive=True,
        health_check_interval=30,
    )


def _decode_redis_text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    raw = bytes(value)
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        try:
            return raw.decode("cp1252")
        except UnicodeDecodeError:
            return raw.decode("utf-8", errors="replace")


def _redis_field(fields: dict, name: str) -> str:
    if name in fields:
        return _decode_redis_text(fields[name])
    key = name.encode("ascii")
    return _decode_redis_text(fields.get(key))


def _render_reply(console: Console, text: str, source: str) -> None:
    stamp = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
    label = "Norm reply" if source != "repeat" else "Repeated Norm reply"
    console.print()
    console.print(Rule(f"{label} · {stamp}", style="cyan"))
    if len(text) <= 250_000:
        console.print(Markdown(text))
    else:
        console.print("Large reply: displaying complete text in chunks without Markdown parsing.", style="dim")
        for start in range(0, len(text), 32_768):
            console.print(Text(text[start:start + 32_768]), end="", soft_wrap=True)
        console.print()


def main() -> int:
    if not acquire_windows_mutex("NormGuiReply"):
        print("Norm Replies is already running in another process. Close the old operator console/process, then run Run-Norm.bat again.")
        return 0
    _install_console_signal_handlers()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    cfg = _queue_config()
    last_answer_key = str(cfg.get("console_last_answer_key", "norm:gui:last-answer"))
    reply_stream = str(cfg.get("console_reply_stream", "norm:gui:replies"))
    client = _redis_client(cfg)
    console = Console(highlight=False)

    console.print(Rule("Norm Replies", style="bold green"))
    console.print("Redis-backed completed answers. Ctrl+C closes only this window.", style="dim")

    last_id = "$"
    try:
        initial = client.get(last_answer_key)
        latest = client.xrevrange(reply_stream, count=1)
        if latest:
            last_id = _decode_redis_text(latest[0][0])
        if initial:
            _render_reply(console, _decode_redis_text(initial), "startup")
    except Exception as exc:
        console.print(f"Replies waiting for Redis: {exc}", style="yellow")

    warned = False
    while True:
        try:
            rows = client.xread({reply_stream: last_id}, block=1000, count=20)
            warned = False
            if not rows:
                continue
            for _, entries in rows:
                for entry_id, fields in entries:
                    last_id = _decode_redis_text(entry_id)
                    text = _redis_field(fields, "text")
                    if text:
                        _render_reply(console, text, _redis_field(fields, "source") or "completed")
        except KeyboardInterrupt:
            return 0
        except Exception as exc:
            if not warned:
                console.print(f"Replies waiting for Redis: {exc}", style="yellow")
                warned = True
            time.sleep(1)


if __name__ == "__main__":
    raise SystemExit(main())
