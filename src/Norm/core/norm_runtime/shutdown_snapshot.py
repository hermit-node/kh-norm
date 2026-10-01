import json
import os
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from .settings import load_path_settings


def _buffer_keys(live) -> list[str]:
    pattern = f"{live.prefix}:*:model-buffer:*"
    keys = []
    for key in live.client.scan_iter(match=pattern):
        keys.append(key.decode() if isinstance(key, bytes) else str(key))
    return sorted(keys)


def _split_key(live, key: str) -> tuple[str, str]:
    prefix = f"{live.prefix}:"
    body = key[len(prefix):] if key.startswith(prefix) else key
    task_id, sep, step_id = body.partition(":model-buffer:")
    if not sep:
        return body, "unknown"
    return task_id, step_id


def _durable_checkpoint(durable, task_id: str) -> list[str]:
    lines: list[str] = []
    try:
        with durable._connect() as conn, conn.cursor() as cur:
            cur.execute(
                f"SELECT title,status,updated_at FROM {durable.schema}.task_runs WHERE task_id=%s",
                (task_id,),
            )
            row = cur.fetchone()
            if row:
                lines.append(f"Task title: {row[0]}")
                lines.append(f"Durable task status: {row[1]}")
                lines.append(f"Durable task updated: {row[2]}")
            cur.execute(
                f"SELECT step_id,name,status,summary,verification,completed_at "
                f"FROM {durable.schema}.task_steps WHERE task_id=%s ORDER BY completed_at DESC LIMIT 1",
                (task_id,),
            )
            step = cur.fetchone()
            if step:
                lines.append(f"Last durable step: {step[0]} | {step[2]} | {step[1]}")
                lines.append(f"Last durable verification: {step[4]}")
                lines.append(f"Last durable summary: {step[3]}")
    except Exception as exc:
        lines.append(f"Durable checkpoint lookup failed: {type(exc).__name__}: {exc}")
    return lines


def write_sos(runtime_root: Path, live, durable, prompt_queue, mode: str, *, output_root: Path | None = None) -> Path:
    now = datetime.now(ZoneInfo("America/New_York"))
    lines = [
        "# Norm SOS / Crash Recovery Buffer",
        "",
        f"Generated: {now.isoformat(timespec='seconds')}",
        f"Stop mode: {mode}",
        "",
        "This file is a one-time materialization of the raw Redis model buffer at shutdown.",
        "The raw fragments below are intentionally unpolished and may end mid-thought.",
        "PostgreSQL remains the durable source of truth for completed/checkpointed work.",
        "",
    ]
    try:
        lines.append(f"Prompt queue at stop: {json.dumps(prompt_queue.stats(), ensure_ascii=False)}")
    except Exception as exc:
        lines.append(f"Prompt queue snapshot failed: {type(exc).__name__}: {exc}")
    task_ids = sorted(live.task_ids()) if hasattr(live, "task_ids") else []
    lines.extend(["", "## Redis live task state / printed working buffer", ""])
    if not task_ids:
        lines.append("No live Redis task-state hashes were present.")
    for task_id in task_ids:
        state = live.state(task_id)
        lines.extend([
            f"### Task {task_id}",
            "",
            f"Redis task state: {json.dumps(state, ensure_ascii=False)}",
            "",
            "#### Redis printed working buffer",
            "",
        ])
        try:
            lines.append(live.working_memory(task_id))
        except Exception as exc:
            lines.append(f"Working-buffer read failed: {type(exc).__name__}: {exc}")
        lines.append("")

    keys = _buffer_keys(live)
    lines.extend(["", "## Redis raw model buffers", ""])
    if not keys:
        lines.append("No raw model buffer was present. Resume from PostgreSQL/Redis queue state.")
    for key in keys:
        task_id, step_id = _split_key(live, key)
        state = live.state(task_id)
        rows = live.client.xrange(key, min="-", max="+")
        last_id = rows[-1][0] if rows else "none"
        lines.extend([
            "",
            f"### Task {task_id} / Step {step_id}",
            "",
            f"Redis task state: {json.dumps(state, ensure_ascii=False)}",
            f"Redis model-buffer key: {key}",
            f"Redis model-buffer rows: {len(rows)}; last stream id: {last_id}",
        ])
        lines.extend(_durable_checkpoint(durable, task_id))
        lines.extend(["", "#### Printed Ollama buffer", ""])
        if not rows:
            lines.append("[buffer empty]")
            continue
        for stream_id, fields in rows:
            raw_chunks = fields.get("chunks", "[]")
            try:
                chunks = json.loads(raw_chunks)
            except json.JSONDecodeError:
                chunks = [{"kind": "unparsed", "text": raw_chunks}]
            if not isinstance(chunks, list):
                chunks = [{"kind": "unparsed", "text": str(chunks)}]
            for chunk in chunks:
                if not isinstance(chunk, dict):
                    chunk = {"kind": "unparsed", "text": str(chunk)}
                kind = str(chunk.get("kind") or "unknown")
                text = str(chunk.get("text") or "")
                lines.append(f"--- BEGIN {kind} [{stream_id}] ---")
                lines.append(text)
                lines.append(f"--- END {kind} ---")

    lines.extend([
        "",
        "## Recovery",
        "",
        "On restart, trust PostgreSQL checkpoints first. Redis task state, working-buffer events, and raw model-buffer streams are the best-effort interrupted-step context.",
        "",
    ])
    runtime_root = Path(runtime_root).resolve()
    if output_root is None:
        temp_root = Path(load_path_settings(runtime_root)["temp_root"])
        recovery_root = temp_root / "recovery"
    else:
        recovery_root = Path(output_root).expanduser().resolve()
    recovery_root.mkdir(parents=True, exist_ok=True)
    target = recovery_root / "SOS.md"
    content = "\n".join(lines)
    with target.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())
    with target.open("r+b") as handle:
        os.fsync(handle.fileno())
    return target
