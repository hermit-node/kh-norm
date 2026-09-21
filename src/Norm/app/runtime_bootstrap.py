from __future__ import annotations

import json
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
from norm_runtime.settings import load_ports, load_path_settings
from norm_runtime.conversation_service import ConversationService


def load_config(root: Path) -> dict[str, Any]:
    path = root / "config" / "runtime.json"
    with path.open("r", encoding="utf-8-sig") as handle:
        return json.load(handle)


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


def build_prompt_queue(root: Path) -> RedisPromptQueue:
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
    workspace_root = path_cfg["workspace_root"]
    allowed_roots = [str(workspace_root), *list(tools_cfg.get("allowed_roots", []))]
    file_tools = None
    if bool(tools_cfg.get("enabled", False)):
        deletion_queue = build_deletion_queue(root)
        file_tools = FileToolExecutor(
            allowed_roots,
            backup_root=str(tools_cfg.get("backup_root", root / "state" / "file-backups")),
            audit_log=str(tools_cfg.get("audit_log", root / "logs" / "tool-audit.jsonl")),
            max_read_bytes=int(tools_cfg.get("max_read_bytes", 131072)),
            max_write_bytes=int(tools_cfg.get("max_write_bytes", 1048576)),
            blocked_write_staging_root=str(tools_cfg.get("blocked_write_staging_root", workspace_root / "docs" / "blocked-writes")),
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
            connection_config={
                "postgres": config.get("postgres", {}),
                "redis": config.get("redis", {}),
                "prompt_queue": config.get("prompt_queue", {}),
                "ollama_base_url": f"http://{ollama_host}:{ports['ollama']}",
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
