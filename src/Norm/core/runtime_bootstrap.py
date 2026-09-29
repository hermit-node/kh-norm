from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import psycopg
import redis

from norm_runtime.coordinator import TaskCoordinator
from norm_runtime.durable_log import PostgresTaskLog
from norm_runtime.deletion_queue import RedisDeletionQueue
from norm_runtime.live_log import RedisTaskLog
from norm_runtime.prompt_queue import RedisPromptQueue
from norm_runtime.conversation_store import ConversationStore
from norm_runtime.file_tool_executor import FileToolExecutor
from norm_runtime.ollama_client import OllamaClient
from norm_runtime.settings import load_ports, load_path_settings, load_plugin_settings, load_network_settings, resolve_network_host, load_secrets
from norm_runtime.conversation_service import ConversationService


def _expand_runtime_values(value: Any, substitutions: dict[str, str]) -> Any:
    if isinstance(value, dict):
        return {key: _expand_runtime_values(item, substitutions) for key, item in value.items()}
    if isinstance(value, list):
        return [_expand_runtime_values(item, substitutions) for item in value]
    if isinstance(value, str):
        expanded = os.path.expandvars(os.path.expanduser(value))
        for token, replacement in substitutions.items():
            expanded = expanded.replace(token, replacement)
        return expanded
    return value


def load_config(root: Path) -> dict[str, Any]:
    path = root / "config" / "runtime.json"
    with path.open("r", encoding="utf-8-sig") as handle:
        config = json.load(handle)
    network = load_network_settings(root)
    ports = load_ports(root)
    secrets = load_secrets(root)
    # Expose only explicitly supported plugin secrets to hot-loaded plugins.
    # Names include SECRET/SECRETS so Norm's redaction registry treats their values as sensitive.
    for key in ("NORM_ROTOR5_SECRET", "NORM_ROTOR5_PREVIOUS_SECRETS"):
        value = secrets.get(key, "").strip()
        if value:
            os.environ[key] = value
        else:
            os.environ.pop(key, None)
    path_cfg = load_path_settings(root)
    documents_root = path_cfg["documents_root"]
    substitutions = {
        "{runtime_root}": str(path_cfg["runtime_root"]),
        "{app_root}": str(path_cfg["app_root"]),
        "{documents_root}": str(path_cfg["documents_root"]),
        "{workspace_root}": str(path_cfg["workspace_root"]),
        "{temp_root}": str(path_cfg["temp_root"]),
    }
    config = _expand_runtime_values(config, substitutions)
    config["_paths"] = {name: str(path) for name, path in path_cfg.items()}
    tools_cfg = config.setdefault("tools", {})
    tools_cfg["blocked_write_staging_root"] = str(path_cfg["temp_root"] / "blocked-writes")
    tools_cfg["image_output_root"] = str(path_cfg["workspace_root"] / "images" / "analysis")
    tools_cfg.setdefault("storage_context", {})["backup_root"] = str(path_cfg["workspace_root"])
    redis_host = resolve_network_host(network, "redis_host")
    for section in ("redis", "prompt_queue", "deletion_queue", "console_queue"):
        config.setdefault(section, {})["host"] = redis_host
        config[section]["port"] = ports["redis"]
    config.setdefault("ollama", {})["host"] = resolve_network_host(network, "ollama_host")
    config.setdefault("http", {})["host"] = resolve_network_host(network, "norm_host", bind=True)
    config.setdefault("activity", {})["host"] = resolve_network_host(network, "activity_host", bind=True)
    pg_host = resolve_network_host(network, "postgres_host")
    pg_user = secrets.get("NORM_POSTGRES_USER", "").strip()
    pg_password = secrets.get("NORM_POSTGRES_PASSWORD", "").strip()
    pg_db = secrets.get("NORM_POSTGRES_DB", "postgres").strip() or "postgres"
    if not pg_user or not pg_password:
        raise ValueError("NORM_POSTGRES_USER and NORM_POSTGRES_PASSWORD are required")
    config.setdefault("postgres", {})["conninfo"] = psycopg.conninfo.make_conninfo(host=pg_host, port=ports["postgres"], dbname=pg_db, user=pg_user, password=pg_password, connect_timeout=5)
    config["postgres"]["schema"] = secrets.get("NORM_POSTGRES_SCHEMA", "norm_runtime").strip() or "norm_runtime"
    stocks_db = secrets.get("NORM_STOCKS_DB", "stocks_api").strip() or "stocks_api"
    config["stocks_postgres"] = {"conninfo": psycopg.conninfo.make_conninfo(host=pg_host, port=ports["postgres"], dbname=stocks_db, user=pg_user, password=pg_password, connect_timeout=5)}
    config["_authority"] = {"host": redis_host, "port": ports["redis"], "required": bool(network.get("require_tailscale", True))}
    return config


def build_deletion_queue(root: Path) -> RedisDeletionQueue:
    config = load_config(root)
    redis_cfg = config.get("redis", {})
    dq = config.get("deletion_queue", {})
    return RedisDeletionQueue(
        host=str(dq.get("host", redis_cfg.get("host", "127.0.0.1"))),
        port=int(dq.get("port", redis_cfg.get("port", 6379))),
        db=int(dq.get("db", 2)),
        stream=str(dq.get("stream", "norm:deletion:queue")),
        trash_root=str(dq.get("trash_root", root / "state" / "deletion-trash")),
    )


def build_runtime(root: Path, *, ensure_schema: bool = False):
    config = load_config(root)
    redis_cfg = config["redis"]
    pg_cfg = config["postgres"]
    client = redis.Redis(
        host=redis_cfg["host"],
        port=int(redis_cfg["port"]),
        db=int(redis_cfg.get("db", 0)),
        decode_responses=True,
    )
    client.ping()
    live = RedisTaskLog(client, prefix=redis_cfg.get("prefix", "norm:task"))
    durable = PostgresTaskLog(
        conninfo=pg_cfg["conninfo"],
        schema=pg_cfg.get("schema", "norm_runtime"),
    )
    if ensure_schema:
        durable.ensure_schema()
    coordinator = TaskCoordinator(
        live=live,
        durable=durable,
        heartbeat_seconds=int(config.get("heartbeat_seconds", 300)),
    )
    return coordinator, live, durable


def build_prompt_queue(root: Path, durable=None) -> RedisPromptQueue:
    config = load_config(root)
    queue_cfg = config["prompt_queue"]
    worker_cfg = config.get("worker", {})
    poll_seconds = max(0.1, int(worker_cfg.get("poll_ms", 5000)) / 1000)
    client = redis.Redis(
        host=queue_cfg.get("host", "127.0.0.1"),
        port=int(queue_cfg.get("port", 6379)),
        db=int(queue_cfg.get("db", 1)),
        decode_responses=True,
        socket_timeout=max(10.0, poll_seconds + 5.0),
        socket_connect_timeout=5.0,
        socket_keepalive=True,
        health_check_interval=30,
    )
    client.ping()
    queue = RedisPromptQueue(
        client,
        stream=queue_cfg.get("stream", "norm:prompt:work"),
        group=queue_cfg.get("group", "norm-workers"),
        retry_stream=queue_cfg.get("retry_stream", "norm:prompt:retry"),
        escalation_stream=queue_cfg.get("escalation_stream", "norm:prompt:escalation"),
        dead_letter_stream=queue_cfg.get("dead_letter_stream", "norm:prompt:dead"),
        stale_ms=int(queue_cfg.get("claim_idle_seconds", 900)) * 1000,
        max_attempts=int(queue_cfg.get("max_attempts", 5)),
        durable=durable,
    )
    queue.ensure_group()
    return queue


def build_conversation_service(
    root: Path,
    *,
    ensure_schema: bool = False,
    activity_sink=None,
    prompt_queue: RedisPromptQueue | None = None,
    coordinator=None,
    durable=None,
) -> ConversationService:
    config = load_config(root)
    pg_cfg = config["postgres"]
    store = ConversationStore(
        conninfo=pg_cfg["conninfo"],
        schema=pg_cfg.get("schema", "norm_runtime"),
    )
    if ensure_schema:
        store.ensure_schema()
    ollama_cfg = config.get("ollama", {})
    ports = load_ports(root)
    ollama_host = str(ollama_cfg.get("host", "127.0.0.1"))
    client = OllamaClient(
        base_url=f"http://{ollama_host}:{ports['ollama']}",
        model=ollama_cfg.get("model", "norm"),
        activity_sink=activity_sink,
        activity_source="chat",
    )
    memory_cfg = config.get("memory", {})
    tools_cfg = config.get("tools", {})
    path_cfg = load_path_settings(root)
    plugin_cfg = load_plugin_settings(root)
    workspace_root = path_cfg["workspace_root"]
    temp_root = path_cfg["temp_root"]
    allowed_roots = [str(workspace_root), str(temp_root), str(root / "docs"), str(plugin_cfg["plugin_root"]), *list(tools_cfg.get("allowed_roots", []))]
    file_tools = None
    if bool(tools_cfg.get("enabled", False)):
        deletion_queue = build_deletion_queue(root)
        file_tools = FileToolExecutor(
            allowed_roots,
            backup_root=str(tools_cfg.get("backup_root", root / "state" / "file-backups")),
            audit_log=str(tools_cfg.get("audit_log", root / "logs" / "tool-audit.jsonl")),
            max_read_bytes=int(tools_cfg.get("max_read_bytes", 131072)),
            max_write_bytes=int(tools_cfg.get("max_write_bytes", 1048576)),
            blocked_write_staging_root=str(tools_cfg.get("blocked_write_staging_root", temp_root / "blocked-writes")),
            write_retry_count=int(tools_cfg.get("write_retry_count", 3)),
            write_retry_delay_seconds=float(tools_cfg.get("write_retry_delay_seconds", 0.25)),
            image_enabled=bool(tools_cfg.get("image_enabled", False)),
            image_python=str(tools_cfg.get("image_python", root / ".venv" / "Scripts" / "python.exe")),
            image_analyzer_script=str(tools_cfg.get("image_analyzer_script", root / "tools" / "image_analyzer.py")),
            image_output_root=str(tools_cfg.get("image_output_root", workspace_root / "images" / "analysis")),
            image_profile_budgets=dict(tools_cfg.get("image_profile_budgets", {})),
            image_max_input_bytes=int(tools_cfg.get("image_max_input_bytes", 25_000_000)),
            vision_client=client,
            deletion_queue=deletion_queue,
            storage_config=dict(tools_cfg.get("storage_context", {})) or None,
            shell_enabled=bool(tools_cfg.get("shell_enabled", False)),
            shell_executable=str(tools_cfg.get("shell_executable", "powershell.exe")),
            shell_timeout_seconds=int(tools_cfg.get("shell_timeout_seconds", 120)),
            shell_max_output_chars=int(tools_cfg.get("shell_max_output_chars", 20000)),
            verbatim_helper=str(path_cfg["verbatim_writer"]),
            plugin_root=str(plugin_cfg["plugin_root"]),
            plugin_registry_file=str(plugin_cfg["registry_file"]),
            connection_config={
                "postgres": config.get("postgres", {}),
                "redis": config.get("redis", {}),
                "prompt_queue": config.get("prompt_queue", {}),
                "ollama_base_url": f"http://{ollama_host}:{ports['ollama']}",
                "stocks_postgres": config.get("stocks_postgres", {}),
                "authority": config.get("_authority", {}),
            },
        )
    return ConversationService(
        store,
        client,
        routing_threshold=float(memory_cfg.get("routing_threshold", 0.58)),
        candidate_threads=int(memory_cfg.get("candidate_threads", 12)),
        recent_messages=int(memory_cfg.get("recent_messages", 12)),
        file_tools=file_tools,
        max_tool_rounds=int(tools_cfg.get("max_rounds", 8)),
        prompt_queue=prompt_queue,
        coordinator=coordinator,
        durable=durable,
        wait_timeout_seconds=float(config.get("http", {}).get("wait_timeout_seconds", 86400)),
        persistent_instructions=list(config.get("persistent_instructions", [])),
    )


def healthcheck(root: Path) -> dict[str, str]:
    config = load_config(root)
    redis_cfg = config["redis"]
    queue_cfg = config.get("prompt_queue")
    pg_cfg = config["postgres"]
    redis_client = redis.Redis(
        host=redis_cfg["host"],
        port=int(redis_cfg["port"]),
        db=int(redis_cfg.get("db", 0)),
        decode_responses=True,
    )
    redis_client.ping()
    statuses = {"redis": "ok"}
    if queue_cfg:
        queue_client = redis.Redis(
            host=queue_cfg.get("host", "127.0.0.1"),
            port=int(queue_cfg.get("port", 6379)),
            db=int(queue_cfg.get("db", 1)),
            decode_responses=True,
        )
        queue_client.ping()
        statuses["prompt_queue"] = "ok"
    with psycopg.connect(pg_cfg["conninfo"]) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT 1")
            cur.fetchone()
    statuses["postgres"] = "ok"
    return statuses
