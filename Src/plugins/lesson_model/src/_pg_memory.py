from __future__ import annotations

import importlib
import json
import os
import sys
from pathlib import Path


def _runtime_root() -> Path:
    override = os.environ.get("NORM_RUNTIME_ROOT", "").strip()
    if override:
        return Path(override).expanduser().resolve()
    return Path(__file__).resolve().parents[3]


def _bootstrap():
    root = _runtime_root()
    core = root / "core"
    core_text = str(core)
    if core_text not in sys.path:
        sys.path.insert(0, core_text)
    return importlib.import_module("runtime_bootstrap")


def _schema() -> str:
    cfg = _bootstrap().load_config(_runtime_root())
    schema = str((cfg.get("postgres") or {}).get("schema") or "norm_runtime").strip()
    if not schema:
        raise ValueError("configured PostgreSQL runtime schema is blank")
    return schema


def _pool():
    return _bootstrap().build_postgres_pool(_runtime_root())


def ensure_table() -> None:
    schema = _schema()
    ddl = f'''
    CREATE TABLE IF NOT EXISTS "{schema}"."code_memory_snapshots" (
        snapshot_id text PRIMARY KEY,
        created_at timestamptz NOT NULL,
        project_name text NOT NULL,
        version_label text NOT NULL DEFAULT '',
        source_kind text NOT NULL,
        source_name text NOT NULL,
        source_path text NOT NULL,
        source_sha256 text NOT NULL,
        manifest_file_count integer NOT NULL,
        manifest jsonb NOT NULL,
        raw_summary jsonb NOT NULL,
        analysis_meta jsonb NOT NULL,
        readme_meta jsonb,
        model_name text NOT NULL DEFAULT '',
        completeness text NOT NULL DEFAULT '',
        intent text NOT NULL DEFAULT '',
        supersedes_snapshot_id text NOT NULL DEFAULT '',
        plugin_version text NOT NULL,
        UNIQUE(project_name, version_label, source_sha256)
    );
    CREATE INDEX IF NOT EXISTS code_memory_project_version_idx
      ON "{schema}"."code_memory_snapshots"(project_name, version_label, created_at DESC);
    '''
    with _pool().connection("norm", timeout=30.0) as conn:
        with conn.transaction():
            with conn.cursor() as cur:
                cur.execute(ddl)


def persist_snapshot(scan: dict, plugin_version: str) -> dict:
    analysis = scan.get("analysis") or {}
    raw_summary = analysis.get("raw_summary")
    if not isinstance(raw_summary, dict) or not raw_summary:
        raise ValueError("scan has no saved raw analysis to persist")
    ensure_table()
    schema = _schema()
    summary_manifest = {
        "schema": scan.get("schema"),
        "scan_id": scan.get("scan_id"),
        "source_size_bytes": scan.get("source_size_bytes"),
        "retained_source_path": scan.get("source_path"),
        "original_source_path": scan.get("original_source_path"),
        "source_retained_wholesale": bool(scan.get("source_retained_wholesale")),
        "archive_total_uncompressed_bytes": scan.get("archive_total_uncompressed_bytes"),
        "manifest_truncated": scan.get("manifest_truncated"),
        "ignored_generated_or_vendor_count": scan.get("ignored_generated_or_vendor_count"),
        "unsafe_member_count": scan.get("unsafe_member_count"),
        "runtime_order": scan.get("runtime_order") or [],
        "likely_entrypoints": scan.get("likely_entrypoints") or [],
        "files": scan.get("files") or [],
        "code_graph": scan.get("code_graph") or {},
    }
    readme_meta = scan.get("readme")
    sql = f'''
    INSERT INTO "{schema}"."code_memory_snapshots"(
        snapshot_id, created_at, project_name, version_label, source_kind,
        source_name, source_path, source_sha256, manifest_file_count, manifest,
        raw_summary, analysis_meta, readme_meta, model_name, completeness,
        intent, supersedes_snapshot_id, plugin_version
    ) VALUES(
        %(snapshot_id)s, %(created_at)s::timestamptz, %(project_name)s, %(version_label)s,
        %(source_kind)s, %(source_name)s, %(source_path)s, %(source_sha256)s,
        %(manifest_file_count)s, %(manifest)s::jsonb, %(raw_summary)s::jsonb,
        %(analysis_meta)s::jsonb, %(readme_meta)s::jsonb, %(model_name)s,
        %(completeness)s, %(intent)s, %(supersedes)s, %(plugin_version)s
    )
    ON CONFLICT(project_name, version_label, source_sha256)
    DO UPDATE SET
        created_at=EXCLUDED.created_at,
        manifest=EXCLUDED.manifest,
        raw_summary=EXCLUDED.raw_summary,
        analysis_meta=EXCLUDED.analysis_meta,
        readme_meta=EXCLUDED.readme_meta,
        model_name=EXCLUDED.model_name,
        completeness=EXCLUDED.completeness,
        intent=EXCLUDED.intent,
        plugin_version=EXCLUDED.plugin_version
    RETURNING snapshot_id;
    '''
    params = {
        "snapshot_id": scan["scan_id"],
        "created_at": scan["created_at"],
        "project_name": scan["project_name"],
        "version_label": scan.get("version_label") or "",
        "source_kind": scan.get("source_kind") or "zip",
        "source_name": scan.get("source_name") or "",
        "source_path": scan.get("source_path") or "",
        "source_sha256": scan.get("source_sha256") or "",
        "manifest_file_count": int(scan.get("manifest_file_count") or 0),
        "manifest": json.dumps(summary_manifest, ensure_ascii=False),
        "raw_summary": json.dumps(raw_summary, ensure_ascii=False),
        "analysis_meta": json.dumps({k: v for k, v in analysis.items() if k != "raw_summary"}, ensure_ascii=False),
        "readme_meta": json.dumps(readme_meta, ensure_ascii=False) if readme_meta else None,
        "model_name": str(analysis.get("analyzed_by_model") or ""),
        "completeness": str(analysis.get("completeness") or ""),
        "intent": str(analysis.get("intent") or ""),
        "supersedes": str((analysis.get("raw_summary") or {}).get("supersedes_snapshot_id") or ""),
        "plugin_version": plugin_version,
    }
    with _pool().connection("norm", timeout=30.0) as conn:
        with conn.transaction():
            with conn.cursor() as cur:
                cur.execute(sql, params)
                row = cur.fetchone()
    return {
        "ok": True,
        "snapshot_id": row[0] if row else scan["scan_id"],
        "schema": schema,
        "table": "code_memory_snapshots",
        "project_name": scan["project_name"],
        "version_label": scan.get("version_label") or "",
        "source_sha256": scan.get("source_sha256") or "",
    }
