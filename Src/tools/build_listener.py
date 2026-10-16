from __future__ import annotations

import argparse
import configparser
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from urllib import request

ROOT = Path(__file__).resolve().parents[1]
SETTINGS = ROOT / "config" / "settings.ini"
RUNTIME_JSON = ROOT / "config" / "runtime.json"
MANIFEST = ROOT / "package-manifest.json"


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def read_settings() -> configparser.ConfigParser:
    if not SETTINGS.is_file():
        raise RuntimeError(f"Missing Norm settings: {SETTINGS}")
    parser = configparser.ConfigParser(interpolation=None)
    parser.read(SETTINGS, encoding="utf-8-sig")
    return parser


def resolve_remote_host(raw: str, machine: str, domain: str, role: str) -> str:
    value = str(raw or "").strip()
    lowered = value.casefold()
    current = f"{machine}.{domain}".strip(".")
    if lowered in {"current", "tailscale"}:
        if not current or "." not in current:
            raise RuntimeError(f"Cannot resolve {role} host from current_machine/current_domain: {current!r}")
        return current
    if lowered in {"loopback", "localhost", "127.0.0.1", "::1"}:
        if not current or "." not in current:
            raise RuntimeError(f"{role} is loopback-only and no tailnet host name is configured")
        print(
            f"WARNING: network.{role}_host={value!r}; listener will target {current}. "
            "The service must actually listen on the tailnet interface."
        )
        return current
    if not value:
        raise RuntimeError(f"network.{role}_host is blank")
    return value


def installed_version() -> str:
    try:
        return str(json.loads(MANIFEST.read_text(encoding="utf-8-sig")).get("version") or "unknown")
    except Exception:
        return "unknown"


def health(url: str) -> tuple[bool, str]:
    try:
        with request.urlopen(url, timeout=2.5) as response:
            return True, f"HTTP {response.status}"
    except Exception as exc:
        return False, str(exc)


def wiring_payload(
    *,
    source_label: str,
    machine: str,
    domain: str,
    norm_host: str,
    norm_port: int,
    activity_host: str,
    activity_port: int,
    redis_host: str,
    redis_port: int,
    queue_cfg: dict,
    version: str,
    client_id: str,
) -> dict:
    endpoints = {
        "host": norm_host,
        "chat": f"http://{norm_host}:{norm_port}/api/chat",
        "chat_health": f"http://{norm_host}:{norm_port}/health",
        "activity": f"http://{activity_host}:{activity_port}",
        "activity_health": f"http://{activity_host}:{activity_port}/health",
        "events": f"http://{activity_host}:{activity_port}/events",
        "busy": f"http://{activity_host}:{activity_port}/status/busy",
        "shutdown": f"http://{activity_host}:{activity_port}/control/shutdown-norm",
        "shutdown_now": f"http://{activity_host}:{activity_port}/control/shutdown-norm-now",
        "stop_all": f"http://{activity_host}:{activity_port}/control/stop-all",
        "stop_all_now": f"http://{activity_host}:{activity_port}/control/stop-all-now",
        "suppress_task": f"http://{activity_host}:{activity_port}/control/suppress-task",
        "resume_maintenance": f"http://{activity_host}:{activity_port}/control/resume-maintenance",
        "inject_context": f"http://{activity_host}:{activity_port}/control/inject-context",
        "flush_suppressed": f"http://{activity_host}:{activity_port}/control/flush-suppressed",
        "soft_delete": f"http://{activity_host}:{activity_port}/control/soft-delete",
        "delete_list": f"http://{activity_host}:{activity_port}/control/delete-list",
        "restore_delete": f"http://{activity_host}:{activity_port}/control/restore-delete",
        "delete_files": f"http://{activity_host}:{activity_port}/control/delete-files",
        "memory_condense": f"http://{activity_host}:{activity_port}/control/memory-condense",
        "switch_model": f"http://{activity_host}:{activity_port}/control/switch-model",
    }
    redis_cfg = dict(queue_cfg)
    redis_cfg["host"] = redis_host
    redis_cfg["port"] = redis_port
    redis_cfg["db"] = int(redis_cfg.get("db", 3))
    return {
        "schema": 1,
        "listener": "Norm-Listener",
        "client_id": client_id,
        "source": {
            "machine": machine,
            "domain": domain,
            "label": source_label,
            "norm_version": version,
        },
        "endpoints": endpoints,
        "redis": redis_cfg,
        "roles": {
            "runtime": {"mode": "sse", "endpoint": endpoints["events"]},
            "replies": {"mode": "redis", "host": redis_host, "port": redis_port, "db": redis_cfg["db"]},
            "prompt": {"mode": "redis-ingress", "host": redis_host, "port": redis_port, "db": redis_cfg["db"]},
        },
        "note": "Connection wiring only. No PostgreSQL password, model API key, or other secret is stored here.",
    }


def write_common(path: Path) -> None:
    code = r'''from __future__ import annotations
import ctypes
import json
import os
import sys
from pathlib import Path

ROOT = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
STATE_DIR = Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "Norm-Listener"
STATE_DIR.mkdir(parents=True, exist_ok=True)
FALLBACK_LOG = STATE_DIR / "history-fallback.jsonl"
_MUTEX_HANDLES = []

def _config_path() -> Path:
    override = os.environ.get("NORM_LISTENER_CONFIG", "").strip()
    if override:
        return Path(override).expanduser().resolve()
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().with_suffix(".json")
    return Path(__file__).resolve().with_name("Norm-Listener.json")

def listener_config() -> dict:
    path = _config_path()
    if not path.is_file():
        raise RuntimeError(f"Listener wiring JSON is missing: {path}")
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    if int(data.get("schema", 0)) != 1:
        raise RuntimeError(f"Unsupported listener wiring schema in {path}")
    return data

def load_runtime_config(root: Path = ROOT) -> dict:
    cfg = listener_config()
    return {"console_queue": dict(cfg.get("redis") or {}), "postgres": {}}

def acquire_windows_mutex(name: str) -> bool:
    if os.name != "nt": return True
    cfg = listener_config()
    client_id = str(cfg.get("client_id") or "listener")
    safe = "".join(ch if ch.isalnum() else "_" for ch in (name + "_" + client_id))
    handle = ctypes.windll.kernel32.CreateMutexW(None, False, safe)
    if not handle: return False
    if ctypes.windll.kernel32.GetLastError() == 183:
        ctypes.windll.kernel32.CloseHandle(handle); return False
    _MUTEX_HANDLES.append(handle); return True

def append_fallback_jsonl(record: dict) -> None:
    with FALLBACK_LOG.open("a", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n"); fh.flush(); os.fsync(fh.fileno())

def endpoints() -> dict[str, str]:
    return dict(listener_config().get("endpoints") or {})

def norm_process_running() -> bool:
    return True

def start_norm_detached():
    raise RuntimeError("This is a remote listener; start Norm on the source machine.")
'''
    path.write_text(code, encoding="utf-8", newline="\n")


def _sub_once(pattern: str, replacement: str, text: str, *, label: str, flags: int = 0) -> str:
    updated, count = re.subn(pattern, replacement, text, count=1, flags=flags)
    if count != 1:
        raise RuntimeError(f"Listener patch failed for {label}: expected exactly one match, got {count}")
    return updated


def patch_prompt(text: str, *, client_id: str) -> str:
    text = text.replace("from psycopg import sql\n", "")
    text = _sub_once(
        r"def read_console_line\(label: str\) -> str:.*?\n\ndef now_iso",
        '''def read_console_line(label: str) -> str:\n    # Listener mode uses ordinary stdio pipes; the desktop shell owns line editing.\n    print(label, flush=True)\n    line = sys.stdin.readline()\n    if line == "":\n        raise EOFError\n    return line.rstrip(chr(13) + chr(10))\n\ndef now_iso''',
        text,
        label="stdio prompt bridge",
        flags=re.S,
    )
    text = _sub_once(
        r"def load_last_turn_from_postgres\(\).*?\n\ndef load_last_turn_from_fallback",
        'def load_last_turn_from_postgres() -> tuple[str | None, str | None, str | None]:\n    raise RuntimeError("PostgreSQL history is intentionally not bundled in the listener")\n\ndef load_last_turn_from_fallback',
        text,
        label="PostgreSQL history removal",
        flags=re.S,
    )
    text = _sub_once(
        r"def ensure_norm_running\(ep: dict\[str, str\]\) -> None:.*?\n\ndef show_help",
        '''def ensure_norm_running(ep: dict[str, str]) -> None:\n    if health_ok(ep["chat_health"]) and health_ok(ep["activity_health"]):\n        return\n    raise RuntimeError("Remote Norm APIs are offline or unreachable. Start Norm on the source machine and verify connectivity.")\n\ndef show_help''',
        text,
        label="remote Norm health guard",
        flags=re.S,
    )
    text = _sub_once(
        r"def run_backup\(\*, full: bool\) -> None:.*?\n\ndef graceful_shutdown",
        '''def run_backup(*, full: bool) -> None:\n    print("Backup is source-machine-only. Run /backup from a local Norm Prompt or on the Norm machine.")\n\ndef graceful_shutdown''',
        text,
        label="remote backup guard",
        flags=re.S,
    )
    text = _sub_once(
        r"def stop_all\(ep: dict\[str, str\], immediate: bool\) -> None:.*?\n\ndef main",
        '''def stop_all(ep: dict[str, str], immediate: bool) -> None:\n    endpoint = ep["stop_all_now"] if immediate else ep["stop_all"]\n    result = post_json(endpoint, timeout=5)\n    print(f"Stop-all accepted: {result.get('mode') or result.get('status') or ('immediate' if immediate else 'after-step')}.")\n\ndef main''',
        text,
        label="remote stop-all routing",
        flags=re.S,
    )
    old_mutex = "acquire_windows_mutex('NormGuiPrompt')"
    if old_mutex not in text:
        raise RuntimeError("Listener patch failed for prompt mutex: source marker not found")
    text = text.replace(old_mutex, f"acquire_windows_mutex('NormListenerPrompt_{client_id}')", 1)
    old_source = 'source_name="ssh-gui"'
    if old_source not in text:
        raise RuntimeError("Listener patch failed for prompt source name: source marker not found")
    text = text.replace(old_source, 'source_name="listener-gui"', 1)
    return text


def desktop_source() -> str:
    return r'''from __future__ import annotations
import codecs
import ctypes
import json
import os
import queue
import re
import subprocess
import sys
import threading
from pathlib import Path

ANSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
ROLES = ("runtime", "replies", "prompt")
ROLE_TITLES = {"runtime": "Runtime", "replies": "Replies", "prompt": "Prompt"}

def config_path() -> Path:
    override = os.environ.get("NORM_LISTENER_CONFIG", "").strip()
    if override:
        return Path(override).expanduser().resolve()
    return Path(sys.executable).resolve().with_suffix(".json")

def load_config() -> dict:
    path = config_path()
    if not path.is_file():
        raise RuntimeError(f"Missing listener wiring JSON: {path}")
    return json.loads(path.read_text(encoding="utf-8-sig"))

def role_main(role: str) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        try: sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True, write_through=True)
        except Exception: pass
    if hasattr(sys.stderr, "reconfigure"):
        try: sys.stderr.reconfigure(encoding="utf-8", errors="replace", line_buffering=True, write_through=True)
        except Exception: pass
    if role == "runtime":
        import norm_gui_stream
        return int(norm_gui_stream.main() or 0)
    if role == "replies":
        import norm_gui_reply
        return int(norm_gui_reply.main() or 0)
    if role == "prompt":
        import norm_gui_prompt
        return int(norm_gui_prompt.main() or 0)
    raise RuntimeError(f"Unknown listener role: {role}")

class RolePane:
    def __init__(self, app, parent, role: str):
        import tkinter as tk
        from tkinter import ttk
        from tkinter.scrolledtext import ScrolledText
        self.app = app; self.role = role; self.proc = None; self.reader = None
        self.frame = ttk.Frame(parent, padding=6)
        head = ttk.Frame(self.frame); head.pack(fill="x")
        ttk.Label(head, text=ROLE_TITLES[role], font=("Segoe UI", 11, "bold")).pack(side="left")
        self.status = tk.StringVar(value="stopped")
        ttk.Label(head, textvariable=self.status).pack(side="left", padx=(8,0))
        ttk.Button(head, text="Restart", command=self.restart).pack(side="right")
        ttk.Button(head, text="Clear", command=self.clear).pack(side="right", padx=(0,6))
        self.output = ScrolledText(self.frame, wrap="word", font=("Cascadia Mono", 9), height=28)
        self.output.pack(fill="both", expand=True, pady=(6,6))
        foot = ttk.Frame(self.frame); foot.pack(fill="x")
        self.input = ttk.Entry(foot)
        self.input.pack(side="left", fill="x", expand=True)
        self.input.bind("<Return>", lambda _e: self.send())
        ttk.Button(foot, text="Send", command=self.send).pack(side="left", padx=(6,0))

    def append(self, text: str):
        clean = ANSI_RE.sub("", text).replace("\r", "")
        if not clean: return
        self.output.insert("end", clean)
        self.output.see("end")

    def clear(self):
        self.output.delete("1.0", "end")

    def start(self):
        if self.proc is not None and self.proc.poll() is None:
            return
        env = os.environ.copy()
        env["NORM_LISTENER_CONFIG"] = str(self.app.config_file)
        env["PYTHONUNBUFFERED"] = "1"
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
        self.proc = subprocess.Popen(
            [sys.executable, "--listener-role", self.role],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            bufsize=0,
            creationflags=flags,
            env=env,
        )
        self.status.set(f"running pid={self.proc.pid}")
        self.append(f"[listener] started {self.role} pid={self.proc.pid}\n")
        self.reader = threading.Thread(target=self._read_output, daemon=True)
        self.reader.start()

    def _read_output(self):
        decoder = codecs.getincrementaldecoder("utf-8")("replace")
        try:
            assert self.proc is not None and self.proc.stdout is not None
            while True:
                chunk = self.proc.stdout.read(4096)
                if not chunk:
                    break
                text = decoder.decode(chunk)
                if text:
                    self.app.events.put(("output", self.role, text))
            tail = decoder.decode(b"", final=True)
            if tail:
                self.app.events.put(("output", self.role, tail))
        except Exception as exc:
            self.app.events.put(("output", self.role, f"\n[listener reader error] {exc}\n"))
        finally:
            rc = self.proc.poll() if self.proc is not None else None
            self.app.events.put(("exit", self.role, rc))

    def send(self):
        text = self.input.get()
        self.input.delete(0, "end")
        if self.proc is None or self.proc.poll() is not None or self.proc.stdin is None:
            self.append("[listener] role is not running; restart it first.\n")
            return
        try:
            self.proc.stdin.write((text + "\n").encode("utf-8"))
            self.proc.stdin.flush()
        except Exception as exc:
            self.append(f"[listener input error] {exc}\n")

    def stop(self):
        proc = self.proc
        self.proc = None
        if proc is None or proc.poll() is not None:
            return
        try:
            if proc.stdin:
                proc.stdin.close()
        except Exception:
            pass
        try:
            proc.terminate(); proc.wait(timeout=2)
        except Exception:
            try: proc.kill()
            except Exception: pass
        self.status.set("stopped")

    def restart(self):
        self.stop(); self.start()

class ListenerApp:
    def __init__(self):
        import tkinter as tk
        from tkinter import ttk
        self.root = tk.Tk()
        self.config_file = config_path()
        self.config = load_config()
        self.events = queue.Queue()
        source = self.config.get("source") or {}
        label = source.get("label") or (self.config.get("endpoints") or {}).get("host") or "Norm"
        self.root.title(f"Norm Listener — {label}")
        self.root.geometry("1500x760")
        self.root.minsize(1000, 520)
        shell = ttk.Frame(self.root, padding=(10,8)); shell.pack(fill="both", expand=True)
        top = ttk.Frame(shell); top.pack(fill="x", pady=(0,6))
        ttk.Label(top, text="Norm Listener", font=("Segoe UI", 16, "bold")).pack(side="left")
        ttk.Label(top, text=f"  {label}  •  wiring: {self.config_file.name}").pack(side="left")
        ttk.Button(top, text="Restart all", command=self.restart_all).pack(side="right")
        panes = ttk.Panedwindow(shell, orient="horizontal"); panes.pack(fill="both", expand=True)
        self.panes = {}
        for role in ROLES:
            pane = RolePane(self, panes, role)
            self.panes[role] = pane
            panes.add(pane.frame, weight=1)
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.root.after(60, self.poll)
        self.root.after(100, self.start_all)

    def start_all(self):
        for role in ROLES: self.panes[role].start()
        self.panes["prompt"].input.focus_set()

    def restart_all(self):
        for role in ROLES: self.panes[role].restart()

    def poll(self):
        try:
            while True:
                kind, role, payload = self.events.get_nowait()
                pane = self.panes.get(role)
                if pane is None: continue
                if kind == "output": pane.append(str(payload))
                elif kind == "exit":
                    pane.status.set(f"exited rc={payload}")
                    pane.append(f"\n[listener] {role} exited rc={payload}\n")
        except queue.Empty:
            pass
        self.root.after(60, self.poll)

    def close(self):
        for role in ROLES: self.panes[role].stop()
        self.root.destroy()

    def run(self) -> int:
        self.root.mainloop(); return 0

def gui_main() -> int:
    # The EXE is built as console-subsystem so role children have real stdio pipes.
    # Detach only the desktop parent so double-clicking leaves one GUI window, not a console.
    if os.name == "nt":
        try: ctypes.windll.kernel32.FreeConsole()
        except Exception: pass
    return ListenerApp().run()

def main() -> int:
    if "--listener-role" in sys.argv:
        i = sys.argv.index("--listener-role")
        if i + 1 >= len(sys.argv): return 2
        return role_main(sys.argv[i + 1].lower())
    return gui_main()

if __name__ == "__main__":
    raise SystemExit(main())
'''


def main() -> int:
    ap = argparse.ArgumentParser(description="Build one desktop Norm listener with Runtime, Replies, and Prompt panes")
    ap.add_argument("--output", default="Norm-Listener.exe")
    ap.add_argument("--clean", action="store_true")
    args = ap.parse_args()

    settings = read_settings()
    machine = settings.get("network", "current_machine", fallback="").strip()
    domain = settings.get("network", "current_domain", fallback="").strip()
    source_label = f"{machine}.{domain}".strip(".")
    norm_host = resolve_remote_host(settings.get("network", "norm_host", fallback="current"), machine, domain, "norm")
    activity_host = resolve_remote_host(settings.get("network", "activity_host", fallback="current"), machine, domain, "activity")
    redis_host = resolve_remote_host(settings.get("network", "redis_host", fallback="current"), machine, domain, "redis")
    norm_port = settings.getint("network", "norm_port", fallback=12543)
    activity_port = settings.getint("network", "activity_port", fallback=8766)
    redis_port = settings.getint("network", "redis_port", fallback=6379)
    runtime = json.loads(RUNTIME_JSON.read_text(encoding="utf-8-sig"))
    queue_cfg = dict(runtime.get("console_queue") or {})
    queue_cfg["host"] = redis_host; queue_cfg["port"] = redis_port; queue_cfg.setdefault("db", 3)
    version = installed_version()
    client_id = hashlib.sha256(f"{source_label}|{norm_host}|{activity_host}|{redis_host}".encode()).hexdigest()[:12]

    output = Path(args.output).expanduser()
    if not output.is_absolute(): output = ROOT / output
    output = output.resolve(); output.parent.mkdir(parents=True, exist_ok=True)
    wiring_file = output.with_suffix(".json")
    wiring = wiring_payload(
        source_label=source_label or norm_host,
        machine=machine,
        domain=domain,
        norm_host=norm_host,
        norm_port=norm_port,
        activity_host=activity_host,
        activity_port=activity_port,
        redis_host=redis_host,
        redis_port=redis_port,
        queue_cfg=queue_cfg,
        version=version,
        client_id=client_id,
    )

    for label, url in (("Norm HTTP", f"http://{norm_host}:{norm_port}/health"), ("Activity", f"http://{activity_host}:{activity_port}/health")):
        ok, detail = health(url); print(f"{label}: {'reachable' if ok else 'NOT reachable'} ({detail})")
    print(f"Listener target: {source_label or norm_host} | Norm {version}")
    print(f"Redis: {redis_host}:{redis_port}/db{queue_cfg.get('db',3)}")

    py = Path(sys.executable).resolve()
    cache = ROOT / "state" / "build-cache" / "listener"
    work = cache / "work"; spec = cache / "spec"
    if args.clean and cache.exists(): shutil.rmtree(cache)
    work.mkdir(parents=True, exist_ok=True); spec.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="norm-listener-build-") as td:
        tmp = Path(td); (tmp / "norm_runtime").mkdir(); (tmp / "docs").mkdir()
        shutil.copy2(ROOT / "core" / "norm_runtime" / "prompt_ingress.py", tmp / "norm_runtime" / "prompt_ingress.py")
        (tmp / "norm_runtime" / "__init__.py").write_text("", encoding="utf-8")
        (tmp / "norm_runtime" / "about.py").write_text(
            f'def format_about(root): return "Norm Listener\\nTarget: {source_label or norm_host}\\nNorm version: {version}\\nDesktop listener only; no local Norm model/runtime process."\n',
            encoding="utf-8",
        )
        (tmp / "runtime_bootstrap.py").write_text('def build_postgres_pool(root): raise RuntimeError("PostgreSQL history is not bundled in Norm Listener")\n', encoding="utf-8")
        for name in ("norm_gui_stream.py", "norm_gui_reply.py", "norm_gui_dispatch.py"):
            shutil.copy2(ROOT / "tools" / name, tmp / name)
        prompt_text = (ROOT / "tools" / "norm_gui_prompt.py").read_text(encoding="utf-8-sig")
        (tmp / "norm_gui_prompt.py").write_text(patch_prompt(prompt_text, client_id=client_id), encoding="utf-8", newline="\n")
        write_common(tmp / "norm_gui_common.py")
        help_src = ROOT / "docs" / "help_menu.txt"
        if help_src.is_file(): shutil.copy2(help_src, tmp / "docs" / "help_menu.txt")
        else: (tmp / "docs" / "help_menu.txt").write_text("Type normal prompts or /status, /queue, /repeat-answer, /shutdown, /exit.\n", encoding="utf-8")
        launcher = tmp / "norm_listener.py"; launcher.write_text(desktop_source(), encoding="utf-8", newline="\n")
        dist = tmp / "dist"
        cmd = [
            str(py), "-m", "PyInstaller", "--noconfirm", "--onefile", "--console",
            "--name", output.stem,
            "--distpath", str(dist), "--workpath", str(work), "--specpath", str(spec),
            "--paths", str(tmp), "--add-data", f"{tmp / 'docs' / 'help_menu.txt'};docs",
            str(launcher),
        ]
        print("Building desktop listener...")
        cp = subprocess.run(cmd, cwd=str(ROOT))
        if cp.returncode: return cp.returncode
        built = dist / (output.stem + ".exe")
        if not built.is_file(): raise RuntimeError(f"PyInstaller did not create {built}")
        if output.exists(): output.unlink()
        shutil.copy2(built, output)

    wiring_file.write_text(json.dumps(wiring, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    digest = sha256_file(output)
    print(f"Created: {output}")
    print(f"Wiring:  {wiring_file}")
    print(f"Size: {output.stat().st_size:,} bytes")
    print(f"SHA256: {digest}")
    print("Move the EXE and its same-named JSON together. Double-click the EXE for the three-pane desktop listener.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(130)
    except Exception as exc:
        print(f"ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise SystemExit(1)
