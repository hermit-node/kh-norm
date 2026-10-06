from __future__ import annotations

from norm_runtime.secret_redaction import RedactingFormatter

import argparse
import ctypes
import ipaddress
import json
import logging
import os
import signal
from pathlib import Path
import subprocess
import sys
import time

import psycopg
import redis
from threading import Event, Lock
from urllib import request
from urllib.parse import urlparse

from runtime_bootstrap import build_conversation_service, build_deletion_queue, build_prompt_queue, build_runtime, healthcheck, load_config
from norm_runtime.activity_stream import ActivityHub, ActivityLogHandler, start_activity_server
from norm_runtime.busy_status import collect_busy_status
from norm_runtime.context_snapshot import collect_context_snapshot
from norm_runtime.context_injections import ContextInjections
from norm_runtime.http_api import start_chat_api
from norm_runtime.ollama_client import ModelOutputTruncated, OllamaClient
from norm_runtime.ollama_lifecycle import shutdown_ollama
from norm_runtime.model_switch import ordered_models, resolve_model_selector, same_model
from norm_runtime.n1_gatekeeper import N1Gatekeeper
from norm_runtime.prompt_worker import PromptWorker
from norm_runtime.shutdown_snapshot import write_sos
from norm_runtime.rich_console import run_console
from norm_runtime.settings import load_ports, load_project_metadata, load_path_settings

MODEL_NAME = "norm"
MODEL_STORE = str(Path(os.environ.get("OLLAMA_MODELS") or (Path.home() / ".ollama" / "models")).expanduser())
_WINDOWS_CTRL_HANDLER = None


class RuntimeTransportFailure(RuntimeError):
    """A startup-owned HTTP transport exited while the runtime was otherwise live."""



def _install_service_signal_guard() -> None:
    """Keep console Ctrl+C/Ctrl+Break events from terminating service mode.

    Service launches are detached from the operator consoles, so this is a second
    line of defense.  The native Windows handler prevents console-control events
    from being translated into KeyboardInterrupt before Python's signal layer can
    ignore them.  Operator shutdown remains API-controlled.
    """
    global _WINDOWS_CTRL_HANDLER

    def _ignore(signum, _frame) -> None:
        logging.warning(
            "Ignored console interrupt signal=%s in service mode; use /shutdown or /stop-all",
            signum,
        )

    signal.signal(signal.SIGINT, _ignore)
    sigbreak = getattr(signal, "SIGBREAK", None)
    if sigbreak is not None:
        signal.signal(sigbreak, _ignore)

    if os.name == "nt":
        handler_type = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_uint)

        @handler_type
        def _windows_handler(ctrl_type: int) -> bool:
            if ctrl_type in (0, 1):  # CTRL_C_EVENT / CTRL_BREAK_EVENT
                return True
            return False

        _WINDOWS_CTRL_HANDLER = _windows_handler
        if not ctypes.windll.kernel32.SetConsoleCtrlHandler(_WINDOWS_CTRL_HANDLER, True):
            raise ctypes.WinError()
        logging.info("Native Windows service console-control guard active")

    logging.info("Service console-control guard active")


def norm_root() -> Path:
    if getattr(sys, "frozen", False):
        app_root = Path(sys.executable).resolve().parent
    else:
        app_root = Path(__file__).resolve().parent
    runtime_root = app_root.parent.resolve()
    os.environ["NORM_APP_ROOT"] = str(app_root)
    os.environ["NORM_RUNTIME_ROOT"] = str(runtime_root)
    return runtime_root


def setup_logging(root: Path) -> None:
    log_dir = root / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.FileHandler(log_dir / "norm-runtime.log", encoding="utf-8"), logging.StreamHandler()],
    )
    for handler in logging.getLogger().handlers:
        handler.setFormatter(RedactingFormatter("%(asctime)s %(levelname)s %(message)s"))


def api_ready(base_url: str, timeout: float = 1.0) -> bool:
    try:
        with request.urlopen(f"{base_url}/api/tags", timeout=timeout) as response:
            return response.status == 200
    except Exception:
        return False


def model_ready(base_url: str, model: str = MODEL_NAME, timeout: float = 1.0) -> bool:
    try:
        with request.urlopen(f"{base_url}/api/tags", timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
        names = {str(item.get("name", "")).split(":", 1)[0] for item in payload.get("models", [])}
        return model.split(":", 1)[0] in names
    except Exception:
        return False


def ollama_exe() -> Path:
    local = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    return local / "Programs" / "Ollama" / "ollama.exe"


def start_ollama_if_needed(root: Path, base_url: str, model_name: str, model_store: str) -> subprocess.Popen | None:
    if api_ready(base_url) and model_ready(base_url, model_name):
        logging.info("Ollama API already available with model=%s", model_name)
        return None
    if api_ready(base_url) and not model_ready(base_url, model_name):
        logging.warning("Ollama is running without model=%s; restarting it with the configured model store", model_name)
        if os.name != "nt":
            raise RuntimeError(f"Ollama is running but model {model_name!r} is unavailable")
        for image in ("ollama.exe", "ollama app.exe"):
            subprocess.run(
                ["taskkill", "/IM", image, "/F"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
        for _ in range(20):
            if not api_ready(base_url, timeout=0.25):
                break
            time.sleep(0.25)
    exe = ollama_exe()
    if not exe.exists():
        raise FileNotFoundError(f"Ollama executable not found: {exe}")
    env = os.environ.copy()
    env.update({
        "OLLAMA_MODELS": model_store,
        "OLLAMA_HOST": urlparse(base_url).netloc,
        "OLLAMA_MAX_LOADED_MODELS": "1",
        "OLLAMA_NUM_PARALLEL": "1",
        "OLLAMA_KEEP_ALIVE": "-1",
        "OLLAMA_FLASH_ATTENTION": "1",
        "OLLAMA_KV_CACHE_TYPE": "q4_0",
    })
    stdout = open(root / "logs" / "server.stdout.log", "a", encoding="utf-8")
    stderr = open(root / "logs" / "server.stderr.log", "a", encoding="utf-8")
    flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    process = subprocess.Popen([str(exe), "serve"], env=env, stdout=stdout, stderr=stderr, creationflags=flags)
    for _ in range(120):
        if api_ready(base_url):
            logging.info("Ollama server started pid=%s", process.pid)
            return process
        time.sleep(0.5)
    raise RuntimeError("Ollama API did not become ready")


def preload_norm(base_url: str, model_name: str) -> None:
    payload = json.dumps({"model": model_name, "keep_alive": -1}).encode("utf-8")
    req = request.Request(
        f"{base_url}/api/generate",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with request.urlopen(req, timeout=120) as response:
        if response.status != 200:
            raise RuntimeError(f"Norm preload failed: HTTP {response.status}")
        response.read()
    logging.info("Norm model preloaded and pinned")


def resolve_bind_host(value: str) -> str:
    raw = str(value or "127.0.0.1").strip()
    if raw.lower() in {"127.0.0.1", "::1"}:
        return raw
    if raw.lower() != "tailscale":
        raise RuntimeError("Norm service bind host must be 'tailscale' or loopback")
    status = subprocess.run(["tailscale", "status", "--json"], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=10)
    if status.returncode != 0:
        raise RuntimeError("Tailscale is unavailable; refusing to expose Norm")
    payload = json.loads(status.stdout or "{}")
    if str(payload.get("BackendState")) != "Running":
        raise RuntimeError("Tailscale is not Running; refusing to expose Norm")
    result = subprocess.run(["tailscale", "ip", "-4"], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=10)
    if result.returncode != 0 or not result.stdout.strip():
        raise RuntimeError("No Tailscale IPv4 address is available")
    host = result.stdout.strip().splitlines()[0].strip()
    if ipaddress.ip_address(host) not in ipaddress.ip_network("100.64.0.0/10"):
        raise RuntimeError(f"Refusing non-Tailscale bind address: {host}")
    return host


def clear_runtime_redis(root: Path) -> dict[str, int]:
    config = load_config(root)
    cleared = {}
    for name in ("redis", "prompt_queue"):
        cfg = config.get(name, {})
        client = redis.Redis(host=cfg.get("host", "127.0.0.1"), port=int(cfg.get("port", 6379)), db=int(cfg.get("db", 0)), decode_responses=True)
        before = int(client.dbsize())
        preserved = {}
        if name == "redis":
            maintenance = config.get("maintenance", {})
            for key_name in ("weekly_cleanup_active_key",):
                key = str(maintenance.get(key_name, "")).strip()
                if key:
                    value = client.get(key)
                    if value is not None:
                        preserved[key] = value
        client.flushdb()
        if preserved:
            client.mset(preserved)
        cleared[name] = max(0, before - len(preserved))
    deletion_queue = build_deletion_queue(root)
    purge = deletion_queue.purge_all()
    if purge.get("held", 0):
        raise RuntimeError(f"Deletion queue purge held {purge['held']} item(s); deletion Redis was preserved")
    deletion_queue.flush_db()
    cleared["deletion_queue"] = int(purge.get("purged", 0))
    return cleared




def _stream_id_tuple(value: str) -> tuple[int, int]:
    try:
        left, right = str(value).split("-", 1)
        return int(left), int(right)
    except (TypeError, ValueError):
        return (-1, -1)


def _suppress_console_prompt_state(config: dict, section: str, *, default_prefix: str, client_factory=redis.Redis) -> dict:
    """Suppress one user-visible ingress prompt before it has a durable task row.

    /suppress-task normally acts on PostgreSQL task identity.  A GUI prompt can spend
    time in canonical DB3 ingress / HTTP planning before /api/chat has returned a
    durable task_id, so the worker legitimately has nothing to suppress yet.  In that
    gap, tombstone the dispatching prompt (preferred) or oldest queued prompt by its
    stable prompt_id.  The ingress dispatcher will park the row instead of retrying it.
    """
    cfg = dict(config.get(section, {}) or {})
    client = client_factory(
        host=str(cfg.get("host", "127.0.0.1")),
        port=int(cfg.get("port", 6379)),
        db=int(cfg.get("db", 3)),
        decode_responses=True,
        socket_connect_timeout=5,
        socket_timeout=10,
        socket_keepalive=True,
        health_check_interval=30,
    )
    stream = str(cfg.get("console_ingress_stream", f"{default_prefix}:ingress"))
    group = str(cfg.get("console_ingress_group", f"{default_prefix.split(':')[-1]}-dispatchers"))
    dispatching_key = str(cfg.get("console_dispatching_key", f"{default_prefix}:dispatching"))
    uncertain_key = str(cfg.get("console_uncertain_key", f"{default_prefix}:uncertain"))
    suppressed_key = str(cfg.get("console_suppressed_prompt_ids_key", f"{default_prefix}:suppressed-prompt-ids"))

    blocked = {str(value) for value in client.smembers(suppressed_key)}
    dispatching = client.hgetall(dispatching_key)
    selected: dict | None = None

    # Prefer the oldest active HTTP dispatch: this is the prompt the operator sees as
    # currently running even when task creation/planning has not reached PostgreSQL.
    active_candidates: list[tuple[tuple[int, int], str, str]] = []
    for entry_id, raw in dispatching.items():
        try:
            record = json.loads(raw)
        except Exception:
            continue
        if not isinstance(record, dict):
            continue
        prompt_id = str(record.get("prompt_id") or "").strip()
        if prompt_id and prompt_id not in blocked:
            active_candidates.append((_stream_id_tuple(str(entry_id)), str(entry_id), prompt_id))
    if active_candidates:
        _, entry_id, prompt_id = min(active_candidates, key=lambda item: item[0])
        selected = {"entry_id": entry_id, "prompt_id": prompt_id, "state": "dispatching"}

    if selected is None:
        pending_ids: set[str] | None = None
        last_delivered: tuple[int, int] | None = None
        try:
            pending = client.xpending_range(stream, group, "-", "+", 10000)
            pending_ids = {str(item.get("message_id") or "") for item in pending}
            for info in client.xinfo_groups(stream):
                if str(info.get("name")) == group:
                    last_delivered = _stream_id_tuple(str(info.get("last-delivered-id") or "0-0"))
                    break
        except Exception:
            pending_ids = None
            last_delivered = None

        rows = client.xrange(stream, min="-", max="+")
        for raw_id, raw_fields in rows:
            entry_id = str(raw_id)
            fields = dict(raw_fields)
            prompt_id = str(fields.get("prompt_id") or "").strip()
            if not prompt_id or prompt_id in blocked or entry_id in dispatching:
                continue
            if pending_ids is not None and last_delivered is not None:
                already_delivered = _stream_id_tuple(entry_id) <= last_delivered
                if already_delivered and entry_id not in pending_ids:
                    continue
                state = "in-flight" if entry_id in pending_ids else "queued"
            else:
                # Visibility must fail safe: without consumer-group metadata, do not
                # guess that an arbitrary historical stream row is live.
                continue
            selected = {"entry_id": entry_id, "prompt_id": prompt_id, "state": state}
            break

    if selected is None:
        return {"status": "ok", "suppressed": False, "reason": "no active or queued ingress prompt"}

    prompt_id = str(selected["prompt_id"])
    client.sadd(suppressed_key, prompt_id)

    # If this prompt is already parked/uncertain, make that record explicitly inert.
    for uncertain_id, raw in client.hgetall(uncertain_key).items():
        try:
            record = json.loads(raw)
        except Exception:
            continue
        if not isinstance(record, dict) or str(record.get("prompt_id") or "").strip() != prompt_id:
            continue
        record["auto_retry"] = False
        record["suppressed"] = True
        client.hset(uncertain_key, uncertain_id, json.dumps(record, ensure_ascii=False))

    return {
        "status": "ok",
        "suppressed": True,
        "delivery_only": True,
        "prompt_id": prompt_id,
        "entry_id": str(selected["entry_id"]),
        "delivery_state": str(selected["state"]),
        "title": f"ingress prompt {prompt_id[:8]} ({selected['state']})",
    }

def _flush_console_suppressed_state(config: dict, section: str, *, default_prefix: str, client_factory=redis.Redis) -> dict:
    """Remove user-facing suppressed delivery records without making them runnable again.

    Task suppression lives in PostgreSQL, but GUI/Rich ingress has an independent
    Redis delivery layer.  A ConnectionResetError can therefore leave a parked
    uncertain record after its task tree was suppressed.  Flush both layers so
    /flush-suppressed means what the operator sees in /queue-full as well as what
    PostgreSQL reports.  Prompt-ID tombstones are retained while an HTTP dispatch
    with that ID is still active, preventing a late socket failure from resurrecting
    the submission after the visible record was flushed.
    """
    cfg = dict(config.get(section, {}) or {})
    client = client_factory(
        host=str(cfg.get("host", "127.0.0.1")),
        port=int(cfg.get("port", 6379)),
        db=int(cfg.get("db", 3)),
        decode_responses=True,
        socket_connect_timeout=5,
        socket_timeout=10,
        socket_keepalive=True,
        health_check_interval=30,
    )
    stream = str(cfg.get("console_ingress_stream", f"{default_prefix}:ingress"))
    group = str(cfg.get("console_ingress_group", f"{default_prefix.split(':')[-1]}-dispatchers"))
    uncertain_key = str(cfg.get("console_uncertain_key", f"{default_prefix}:uncertain"))
    dispatching_key = str(cfg.get("console_dispatching_key", f"{default_prefix}:dispatching"))
    suppressed_key = str(cfg.get("console_suppressed_prompt_ids_key", f"{default_prefix}:suppressed-prompt-ids"))

    blocked = {str(value) for value in client.smembers(suppressed_key)}
    suppressed_records: dict[str, str] = {}
    for entry_id, raw in client.hgetall(uncertain_key).items():
        try:
            record = json.loads(raw)
        except Exception:
            continue
        if not isinstance(record, dict):
            continue
        prompt_id = str(record.get("prompt_id") or "").strip()
        if bool(record.get("suppressed")) or (prompt_id and prompt_id in blocked):
            suppressed_records[str(entry_id)] = prompt_id
            if prompt_id:
                blocked.add(prompt_id)

    if blocked:
        client.sadd(suppressed_key, *sorted(blocked))
    if suppressed_records:
        client.hdel(uncertain_key, *sorted(suppressed_records))

    dispatching = client.hgetall(dispatching_key)
    active_entry_ids = {str(entry_id) for entry_id in dispatching}
    active_prompt_ids: set[str] = set()
    for raw in dispatching.values():
        try:
            record = json.loads(raw)
        except Exception:
            continue
        if isinstance(record, dict):
            prompt_id = str(record.get("prompt_id") or "").strip()
            if prompt_id:
                active_prompt_ids.add(prompt_id)

    stream_deleted = 0
    try:
        rows = client.xrange(stream, min="-", max="+")
    except Exception:
        rows = []
    for entry_id, fields in rows:
        entry_id = str(entry_id)
        prompt_id = str(dict(fields).get("prompt_id") or "").strip()
        if not prompt_id or prompt_id not in blocked or entry_id in active_entry_ids:
            continue
        try:
            client.xack(stream, group, entry_id)
        except Exception:
            # Never delete the backing stream row after an ACK failure.  Doing so
            # creates an invisible PEL ghost: XINFO still counts it while XRANGE
            # and /queue-full cannot show it.
            logging.exception("Could not ACK suppressed ingress entry %s; preserving stream row", entry_id)
            continue
        try:
            stream_deleted += int(client.xdel(stream, entry_id) or 0)
        except Exception:
            pass

    # Repair orphaned pending IDs left by older versions or interrupted cleanup.
    pending_ghosts_cleared = 0
    try:
        pending = client.xpending_range(stream, group, "-", "+", 10000)
    except Exception:
        pending = []
    for item in pending:
        entry_id = str(item.get("message_id") or "")
        if not entry_id:
            continue
        try:
            backing = client.xrange(stream, min=entry_id, max=entry_id, count=1)
        except Exception:
            continue
        if backing:
            continue
        try:
            pending_ghosts_cleared += int(client.xack(stream, group, entry_id) or 0)
            client.hdel(dispatching_key, entry_id)
        except Exception:
            logging.exception("Could not clear orphaned ingress PEL entry %s during flush", entry_id)

    # Keep only tombstones that still guard an in-flight HTTP dispatch.  Everything
    # else has been durably discarded and should disappear from future queue views.
    retained = blocked & active_prompt_ids
    clear_ids = blocked - retained
    if clear_ids:
        client.srem(suppressed_key, *sorted(clear_ids))

    return {
        "uncertain_deleted": len(suppressed_records),
        "stream_deleted": stream_deleted,
        "prompt_tombstones_cleared": len(clear_ids),
        "prompt_tombstones_retained": len(retained),
        "pending_ghosts_cleared": pending_ghosts_cleared,
    }


def run_host(root: Path, ollama_process: subprocess.Popen | None, ollama_url: str, model_name: str) -> None:
    config = load_config(root)
    ports = load_ports(root)
    shutdown_requested = Event()
    shutdown_now = Event()
    stop_all_requested = Event()
    stop_all_now = Event()
    resources: dict[str, object] = {
        "startup_phase": "initializing",
        "startup_ready": False,
        "active_model": str(model_name or MODEL_NAME),
    }
    model_switch_lock = Lock()

    def request_activity_health() -> dict:
        if bool(resources.get("startup_ready")):
            return {"status": "ok", "phase": "ready"}
        return {"status": "initializing", "phase": str(resources.get("startup_phase") or "initializing")}

    def request_ollama_shutdown() -> dict:
        OllamaClient.cancel_active()
        active_model = str(resources.get("active_model") or model_name or MODEL_NAME)
        result = shutdown_ollama(ollama_process, ollama_url, active_model)
        logging.info("Ollama shutdown result: %s", result)
        return result

    def request_shutdown(immediate: bool) -> dict:
        chat_server = resources.get("chat_server")
        if chat_server is not None:
            chat_server.accepting_requests = False
        worker_obj = resources.get("worker")
        if immediate:
            if worker_obj is not None:
                worker_obj.request_drain()
            shutdown_now.set()
            OllamaClient.cancel_active()
        shutdown_requested.set()
        logging.info("Norm shutdown requested mode=%s", "now" if immediate else "graceful")
        return {"status": "ok", "shutdown": "now" if immediate else "graceful"}

    def request_busy_status() -> dict:
        payload = collect_busy_status(
            chat_server=resources.get("chat_server"),
            worker=resources.get("worker"),
            prompt_queue=resources.get("prompt_queue"),
            durable=resources.get("durable"),
            ollama_active_calls=OllamaClient.active_count(),
        )
        worker_obj = resources.get("worker")
        maintenance = (
            worker_obj.maintenance_status()
            if worker_obj is not None and hasattr(worker_obj, "maintenance_status")
            else {"active": False, "parked": False, "status": "unavailable"}
        )
        payload["maintenance"] = maintenance
        if maintenance.get("active"):
            payload["status"] = "busy"
            payload["busy"] = True
            payload["phase"] = "maintenance:" + str(maintenance.get("phase") or maintenance.get("mode") or "scheduled")
            payload["confidence"] = 1.0
        if not bool(resources.get("startup_ready")):
            payload["status"] = "initializing"
            payload["busy"] = True
            payload["phase"] = str(resources.get("startup_phase") or "initializing")
            payload["confidence"] = 1.0
        return payload

    def _switch_clients() -> list[OllamaClient]:
        candidates = [
            resources.get("context_client"),
            resources.get("n1_client"),
            resources.get("n2_client"),
        ]
        worker_obj = resources.get("worker")
        if worker_obj is not None:
            candidates.append(getattr(worker_obj, "client", None))
        gatekeeper = resources.get("n1_gatekeeper")
        if gatekeeper is not None:
            candidates.append(getattr(gatekeeper, "client", None))
        service_obj = resources.get("conversation_service")
        if service_obj is not None:
            candidates.append(getattr(service_obj, "ollama", None))
            file_tools = getattr(service_obj, "file_tools", None)
            if file_tools is not None:
                candidates.append(getattr(file_tools, "vision_client", None))
        unique = []
        seen = set()
        for client in candidates:
            if not isinstance(client, OllamaClient):
                continue
            identity = id(client)
            if identity in seen:
                continue
            seen.add(identity)
            unique.append(client)
        return unique

    def _tracked_work_busy() -> bool:
        if OllamaClient.active_count() > 0:
            return True
        chat_server = resources.get("chat_server")
        if int(getattr(chat_server, "active_request_count", 0) or 0) > 0:
            return True
        worker_obj = resources.get("worker")
        if worker_obj is not None and not bool(worker_obj.is_idle()):
            return True
        prompt_queue_obj = resources.get("prompt_queue")
        if prompt_queue_obj is not None:
            stats = prompt_queue_obj.stats()
            if int(stats.get("stream_length", 0) or 0) > 0 or int(stats.get("pending_count", 0) or 0) > 0:
                return True
            if int(prompt_queue_obj.r.xlen(prompt_queue_obj.retry_stream) or 0) > 0:
                return True
            if int(prompt_queue_obj.r.xlen(prompt_queue_obj.escalation_stream) or 0) > 0:
                return True
        return False

    def request_model_switch(selector: str | None = None) -> dict:
        clients = _switch_clients()
        reference = next((client for client in clients if client.activity_source == "n2-worker"), None)
        if reference is None:
            reference = next((client for client in clients if client.activity_source == "n2-chat"), None)
        if reference is None and clients:
            reference = clients[0]
        if reference is None:
            return {
                "status": "unavailable",
                "switched": False,
                "reason": "model clients are still initializing",
                "boot_model": MODEL_NAME,
            }

        try:
            models = ordered_models(reference.list_models(), str(resources.get("active_model") or MODEL_NAME))
        except Exception as exc:
            logging.warning("Could not list Ollama models for switch-model: %s", exc)
            return {
                "status": "error",
                "switched": False,
                "reason": f"could not list Ollama models: {type(exc).__name__}: {exc}",
                "boot_model": MODEL_NAME,
            }

        current = str(resources.get("active_model") or MODEL_NAME)
        listing = [
            {"index": index, "name": name, "current": same_model(name, current)}
            for index, name in enumerate(models, start=1)
        ]
        if selector is None:
            return {
                "status": "listed",
                "current": current,
                "boot_model": MODEL_NAME,
                "models": listing,
            }
        if not bool(resources.get("startup_ready")):
            return {
                "status": "unavailable",
                "switched": False,
                "current": current,
                "boot_model": MODEL_NAME,
                "reason": "Norm is still initializing; model switches are enabled after startup completes",
                "models": listing,
            }

        with model_switch_lock:
            if _tracked_work_busy():
                return {
                    "status": "busy",
                    "switched": False,
                    "current": current,
                    "boot_model": MODEL_NAME,
                    "reason": "Norm has active or queued work; retry the switch when idle",
                    "models": listing,
                }
            try:
                candidate = resolve_model_selector(models, selector)
            except ValueError as exc:
                return {
                    "status": "error",
                    "switched": False,
                    "current": current,
                    "boot_model": MODEL_NAME,
                    "reason": str(exc),
                    "models": listing,
                }

            if same_model(candidate, current):
                return {
                    "status": "ok",
                    "switched": False,
                    "current": current,
                    "boot_model": MODEL_NAME,
                    "reason": "requested model is already active",
                    "models": listing,
                }

            resolved_by_url: dict[str, str] = {}
            endpoint_clients: dict[str, OllamaClient] = {}
            for client in _switch_clients():
                endpoint_clients.setdefault(client.base_url, client)
            try:
                for base_url, endpoint_client in endpoint_clients.items():
                    endpoint_models = ordered_models(endpoint_client.list_models(), current)
                    endpoint_candidate = resolve_model_selector(endpoint_models, candidate)
                    probe = endpoint_client.probe_model(endpoint_candidate)
                    if not probe.strip():
                        raise RuntimeError(f"{base_url} returned no usable output")
                    resolved_by_url[base_url] = endpoint_candidate
            except Exception as exc:
                logging.warning(
                    "Model switch probe failed candidate=%s error=%s", candidate, exc
                )
                return {
                    "status": "error",
                    "switched": False,
                    "current": current,
                    "candidate": candidate,
                    "boot_model": MODEL_NAME,
                    "reason": f"candidate probe failed on at least one Ollama endpoint: {type(exc).__name__}: {exc}",
                    "models": listing,
                }

            if _tracked_work_busy():
                return {
                    "status": "busy",
                    "switched": False,
                    "current": current,
                    "candidate": candidate,
                    "boot_model": MODEL_NAME,
                    "reason": "work started while the candidate was being probed; active model was not changed",
                    "models": listing,
                }

            clients = _switch_clients()
            if not clients:
                return {
                    "status": "unavailable",
                    "switched": False,
                    "current": current,
                    "candidate": candidate,
                    "boot_model": MODEL_NAME,
                    "reason": "live model clients disappeared before commit",
                    "models": listing,
                }
            previous = [(client, str(client.model)) for client in clients]
            try:
                for client in clients:
                    client.model = resolved_by_url.get(client.base_url, candidate)
                resources["active_model"] = candidate
            except Exception as exc:
                for client, old_model in previous:
                    client.model = old_model
                resources["active_model"] = current
                logging.exception("Model switch commit failed candidate=%s", candidate)
                return {
                    "status": "error",
                    "switched": False,
                    "current": current,
                    "candidate": candidate,
                    "boot_model": MODEL_NAME,
                    "reason": f"commit failed and was rolled back: {type(exc).__name__}: {exc}",
                    "models": listing,
                }

            logging.info(
                "Norm session model switched previous=%s current=%s clients=%s",
                current, candidate, len(clients),
            )
            return {
                "status": "ok",
                "switched": True,
                "previous": current,
                "current": candidate,
                "boot_model": MODEL_NAME,
                "client_count": len(clients),
                "models": [
                    {"index": index, "name": name, "current": same_model(name, candidate)}
                    for index, name in enumerate(models, start=1)
                ],
            }

    def generate_context_summary(raw_markdown: str, raw_path: str, raw_hash: str, generated_at: str) -> str:
        client = resources.get("context_client")
        if client is None:
            return ""
        prompt = (
            "Create a concise, current Markdown handoff describing this Norm runtime from the evidence below. "
            "SOURCE PRECEDENCE: use README.md, CURRENT_STATUS.md, DEVELOPMENT_NOTES.md, and FUTURE_IMPLEMENTATION_NOTES.md as the primary maintained sources for stable architecture, capabilities, design intent, history, and planned work. "
            "Use live probes/filesystem/Redis/PostgreSQL/log evidence as authoritative for CURRENT operational facts such as process/busy state, queues, running tasks, deployed executable/hash, recent failures, and source-vs-EXE drift. "
            "If maintained documentation conflicts with newer live evidence, use the live value for the current fact and explicitly flag the relevant documentation as possibly stale. "
            "Prioritize the newest timestamped evidence and unresolved/current state. Compress old completed history unless it still explains a live capability, constraint, or problem. "
            "Do not invent facts or infer completion from intent. Clearly distinguish deployed behavior, possible unpromoted source edits, running/unfinished work, and historical notes. "
            "Include: what Norm can do; its operational architecture; current live state; important changes from roughly the last 72 hours; unfinished/unpromoted work or warnings; and useful troubleshooting/resume pointers. "
            "Keep it practical and compact, ideally 3500-7000 characters. Use timezone-aware timestamps as supplied. Return Markdown only.\n\n"
            f"Snapshot generated: {generated_at}\nRaw snapshot path: {raw_path}\nRaw snapshot SHA-256: {raw_hash}\n\n"
            "AUTHORITATIVE EVIDENCE:\n" + raw_markdown
        )
        try:
            return client.generate(prompt, think=False, num_predict=2400, temperature=0.1)
        except ModelOutputTruncated as exc:
            partial = str(exc.partial or "").strip()
            if partial:
                return partial + "\n\n> Summary generation hit its output limit; consult the raw evidence snapshot for omitted details."
            raise

    def request_context_status() -> dict:
        prompt_queue = resources.get("prompt_queue")
        durable = resources.get("durable")
        if prompt_queue is None or durable is None:
            return {"status": "initializing", "handoff_markdown": "Norm context snapshot is still initializing."}
        return collect_context_snapshot(
            root, busy_status=request_busy_status, prompt_queue=prompt_queue, durable=durable, config=config,
            summary_generator=generate_context_summary,
        )

    def request_stop_all(immediate: bool) -> dict:
        chat_server = resources.get("chat_server")
        if chat_server is not None:
            chat_server.accepting_requests = False
        worker_obj = resources.get("worker")
        if worker_obj is not None:
            if immediate:
                worker_obj.request_stop_all_now()
            else:
                worker_obj.request_stop_after_current_step()
        stop_all_requested.set()
        if immediate:
            stop_all_now.set()
            OllamaClient.cancel_active()
        shutdown_requested.set()
        mode = "now" if immediate else "after-step"
        logging.info("Stop-all requested mode=%s", mode)
        return {"status": "ok", "mode": mode}

    def request_suppress_task(task_id: str | None, reason: str) -> dict:
        worker_obj = resources.get("worker")
        if worker_obj is None:
            return {"status": "unavailable", "suppressed": False, "reason": "worker is still initializing"}
        result = worker_obj.request_suppress_task(task_id=task_id, reason=reason)
        if bool(result.get("suppressed")) or str(task_id or "").strip():
            return result
        if not str(result.get("reason") or "").startswith("no active or queued task"):
            return result
        try:
            ingress = _suppress_console_prompt_state(config, "console_queue", default_prefix="norm:gui")
        except Exception as exc:
            logging.warning("Ingress-level suppression fallback failed: %s", exc)
            return result
        if bool(ingress.get("suppressed")):
            if str(ingress.get("delivery_state") or "") == "dispatching":
                OllamaClient.cancel_active()
            logging.info(
                "Suppressed pre-task ingress prompt prompt_id=%s entry_id=%s state=%s",
                ingress.get("prompt_id"), ingress.get("entry_id"), ingress.get("delivery_state"),
            )
            return ingress
        return result

    def request_resume_maintenance() -> dict:
        worker_obj = resources.get("worker")
        if worker_obj is None:
            return {"status": "unavailable", "resumed": False, "reason": "worker is still initializing"}
        return worker_obj.request_resume_maintenance()

    def request_flush_suppressed() -> dict:
        worker_obj = resources.get("worker")
        if worker_obj is None:
            durable = resources.get("durable")
            if durable is None:
                task_result = {"status": "unavailable", "deleted": 0}
            else:
                task_result = {"status": "ok", "deleted": durable.flush_suppressed()}
        else:
            task_result = worker_obj.flush_suppressed()

        delivery = {}
        errors = []
        for section, prefix in (("console_queue", "norm:gui"),):
            try:
                delivery[section] = _flush_console_suppressed_state(config, section, default_prefix=prefix)
            except Exception as exc:
                errors.append(f"{section}: {type(exc).__name__}: {exc}")
                logging.warning("Could not flush suppressed %s delivery state: %s", section, exc)

        result = dict(task_result)
        result["delivery"] = delivery
        result["delivery_deleted"] = sum(
            int(item.get("uncertain_deleted") or 0) + int(item.get("stream_deleted") or 0)
            for item in delivery.values()
        )
        if errors:
            result["delivery_errors"] = errors
        return result

    def request_trash_list() -> dict:
        try:
            queue_obj = build_deletion_queue(root)
            items = queue_obj.list_items()
            return {"status": "ok", "count": len(items), "items": items}
        except Exception as exc:
            logging.exception("Could not list deletion trash")
            return {"status": "error", "count": 0, "error": f"{type(exc).__name__}: {exc}"}

    def request_trash_restore(deletion_id: str) -> dict:
        try:
            return {"status": "ok", **build_deletion_queue(root).restore(deletion_id)}
        except Exception as exc:
            logging.exception("Could not restore deletion id=%s", deletion_id)
            return {"status": "error", "error": f"{type(exc).__name__}: {exc}"}

    def request_trash_purge() -> dict:
        try:
            return {"status": "ok", **build_deletion_queue(root).purge_all()}
        except Exception as exc:
            logging.exception("Could not purge deletion trash")
            return {"status": "error", "error": f"{type(exc).__name__}: {exc}"}

    def request_memory_condense(full: bool = False, deep: bool = False) -> dict:
        worker_obj = resources.get("worker")
        if worker_obj is None:
            return {"status": "unavailable", "scheduled": False, "reason": "worker is still initializing"}
        return worker_obj.request_memory_condense(full=bool(full), deep=bool(deep))

    def request_inject_context(task_id: str | None, content: str, request_id: str) -> dict:
        worker_obj = resources.get("worker")
        injections = resources.get("context_injections")
        prompt_queue_obj = resources.get("prompt_queue")
        if worker_obj is None or injections is None:
            raise ValueError("Context injection is still initializing")
        target = str(task_id or "").strip()
        if not target:
            target = str(worker_obj.active_task_id() or "").strip()
        if not target and prompt_queue_obj is not None:
            target = str(prompt_queue_obj.oldest_task_id() or "").strip()
        if not target:
            running = resources.get("durable").running_tasks(limit=2) if resources.get("durable") is not None else []
            if len(running) == 1:
                target = str(running[0].get("task_id") or "").strip()
        if not target:
            raise ValueError("No active task is available for context injection")
        result = injections.save(target, content, request_id)
        logging.info("Context injection saved task=%s injection_id=%s", result.get("task_id"), result.get("injection_id"))
        return result

    activity_cfg = config.get("activity", {})
    activity_host = resolve_bind_host(str(activity_cfg.get("host", "127.0.0.1")))
    activity_port = int(ports['activity'])
    activity_hub = ActivityHub()
    context_ollama_cfg = config.get("ollama", {})
    context_ollama_host = str(context_ollama_cfg.get("host", "127.0.0.1"))
    resources["context_client"] = OllamaClient(
        base_url=f"http://{context_ollama_host}:{ports['ollama']}",
        model=str(model_name or MODEL_NAME),
        timeout_seconds=600,
        activity_sink=activity_hub.publish,
        activity_source="context-snapshot",
    )

    # Every startup-owned resource lives under one cleanup boundary.  A failed
    # PostgreSQL migration, conversation-store initialization, worker startup, or
    # chat bind must release :8766 before the outer retry loop gets another turn.
    activity_server = None
    activity_thread = None
    activity_handler = None
    chat_server = None
    chat_thread = None
    worker = None
    prompt_queue = None
    durable = None
    live = None
    startup_completed = False
    graceful_exit = False

    try:
        resources["startup_phase"] = "activity-api-initialization"
        activity_server, activity_thread = start_activity_server(
            activity_hub,
            host=activity_host,
            port=activity_port,
            cancel_ollama=OllamaClient.cancel_active,
            shutdown_ollama=request_ollama_shutdown,
            shutdown_norm=request_shutdown,
            stop_all=request_stop_all,
            suppress_task=request_suppress_task,
            resume_maintenance=request_resume_maintenance,
            flush_suppressed=request_flush_suppressed,
            trash_list=request_trash_list,
            trash_restore=request_trash_restore,
            trash_purge=request_trash_purge,
            memory_condense=request_memory_condense,
            switch_model=request_model_switch,
            inject_context=request_inject_context,
            busy_status=request_busy_status,
            context_status=request_context_status,
            health_status=request_activity_health,
        )
        activity_handler = ActivityLogHandler(activity_hub)
        logging.getLogger().addHandler(activity_handler)
        logging.info("Activity/control API available during startup: http://%s:%s", activity_host, activity_port)

        if shutdown_requested.is_set():
            logging.info("Shutdown requested during startup; stopping before dependency initialization")
            graceful_exit = True
            return

        # build_runtime performs the authoritative Redis connection and PostgreSQL
        # schema migration.  Avoid a second PostgreSQL preflight before it: any
        # psycopg failure here remains inside main()'s bounded retry policy.
        resources["startup_phase"] = "postgres-schema-migration"
        coordinator, live, durable = build_runtime(root, ensure_schema=True)
        resources["durable"] = durable
        durable.set_runtime_state("deployed_version", load_project_metadata(root)["version"])
        context_injections = ContextInjections(durable)
        context_injections.ensure_schema()
        resources["context_injections"] = context_injections

        if shutdown_requested.is_set():
            logging.info("Shutdown requested during startup; stopping after durable initialization")
            graceful_exit = True
            return

        resources["startup_phase"] = "prompt-queue-initialization"
        prompt_queue = build_prompt_queue(root, durable=durable)
        resources["prompt_queue"] = prompt_queue

        resources["startup_phase"] = "n1-gatekeeper-initialization"
        agents_cfg = config.get("agents", {}) or {}
        n1_cfg = dict(agents_cfg.get("n1", {}) or {})
        n1_host = str(n1_cfg.get("host", config.get("ollama", {}).get("host", "127.0.0.1")))
        n1_port = int(n1_cfg.get("port", ports["ollama"]))
        n1_model = str(model_name or MODEL_NAME)
        n1_client = OllamaClient(
            base_url=f"http://{n1_host}:{n1_port}",
            model=n1_model,
            timeout_seconds=float(n1_cfg.get("timeout_seconds", 86400)),
            activity_sink=activity_hub.publish,
            activity_source="n1-gatekeeper",
        )
        n1_gatekeeper = N1Gatekeeper(
            n1_client,
            live,
            durable,
            enabled=bool(n1_cfg.get("enabled", True)),
            candidate_limit=int(n1_cfg.get("candidate_limit", 12)),
            semantic_match_floor=float(n1_cfg.get("semantic_match_floor", 0.45)),
            loop_repeat_threshold=int(n1_cfg.get("loop_repeat_threshold", 3)),
            reasoning_loop_threshold=int(n1_cfg.get("reasoning_loop_threshold", 3)),
            reasoning_similarity_floor=float(n1_cfg.get("reasoning_similarity_floor", 0.78)),
        )
        resources["n1_client"] = n1_client
        resources["n1_gatekeeper"] = n1_gatekeeper
        logging.info("N1 gatekeeper started model=%s endpoint=http://%s:%s", n1_model, n1_host, n1_port)

        resources["startup_phase"] = "conversation-store-initialization"
        service = build_conversation_service(
            root,
            ensure_schema=True,
            activity_sink=activity_hub.publish,
            prompt_queue=prompt_queue,
            coordinator=coordinator,
            durable=durable,
            n1_gatekeeper=n1_gatekeeper,
            model_override=str(model_name or MODEL_NAME),
        )

        resources["conversation_service"] = service
        statuses = {"redis": "ok", "prompt_queue": "ok", "postgres": "ok"}
        http_cfg = config.get("http", {})
        worker_cfg = config.get("worker", {})
        queue_cfg = config.get("prompt_queue", {})
        host = resolve_bind_host(str(http_cfg.get("host", "127.0.0.1")))
        port = int(ports['norm_http'])

        if shutdown_requested.is_set():
            logging.info("Shutdown requested during startup; stopping before worker/chat activation")
            graceful_exit = True
            return

        logging.info("Runtime connected: %s", statuses)
        logging.info("Coordinator ready; heartbeat interval=%ss", coordinator.heartbeat_seconds)
        logging.info("Prompt queue ready: %s", prompt_queue.stats())
        logging.info("Activity stream ready: http://%s:%s/events", activity_host, activity_port)

        resources["startup_phase"] = "worker-initialization"
        if bool(worker_cfg.get("enabled", True)):
            ollama_cfg = config.get("ollama", {})
            n2_cfg = dict((config.get("agents", {}) or {}).get("n2", {}) or {})
            ollama_host = str(n2_cfg.get("host", ollama_cfg.get('host', '127.0.0.1')))
            ollama_port = int(n2_cfg.get("port", ports['ollama']))
            n2_model = str(model_name or MODEL_NAME)
            worker_client = OllamaClient(
                base_url=f"http://{ollama_host}:{ollama_port}",
                model=n2_model,
                timeout_seconds=worker_cfg.get("model_timeout_seconds", 86400),
                activity_sink=activity_hub.publish,
                activity_source="n2-worker",
                crash_sink=live.record_model_buffer,
            )
            worker = PromptWorker(
                prompt_queue,
                worker_client,
                live,
                durable,
                heartbeat_seconds=coordinator.heartbeat_seconds,
                claim_idle_seconds=int(queue_cfg.get("claim_idle_seconds", 900)),
                poll_ms=int(worker_cfg.get("poll_ms", 5000)),
                deferred_append_planner=service.materialize_deferred_append,
                context_injections=resources.get("context_injections"),
                n1_gatekeeper=n1_gatekeeper,
            )
            worker.start()
            resources["worker"] = worker
            resources["n2_client"] = worker_client
            logging.info("N2 worker started model=%s endpoint=http://%s:%s consumer=%s", n2_model, ollama_host, ollama_port, worker.consumer)

        resources["startup_phase"] = "chat-api-initialization"
        chat_server, chat_thread = start_chat_api(service, host=host, port=port)
        resources["chat_server"] = chat_server
        resources["startup_phase"] = "ready"
        resources["startup_ready"] = True
        startup_completed = True
        logging.info("Conversation persistence ready")

        # Do not remain alive-but-unreachable if a dedicated HTTP transport
        # thread exits unexpectedly.  SelectorEventLoop prevents the observed
        # Windows AcceptEx failure; this watchdog is the second line of defense.
        while not shutdown_requested.wait(timeout=1.0):
            if chat_thread is not None and not chat_thread.is_alive():
                raise RuntimeTransportFailure("chat HTTP server thread exited unexpectedly")
            if activity_thread is not None and not activity_thread.is_alive():
                raise RuntimeTransportFailure("activity/control HTTP server thread exited unexpectedly")
        graceful_exit = True
        if worker:
            if stop_all_requested.is_set():
                if stop_all_now.is_set():
                    worker.wait_idle(timeout=30)
                else:
                    if not worker.is_idle():
                        logging.info("Stop-all waiting for current step to finish")
                    while not worker.wait_idle(timeout=30):
                        if stop_all_now.is_set():
                            worker.wait_idle(timeout=30)
                            break
            elif shutdown_now.is_set():
                worker.wait_idle(timeout=10)
            else:
                while True:
                    idle = worker.wait_idle(timeout=1)
                    stats = prompt_queue.stats()
                    retry_count = int(prompt_queue.r.xlen(prompt_queue.retry_stream))
                    if idle and int(stats.get("stream_length", 0)) == 0 and int(stats.get("pending_count", 0)) == 0 and retry_count == 0:
                        worker.request_drain()
                        break
                    logging.info(
                        "Graceful shutdown draining queued work stream=%s pending=%s retry=%s",
                        stats.get("stream_length"), stats.get("pending_count"), retry_count,
                    )
    except KeyboardInterrupt:
        # Interactive/manual mode keeps Ctrl+C semantics.  --service installs the
        # console-control guard, so a stray control event should never reach here.
        graceful_exit = True
        logging.info("Norm coordinator stopping normally")
        if chat_server is not None:
            chat_server.accepting_requests = False
        if worker:
            worker.request_drain()
            OllamaClient.cancel_active()
            worker.wait_idle(timeout=10)
    finally:
        cleanup_origin_phase = str(resources.get("startup_phase") or "unknown")
        resources["startup_ready"] = False
        resources["startup_phase"] = "stopping"

        if chat_server is not None:
            chat_server.accepting_requests = False

        if worker is not None:
            try:
                worker.note_shutdown_state()
            except Exception:
                logging.exception("Shutdown Redis scan failed; preserving Redis state")
            try:
                worker.stop(timeout=10)
            except KeyboardInterrupt:
                logging.warning("Interrupted while stopping prompt worker; continuing resource cleanup")

        if stop_all_requested.is_set():
            mode = "stop-all-now" if stop_all_now.is_set() else "stop-all-after-step"
            if live is not None and durable is not None and prompt_queue is not None:
                try:
                    sos_path = write_sos(root, live, durable, prompt_queue, mode)
                    logging.info("Stop-all recovery snapshot written: %s", sos_path)
                except Exception:
                    logging.exception("Failed to write SOS.md; Redis/PostgreSQL state preserved")
            else:
                logging.warning("Stop-all occurred before durable runtime initialization; no task SOS snapshot was available")
            ollama_result = request_ollama_shutdown()
            logging.info("Stop-all Ollama result: %s", ollama_result)
        elif graceful_exit and startup_completed and not shutdown_now.is_set():
            cleared = clear_runtime_redis(root)
            logging.info("Graceful shutdown purged deletion queue and cleared Redis: %s", cleared)

        if chat_server is not None:
            try:
                chat_server.shutdown()
            finally:
                chat_server.server_close()
            if chat_thread is not None:
                chat_thread.join(timeout=5)
        resources.pop("chat_server", None)

        if activity_server is not None:
            try:
                activity_server.shutdown()
            finally:
                activity_server.server_close()
            if activity_thread is not None:
                activity_thread.join(timeout=5)

        if activity_handler is not None:
            root_logger = logging.getLogger()
            root_logger.removeHandler(activity_handler)
            try:
                activity_handler.close()
            except Exception:
                pass

        if durable is not None:
            try:
                durable.pool.close()
            except Exception:
                logging.exception("PostgreSQL pool shutdown failed")

        if startup_completed or graceful_exit:
            logging.info("Norm coordinator stopped normally")
        else:
            logging.info("Startup resources released after failure phase=%s", cleanup_origin_phase)


def main() -> int:
    parser = argparse.ArgumentParser(prog="norm")
    parser.add_argument("--check", action="store_true", help="Verify Ollama, Redis, and PostgreSQL, then exit")
    parser.add_argument("--version", action="store_true", help="Print Norm project/version metadata, then exit")
    parser.add_argument("--console", action="store_true", help="Open the interactive Rich console")
    parser.add_argument("--service", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    root = norm_root()
    path_cfg = load_path_settings(root)
    if args.version:
        metadata = load_project_metadata(root)
        print(f"{metadata['name']} {metadata['version']} | {metadata['author']} | {metadata['repository']}")
        return 0
    if args.console:
        config = load_config(root)
        ports = load_ports(root)
        http_cfg = config.get("http", {})
        activity_cfg = config.get("activity", {})
        return run_console(
            resolve_bind_host(str(http_cfg.get("host", "127.0.0.1"))),
            int(ports['norm_http']),
            resolve_bind_host(str(activity_cfg.get("host", "127.0.0.1"))),
            int(ports['activity']),
            queue_config=config.get("console_queue", {}),
        )
    setup_logging(root)
    if args.service:
        _install_service_signal_guard()
    logging.info("Norm startup begin")
    logging.info("Resolved paths: app_root=%s runtime_root=%s documents_root=%s workspace_root=%s verbatim_writer=%s", path_cfg["app_root"], path_cfg["runtime_root"], path_cfg["documents_root"], path_cfg["workspace_root"], path_cfg["verbatim_writer"])
    config = load_config(root)
    ports = load_ports(root)
    ollama_cfg = config.get('ollama', {})
    ollama_host = str(ollama_cfg.get('host', '127.0.0.1'))
    ollama_url = f"http://{ollama_host}:{ports['ollama']}"
    # Session model selection is intentionally ephemeral. Every Norm process
    # starts on the canonical "norm" model; /switch-model never rewrites startup config.
    model_name = MODEL_NAME
    model_store = str(ollama_cfg.get('model_store') or os.environ.get("OLLAMA_MODELS") or MODEL_STORE)
    logging.info("Resolved service ports: ollama=%s norm_http=%s activity=%s", ports['ollama'], ports['norm_http'], ports['activity'])
    ollama_process = start_ollama_if_needed(root, ollama_url, model_name, model_store)
    preload_norm(ollama_url, model_name)
    if args.check:
        statuses = healthcheck(root)
        logging.info("Health check: %s", statuses)
        print(json.dumps({"ollama": "ok", **statuses}))
        return 0
    startup_attempts = 3
    postgres_attempt = 0
    transport_restarts = 0
    max_transport_restarts = 5
    while True:
        try:
            run_host(root, ollama_process, ollama_url, model_name)
            return 0
        except psycopg.Error:
            postgres_attempt += 1
            if postgres_attempt >= startup_attempts:
                raise
            delay = postgres_attempt * 3
            logging.exception(
                "PostgreSQL startup failed on attempt %s/%s; retrying in %ss",
                postgres_attempt,
                startup_attempts,
                delay,
            )
            time.sleep(delay)
        except RuntimeTransportFailure:
            transport_restarts += 1
            if transport_restarts > max_transport_restarts:
                raise
            delay = min(30, 2 ** (transport_restarts - 1))
            logging.exception(
                "Norm HTTP transport failed while live; restarting runtime resources in %ss (%s/%s)",
                delay,
                transport_restarts,
                max_transport_restarts,
            )
            time.sleep(delay)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except BaseException:
        logging.exception("FATAL: Norm runtime terminated unexpectedly")
        logging.shutdown()
        raise
