from __future__ import annotations

import json
import os
import queue
import threading
import ctypes
from contextlib import nullcontext
from urllib import request

from prompt_toolkit import PromptSession
from prompt_toolkit.patch_stdout import patch_stdout
from rich.console import Console
from rich.rule import Rule
from rich.text import Text


class NormConsole:
    def __init__(self, chat_url: str, activity_url: str) -> None:
        self.chat_url = chat_url
        self.activity_url = activity_url
        control_base = activity_url.rsplit("/", 1)[0] + "/control"
        self.control_url = control_base + "/cancel-ollama"
        self.shutdown_ollama_url = control_base + "/shutdown-ollama"
        self.shutdown_url = control_base + "/shutdown-norm"
        self.shutdown_now_url = control_base + "/shutdown-norm-now"
        self.stop_all_url = control_base + "/stop-all"
        self.stop_all_now_url = control_base + "/stop-all-now"
        self.console = Console(highlight=False)
        self.muted: set[str] = set()
        self.muted_lock = threading.Lock()
        self.pending: queue.Queue[str] = queue.Queue()
        self.stop_event = threading.Event()
        self.norm_paused = threading.Event()
        self.thread_id: str | None = None

    def _is_muted(self, channel: str) -> bool:
        with self.muted_lock:
            return channel in self.muted

    def _set_muted(self, channel: str, muted: bool) -> None:
        with self.muted_lock:
            if muted:
                self.muted.add(channel)
            else:
                self.muted.discard(channel)

    def _listen(self) -> None:
        while not self.stop_event.is_set():
            try:
                with request.urlopen(self.activity_url, timeout=None) as response:
                    for raw_line in response:
                        if self.stop_event.is_set():
                            return
                        line = raw_line.decode("utf-8").strip()
                        if line.startswith("data: "):
                            self._render(json.loads(line[6:]))
            except Exception as exc:
                if not self.stop_event.is_set():
                    self.console.print(f"[yellow]Activity stream reconnecting: {exc}[/]")
                    self.stop_event.wait(2)

    def _render(self, event: dict) -> None:
        channel = str(event.get("channel", "norm"))
        if self._is_muted(channel):
            return
        kind = str(event.get("type", ""))
        text = str(event.get("text", ""))
        source = str(event.get("source", ""))
        if channel == "norm":
            self.console.print(Text(text, style="dim white"))
        elif kind == "model_start":
            thinking = "on" if event.get("thinking") else "off"
            self.console.print(Rule(f"Ollama · {source or 'unknown'} · thinking {thinking}"))
        elif kind == "thinking":
            self.console.print(Text(text, style="dim cyan"), end="", soft_wrap=True)
        elif kind == "answer":
            self.console.print(Text(text, style="green"), end="", soft_wrap=True)
        elif kind == "tool_call":
            self.console.print()
            self.console.print(Text(f"Tool call: {text}", style="bold yellow"))
        elif kind == "model_end":
            self.console.print()
            self.console.print(Rule("Ollama complete"))
        elif kind == "model_error":
            self.console.print(f"\n[bold red]Ollama call ended: {text}[/]")

    def _submission_worker(self) -> None:
        while not self.stop_event.is_set():
            try:
                message = self.pending.get(timeout=0.25)
            except queue.Empty:
                continue
            while self.norm_paused.is_set() and not self.stop_event.wait(0.25):
                pass
            if self.stop_event.is_set():
                return
            payload = {"message": message, "project_id": "norm-console"}
            if self.thread_id:
                payload["thread_id"] = self.thread_id
            req = request.Request(
                self.chat_url,
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            try:
                with request.urlopen(req, timeout=None) as response:
                    result = json.loads(response.read().decode("utf-8"))
                returned_thread = result.get("primary_thread_id") or result.get("thread_id")
                if isinstance(returned_thread, str) and returned_thread:
                    self.thread_id = returned_thread
            except Exception as exc:
                self.console.print(f"[bold red]Norm request failed: {exc}[/]")
            finally:
                self.pending.task_done()

    def _cancel_ollama(self) -> None:
        req = request.Request(self.control_url, data=b"{}", method="POST")
        try:
            with request.urlopen(req, timeout=5) as response:
                result = json.loads(response.read().decode("utf-8"))
            count = result.get("cancelled", 0)
            self.console.print(f"[yellow]Cancellation requested for {count} active call(s).[/]")
        except Exception as exc:
            self.console.print(f"[red]Could not cancel Ollama: {exc}[/]")

    def _shutdown_ollama(self) -> None:
        req = request.Request(self.shutdown_ollama_url, data=b"{}", method="POST")
        try:
            with request.urlopen(req, timeout=30) as response:
                result = json.loads(response.read().decode("utf-8"))
            status = result.get("status", "unknown")
            remaining = result.get("remaining_processes", [])
            self.console.print(f"[yellow]Ollama shutdown: {status}; remaining processes: {len(remaining)}.[/]")
        except Exception as exc:
            self.console.print(f"[red]Could not shut down Ollama: {exc}[/]")

    def _shutdown_norm(self, immediate: bool = False) -> None:
        url = self.shutdown_now_url if immediate else self.shutdown_url
        req = request.Request(url, data=b"{}", method="POST")
        try:
            with request.urlopen(req, timeout=5) as response:
                result = json.loads(response.read().decode("utf-8"))
            mode = result.get("shutdown", "now" if immediate else "graceful")
            self.console.print(f"[yellow]Norm {mode} shutdown requested.[/]")
            self.stop_event.set()
        except Exception as exc:
            self.console.print(f"[red]Could not shut down Norm: {exc}[/]")

    def _stop_all(self, immediate: bool = False) -> None:
        url = self.stop_all_now_url if immediate else self.stop_all_url
        req = request.Request(url, data=b"{}", method="POST")
        try:
            with request.urlopen(req, timeout=5) as response:
                result = json.loads(response.read().decode("utf-8"))
            mode = result.get("mode", "now" if immediate else "after-step")
            self.console.print(f"[yellow]Stop-all requested ({mode}).[/]")
            self.stop_event.set()
        except Exception as exc:
            self.console.print(f"[red]Could not stop all: {exc}[/]")

    def _show_help(self) -> None:
        self.console.print(
            "[bold]Commands[/]\n"
            "  /mute ollama    Hide Ollama thinking/answer output; work continues.\n"
            "  /unmute ollama  Show Ollama output again.\n"
            "  /mute norm      Hide Norm runtime messages; work continues.\n"
            "  /unmute norm    Show Norm runtime messages again.\n"
            "  /stop ollama    Cancel active Ollama generation.\n"
            "  /shutdown ollama  Unload the model and gracefully stop the Ollama server.\n"
            "  /stop norm      Pause sending queued console prompts after the current one.\n"
            "  /start norm     Resume sending queued console prompts.\n"
            "  /shutdown norm  Stop new work, finish the current slice, persist state, and exit.\n"
            "  /shutdown norm now  Cancel active Ollama work, persist cancellation, and exit promptly.\n"
            "  /stop all       Finish the current step, write SOS.md, unload the model, then stop Ollama and Norm.\n"
            "  /stop all -now  Stop the current generation now, preserve it for resume, write SOS.md, unload/stop all.\n"
            "  /status         Show mute, pause, and pending-input state.\n"
            "  /help           Show these commands.\n"
            "  /exit           Close this console only; Norm keeps running."
        )

    def _command(self, text: str) -> bool:
        parts = text.lower().split()
        if parts == ["/help"]:
            self._show_help()
        elif len(parts) == 2 and parts[0] in {"/mute", "/unmute"} and parts[1] in {"ollama", "norm"}:
            muted = parts[0] == "/mute"
            self._set_muted(parts[1], muted)
            action = "muted" if muted else "unmuted"
            self.console.print(f"[yellow]{parts[1].title()} stream {action}.[/]")
        elif parts == ["/stop", "ollama"]:
            threading.Thread(target=self._cancel_ollama, daemon=True).start()
        elif parts == ["/shutdown", "ollama"]:
            threading.Thread(target=self._shutdown_ollama, daemon=True).start()
        elif parts == ["/stop", "norm"]:
            self.norm_paused.set()
            self.console.print("[yellow]Norm console submissions paused; queued input is preserved.[/]")
        elif parts == ["/start", "norm"]:
            self.norm_paused.clear()
            self.console.print("[yellow]Norm console submissions resumed.[/]")
        elif parts == ["/shutdown", "norm"]:
            self.norm_paused.set()
            self.console.print("[yellow]Norm is checkpointing current work and shutting down.[/]")
            self._shutdown_norm(immediate=False)
            return False
        elif parts == ["/shutdown", "norm", "now"]:
            self.norm_paused.set()
            self.console.print("[yellow]Norm is cancelling active work and shutting down now.[/]")
            self._shutdown_norm(immediate=True)
            return False
        elif parts == ["/stop", "all"]:
            self.norm_paused.set()
            self.console.print("[yellow]Norm will stop after the current step, write SOS.md, unload the model, and stop Ollama.[/]")
            self._stop_all(immediate=False)
            return False
        elif parts == ["/stop", "all", "-now"]:
            self.norm_paused.set()
            self.console.print("[yellow]Norm is checkpointing the current generation, writing SOS.md, and stopping everything now.[/]")
            self._stop_all(immediate=True)
            return False
        elif parts == ["/status"]:
            with self.muted_lock:
                muted = ", ".join(sorted(self.muted)) or "none"
            paused = "yes" if self.norm_paused.is_set() else "no"
            self.console.print(
                f"[cyan]Muted: {muted}; Norm paused: {paused}; queued prompts: {self.pending.qsize()}[/]"
            )
        elif parts == ["/exit"]:
            return False
        else:
            self.console.print("[red]Unknown command. Type /help.[/]")
        return True

    def run(self) -> int:
        listener = threading.Thread(target=self._listen, name="norm-console-events", daemon=True)
        submitter = threading.Thread(target=self._submission_worker, name="norm-console-submit", daemon=True)
        listener.start()
        submitter.start()
        self.console.print(Rule("Norm interactive console"))
        self.console.print("Type a message at any time. Type [bold]/help[/] for controls.")
        try:
            session = PromptSession()
        except Exception:
            session = None
        try:
            output_context = patch_stdout(raw=True) if session is not None else nullcontext()
            with output_context:
                while not self.stop_event.is_set():
                    try:
                        if session is None:
                            text = input("You> ").strip()
                        else:
                            text = session.prompt("You> ").strip()
                    except (EOFError, KeyboardInterrupt):
                        break
                    if not text:
                        continue
                    if text.startswith("/"):
                        if not self._command(text):
                            break
                    else:
                        self.pending.put(text)
                        self.console.print(
                            f"[dim]Queued for Norm ({self.pending.qsize()} waiting). You can keep typing.[/]"
                        )
        finally:
            self.stop_event.set()
        return 0


def run_console(chat_host: str, chat_port: int, activity_host: str, activity_port: int) -> int:
    mutex = None
    if os.name == "nt":
        mutex = ctypes.windll.kernel32.CreateMutexW(None, False, "Local\\NormRichConsole")
        if ctypes.windll.kernel32.GetLastError() == 183:
            if mutex:
                ctypes.windll.kernel32.CloseHandle(mutex)
            Console().print("[yellow]The Norm interactive console is already open.[/]")
            return 0
    chat_url = f"http://{chat_host}:{chat_port}/api/chat"
    activity_url = f"http://{activity_host}:{activity_port}/events"
    try:
        return NormConsole(chat_url, activity_url).run()
    finally:
        if mutex:
            ctypes.windll.kernel32.CloseHandle(mutex)

