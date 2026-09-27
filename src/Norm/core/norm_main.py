from __future__ import annotations

from norm_runtime.secret_redaction import RedactingFormatter

import argparse
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
from threading import Event
from urllib import request
from urllib.parse import urlparse

from runtime_bootstrap import build_conversation_service, build_deletion_queue, build_prompt_queue, build_runtime, healthcheck, load_config
from norm_runtime.activity_stream import ActivityHub, ActivityLogHandler, start_activity_server
from norm_runtime.busy_status import collect_busy_status
from norm_runtime.context_snapshot import collect_context_snapshot
from norm_runtime.http_api import start_chat_api
from norm_runtime.ollama_client import ModelOutputTruncated, OllamaClient
from norm_runtime.ollama_lifecycle import shutdown_ollama
from norm_runtime.prompt_worker import PromptWorker
from norm_runtime.shutdown_snapshot import write_sos
from norm_runtime.rich_console import run_console
from norm_runtime.settings import load_ports, load_project_metadata, load_path_settings

MODEL_NAME = "norm"
MODEL_STORE = r"G:\Ollama\models"


def _install_service_signal_guard() -> None:
    """Keep helper-console Ctrl+C/Ctrl+Break events from terminating the service.

    Run-Norm.bat launches norm.exe with --service. Operator shutdown in service mode
    is intentionally controlled through the activity/control API (/shutdown or
    /stop-all), while a directly-invoked norm.exe keeps normal KeyboardInterrupt
    behavior for debugging.
    """
    def _ignore(signum, _frame) -> None:
        logging.warning(
            "Ignored console interrupt signal=%s in service mode; use /shutdown or /stop-all",
            signum,
        )

    signal.signal(signal.SIGINT, _ignore)
    sigbreak = getattr(signal, "SIGBREAK", None)
    if sigbreak is not None:
        signal.signal(sigbreak, _ignore)
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
            pass
        try:
            stream_deleted += int(client.xdel(stream, entry_id) or 0)
        except Exception:
            pass

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
    }


def run_host(root: Path, ollama_process: subprocess.Popen | None, ollama_url: str, model_name: str) -> None:
    config = load_config(root)
    ports = load_ports(root)
    shutdown_requested = Event()
    shutdown_now = Event()
    stop_all_requested = Event()
    stop_all_now = Event()
    resources: dict[str, object] = {"startup_phase": "initializing", "startup_ready": False}

    def request_activity_health() -> dict:
        if bool(resources.get("startup_ready")):
            return {"status": "ok", "phase": "ready"}
        return {"status": "initializing", "phase": str(resources.get("startup_phase") or "initializing")}

    def request_ollama_shutdown() -> dict:
        OllamaClient.cancel_active()
        result = shutdown_ollama(ollama_process, ollama_url, model_name)
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
        if not bool(resources.get("startup_ready")):
            payload["status"] = "initializing"
            payload["busy"] = True
            payload["phase"] = str(resources.get("startup_phase") or "initializing")
            payload["confidence"] = 1.0
        return payload

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
        return worker_obj.request_suppress_task(task_id=task_id, reason=reason)

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

    activity_cfg = config.get("activity", {})
    activity_host = resolve_bind_host(str(activity_cfg.get("host", "127.0.0.1")))
    activity_port = int(ports['activity'])
    activity_hub = ActivityHub()
    context_ollama_cfg = config.get("ollama", {})
    context_ollama_host = str(context_ollama_cfg.get("host", "127.0.0.1"))
    resources["context_client"] = OllamaClient(
        base_url=f"http://{context_ollama_host}:{ports['ollama']}",
        model=context_ollama_cfg.get("model", MODEL_NAME),
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
            flush_suppressed=request_flush_suppressed,
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

        if shutdown_requested.is_set():
            logging.info("Shutdown requested during startup; stopping after durable initialization")
            graceful_exit = True
            return

        resources["startup_phase"] = "prompt-queue-initialization"
        prompt_queue = build_prompt_queue(root, durable=durable)
        resources["prompt_queue"] = prompt_queue

        resources["startup_phase"] = "conversation-store-initialization"
        service = build_conversation_service(
            root,
            ensure_schema=True,
            activity_sink=activity_hub.publish,
            prompt_queue=prompt_queue,
            coordinator=coordinator,
            durable=durable,
        )

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
            ollama_host = str(ollama_cfg.get('host', '127.0.0.1'))
            worker_client = OllamaClient(
                base_url=f"http://{ollama_host}:{ports['ollama']}",
                model=ollama_cfg.get("model", MODEL_NAME),
                timeout_seconds=worker_cfg.get("model_timeout_seconds", 86400),
                activity_sink=activity_hub.publish,
                activity_source="worker",
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
            )
            worker.start()
            resources["worker"] = worker
            logging.info("Prompt worker started consumer=%s", worker.consumer)

        resources["startup_phase"] = "chat-api-initialization"
        chat_server, chat_thread = start_chat_api(service, host=host, port=port)
        resources["chat_server"] = chat_server
        resources["startup_phase"] = "ready"
        resources["startup_ready"] = True
        startup_completed = True
        logging.info("Conversation persistence ready")

        shutdown_requested.wait()
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
    model_name = str(ollama_cfg.get('model', MODEL_NAME))
    model_store = str(ollama_cfg.get('model_store', MODEL_STORE))
    logging.info("Resolved service ports: ollama=%s norm_http=%s activity=%s", ports['ollama'], ports['norm_http'], ports['activity'])
    ollama_process = start_ollama_if_needed(root, ollama_url, model_name, model_store)
    preload_norm(ollama_url, model_name)
    if args.check:
        statuses = healthcheck(root)
        logging.info("Health check: %s", statuses)
        print(json.dumps({"ollama": "ok", **statuses}))
        return 0
    startup_attempts = 3
    for attempt in range(1, startup_attempts + 1):
        try:
            run_host(root, ollama_process, ollama_url, model_name)
            return 0
        except psycopg.Error:
            if attempt >= startup_attempts:
                raise
            delay = attempt * 3
            logging.exception(
                "PostgreSQL startup failed on attempt %s/%s; retrying in %ss",
                attempt,
                startup_attempts,
                delay,
            )
            time.sleep(delay)
    return 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except BaseException:
        logging.exception("FATAL: Norm runtime terminated unexpectedly")
        logging.shutdown()
        raise
