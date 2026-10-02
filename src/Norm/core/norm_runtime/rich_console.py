from __future__ import annotations

import json
import os
import socket
import threading
import ctypes
import uuid
import subprocess
import sys
from pathlib import Path
from contextlib import nullcontext
from datetime import datetime, timezone
from urllib import request
from urllib.parse import quote

from .prompt_ingress import GuiPromptDispatcher
from .about import format_about

from prompt_toolkit import PromptSession
from prompt_toolkit.patch_stdout import patch_stdout
from rich.console import Console
from rich.rule import Rule
from rich.text import Text


class NormConsole:
    def __init__(self, chat_url: str, activity_url: str, queue_config: dict | None = None) -> None:
        self.chat_url = chat_url
        self.activity_url = activity_url
        cfg = dict(queue_config or {})
        control_base = activity_url.rsplit("/", 1)[0] + "/control"
        self.control_url = control_base + "/cancel-ollama"
        self.shutdown_ollama_url = control_base + "/shutdown-ollama"
        self.shutdown_url = control_base + "/shutdown-norm"
        self.shutdown_now_url = control_base + "/shutdown-norm-now"
        self.stop_all_url = control_base + "/stop-all"
        self.stop_all_now_url = control_base + "/stop-all-now"
        self.suppress_task_url = control_base + "/suppress-task"
        self.flush_suppressed_url = control_base + "/flush-suppressed"
        self.delete_list_url = control_base + "/delete-list"
        self.restore_delete_url = control_base + "/restore-delete"
        self.delete_files_url = control_base + "/delete-files"
        self.memory_condense_url = control_base + "/memory-condense"
        self.busy_url = activity_url.rsplit("/", 1)[0] + "/status/busy"
        shared_endpoints = {
            "chat": self.chat_url,
            "events": self.activity_url,
            "busy": self.busy_url,
        }
        self.ingress = GuiPromptDispatcher(shared_endpoints, cfg, on_result=self._on_ingress_result, source_name="local-rich")
        self.redis = self.ingress.redis
        self.ingress_stream = self.ingress.stream
        self.ingress_group = self.ingress.group
        self.ingress_consumer = self.ingress.consumer
        self.thread_key = self.ingress.thread_key
        self.dispatching_key = self.ingress.dispatching_key
        self.uncertain_key = self.ingress.uncertain_key
        self.console = Console(highlight=False)
        self.muted: set[str] = set()
        self.muted_lock = threading.Lock()
        self.stop_event = threading.Event()
        self.norm_paused = self.ingress.pause_dispatch
        self.activity_wakeup = threading.Event()
        self.thread_id: str | None = self.redis.get(self.thread_key) or None

    def _on_ingress_result(self, message: str, reply: str, result: dict) -> None:
        task_id = str(result.get("task_id") or "")
        self.console.print(f"[dim]Completed queued prompt task={task_id or 'n/a'}.[/]")

    def _is_muted(self, channel: str) -> bool:
        with self.muted_lock:
            return channel in self.muted

    def _set_muted(self, channel: str, muted: bool) -> None:
        with self.muted_lock:
            if muted:
                self.muted.add(channel)
            else:
                self.muted.discard(channel)

    @staticmethod
    def _now_iso() -> str:
        return datetime.now(timezone.utc).isoformat()

    def _api_base(self) -> str:
        suffix = "/api/chat"
        return self.chat_url[:-len(suffix)] if self.chat_url.endswith(suffix) else self.chat_url.rsplit("/", 1)[0]

    def _thread_rows(self, limit: int = 100) -> list[dict]:
        project_id = "default"
        url = f"{self._api_base()}/api/threads?project_id={quote(project_id)}&limit={max(1, min(int(limit), 200))}"
        with request.urlopen(url, timeout=10) as response:
            payload = json.loads(response.read().decode("utf-8"))
        rows = payload.get("threads")
        return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []

    def _create_named_thread(self, title: str) -> dict:
        req = request.Request(
            f"{self._api_base()}/api/threads/new",
            data=json.dumps({"project_id": "default", "title": title}, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json; charset=utf-8"},
            method="POST",
        )
        with request.urlopen(req, timeout=10) as response:
            return json.loads(response.read().decode("utf-8"))

    @staticmethod
    def _resolve_thread_selector(rows: list[dict], selector: str) -> tuple[dict | None, list[dict]]:
        folded = str(selector or "").strip().casefold()
        if not folded:
            return None, []
        exact_id = [row for row in rows if str(row.get("thread_id") or "").casefold() == folded]
        if len(exact_id) == 1:
            return exact_id[0], exact_id
        exact_title = [row for row in rows if str(row.get("title") or "").strip().casefold() == folded]
        if len(exact_title) == 1:
            return exact_title[0], exact_title
        id_prefix = [row for row in rows if str(row.get("thread_id") or "").casefold().startswith(folded)]
        if len(id_prefix) == 1:
            return id_prefix[0], id_prefix
        title_prefix = [row for row in rows if str(row.get("title") or "").strip().casefold().startswith(folded)]
        if len(title_prefix) == 1:
            return title_prefix[0], title_prefix
        return None, exact_title or id_prefix or title_prefix

    def _enqueue_thread_control(self, kind: str, *, thread_id: str = "", title: str = "") -> str:
        if kind == "thread_reset":
            return self.ingress.enqueue_thread_reset()
        if kind == "thread_switch":
            return self.ingress.enqueue_thread_switch(thread_id, title)
        raise ValueError(f"unsupported thread control: {kind}")

    def _enqueue_prompt(self, message: str) -> tuple[str, str]:
        return self.ingress.enqueue_prompt(message)

    def _queue_stats(self) -> tuple[int | None, int]:
        return self.ingress.queue_stats()

    def _listen(self) -> None:
        while not self.stop_event.is_set():
            try:
                with request.urlopen(self.activity_url, timeout=None) as response:
                    for raw_line in response:
                        if self.stop_event.is_set():
                            return
                        line = raw_line.decode("utf-8").strip()
                        if line.startswith("data: "):
                            self.activity_wakeup.set()
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
            self.console.print(Rule(f"Ollama Â· {source or 'unknown'} Â· thinking {thinking}"))
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
            if event.get("cancelled"):
                self.console.print(f"\n[yellow]Ollama cancelled: {text}[/]")
            else:
                self.console.print(f"\n[bold red]Ollama call ended: {text}[/]")

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

    def _suppress_task(self) -> None:
        req = request.Request(self.suppress_task_url, data=json.dumps({"reason": "Operator requested /suppress-task from Norm console."}).encode("utf-8"), headers={"Content-Type": "application/json"}, method="POST")
        try:
            with request.urlopen(req, timeout=5) as response:
                result = json.loads(response.read().decode("utf-8"))
            self.console.print(f"[yellow]Suppressed: {result.get('title') or result.get('task_id') or result.get('reason', 'none')}.[/]")
        except Exception as exc:
            self.console.print(f"[red]Could not suppress task: {exc}[/]")

    def _flush_suppressed(self) -> None:
        req = request.Request(self.flush_suppressed_url, data=b"{}", method="POST")
        try:
            with request.urlopen(req, timeout=10) as response:
                result = json.loads(response.read().decode("utf-8"))
            self.console.print(f"[yellow]Flushed {int(result.get('deleted') or 0)} suppressed task(s) and {int(result.get('delivery_deleted') or 0)} suppressed delivery record(s).[/]")
        except Exception as exc:
            self.console.print(f"[red]Could not flush suppressed tasks: {exc}[/]")

    def _trash_command(self, action: str, deletion_id: str = "") -> None:
        try:
            if action == "list":
                req = request.Request(self.delete_list_url, data=b"{}", method="POST")
            elif action == "restore":
                req = request.Request(self.restore_delete_url, data=json.dumps({"deletion_id": deletion_id}).encode("utf-8"), headers={"Content-Type": "application/json"}, method="POST")
            else:
                req = request.Request(self.delete_files_url, data=b"{}", method="POST")
            with request.urlopen(req, timeout=10) as response:
                result = json.loads(response.read().decode("utf-8"))
            if action == "list":
                self.console.print_json(data=result)
            elif action == "restore":
                self.console.print(f"[yellow]Restored {len(result.get('restored') or [])}; conflicts {len(result.get('conflicts') or [])}; missing {len(result.get('missing') or [])}.[/]")
            else:
                self.console.print(f"[yellow]Permanently purged {int(result.get('purged') or 0)} trash item(s); held {int(result.get('held') or 0)}.[/]")
        except Exception as exc:
            self.console.print(f"[red]Trash operation failed: {exc}[/]")

    def _run_backup(self, full: bool = False) -> None:
        runtime_root = Path(__file__).resolve().parents[2]
        helper = runtime_root / "tools" / "norm_backup.py"
        python_exe = runtime_root / ".venv" / "Scripts" / "python.exe"
        if not python_exe.is_file():
            python_exe = Path(sys.executable)
        mode = "full" if full else "source"
        label = "sensitive full" if full else "portable source"
        self.console.print(f"[yellow]Creating {label} Norm backup...[/]")
        try:
            proc = subprocess.run([str(python_exe), str(helper), "--mode", mode, "--json"], cwd=str(runtime_root),
                                  capture_output=True, text=True, encoding="utf-8", errors="replace", check=False)
            if proc.returncode != 0:
                raise RuntimeError((proc.stderr or proc.stdout).strip() or f"exit code {proc.returncode}")
            lines = [line.strip() for line in proc.stdout.splitlines() if line.strip()]
            result = json.loads(lines[-1])
            self.console.print(f"[green]Backup created: {result.get('backup_zip')}[/]")
        except Exception as exc:
            self.console.print(f"[red]Backup failed: {exc}[/]")

    def _show_about(self) -> None:
        try:
            runtime_root = Path(os.environ.get("NORM_RUNTIME_ROOT") or Path(__file__).resolve().parents[2]).resolve()
            self.console.print(format_about(runtime_root))
        except Exception as exc:
            self.console.print(f"[red]Could not build about information: {exc}[/]")

    def _memory_condense(self, full: bool = False) -> None:
        req = request.Request(
            self.memory_condense_url,
            data=json.dumps({"full": bool(full)}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with request.urlopen(req, timeout=10) as response:
                result = json.loads(response.read().decode("utf-8"))
            if result.get("scheduled"):
                self.console.print(
                    f"[yellow]Memory condensation scheduled in {result.get('mode')} mode; "
                    "it will run inside the existing worker when idle (no new user task).[/]"
                )
            else:
                self.console.print(f"[red]Memory condensation was not scheduled: {result.get('reason') or result.get('status')}[/]")
        except Exception as exc:
            self.console.print(f"[red]Could not schedule memory condensation: {exc}[/]")

    def _show_busy(self) -> None:
        try:
            with request.urlopen(self.busy_url, timeout=5) as response:
                result = json.loads(response.read().decode("utf-8"))
            self.console.print_json(data=result)
        except Exception as exc:
            self.console.print(f"[red]Could not read busy status: {exc}[/]")

    def _show_help(self) -> None:
        self.console.print(
            "[bold]Commands[/]\n"
            "  /about             Show Norm version, runtime, package, and plugin summary.\n"
            "  /mute ollama       Hide Ollama thinking/answer output; work continues.\n"
            "  /unmute ollama     Show Ollama output again.\n"
            "  /mute norm         Hide Norm runtime messages; work continues.\n"
            "  /unmute norm       Show Norm runtime messages again.\n"
            "  /stop ollama       Cancel active Ollama generation.\n"
            "  /shutdown ollama   Unload the model and gracefully stop the Ollama server.\n"
            "  /stop norm         Pause sending queued console prompts after the current one.\n"
            "  /start norm        Resume sending queued console prompts.\n"
            "  /new [name]        Start a fresh conversation thread, optionally with a title.\n"
            "  /thread-list       List active conversation threads.\n"
            "  /thread-resume NAME  Switch to a thread by exact title, unique prefix, ID, or ID prefix.\n"
            "  /suppress-task     Park the active root task tree, or oldest next queued task tree.\n"
            "  /flush-suppressed  Permanently delete suppressed tasks and parked delivery records.\n"
            "  /delete-list       List reversible soft-deleted files.\n"
            "  /restore-delete ID Restore one deletion ID; use all to restore all non-conflicting items.\n"
            "  /delete-files      Permanently purge reversible trash now.\n"
            "  /backup            Create a portable installer/source backup.\n"
            "  /backup full       Create a sensitive full backup with private state and PostgreSQL.\n"
            "  /memory-condense   Incrementally refresh consolidated background memory when idle.\n"
            "  /memory-condense -full  Rebuild consolidated background memory from the full surviving source set.\n"
            "  /status            Show local mute, pause, and pending-input state.\n"
            "  /status/busy       Show authoritative runtime busy state.\n"
            "  /stop-all          Finish the current step, snapshot recovery state, then stop Norm/Ollama.\n"
            "  /stop-all now      Emergency checkpoint/snapshot and stop Norm/Ollama now.\n"
            "  /shutdown norm     Gracefully stop Norm after checkpointing current work.\n"
            "  /shutdown norm now Cancel active work and stop Norm promptly.\n"
            "  /help              Show these commands.\n"
            "  /exit              Close this console only; Norm keeps running."
        )

    def _command(self, text: str) -> bool:
        stripped = text.strip()
        parts = stripped.lower().split()
        if parts == ["/help"]:
            self._show_help()
        elif parts == ["/about"]:
            self._show_about()
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
        elif parts in (["/stop-all"], ["/stop", "all"]):
            self.norm_paused.set()
            self.console.print("[yellow]Norm will stop after the current step and preserve recovery state.[/]")
            self._stop_all(immediate=False)
            return False
        elif parts in (["/stop-all", "now"], ["/stop-all", "-now"], ["/stop", "all", "now"], ["/stop", "all", "-now"]):
            self.norm_paused.set()
            self.console.print("[yellow]Norm is checkpointing the current generation and stopping everything now.[/]")
            self._stop_all(immediate=True)
            return False
        elif parts == ["/suppress-task"]:
            threading.Thread(target=self._suppress_task, daemon=True).start()
        elif parts == ["/flush-suppressed"]:
            threading.Thread(target=self._flush_suppressed, daemon=True).start()
        elif parts == ["/delete-list"]:
            threading.Thread(target=self._trash_command, args=("list",), daemon=True).start()
        elif parts and parts[0] == "/restore-delete" and len(parts) == 2:
            threading.Thread(target=self._trash_command, args=("restore", parts[1]), daemon=True).start()
        elif parts == ["/delete-files"]:
            threading.Thread(target=self._trash_command, args=("purge",), daemon=True).start()
        elif parts == ["/backup"]:
            threading.Thread(target=self._run_backup, kwargs={"full": False}, daemon=True).start()
        elif parts in (["/backup", "full"], ["/backup-zip"]):
            threading.Thread(target=self._run_backup, kwargs={"full": True}, daemon=True).start()
        elif parts == ["/memory-condense"]:
            threading.Thread(target=self._memory_condense, kwargs={"full": False}, daemon=True).start()
        elif parts in (["/memory-condense", "-full"], ["/memory-condense", "--full"], ["/memory-condense", "full"]):
            threading.Thread(target=self._memory_condense, kwargs={"full": True}, daemon=True).start()
        elif parts == ["/new"] or (parts and parts[0] == "/new"):
            name = stripped[len("/new"):].strip()
            try:
                if name:
                    created = self._create_named_thread(name)
                    thread_id = str(created.get("thread_id") or "")
                    title = str(created.get("title") or name)
                    entry_id = self._enqueue_thread_control("thread_switch", thread_id=thread_id, title=title)
                    self.console.print(f"[yellow]Created '{title}' ({thread_id}); queued thread switch as {entry_id}.[/]")
                else:
                    entry_id = self._enqueue_thread_control("thread_reset")
                    self.console.print(f"[yellow]Queued a fresh auto-titled thread as {entry_id}.[/]")
            except Exception as exc:
                self.console.print(f"[red]Could not create thread: {exc}[/]")
        elif parts == ["/thread-list"]:
            try:
                rows = self._thread_rows()
                current = self.redis.get(self.thread_key) or self.thread_id or ""
                if not rows:
                    self.console.print("[yellow]No active conversation threads found.[/]")
                else:
                    self.console.print("[bold]Conversation threads[/] (* = current)")
                    for index, row in enumerate(rows):
                        tid = str(row.get("thread_id") or "")
                        marker = "*" if tid == current else " "
                        self.console.print(f" {marker} [{index}] {row.get('title') or '[untitled]'}  ({tid})")
            except Exception as exc:
                self.console.print(f"[red]Could not list threads: {exc}[/]")
        elif parts and parts[0] == "/thread-resume":
            selector = stripped[len("/thread-resume"):].strip()
            if not selector:
                self.console.print("[yellow]Usage: /thread-resume <thread name|thread id>[/]")
            else:
                try:
                    rows = self._thread_rows()
                    selected, candidates = self._resolve_thread_selector(rows, selector)
                    if selected is None:
                        if candidates:
                            self.console.print("[yellow]Thread selector is ambiguous:[/]")
                            for row in candidates:
                                self.console.print(f"  {row.get('title') or '[untitled]'}  ({row.get('thread_id')})")
                        else:
                            self.console.print(f"[yellow]No active thread matched: {selector}[/]")
                    else:
                        tid = str(selected.get("thread_id") or "")
                        title = str(selected.get("title") or "[untitled]")
                        entry_id = self._enqueue_thread_control("thread_switch", thread_id=tid, title=title)
                        self.console.print(f"[yellow]Queued switch to '{title}' ({tid}) as {entry_id}.[/]")
                except Exception as exc:
                    self.console.print(f"[red]Could not resume thread: {exc}[/]")
        elif parts == ["/status/busy"]:
            threading.Thread(target=self._show_busy, daemon=True).start()
        elif parts == ["/status"]:
            with self.muted_lock:
                muted = ", ".join(sorted(self.muted)) or "none"
            paused = "yes" if self.norm_paused.is_set() else "no"
            queued, uncertain = self._queue_stats()
            self.console.print(
                f"[cyan]Muted: {muted}; Norm paused: {paused}; Redis queued/in-flight: {queued}; uncertain: {uncertain}[/]"
            )
        elif parts == ["/exit"]:
            return False
        else:
            self.console.print("[red]Unknown command. Type /help.[/]")
        return True

    def run(self) -> int:
        listener = threading.Thread(target=self._listen, name="norm-console-events", daemon=True)
        listener.start()
        self.ingress.start()
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
                        entry_id, prompt_id = self._enqueue_prompt(text)
                        queued, uncertain = self._queue_stats()
                        self.console.print(
                            f"[dim]Queued {prompt_id[:8]} in Redis as {entry_id} ({queued} queued/in-flight; {uncertain} uncertain). You can keep typing.[/]"
                        )
        finally:
            self.stop_event.set()
            self.ingress.stop()
        return 0


def run_console(chat_host: str, chat_port: int, activity_host: str, activity_port: int, queue_config: dict | None = None) -> int:
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
        return NormConsole(chat_url, activity_url, queue_config=queue_config).run()
    finally:
        if mutex:
            ctypes.windll.kernel32.CloseHandle(mutex)
