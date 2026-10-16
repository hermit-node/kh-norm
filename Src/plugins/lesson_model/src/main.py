from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

PLUGIN_VERSION = "0.3.0a1"
DEFAULT_TIMEZONE = "America/New_York"
_ALLOWED_STATUS = {"candidate", "approved", "rejected", "superseded"}
_SAFE_MODEL_PREFIX = re.compile(r"^[A-Za-z0-9._-]+$")


def _runtime_root() -> Path:
    override = os.environ.get("NORM_RUNTIME_ROOT", "").strip()
    if override:
        return Path(override).expanduser().resolve()
    return Path(__file__).resolve().parents[3]


def _state_root() -> Path:
    override = os.environ.get("NORM_LESSON_MODEL_STATE", "").strip()
    if override:
        root = Path(override).expanduser().resolve()
    else:
        root = _runtime_root() / "state" / "lesson_model"
    root.mkdir(parents=True, exist_ok=True)
    (root / "exports").mkdir(exist_ok=True)
    (root / "builds").mkdir(exist_ok=True)
    return root


def _db_path() -> Path:
    return _state_root() / "lessons.sqlite3"


def _now() -> datetime:
    return datetime.now(ZoneInfo(DEFAULT_TIMEZONE))


def _ts() -> str:
    return _now().isoformat(timespec="seconds")


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _from_json(value: str | None, fallback: Any) -> Any:
    if not value:
        return fallback
    try:
        return json.loads(value)
    except Exception:
        return fallback


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(_db_path(), timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    _init_schema(conn)
    return conn


def _init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS lessons (
            lesson_id TEXT PRIMARY KEY,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            status TEXT NOT NULL,
            category TEXT NOT NULL,
            lesson TEXT NOT NULL,
            lesson_sha256 TEXT NOT NULL,
            rationale TEXT NOT NULL DEFAULT '',
            tags_json TEXT NOT NULL DEFAULT '[]',
            source_refs_json TEXT NOT NULL DEFAULT '[]',
            proposed_by TEXT NOT NULL DEFAULT 'norm',
            approved_by TEXT NOT NULL DEFAULT '',
            rejected_by TEXT NOT NULL DEFAULT '',
            rejection_reason TEXT NOT NULL DEFAULT '',
            training_eligible INTEGER NOT NULL DEFAULT 0,
            superseded_by TEXT NOT NULL DEFAULT '',
            metadata_json TEXT NOT NULL DEFAULT '{}'
        );

        CREATE UNIQUE INDEX IF NOT EXISTS idx_lessons_live_hash
        ON lessons(lesson_sha256, status)
        WHERE status IN ('candidate', 'approved');

        CREATE INDEX IF NOT EXISTS idx_lessons_status
        ON lessons(status, training_eligible, created_at);

        CREATE TABLE IF NOT EXISTS builds (
            build_id TEXT PRIMARY KEY,
            created_at TEXT NOT NULL,
            build_date TEXT NOT NULL,
            base_model TEXT NOT NULL,
            candidate_tag TEXT NOT NULL,
            promotion_tag TEXT NOT NULL,
            status TEXT NOT NULL,
            dataset_path TEXT NOT NULL,
            dataset_sha256 TEXT NOT NULL,
            lesson_count INTEGER NOT NULL,
            lesson_ids_json TEXT NOT NULL,
            notes TEXT NOT NULL DEFAULT '',
            backend TEXT NOT NULL DEFAULT 'not_configured',
            auto_promote INTEGER NOT NULL DEFAULT 0,
            metadata_json TEXT NOT NULL DEFAULT '{}'
        );
        """
    )
    conn.commit()


def _lesson_hash(text: str) -> str:
    normalized = " ".join(str(text or "").strip().split())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _lesson_row(row: sqlite3.Row) -> dict:
    return {
        "lesson_id": row["lesson_id"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "status": row["status"],
        "category": row["category"],
        "lesson": row["lesson"],
        "rationale": row["rationale"],
        "tags": _from_json(row["tags_json"], []),
        "source_refs": _from_json(row["source_refs_json"], []),
        "proposed_by": row["proposed_by"],
        "approved_by": row["approved_by"],
        "rejected_by": row["rejected_by"],
        "rejection_reason": row["rejection_reason"],
        "training_eligible": bool(row["training_eligible"]),
        "superseded_by": row["superseded_by"],
        "metadata": _from_json(row["metadata_json"], {}),
    }


def _clean_list(values: list[str] | None, *, max_items: int = 64, max_len: int = 512) -> list[str]:
    result: list[str] = []
    for item in values or []:
        text = str(item or "").strip()
        if not text or text in result:
            continue
        result.append(text[:max_len])
        if len(result) >= max_items:
            break
    return result


def propose_lesson(
    lesson: str,
    category: str = "general",
    tags: list[str] | None = None,
    source_refs: list[str] | None = None,
    rationale: str = "",
    proposed_by: str = "norm",
    metadata: dict | None = None,
) -> dict:
    """Create a reviewable lesson candidate. This never approves or trains it."""
    lesson_text = str(lesson or "").strip()
    if len(lesson_text) < 12:
        raise ValueError("lesson must contain at least 12 non-whitespace characters")
    category_text = str(category or "general").strip()[:80] or "general"
    digest = _lesson_hash(lesson_text)
    with _connect() as conn:
        existing = conn.execute(
            "SELECT * FROM lessons WHERE lesson_sha256=? AND status IN ('candidate','approved') "
            "ORDER BY created_at DESC LIMIT 1",
            (digest,),
        ).fetchone()
        if existing:
            payload = _lesson_row(existing)
            payload.update({
                "ok": True,
                "created": False,
                "duplicate": True,
                "requires_user_approval": payload["status"] == "candidate",
            })
            return payload

        lesson_id = f"L-{_now():%Y%m%d}-{uuid.uuid4().hex[:8]}"
        stamp = _ts()
        conn.execute(
            """
            INSERT INTO lessons(
                lesson_id, created_at, updated_at, status, category, lesson,
                lesson_sha256, rationale, tags_json, source_refs_json, proposed_by,
                training_eligible, metadata_json
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                lesson_id, stamp, stamp, "candidate", category_text, lesson_text,
                digest, str(rationale or "").strip()[:4000],
                _json(_clean_list(tags)), _json(_clean_list(source_refs)),
                str(proposed_by or "norm").strip()[:80] or "norm",
                0, _json(dict(metadata or {})),
            ),
        )
        conn.commit()

    return {
        "ok": True,
        "created": True,
        "duplicate": False,
        "lesson_id": lesson_id,
        "status": "candidate",
        "category": category_text,
        "lesson": lesson_text,
        "tags": _clean_list(tags),
        "source_refs": _clean_list(source_refs),
        "requires_user_approval": True,
        "approval_prompt": "Tag this as a lesson? Approve, edit, or reject this candidate.",
    }


def approve_lesson(
    lesson_id: str,
    edited_lesson: str = "",
    training_eligible: bool = True,
    approved_by: str = "user",
) -> dict:
    """Approve a candidate after explicit operator/user consent."""
    key = str(lesson_id or "").strip()
    with _connect() as conn:
        row = conn.execute("SELECT * FROM lessons WHERE lesson_id=?", (key,)).fetchone()
        if not row:
            raise KeyError(f"unknown lesson_id: {key}")
        if row["status"] != "candidate":
            raise ValueError(f"lesson must be candidate to approve; current status={row['status']}")
        text = str(edited_lesson or "").strip() or row["lesson"]
        if len(text) < 12:
            raise ValueError("approved lesson must contain at least 12 non-whitespace characters")
        digest = _lesson_hash(text)
        duplicate = conn.execute(
            "SELECT lesson_id FROM lessons WHERE lesson_sha256=? AND status='approved' AND lesson_id<>?",
            (digest, key),
        ).fetchone()
        if duplicate:
            raise ValueError(f"equivalent approved lesson already exists: {duplicate['lesson_id']}")
        stamp = _ts()
        conn.execute(
            """
            UPDATE lessons
            SET status='approved', lesson=?, lesson_sha256=?, updated_at=?,
                approved_by=?, training_eligible=?, rejected_by='',
                rejection_reason=''
            WHERE lesson_id=?
            """,
            (
                text, digest, stamp,
                str(approved_by or "user").strip()[:80] or "user",
                1 if training_eligible else 0,
                key,
            ),
        )
        conn.commit()
        out = conn.execute("SELECT * FROM lessons WHERE lesson_id=?", (key,)).fetchone()
    payload = _lesson_row(out)
    payload.update({"ok": True, "requires_user_approval": False})
    return payload


def reject_lesson(lesson_id: str, reason: str = "", rejected_by: str = "user") -> dict:
    """Reject a lesson candidate; rejected lessons are excluded from training."""
    key = str(lesson_id or "").strip()
    with _connect() as conn:
        row = conn.execute("SELECT * FROM lessons WHERE lesson_id=?", (key,)).fetchone()
        if not row:
            raise KeyError(f"unknown lesson_id: {key}")
        if row["status"] != "candidate":
            raise ValueError(f"lesson must be candidate to reject; current status={row['status']}")
        conn.execute(
            """
            UPDATE lessons SET status='rejected', updated_at=?, training_eligible=0,
                rejected_by=?, rejection_reason=?
            WHERE lesson_id=?
            """,
            (
                _ts(),
                str(rejected_by or "user").strip()[:80] or "user",
                str(reason or "").strip()[:2000],
                key,
            ),
        )
        conn.commit()
        out = conn.execute("SELECT * FROM lessons WHERE lesson_id=?", (key,)).fetchone()
    payload = _lesson_row(out)
    payload.update({"ok": True})
    return payload


def set_training_eligibility(lesson_id: str, eligible: bool) -> dict:
    """Toggle training eligibility for an already approved lesson."""
    key = str(lesson_id or "").strip()
    with _connect() as conn:
        row = conn.execute("SELECT * FROM lessons WHERE lesson_id=?", (key,)).fetchone()
        if not row:
            raise KeyError(f"unknown lesson_id: {key}")
        if row["status"] != "approved":
            raise ValueError("only approved lessons can be training eligible")
        conn.execute(
            "UPDATE lessons SET training_eligible=?, updated_at=? WHERE lesson_id=?",
            (1 if eligible else 0, _ts(), key),
        )
        conn.commit()
        out = conn.execute("SELECT * FROM lessons WHERE lesson_id=?", (key,)).fetchone()
    payload = _lesson_row(out)
    payload.update({"ok": True})
    return payload


def supersede_lesson(lesson_id: str, superseded_by: str = "", reason: str = "") -> dict:
    """Retire an approved lesson without deleting provenance."""
    key = str(lesson_id or "").strip()
    replacement = str(superseded_by or "").strip()
    with _connect() as conn:
        row = conn.execute("SELECT * FROM lessons WHERE lesson_id=?", (key,)).fetchone()
        if not row:
            raise KeyError(f"unknown lesson_id: {key}")
        if row["status"] not in {"candidate", "approved"}:
            raise ValueError(f"cannot supersede lesson with status={row['status']}")
        if replacement:
            target = conn.execute("SELECT * FROM lessons WHERE lesson_id=?", (replacement,)).fetchone()
            if not target:
                raise KeyError(f"unknown superseding lesson: {replacement}")
            if target["status"] != "approved":
                raise ValueError("superseding lesson must already be approved")
        meta = _from_json(row["metadata_json"], {})
        if reason:
            meta["supersede_reason"] = str(reason)[:2000]
        conn.execute(
            """
            UPDATE lessons SET status='superseded', updated_at=?,
                training_eligible=0, superseded_by=?, metadata_json=?
            WHERE lesson_id=?
            """,
            (_ts(), replacement, _json(meta), key),
        )
        conn.commit()
        out = conn.execute("SELECT * FROM lessons WHERE lesson_id=?", (key,)).fetchone()
    payload = _lesson_row(out)
    payload.update({"ok": True})
    return payload


def get_lesson(lesson_id: str) -> dict:
    """Return one lesson with provenance and review state."""
    key = str(lesson_id or "").strip()
    with _connect() as conn:
        row = conn.execute("SELECT * FROM lessons WHERE lesson_id=?", (key,)).fetchone()
    if not row:
        raise KeyError(f"unknown lesson_id: {key}")
    payload = _lesson_row(row)
    payload.update({"ok": True})
    return payload


def list_lessons(status: str = "", category: str = "", limit: int = 100) -> dict:
    """List bounded lesson metadata for review."""
    clauses: list[str] = []
    params: list[Any] = []
    status_text = str(status or "").strip().lower()
    if status_text:
        if status_text not in _ALLOWED_STATUS:
            raise ValueError(f"status must be one of: {', '.join(sorted(_ALLOWED_STATUS))}")
        clauses.append("status=?")
        params.append(status_text)
    category_text = str(category or "").strip()
    if category_text:
        clauses.append("category=?")
        params.append(category_text)
    sql = "SELECT * FROM lessons"
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY created_at DESC LIMIT ?"
    params.append(max(1, min(int(limit), 500)))
    with _connect() as conn:
        rows = conn.execute(sql, params).fetchall()
        counts = {
            row["status"]: row["n"]
            for row in conn.execute(
                "SELECT status, COUNT(*) AS n FROM lessons GROUP BY status"
            ).fetchall()
        }
    return {
        "ok": True,
        "lessons": [_lesson_row(row) for row in rows],
        "returned": len(rows),
        "counts": counts,
    }


def export_training_corpus(export_name: str = "") -> dict:
    """Export approved+eligible lessons as canonical JSONL; no model training occurs."""
    requested = str(export_name or "").strip()
    if requested:
        if not re.fullmatch(r"[A-Za-z0-9._-]{1,80}", requested):
            raise ValueError("export_name may contain only letters, numbers, dot, underscore, hyphen")
        name = requested
        if not name.endswith(".jsonl"):
            name += ".jsonl"
    else:
        name = f"lessons-{_now():%Y-%m-%d-%H%M%S}.jsonl"

    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT * FROM lessons
            WHERE status='approved' AND training_eligible=1
            ORDER BY created_at ASC, lesson_id ASC
            """
        ).fetchall()

    path = _state_root() / "exports" / name
    h = hashlib.sha256()
    with path.open("wb") as handle:
        for row in rows:
            item = _lesson_row(row)
            record = {
                "schema": "norm.lesson.v1",
                "lesson_id": item["lesson_id"],
                "category": item["category"],
                "lesson": item["lesson"],
                "rationale": item["rationale"],
                "tags": item["tags"],
                "source_refs": item["source_refs"],
                "approved_by": item["approved_by"],
                "created_at": item["created_at"],
                "metadata": item["metadata"],
            }
            line = (_json(record) + "\n").encode("utf-8")
            handle.write(line)
            h.update(line)

    return {
        "ok": True,
        "path": str(path),
        "sha256": h.hexdigest(),
        "lesson_count": len(rows),
        "format": "norm.lesson.v1/jsonl",
        "trained": False,
    }


def _next_model_tag(conn: sqlite3.Connection, model_prefix: str, build_date: str) -> str:
    base = f"{model_prefix}:{build_date}"
    existing = {
        row["candidate_tag"]
        for row in conn.execute(
            "SELECT candidate_tag FROM builds WHERE build_date=? AND candidate_tag LIKE ?",
            (build_date, base + "%"),
        ).fetchall()
    }
    if base not in existing:
        return base
    index = 2
    while f"{base}.{index}" in existing:
        index += 1
    return f"{base}.{index}"


def prepare_model_build(
    base_model: str,
    model_prefix: str = "norm",
    notes: str = "",
    minimum_lessons: int = 1,
) -> dict:
    """Freeze an approved lesson set into a dated build manifest. Does not train or promote."""
    base = str(base_model or "").strip()
    if not base:
        raise ValueError("base_model is required")
    prefix = str(model_prefix or "norm").strip()
    if not _SAFE_MODEL_PREFIX.fullmatch(prefix):
        raise ValueError("model_prefix may contain only letters, numbers, dot, underscore, hyphen")
    minimum = max(1, int(minimum_lessons))
    exported = export_training_corpus()
    if int(exported["lesson_count"]) < minimum:
        try:
            Path(exported["path"]).unlink(missing_ok=True)
        finally:
            raise ValueError(
                f"need at least {minimum} approved training-eligible lessons; "
                f"found {exported['lesson_count']}"
            )

    build_date = _now().date().isoformat()
    with _connect() as conn:
        candidate_tag = _next_model_tag(conn, prefix, build_date)
        rows = conn.execute(
            """
            SELECT lesson_id FROM lessons
            WHERE status='approved' AND training_eligible=1
            ORDER BY created_at ASC, lesson_id ASC
            """
        ).fetchall()
        lesson_ids = [row["lesson_id"] for row in rows]
        build_id = f"B-{_now():%Y%m%d}-{uuid.uuid4().hex[:8]}"
        manifest = {
            "schema": "norm.model_build.v1",
            "build_id": build_id,
            "created_at": _ts(),
            "build_date": build_date,
            "plugin_version": PLUGIN_VERSION,
            "base_model": base,
            "candidate_tag": candidate_tag,
            "promotion_tag": f"{prefix}:latest",
            "status": "prepared",
            "lesson_count": len(lesson_ids),
            "lesson_ids": lesson_ids,
            "dataset_path": exported["path"],
            "dataset_sha256": exported["sha256"],
            "training_backend": "not_configured",
            "training_enabled": False,
            "auto_promote": False,
            "promotion_policy": "external regression-test gate required",
            "notes": str(notes or "").strip(),
        }
        manifest_path = _state_root() / "builds" / f"{build_id}.json"
        manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        conn.execute(
            """
            INSERT INTO builds(
                build_id, created_at, build_date, base_model, candidate_tag,
                promotion_tag, status, dataset_path, dataset_sha256, lesson_count,
                lesson_ids_json, notes, backend, auto_promote, metadata_json
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                build_id, manifest["created_at"], build_date, base, candidate_tag,
                manifest["promotion_tag"], "prepared", exported["path"],
                exported["sha256"], len(lesson_ids), _json(lesson_ids),
                manifest["notes"], "not_configured", 0,
                _json({"manifest_path": str(manifest_path)}),
            ),
        )
        conn.commit()

    return {
        "ok": True,
        "build_id": build_id,
        "candidate_tag": candidate_tag,
        "promotion_tag": f"{prefix}:latest",
        "manifest_path": str(manifest_path),
        "dataset_path": exported["path"],
        "dataset_sha256": exported["sha256"],
        "lesson_count": len(lesson_ids),
        "status": "prepared",
        "training_enabled": False,
        "auto_promote": False,
        "next_step": "Attach a reviewed LoRA/QLoRA backend, train candidate_tag, run regression gates, then explicitly promote to promotion_tag.",
    }


def list_model_builds(limit: int = 50) -> dict:
    """List prepared model-build manifests."""
    cap = max(1, min(int(limit), 200))
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM builds ORDER BY created_at DESC LIMIT ?", (cap,)
        ).fetchall()
    builds = []
    for row in rows:
        builds.append({
            "build_id": row["build_id"],
            "created_at": row["created_at"],
            "build_date": row["build_date"],
            "base_model": row["base_model"],
            "candidate_tag": row["candidate_tag"],
            "promotion_tag": row["promotion_tag"],
            "status": row["status"],
            "dataset_path": row["dataset_path"],
            "dataset_sha256": row["dataset_sha256"],
            "lesson_count": row["lesson_count"],
            "lesson_ids": _from_json(row["lesson_ids_json"], []),
            "notes": row["notes"],
            "backend": row["backend"],
            "auto_promote": bool(row["auto_promote"]),
            "metadata": _from_json(row["metadata_json"], {}),
        })
    return {"ok": True, "builds": builds, "returned": len(builds)}


def lesson_model_status() -> dict:
    """Report lesson-registry and training-backend status."""
    with _connect() as conn:
        counts = {
            row["status"]: row["n"]
            for row in conn.execute(
                "SELECT status, COUNT(*) AS n FROM lessons GROUP BY status"
            ).fetchall()
        }
        eligible = conn.execute(
            "SELECT COUNT(*) AS n FROM lessons WHERE status='approved' AND training_eligible=1"
        ).fetchone()["n"]
        build_count = conn.execute("SELECT COUNT(*) AS n FROM builds").fetchone()["n"]
    return {
        "ok": True,
        "plugin_version": PLUGIN_VERSION,
        "state_root": str(_state_root()),
        "lesson_counts": counts,
        "training_eligible_lessons": int(eligible),
        "build_count": int(build_count),
        "training_backend": "not_configured",
        "training_enabled": False,
        "auto_promote": False,
        "approval_required": True,
    }


# ---------------------------------------------------------------------------
# Code-memory / project reconstruction subsystem
# ---------------------------------------------------------------------------

def _code_memory_module():
    import sys
    src_dir = str(Path(__file__).resolve().parent)
    if src_dir not in sys.path:
        sys.path.insert(0, src_dir)
    import _code_memory
    return _code_memory


def code_memory_scan_zip(
    path: str,
    project_name: str = "",
    version_label: str = "",
    max_files: int = 10000,
) -> dict:
    """Inventory a source ZIP and build a runtime-prioritized, non-extracting scan plan."""
    return _code_memory_module().scan_zip(
        path, project_name=project_name, version_label=version_label, max_files=max_files
    )


def code_memory_next(scan_id: str, cursor: str = "", max_chars: int = 20000) -> dict:
    """Return exactly one bounded source chunk in runtime-priority order."""
    return _code_memory_module().next_chunk(scan_id, cursor=cursor, max_chars=max_chars)


def code_memory_save_analysis(
    scan_id: str,
    raw_summary: dict,
    analyzed_by_model: str = "",
    completeness: str = "",
    intent: str = "",
    suggestions: list[dict] | None = None,
    uncertainties: list[str] | None = None,
) -> dict:
    """Save Norm's dense structured project understanding locally before PostgreSQL persistence."""
    return _code_memory_module().save_analysis(
        scan_id,
        raw_summary=raw_summary,
        analyzed_by_model=analyzed_by_model,
        completeness=completeness,
        intent=intent,
        suggestions=suggestions,
        uncertainties=uncertainties,
    )


def code_memory_persist_postgres(scan_id: str) -> dict:
    """Persist the raw structured project/code memory to Norm's canonical PostgreSQL runtime schema."""
    module = _code_memory_module()
    scan = module._load_scan(scan_id)
    import sys
    src_dir = str(Path(__file__).resolve().parent)
    if src_dir not in sys.path:
        sys.path.insert(0, src_dir)
    import _pg_memory
    result = _pg_memory.persist_snapshot(scan, PLUGIN_VERSION)
    scan["postgres"] = {
        "persisted": True,
        "persisted_at": _ts(),
        "schema": result["schema"],
        "table": result["table"],
        "snapshot_id": result["snapshot_id"],
    }
    module._save_scan(scan)
    return result


def code_memory_write_readme(scan_id: str, markdown: str, output_path: str = "") -> dict:
    """Write the polished human-facing README separately from raw PostgreSQL memory."""
    return _code_memory_module().write_readme(scan_id, markdown, output_path=output_path)


def code_memory_get(scan_id: str, include_files: bool = False) -> dict:
    """Retrieve one local project scan/memory snapshot."""
    return _code_memory_module().get_scan(scan_id, include_files=include_files)


def code_memory_list(project_name: str = "", limit: int = 50) -> dict:
    """List local code-memory snapshots, including version labels and PostgreSQL status."""
    return _code_memory_module().list_scans(project_name=project_name, limit=limit)


# ---------------------------------------------------------------------------
# Executable architecture / capability topology map
# ---------------------------------------------------------------------------

def _code_graph_module():
    import sys
    src_dir = str(Path(__file__).resolve().parent)
    if src_dir not in sys.path:
        sys.path.insert(0, src_dir)
    import _code_graph
    return _code_graph


def code_map_build(scan_id: str, max_nodes: int = 5000, output_dir: str = "") -> dict:
    """Build canonical CodeGraph JSON plus a standalone interactive HTML topology map."""
    memory = _code_memory_module()
    scan = memory._load_scan(scan_id)
    graph_mod = _code_graph_module()
    graph = graph_mod.build_graph(scan, max_nodes=max_nodes, include_external=True)
    result = graph_mod.write_graph_files(scan, graph, output_dir=output_dir)
    scan["code_graph"] = {
        **(scan.get("code_graph") or {}),
        "schema": graph.get("schema"),
        "graph_path": result["graph_path"],
        "graph_sha256": result["graph_sha256"],
        "html_path": result["html_path"],
        "html_sha256": result["html_sha256"],
        "stats": result["stats"],
        "parse_errors": result["parse_errors"],
    }
    memory._save_scan(scan)
    return {"ok": True, "scan_id": scan_id, **result}


def code_map_save_semantics(scan_id: str, semantics: dict, rebuild: bool = True, output_dir: str = "") -> dict:
    """Attach Norm's evidence-backed semantic clusters/data meanings, optionally rebuilding the map."""
    if not isinstance(semantics, dict):
        raise ValueError("semantics must be an object")
    memory = _code_memory_module()
    scan = memory._load_scan(scan_id)
    cg = dict(scan.get("code_graph") or {})
    cg["semantics"] = semantics
    scan["code_graph"] = cg
    memory._save_scan(scan)
    if rebuild:
        return code_map_build(scan_id, output_dir=output_dir)
    return {
        "ok": True,
        "scan_id": scan_id,
        "semantic_node_annotations": len(semantics.get("nodes") or {}) if isinstance(semantics.get("nodes"), dict) else 0,
        "semantic_edge_annotations": len(semantics.get("edges") or {}) if isinstance(semantics.get("edges"), dict) else 0,
        "semantic_cluster_annotations": len(semantics.get("clusters") or {}) if isinstance(semantics.get("clusters"), dict) else 0,
        "extra_edges": len(semantics.get("extra_edges") or []) if isinstance(semantics.get("extra_edges"), list) else 0,
    }


def code_map_get(scan_id: str) -> dict:
    """Return the current topology artifact metadata for a scan."""
    scan = _code_memory_module()._load_scan(scan_id)
    return {"ok": True, "scan_id": scan_id, "code_graph": scan.get("code_graph") or {}}
