from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import time
from pathlib import Path
from urllib import request

ROOT = Path(__file__).resolve().parents[1]
EMERGENCY_SNAPSHOT_ROOT = ROOT / "state" / "emergency-stop"
APP = ROOT / "core"
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))

from runtime_bootstrap import build_prompt_queue, build_runtime
from norm_runtime.settings import load_ports
from norm_runtime.shutdown_snapshot import write_sos


def _tailscale_ipv4() -> str:
    proc = subprocess.run(
        ["tailscale", "ip", "-4"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=5,
        check=False,
    )
    return next((line.strip() for line in proc.stdout.splitlines() if line.strip()), "")


def _request_stop_all_now() -> str:
    host = _tailscale_ipv4()
    if not host:
        return "control unavailable: no Tailscale IPv4"
    port = int(load_ports(ROOT)["activity"])
    req = request.Request(
        f"http://{host}:{port}/control/stop-all-now",
        data=b"{}",
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with request.urlopen(req, timeout=2) as response:
            payload = json.loads(response.read().decode("utf-8"))
        return f"control accepted: {payload.get('mode', 'now')}"
    except Exception as exc:
        return f"control unavailable: {type(exc).__name__}: {exc}"


def _buffer_signature(live) -> tuple:
    items = []
    pattern = f"{live.prefix}:*:model-buffer:*"
    for key in live.client.scan_iter(match=pattern):
        text = key.decode() if isinstance(key, bytes) else str(key)
        length = int(live.client.xlen(key))
        latest = live.client.xrevrange(key, count=1)
        latest_id = latest[0][0] if latest else ""
        items.append((text, length, str(latest_id)))
    states = []
    for task_id in sorted(live.task_ids()):
        state = live.state(task_id)
        states.append((task_id, state.get("current_step", ""), state.get("updated_at", "")))
    return tuple(sorted(items)), tuple(states)


def _active_ollama_calls() -> int | None:
    host = _tailscale_ipv4()
    if not host:
        return None
    port = int(load_ports(ROOT)["activity"])
    try:
        with request.urlopen(f"http://{host}:{port}/status/busy", timeout=0.5) as response:
            payload = json.loads(response.read().decode("utf-8"))
        return int((payload.get("signals") or {}).get("ollama_active_calls") or 0)
    except Exception:
        return None


def _wait_for_redis_flush(timeout: float = 5.0) -> str:
    _coordinator, live, _durable = build_runtime(ROOT, ensure_schema=False)
    deadline = time.monotonic() + timeout
    previous = None
    stable = 0
    while time.monotonic() < deadline:
        current = _buffer_signature(live)
        stable = stable + 1 if current == previous else 0
        previous = current
        active = _active_ollama_calls()
        if stable >= 3 and (active == 0 or active is None):
            return f"Redis/model buffer settled; active Ollama calls={active}"
        time.sleep(0.15)
    return "Redis/model buffer settle wait reached emergency timeout; snapshotting latest committed Redis state"


def _snapshot() -> Path:
    # The running norm.exe also writes temp\recovery\SOS.md while handling /stop-all-now.
    # Writing the helper snapshot to the same pathname creates a cross-process write race.
    # Keep the emergency helper copy separate; both snapshots can coexist and be compared.
    EMERGENCY_SNAPSHOT_ROOT.mkdir(parents=True, exist_ok=True)
    _coordinator, live, durable = build_runtime(ROOT, ensure_schema=False)
    queue = build_prompt_queue(ROOT)
    return write_sos(ROOT, live, durable, queue, "stop-all-now-emergency-helper", output_root=EMERGENCY_SNAPSHOT_ROOT)


def _verify_snapshot(target: Path) -> tuple[int, str]:
    data = target.read_bytes()
    if not data:
        raise RuntimeError("SOS.md is empty after write")
    text = data.decode("utf-8")
    if not text.startswith("# Norm SOS / Crash Recovery Buffer"):
        raise RuntimeError("SOS.md header verification failed")
    digest = hashlib.sha256(data).hexdigest()
    return len(data), digest


def _force_kill_image(image: str) -> tuple[int, str]:
    proc = subprocess.run(
        ["taskkill", "/F", "/T", "/IM", image],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=10,
        check=False,
    )
    detail = (proc.stdout or proc.stderr or "").strip().replace("\r", " ").replace("\n", " ")
    return proc.returncode, detail[:500]


def main() -> int:
    print("Emergency stop-all-now: cancelling active generation...")
    print(_request_stop_all_now())
    try:
        print(_wait_for_redis_flush())
        sos_path = _snapshot()
        sos_size, sos_sha = _verify_snapshot(sos_path)
        print(f"SOS snapshot durably written and verified: {sos_path} bytes={sos_size} sha256={sos_sha}")
    except Exception as exc:
        print(f"SOS snapshot FAILED; refusing force-kill: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2

    for image in ("llama-server.exe", "ollama.exe", "ollama app.exe"):
        rc, detail = _force_kill_image(image)
        print(f"force-stop {image}: rc={rc} {detail}")
    rc, detail = _force_kill_image("norm.exe")
    print(f"force-stop norm.exe: rc={rc} {detail}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

