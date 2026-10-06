from __future__ import annotations

import importlib
import json
import os
import sys
from pathlib import Path
from typing import Any

import redis

from norm_runtime.coordinator import TaskCoordinator
from norm_runtime.durable_log import PostgresTaskLog
from norm_runtime.deletion_queue import RedisDeletionQueue
from norm_runtime.live_log import RedisTaskLog
from norm_runtime.prompt_queue import RedisPromptQueue
from norm_runtime.conversation_store import ConversationStore
from norm_runtime.file_tool_executor import FileToolExecutor
from norm_runtime.file_access_policy import load_file_access_policy
from norm_runtime.ollama_client import OllamaClient
from norm_runtime.settings import load_ports, load_path_settings, load_plugin_settings, load_network_settings, load_postgres_settings, resolve_network_host, load_secrets
from norm_runtime.conversation_service import ConversationService


def postgres_pool_class(root: Path):
    """Load the built-in pool implementation from the schema-2 plugin source tree."""
    import importlib.util

    source_root = Path(__file__).resolve().parents[1]
    runtime_root = Path(root).resolve()
    candidates = [
        source_root / "plugins" / "postgres_pool" / "src" / "_pool.py",
        runtime_root / "plugins" / "postgres_pool" / "src" / "_pool.py",
        # Read-only compatibility with an older installed tree during migration/recovery.
        source_root / "plugins" / "postgres_pool" / "_pool.py",
        runtime_root / "plugins" / "postgres_pool" / "_pool.py",
    ]
    pool_file = next((path for path in candidates if path.is_file()), None)
    if pool_file is None:
        raise FileNotFoundError("PostgreSQL pool plugin source is missing")
    module_name = "norm_builtin_postgres_pool_internal"
    module = sys.modules.get(module_name)
    if module is None or Path(getattr(module, "__file__", "")).resolve() != pool_file.resolve():
        spec = importlib.util.spec_from_file_location(module_name, pool_file)
        if spec is None or spec.loader is None:
            raise ImportError(f"cannot load PostgreSQL pool plugin: {pool_file}")
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
    return module.PostgresPool


def build_postgres_pool(root: Path):
    pool = postgres_pool_class(root)
    if not pool.configured():
        pool.configure_from_runtime(root)
    return pool


def close_postgres_pool(root: Path) -> None:
    """Close this process's shared PostgreSQL pools before interpreter finalization."""
    pool = postgres_pool_class(root)
    if pool.configured():
        pool.close()


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
        "{state_root}": str(path_cfg["state_root"]),
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
    ollama_cfg = config.get("ollama", {})
    agents = config.setdefault("agents", {})
    for role in ("n1", "n2"):
        role_cfg = agents.setdefault(role, {})
        role_cfg.setdefault("host", ollama_cfg.get("host", "127.0.0.1"))
        role_cfg.setdefault("port", ports["ollama"])
        role_cfg.setdefault("model", ollama_cfg.get("model", "norm"))
    config.setdefault("http", {})["host"] = resolve_network_host(network, "norm_host", bind=True)
    config.setdefault("activity", {})["host"] = resolve_network_host(network, "activity_host", bind=True)
    pg = load_postgres_settings(root)
    config.setdefault("postgres", {})["schema"] = pg["schema"]
    config["postgres"]["connection"] = "norm"
    config["stocks_postgres"] = {"connection": "stocks"}
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
        trash_root=str(dq.get("trash_root", load_path_settings(root)["state_root"] / "deletion-trash")),
        items_key=str(dq.get("items_key", "norm:trash:items")),
        batches_key=str(dq.get("batches_key", "norm:trash:batches")),
        cross_volume_move_max_bytes=int(dq.get("cross_volume_move_max_bytes", 268_435_456)),
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
    pg_pool = build_postgres_pool(root)
    durable = PostgresTaskLog(
        pool=pg_pool,
        schema=pg_cfg.get("schema", "norm_runtime"),
        connection_name=pg_cfg.get("connection", "norm"),
    )
    try:
        if ensure_schema:
            durable.ensure_schema()
    except Exception:
        pg_pool.close()
        raise
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
    n1_gatekeeper=None,
    model_override: str | None = None,
) -> ConversationService:
    config = load_config(root)
    pg_cfg = config["postgres"]
    pg_pool = build_postgres_pool(root)
    store = ConversationStore(
        pool=pg_pool,
        schema=pg_cfg.get("schema", "norm_runtime"),
        connection_name=pg_cfg.get("connection", "norm"),
    )
    if ensure_schema:
        store.ensure_schema()
    ollama_cfg = config.get("ollama", {})
    ports = load_ports(root)
    n2_cfg = dict((config.get("agents", {}) or {}).get("n2", {}) or {})
    ollama_host = str(n2_cfg.get("host", ollama_cfg.get("host", "127.0.0.1")))
    ollama_port = int(n2_cfg.get("port", ports["ollama"]))
    client = OllamaClient(
        base_url=f"http://{ollama_host}:{ollama_port}",
        model=str(model_override or n2_cfg.get("model", ollama_cfg.get("model", "norm"))),
        activity_sink=activity_sink,
        activity_source="n2-chat",
    )
    memory_cfg = config.get("memory", {})
    ingrained_cfg = config.get("ingrained_details", {})
    tools_cfg = config.get("tools", {})
    path_cfg = load_path_settings(root)
    plugin_cfg = load_plugin_settings(root)
    workspace_root = path_cfg["workspace_root"]
    temp_root = path_cfg["temp_root"]
    file_policy = load_file_access_policy(root, capability="core")
    allowed_roots = sorted({str(p) for p in (*file_policy.read_roots, *file_policy.write_roots)})
    file_tools = None
    if bool(tools_cfg.get("enabled", False)):
        deletion_queue = build_deletion_queue(root)
        file_tools = FileToolExecutor(
            allowed_roots,
            backup_root=str(tools_cfg.get("backup_root", path_cfg["state_root"] / "file-backups")),
            audit_log=str(tools_cfg.get("audit_log", root / "logs" / "tool-audit.jsonl")),
            max_read_bytes=file_policy.read_processing_buffer_bytes,
            max_tool_return_bytes=file_policy.read_chunk_bytes,
            read_roots=[str(p) for p in file_policy.read_roots],
            write_roots=[str(p) for p in file_policy.write_roots],
            enforce_read_directories=file_policy.enforce_read_directories,
            enforce_write_directories=file_policy.enforce_write_directories,
            read_chunk_bytes=file_policy.read_chunk_bytes,
            read_chunk_max_bytes=file_policy.read_chunk_max_bytes,
            max_write_bytes=int(tools_cfg.get("max_write_bytes", 5_242_880)),
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
            temp_root=str(temp_root),
            workspace_root=str(workspace_root),
            task_storage_config=dict(config.get("task_storage", {})),
            postgres_pool=pg_pool,
            connection_config={
                "redis": config.get("redis", {}),
                "prompt_queue": config.get("prompt_queue", {}),
                "ollama_base_url": f"http://{ollama_host}:{ollama_port}",
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
        n1_gatekeeper=n1_gatekeeper,
        wait_timeout_seconds=float(config.get("http", {}).get("wait_timeout_seconds", 86400)),
        persistent_instructions=list(config.get("persistent_instructions", [])),
        ingrained_details_enabled=bool(ingrained_cfg.get("enabled", True)),
        unresolved_test_limit=int(ingrained_cfg.get("test_limit", 3)),
        unresolved_explore_every_tasks=int(ingrained_cfg.get("explore_every_tasks", 4)),
        unresolved_delete_after_trials=int(ingrained_cfg.get("delete_after_trials", 15)),
        unresolved_delete_after_domains=int(ingrained_cfg.get("delete_after_distinct_domains", 3)),
        runtime_root=root,
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
    pg_health = build_postgres_pool(root).health("norm")
    if not pg_health.get("responsive"):
        raise RuntimeError("PostgreSQL pool health check failed")
    statuses["postgres"] = "ok"
    return statuses
