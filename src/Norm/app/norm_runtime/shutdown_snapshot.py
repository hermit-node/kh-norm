import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo


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


def write_sos(root: Path, live, durable, prompt_queue, mode: str) -> Path:
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
    keys = _buffer_keys(live)
    if not keys:
        lines.extend(["", "## Raw model buffers", "", "No raw model buffer was present. Resume from PostgreSQL/Redis queue state."])
    for key in keys:
        task_id, step_id = _split_key(live, key)
        state = live.state(task_id)
        lines.extend([
            "",
            f"## Task {task_id} / Step {step_id}",
            "",
            f"Redis task state: {json.dumps(state, ensure_ascii=False)}",
        ])
        lines.extend(_durable_checkpoint(durable, task_id))
        lines.extend(["", "### Raw Ollama stream", ""])
        rows = live.client.xrange(key, min="-", max="+")
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
        "On restart, trust PostgreSQL checkpoints first. Redis/SOS content is best-effort context for the interrupted step.",
        "",
    ])
    target = root / "SOS.md"
    target.write_text("\n".join(lines), encoding="utf-8")
    return target
