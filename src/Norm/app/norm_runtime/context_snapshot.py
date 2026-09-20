from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Callable
from zoneinfo import ZoneInfo

import redis

from .settings import load_document_paths

NY = ZoneInfo("America/New_York")


def _iso_mtime(path: Path) -> str:
    return datetime.fromtimestamp(path.stat().st_mtime, NY).isoformat(timespec="seconds")


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _compact(text: str, limit: int = 1200) -> str:
    value = re.sub(r"\s+", " ", str(text or "")).strip()
    if len(value) <= limit:
        return value
    return value[: max(0, limit - 1)].rstrip() + "…"

def _markdown_sections(text: str) -> list[tuple[str, str]]:
    sections: list[tuple[str, str]] = []
    title = "preamble"
    body: list[str] = []
    for line in text.splitlines():
        if line.startswith("## "):
            if body:
                sections.append((title, "\n".join(body).strip()))
            title = line[3:].strip()
            body = []
        else:
            body.append(line)
    if body:
        sections.append((title, "\n".join(body).strip()))
    return sections


def _doc_digest(path: Path, keywords: tuple[str, ...], *, section_chars: int = 1100) -> dict:
    if not path.is_file():
        return {"path": str(path), "exists": False}
    text = path.read_text(encoding="utf-8-sig", errors="replace")
    picked = []
    for title, body in _markdown_sections(text):
        key = title.lower()
        if title == "preamble" or any(word in key for word in keywords):
            picked.append({"section": title, "text": _compact(body, section_chars)})
    return {
        "path": str(path),
        "exists": True,
        "modified_at": _iso_mtime(path),
        "sha256": _sha256(path),
        "sections": picked[:10],
    }


def _recent_files(root: Path, limit: int = 24) -> list[dict]:
    candidates: list[Path] = []
    for base in (root / "app", root / "config", root / "docs", root / "tools"):
        if not base.exists():
            continue
        for path in base.rglob("*"):
            if not path.is_file() or "__pycache__" in path.parts:
                continue
            if path.suffix.lower() not in {".py", ".md", ".json", ".ini", ".txt", ".readme"}:
                continue
            candidates.append(path)
    candidates.extend(path for path in (root / "README.md", root / "CURRENT_STATUS.md", root / "DEVELOPMENT_NOTES.md") if path.is_file())
    unique = {path.resolve(): path for path in candidates}
    ordered = sorted(unique.values(), key=lambda p: p.stat().st_mtime, reverse=True)[:limit]
    return [{"path": str(p.relative_to(root)), "modified_at": _iso_mtime(p), "size": p.stat().st_size} for p in ordered]

def _source_newer_than_exe(root: Path) -> list[dict]:
    exe = root / "app" / "norm.exe"
    if not exe.is_file():
        return []
    exe_mtime = exe.stat().st_mtime
    rows = []
    for path in (root / "app").rglob("*.py"):
        if "__pycache__" in path.parts or not path.is_file():
            continue
        if path.stat().st_mtime > exe_mtime + 1.0:
            rows.append({
                "path": str(path.relative_to(root)),
                "modified_at": _iso_mtime(path),
            })
    return sorted(rows, key=lambda row: row["modified_at"], reverse=True)


def _log_snapshot(root: Path) -> dict:
    path = root / "logs" / "norm-runtime.log"
    if not path.is_file():
        return {"exists": False, "recent": [], "warnings_errors": []}
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()[-300:]
    warnings = [line for line in lines if " WARNING " in line or " ERROR " in line or " CRITICAL " in line]
    return {
        "exists": True,
        "modified_at": _iso_mtime(path),
        "recent": lines[-18:],
        "warnings_errors": warnings[-18:],
    }

def _postgres_snapshot(durable) -> dict:
    s = durable.schema
    with durable._connect() as conn, conn.cursor() as cur:
        cur.execute(f"""
            SELECT tr.task_id,tr.title,tr.status,tr.updated_at,tr.effectiveness_note,
                   (SELECT ts.summary FROM {s}.task_summaries ts
                    WHERE ts.task_id=tr.task_id ORDER BY ts.created_at DESC LIMIT 1)
            FROM {s}.task_runs tr ORDER BY tr.updated_at DESC LIMIT 12
        """)
        recent = [
            {
                "task_id": r[0], "title": r[1], "status": r[2],
                "updated_at": r[3].astimezone(NY).isoformat(timespec="seconds"),
                "effectiveness_note": _compact(r[4] or "", 500),
                "latest_summary": _compact(r[5] or "", 900),
            }
            for r in cur.fetchall()
        ]
        cur.execute(f"SELECT task_id,title,updated_at FROM {s}.task_runs WHERE status='running' ORDER BY updated_at")
        running = [
            {"task_id": r[0], "title": r[1], "updated_at": r[2].astimezone(NY).isoformat(timespec="seconds")}
            for r in cur.fetchall()
        ]
        cur.execute(f"""
            SELECT created_at,phase,task_id,note,details
            FROM {s}.runtime_maintenance_notes
            ORDER BY created_at DESC LIMIT 10
        """)
        maintenance = [
            {
                "created_at": r[0].astimezone(NY).isoformat(timespec="seconds"),
                "phase": r[1], "task_id": r[2], "note": _compact(r[3] or "", 600),
                "details": r[4] or {},
            }
            for r in cur.fetchall()
        ]
    return {"running_tasks": running, "recent_tasks": recent, "maintenance": maintenance}


def _redis_snapshot(config: dict, prompt_queue) -> dict:
    queue = prompt_queue.stats()
    queue.update({
        "retry_count": int(prompt_queue.r.xlen(prompt_queue.retry_stream)),
        "escalation_count": int(prompt_queue.r.xlen(prompt_queue.escalation_stream)),
        "dead_letter_count": int(prompt_queue.r.xlen(prompt_queue.dead_letter_stream)),
    })
    cfg = config.get("redis", {})
    client = redis.Redis(
        host=cfg.get("host", "127.0.0.1"), port=int(cfg.get("port", 6379)),
        db=int(cfg.get("db", 0)), decode_responses=True,
    )
    keys = list(client.scan_iter(match="norm:*", count=200))
    return {"live_db_size": int(client.dbsize()), "live_norm_keys": len(keys), "queue": queue}

def _tail_text(path: Path, lines: int = 80, limit: int = 9000) -> str:
    if not path.is_file():
        return ""
    selected = path.read_text(encoding="utf-8-sig", errors="replace").splitlines()[-lines:]
    text = "\n".join(selected).strip()
    return text[-limit:]


def _format_task(task: dict) -> str:
    suffix = f" — {task.get('latest_summary')}" if task.get("latest_summary") else ""
    return f"- {task.get('updated_at')} | {task.get('status')} | {task.get('title')} | {task.get('task_id')}{suffix}"


def _doc_lines(doc: dict) -> list[str]:
    if not doc.get("exists"):
        return [f"- Missing: {doc.get('path')}"]
    lines = [f"- `{Path(doc['path']).name}` modified {doc['modified_at']} sha256 `{doc['sha256'][:12]}…`"]
    for section in doc.get("sections", []):
        if section.get("text"):
            lines.append(f"  - **{section['section']}**: {section['text']}")
    return lines


def _build_markdown(snapshot: dict) -> str:
    busy = snapshot["busy"]
    pg = snapshot["postgres"]
    rd = snapshot["redis"]
    unfinished = snapshot["unfinished"]
    lines = ["# Norm live handoff", "", f"Generated: {snapshot['generated_at']}"]
    lines += ["", "## Live state", f"- Busy: **{busy.get('busy')}**; phase: `{busy.get('phase')}`; confidence: {busy.get('confidence')}"]
    lines.append(f"- Redis: work={rd['queue'].get('stream_length', 0)}, pending={rd['queue'].get('pending_count', 0)}, retry={rd['queue'].get('retry_count', 0)}, escalation={rd['queue'].get('escalation_count', 0)}")
    lines.append(f"- PostgreSQL running tasks: {len(pg['running_tasks'])}; source files newer than deployed EXE: {len(unfinished['source_newer_than_exe'])}")
    runtime = snapshot.get("runtime") or {}
    lines.append(f"- Deployed EXE: `{runtime.get('exe')}`; modified {runtime.get('exe_modified_at')}; SHA-256 `{runtime.get('exe_sha256')}`")
    lines.append(f"- SOS present: {unfinished['sos_present']}")
    lines += ["", "## Capabilities and operating structure"]
    for name in ("readme", "current_status", "future_notes"):
        lines.extend(_doc_lines(snapshot["docs"][name]))
    lines += ["", "## Recent finished / unfinished work"]
    if pg["running_tasks"]:
        lines.append("### Running")
        for task in pg["running_tasks"]:
            lines.append(f"- {task['updated_at']} | {task['title']} | {task['task_id']}")
    else:
        lines.append("- No PostgreSQL task is currently marked running.")
    lines.append("### Recent tasks")
    lines.extend(_format_task(task) for task in pg["recent_tasks"][:8])
    if unfinished["source_newer_than_exe"]:
        lines.append("### Possible unpromoted runtime edits")
        for item in unfinished["source_newer_than_exe"][:12]:
            lines.append(f"- {item['modified_at']} | `{item['path']}`")
    lines += ["", "## Recently edited Norm files"]
    for item in snapshot["recent_files"][:18]:
        lines.append(f"- {item['modified_at']} | `{item['path']}` | {item['size']} bytes")
    lines += ["", "## Recent development notes", snapshot["development_tail"] or "(none)"]
    lines += ["", "## Recent runtime warnings/errors"]
    warnings = snapshot["logs"].get("warnings_errors", [])
    lines.extend(f"- {line}" for line in warnings[-12:]) if warnings else lines.append("- None in the sampled log tail.")
    lines += ["", "## Recent maintenance"]
    for item in pg["maintenance"][:8]:
        lines.append(f"- {item['created_at']} | {item['phase']} | {item['note']}")
    return "\n".join(lines)

def _persist_context_handoff(root: Path, markdown: str, generated_at: str) -> Path:
    stamp = generated_at.replace(":", "").replace("-", "").replace("T", "-")[:15]
    directory = root / "docs" / "recovery-notes" / "system-context"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"context-{stamp}.md"
    tmp = path.with_suffix(".md.tmp")
    tmp.write_text(markdown, encoding="utf-8")
    tmp.replace(path)
    old = sorted(directory.glob("context-*.md"), key=lambda p: p.stat().st_mtime, reverse=True)[6:]
    for stale in old:
        stale.unlink(missing_ok=True)
    return path

def _persist_generated_handoff(root: Path, markdown: str, generated_at: str) -> Path:
    stamp = generated_at.replace(":", "").replace("-", "").replace("T", "-")[:15]
    directory = root / "docs" / "recovery-notes" / "system-context"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"generated-{stamp}.md"
    tmp = path.with_suffix(".md.tmp")
    tmp.write_text(markdown, encoding="utf-8")
    tmp.replace(path)
    old = sorted(directory.glob("generated-*.md"), key=lambda p: p.stat().st_mtime, reverse=True)[6:]
    for stale in old:
        stale.unlink(missing_ok=True)
    return path


def _provenance_footer() -> str:
    return (
        "\n\n---\n### Sources and AI notice\n"
        "This file was populated from `README.md`, the maintained documentation paths configured in `config/settings.ini`, "
        "recent Norm files/logs, live Redis queue state, "
        "and PostgreSQL durable memory/task state. "
        "AI-generated summaries or interpretations may contain mistakes; verify important details "
        "against the cited files and live system state.\n"
    )


def collect_context_snapshot(
    root: Path, *, busy_status, prompt_queue, durable, config: dict,
    summary_generator: Callable[[str, str, str, str], str] | None = None,
) -> dict:
    generated_at = datetime.now(NY).isoformat(timespec="milliseconds")
    document_paths = load_document_paths(root)
    readme = _doc_digest(root / "README.md", ("runtime", "control", "durable", "princip", "tool", "maintenance", "architecture"))
    current = _doc_digest(document_paths["current_status"], ("source", "configuration", "queue", "princip", "file", "persistence", "limitations"))
    future = _doc_digest(document_paths["future_implementation_notes"], ("coordinator", "memory", "future", "retrieval", "backlog"), section_chars=900)
    postgres = _postgres_snapshot(durable)
    redis_state = _redis_snapshot(config, prompt_queue)
    snapshot = {
        "status": "ok",
        "generated_at": generated_at,
        "busy": busy_status(),
        "docs": {"readme": readme, "current_status": current, "future_notes": future},
        "development_tail": _tail_text(document_paths["development_notes"]),
        "recent_files": _recent_files(root),
        "logs": _log_snapshot(root),
        "redis": redis_state,
        "postgres": postgres,
        "unfinished": {
            "sos_present": any((root / name).exists() for name in ("SOS.readme", "SOS.md")),
            "source_newer_than_exe": _source_newer_than_exe(root),
        },
        "runtime": {
            "exe": str(root / "app" / "norm.exe"),
            "exe_sha256": _sha256(root / "app" / "norm.exe") if (root / "app" / "norm.exe").is_file() else None,
            "exe_modified_at": _iso_mtime(root / "app" / "norm.exe") if (root / "app" / "norm.exe").is_file() else None,
        },
    }
    full_handoff = _build_markdown(snapshot).rstrip() + _provenance_footer()
    raw_path = _persist_context_handoff(root, full_handoff, generated_at)
    raw_hash = _sha256(raw_path)
    served_markdown = full_handoff
    served_path = raw_path
    generation_error = None
    if summary_generator is not None:
        try:
            generated = str(summary_generator(full_handoff, str(raw_path), raw_hash, generated_at) or "").strip()
            if generated.startswith("```markdown"):
                generated = generated[len("```markdown"):].lstrip("\r\n")
            elif generated.startswith("```"):
                generated = generated[3:].lstrip("\r\n")
            if generated.endswith("```"):
                generated = generated[:-3].rstrip()
            if generated:
                generated += (
                    f"\n\n---\nRaw evidence snapshot: `{raw_path}`\n"
                    f"SHA-256: `{raw_hash}`\nGenerated: {generated_at}\n"
                )
                generated = generated.rstrip() + _provenance_footer()
                served_path = _persist_generated_handoff(root, generated, generated_at)
                served_markdown = generated
        except Exception as exc:
            generation_error = f"{type(exc).__name__}: {exc}"

    served_hash = _sha256(served_path)
    estimated_chars = len(json.dumps(snapshot, ensure_ascii=False, default=str)) + len(served_markdown)
    inline_overflow = len(served_markdown) > 24000 or estimated_chars > 56000
    snapshot["overflowed"] = inline_overflow
    snapshot["raw_snapshot"] = {"path": str(raw_path), "sha256": raw_hash, "modified_at": _iso_mtime(raw_path)}
    snapshot["handoff_pointer"] = {"path": str(served_path), "sha256": served_hash, "modified_at": _iso_mtime(served_path)}
    snapshot["generation_error"] = generation_error
    if inline_overflow:
        snapshot["handoff_markdown"] = (
            "# RECOVERY POINTER HANDOFF\n\n"
            f"Generated: {generated_at}\n"
            f"Full Norm context handoff: {served_path}\n"
            f"SHA-256: {served_hash}\n"
            "The inline JSON handoff exceeded its bounded response budget. The full Markdown file is authoritative.\n"
        )
    else:
        snapshot["handoff_markdown"] = served_markdown
    return snapshot
