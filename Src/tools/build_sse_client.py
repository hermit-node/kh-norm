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
MANIFEST = ROOT / "package-manifest.json"


def _read_settings() -> configparser.ConfigParser:
    if not SETTINGS.is_file():
        raise RuntimeError(f"Missing Norm settings: {SETTINGS}")
    parser = configparser.ConfigParser(interpolation=None)
    with SETTINGS.open("r", encoding="utf-8-sig") as handle:
        parser.read_file(handle)
    return parser


def _resolve_remote_host(raw: str, machine: str, domain: str, *, role: str) -> str:
    value = str(raw or "").strip()
    lowered = value.casefold()
    if lowered in {"current", "tailscale"}:
        host = f"{machine}.{domain}".strip(".")
        if not host or "." not in host:
            raise RuntimeError(
                f"Cannot resolve {role} host from network.current_machine/current_domain: {host!r}"
            )
        return host
    if lowered in {"loopback", "localhost", "127.0.0.1", "::1"}:
        raise RuntimeError(
            f"network.{role}_host is {value!r}. A relocatable SSE client cannot reach a loopback-only service. "
            f"Bind {role} to the Norm machine's tailnet address/current host first."
        )
    if not value:
        raise RuntimeError(f"network.{role}_host is blank")
    return value


def _project_version() -> str:
    try:
        payload = json.loads(MANIFEST.read_text(encoding="utf-8-sig"))
        value = str(payload.get("version") or "").strip()
        if value:
            return value
    except Exception:
        pass
    try:
        parser = _read_settings()
        return parser.get("project", "version", fallback="unknown").strip() or "unknown"
    except Exception:
        return "unknown"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_name(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", value.strip()).strip("-.")
    return cleaned or "norm"


def _health_probe(url: str) -> tuple[bool, str]:
    try:
        req = request.Request(url, headers={"User-Agent": "Norm-SSE-Builder"})
        with request.urlopen(req, timeout=2.5) as response:
            return True, f"HTTP {response.status}"
    except Exception as exc:
        return False, str(exc)


def _client_source(*, events_url: str, health_url: str, source_name: str, version: str) -> str:
    config = json.dumps(
        {
            "events_url": events_url,
            "health_url": health_url,
            "source_name": source_name,
            "norm_version": version,
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return f'''from __future__ import annotations

import argparse
import ctypes
import json
import os
import signal
import sys
import time
from urllib import request

CONFIG = json.loads({config!r})
ANSI = sys.stdout.isatty()


def _enable_vt() -> None:
    global ANSI
    if os.name != "nt" or not ANSI:
        return
    try:
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.GetStdHandle(-11)
        mode = ctypes.c_uint()
        if kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            ANSI = bool(kernel32.SetConsoleMode(handle, mode.value | 0x0004))
    except Exception:
        ANSI = False


def _c(code: str, text: str) -> str:
    return f"\\x1b[{{code}}m{{text}}\\x1b[0m" if ANSI else text


def _write(text: str, *, color: str = "", end: str = "\\n", stream=None) -> None:
    target = stream or sys.stdout
    target.write(_c(color, text) if color else text)
    target.write(end)
    target.flush()


def _render(event: dict, raw: bool) -> None:
    if raw:
        _write(json.dumps(event, ensure_ascii=False))
        return
    channel = str(event.get("channel", "norm"))
    kind = str(event.get("type", ""))
    text = str(event.get("text", ""))
    source = str(event.get("source", ""))
    if channel == "norm":
        if text and '"GET /health HTTP/1.1" 200' not in text and '"GET /events HTTP/1.1" 200' not in text and '"GET /status/busy HTTP/1.1" 200' not in text:
            upper = text.upper()
            color = "91" if (" ERROR " in upper or upper.startswith("ERROR")) else "93" if (" WARNING " in upper or upper.startswith("WARNING")) else "90"
            _write(text, color=color)
        return
    if kind == "model_start":
        thinking = "on" if event.get("thinking") else "off"
        _write(f"\\n---------------- Ollama · {{source or 'unknown'}} · thinking {{thinking}} ----------------", color="96")
    elif kind == "thinking":
        _write(text, color="36", end="")
    elif kind == "answer":
        _write(text, color="92", end="")
    elif kind == "tool_call":
        _write(f"\\nTool call: {{text}}", color="93")
    elif kind == "model_end":
        _write("\\n---------------- Ollama complete ----------------", color="90")
    elif kind == "model_error":
        label = "Ollama cancelled" if event.get("cancelled") else "Ollama error"
        _write(f"\\n{{label}}: {{text}}", color="91")
    elif text:
        _write(text)


def _iter_sse(response):
    data_lines: list[str] = []
    for raw in response:
        line = raw.decode("utf-8", errors="replace").rstrip("\\r\\n")
        if not line:
            if data_lines:
                yield "\\n".join(data_lines)
                data_lines.clear()
            continue
        if line.startswith(":"):
            continue
        if line.startswith("data:"):
            data_lines.append(line[5:].lstrip())
    if data_lines:
        yield "\\n".join(data_lines)


def main() -> int:
    ap = argparse.ArgumentParser(description="Portable read-only Norm SSE viewer")
    ap.add_argument("--url", default=CONFIG["events_url"], help="Override the embedded /events URL")
    ap.add_argument("--raw", action="store_true", help="Print raw event JSON")
    ap.add_argument("--no-color", action="store_true", help="Disable ANSI colors")
    ap.add_argument("--once", action="store_true", help="Exit instead of reconnecting after a disconnect")
    args = ap.parse_args()
    global ANSI
    if args.no_color:
        ANSI = False
    _enable_vt()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

    _write("Norm SSE Remote", color="96")
    _write(f"Source: {{CONFIG['source_name']}}  |  Norm {{CONFIG['norm_version']}}", color="90")
    _write(f"Events: {{args.url}}", color="90")
    _write("Read-only activity stream. Ctrl+C closes only this viewer.\\n", color="90")

    warned = False
    while True:
        try:
            req = request.Request(
                args.url,
                headers={{"Accept": "text/event-stream", "Cache-Control": "no-cache", "User-Agent": "Norm-SSE-Remote"}},
            )
            with request.urlopen(req, timeout=None) as response:
                warned = False
                for payload in _iter_sse(response):
                    if not payload:
                        continue
                    try:
                        event = json.loads(payload)
                    except json.JSONDecodeError:
                        _write(payload, color="93")
                        continue
                    if isinstance(event, dict):
                        _render(event, args.raw)
        except KeyboardInterrupt:
            return 0
        except Exception as exc:
            if args.once:
                _write(f"Disconnected: {{exc}}", color="91", stream=sys.stderr)
                return 1
            if not warned:
                _write(f"SSE disconnected; retrying in 2s: {{exc}}", color="93", stream=sys.stderr)
                warned = True
            time.sleep(2)


if __name__ == "__main__":
    raise SystemExit(main())
'''


def main() -> int:
    ap = argparse.ArgumentParser(description="Build a portable Norm SSE viewer from this installed Norm configuration")
    ap.add_argument("--output", default="Norm-SSE.exe", help="Output filename/path (default: Norm-SSE.exe beside make_sse.bat)")
    ap.add_argument("--clean", action="store_true", help="Discard the reusable PyInstaller work cache before building")
    args = ap.parse_args()

    settings = _read_settings()
    if not settings.has_section("network"):
        raise RuntimeError("settings.ini is missing [network]")
    machine = settings.get("network", "current_machine", fallback="").strip()
    domain = settings.get("network", "current_domain", fallback="").strip()
    activity_raw = settings.get("network", "activity_host", fallback="current")
    activity_host = _resolve_remote_host(activity_raw, machine, domain, role="activity")
    activity_port = settings.getint("network", "activity_port", fallback=8766)
    source_name = f"{machine}.{domain}".strip(".") or activity_host
    version = _project_version()
    events_url = f"http://{activity_host}:{activity_port}/events"
    health_url = f"http://{activity_host}:{activity_port}/health"

    output = Path(args.output).expanduser()
    if not output.is_absolute():
        output = ROOT / output
    output = output.resolve()
    if output.suffix.lower() != ".exe":
        output = output.with_suffix(".exe")
    output.parent.mkdir(parents=True, exist_ok=True)

    ok, detail = _health_probe(health_url)
    print(f"Norm source:  {source_name}")
    print(f"SSE endpoint: {events_url}")
    print(f"Health:       {'reachable' if ok else 'not currently reachable'} ({detail})")
    if not ok:
        print("WARNING: build will continue; the generated viewer will reconnect until Norm is reachable.")

    python = Path(sys.executable).resolve()
    cache_root = ROOT / "state" / "build-cache" / "sse-client"
    work_dir = cache_root / "work"
    spec_dir = cache_root / "spec"
    if args.clean and cache_root.exists():
        shutil.rmtree(cache_root)
    work_dir.mkdir(parents=True, exist_ok=True)
    spec_dir.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="norm-sse-build-") as temp_name:
        temp = Path(temp_name)
        source = temp / "norm_sse_remote.py"
        dist = temp / "dist"
        source.write_text(
            _client_source(events_url=events_url, health_url=health_url, source_name=source_name, version=version),
            encoding="utf-8",
            newline="\n",
        )
        name = _safe_name(output.stem)
        cmd = [
            str(python), "-m", "PyInstaller", "--noconfirm", "--onefile", "--console",
            "--name", name,
            "--distpath", str(dist),
            "--workpath", str(work_dir),
            "--specpath", str(spec_dir),
        ]
        if args.clean:
            cmd.append("--clean")
        cmd.append(str(source))
        print("Building portable SSE viewer...")
        cp = subprocess.run(cmd, cwd=str(ROOT))
        if cp.returncode:
            return cp.returncode
        built = dist / f"{name}.exe"
        if not built.is_file() or built.stat().st_size == 0:
            raise RuntimeError(f"PyInstaller did not produce {built}")
        if output.exists():
            output.unlink()
        shutil.copy2(built, output)

    digest = _sha256(output)
    print()
    print(f"Created: {output}")
    print(f"Size:    {output.stat().st_size:,} bytes")
    print(f"SHA256:  {digest}")
    print("Copy this single EXE to another Windows PC on the same tailnet and run it.")
    print("It contains no Norm passwords or Redis/PostgreSQL credentials.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(130)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
