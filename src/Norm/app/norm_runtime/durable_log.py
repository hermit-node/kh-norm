from __future__ import annotations

import json
import re
import subprocess
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator

import psycopg
from psycopg import sql
from psycopg.types.json import Jsonb

from .models import StepResult, TaskPlan
from .resource_status import merge_resource_status

_SCHEMA_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class PostgresTaskLog:
    def __init__(self, conninfo: str, schema: str = "norm_runtime") -> None:
        if not conninfo:
            raise ValueError("An explicit PostgreSQL connection string is required")
        if not _SCHEMA_RE.fullmatch(schema):
            raise ValueError("Unsafe PostgreSQL schema name")
        self.conninfo = conninfo
        self.schema = schema

    @contextmanager
    def _connect(self) -> Iterator[psycopg.Connection]:
        with psycopg.connect(self.conninfo) as conn:
            yield conn

    def ensure_schema(self) -> None:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(sql.SQL("CREATE SCHEMA IF NOT EXISTS {}").format(sql.Identifier(self.schema)))
            cur.execute(sql.SQL("""
                CREATE TABLE IF NOT EXISTS {}.task_runs (
                    task_id text PRIMARY KEY,
                    title text NOT NULL,
                    status text NOT NULL,
                    plan jsonb NOT NULL,
                    started_at timestamptz NOT NULL DEFAULT now(),
                    updated_at timestamptz NOT NULL DEFAULT now()
                )
            """).format(sql.Identifier(self.schema)))
            cur.execute(sql.SQL("ALTER TABLE {}.task_runs ADD COLUMN IF NOT EXISTS task_uuid uuid").format(sql.Identifier(self.schema)))
            cur.execute(sql.SQL("SELECT task_id FROM {}.task_runs WHERE task_uuid IS NULL").format(sql.Identifier(self.schema)))
            for (legacy_task_id,) in cur.fetchall():
                cur.execute(sql.SQL("UPDATE {}.task_runs SET task_uuid=%s::uuid WHERE task_id=%s").format(sql.Identifier(self.schema)), (str(uuid.uuid4()), legacy_task_id))
            cur.execute(sql.SQL("ALTER TABLE {}.task_runs ALTER COLUMN task_uuid SET DEFAULT gen_random_uuid()").format(sql.Identifier(self.schema)))
            cur.execute(sql.SQL("ALTER TABLE {}.task_runs ALTER COLUMN task_uuid SET NOT NULL").format(sql.Identifier(self.schema)))
            cur.execute(sql.SQL("CREATE UNIQUE INDEX IF NOT EXISTS {} ON {}.task_runs(task_uuid)").format(sql.Identifier(f"idx_{self.schema}_task_runs_uuid"), sql.Identifier(self.schema)))
            cur.execute(sql.SQL("""
                CREATE TABLE IF NOT EXISTS {}.task_nodes (
                    node_id uuid PRIMARY KEY,
                    task_uuid uuid NOT NULL REFERENCES {}.task_runs(task_uuid) ON DELETE CASCADE,
                    task_id text NOT NULL,
                    legacy_step_id text NOT NULL,
                    node_kind text NOT NULL DEFAULT 'step',
                    ordinal integer NOT NULL,
                    name text NOT NULL DEFAULT '',
                    active boolean NOT NULL DEFAULT true,
                    created_at timestamptz NOT NULL DEFAULT now(),
                    UNIQUE(task_id, legacy_step_id)
                )
            """).format(sql.Identifier(self.schema), sql.Identifier(self.schema)))
            cur.execute(sql.SQL("CREATE INDEX IF NOT EXISTS {} ON {}.task_nodes(task_uuid, ordinal)").format(sql.Identifier(f"idx_{self.schema}_task_nodes_task"), sql.Identifier(self.schema)))
            cur.execute(sql.SQL("""
                CREATE TABLE IF NOT EXISTS {}.task_dependency_edges (
                    edge_id uuid PRIMARY KEY,
                    source_task_uuid uuid NOT NULL REFERENCES {}.task_runs(task_uuid) ON DELETE CASCADE,
                    source_node_id uuid REFERENCES {}.task_nodes(node_id) ON DELETE SET NULL,
                    target_task_uuid uuid NOT NULL REFERENCES {}.task_runs(task_uuid) ON DELETE CASCADE,
                    target_node_id uuid REFERENCES {}.task_nodes(node_id) ON DELETE SET NULL,
                    edge_type text NOT NULL,
                    metadata jsonb NOT NULL DEFAULT '{{}}'::jsonb,
                    created_at timestamptz NOT NULL DEFAULT now()
                )
            """).format(sql.Identifier(self.schema), sql.Identifier(self.schema), sql.Identifier(self.schema), sql.Identifier(self.schema), sql.Identifier(self.schema)))
            cur.execute(sql.SQL("CREATE INDEX IF NOT EXISTS {} ON {}.task_dependency_edges(target_task_uuid, edge_type)").format(sql.Identifier(f"idx_{self.schema}_task_edges_target"), sql.Identifier(self.schema)))
            cur.execute(sql.SQL("""
                CREATE TABLE IF NOT EXISTS {}.task_steps (
                    task_id text NOT NULL REFERENCES {}.task_runs(task_id) ON DELETE CASCADE,
                    step_id text NOT NULL,
                    name text NOT NULL,
                    status text NOT NULL,
                    summary text NOT NULL,
                    artifacts jsonb NOT NULL DEFAULT '[]'::jsonb,
                    verification text NOT NULL DEFAULT '',
                    error text,
                    started_at timestamptz NOT NULL,
                    completed_at timestamptz NOT NULL,
                    PRIMARY KEY (task_id, step_id)
                )
            """).format(sql.Identifier(self.schema), sql.Identifier(self.schema)))
            cur.execute(sql.SQL("""
                CREATE TABLE IF NOT EXISTS {}.task_evidence (
                    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                    task_id text NOT NULL REFERENCES {}.task_runs(task_id) ON DELETE CASCADE,
                    step_id text NOT NULL,
                    tool text NOT NULL,
                    arguments jsonb NOT NULL DEFAULT '{{}}'::jsonb,
                    result jsonb NOT NULL DEFAULT '{{}}'::jsonb,
                    deterministic_verification boolean NOT NULL DEFAULT false,
                    created_at timestamptz NOT NULL DEFAULT now()
                )
            """).format(sql.Identifier(self.schema), sql.Identifier(self.schema)))
            cur.execute(sql.SQL("CREATE INDEX IF NOT EXISTS {} ON {}.task_evidence(task_id, step_id, id)").format(
                sql.Identifier(f"idx_{self.schema}_task_evidence_task_step"), sql.Identifier(self.schema)
            ))
            cur.execute(sql.SQL("""
                CREATE TABLE IF NOT EXISTS {}.task_step_segments (
                    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                    task_id text NOT NULL REFERENCES {}.task_runs(task_id) ON DELETE CASCADE,
                    step_id text NOT NULL,
                    segment_index integer NOT NULL,
                    content text NOT NULL,
                    resume_summary text NOT NULL DEFAULT '',
                    item_start integer,
                    item_end integer,
                    created_at timestamptz NOT NULL DEFAULT now(),
                    UNIQUE(task_id, step_id, segment_index)
                )
            """).format(sql.Identifier(self.schema), sql.Identifier(self.schema)))
            cur.execute(sql.SQL("CREATE INDEX IF NOT EXISTS {} ON {}.task_step_segments(task_id, step_id, segment_index)").format(
                sql.Identifier(f"idx_{self.schema}_task_step_segments"), sql.Identifier(self.schema)
            ))
            cur.execute(sql.SQL("""
                CREATE TABLE IF NOT EXISTS {}.task_thinking_segments (
                    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                    task_id text NOT NULL REFERENCES {}.task_runs(task_id) ON DELETE CASCADE,
                    step_id text NOT NULL,
                    thinking_index integer NOT NULL,
                    raw_content text NOT NULL,
                    condensed_note text NOT NULL DEFAULT '',
                    eval_count integer NOT NULL DEFAULT 0,
                    purged_at timestamptz,
                    created_at timestamptz NOT NULL DEFAULT now(),
                    UNIQUE(task_id, step_id, thinking_index)
                )
            """).format(sql.Identifier(self.schema), sql.Identifier(self.schema)))
            cur.execute(sql.SQL("ALTER TABLE {}.task_thinking_segments ADD COLUMN IF NOT EXISTS purged_at timestamptz").format(sql.Identifier(self.schema)))
            cur.execute(sql.SQL("CREATE INDEX IF NOT EXISTS {} ON {}.task_thinking_segments(task_id, step_id, thinking_index)").format(
                sql.Identifier(f"idx_{self.schema}_task_thinking_segments"), sql.Identifier(self.schema)
            ))
            cur.execute(sql.SQL("""
                CREATE TABLE IF NOT EXISTS {}.task_recovery_notes (
                    task_id text NOT NULL REFERENCES {}.task_runs(task_id) ON DELETE CASCADE,
                    step_id text NOT NULL,
                    scope_items jsonb NOT NULL DEFAULT '[]'::jsonb,
                    note text NOT NULL,
                    created_at timestamptz NOT NULL DEFAULT now(),
                    updated_at timestamptz NOT NULL DEFAULT now(),
                    PRIMARY KEY(task_id, step_id)
                )
            """).format(sql.Identifier(self.schema), sql.Identifier(self.schema)))
            cur.execute(sql.SQL("""
                CREATE TABLE IF NOT EXISTS {}.task_evidence_archive (
                    archive_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                    source_evidence_id bigint NOT NULL UNIQUE,
                    task_id text NOT NULL, step_id text NOT NULL, tool text NOT NULL,
                    arguments jsonb NOT NULL, result jsonb NOT NULL,
                    deterministic_verification boolean NOT NULL,
                    created_at timestamptz NOT NULL, archived_at timestamptz NOT NULL DEFAULT now()
                )
            """).format(sql.Identifier(self.schema)))
            cur.execute(sql.SQL("""
                CREATE TABLE IF NOT EXISTS {}.task_summaries (
                    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                    task_id text NOT NULL REFERENCES {}.task_runs(task_id) ON DELETE CASCADE,
                    summary text NOT NULL,
                    created_at timestamptz NOT NULL DEFAULT now()
                )
            """).format(sql.Identifier(self.schema), sql.Identifier(self.schema)))
            cur.execute(sql.SQL("ALTER TABLE {}.task_runs ADD COLUMN IF NOT EXISTS effectiveness_note text").format(sql.Identifier(self.schema)))
            cur.execute(sql.SQL("ALTER TABLE {}.task_runs ADD COLUMN IF NOT EXISTS parent_task_id text").format(sql.Identifier(self.schema)))
            cur.execute(sql.SQL("ALTER TABLE {}.task_runs ADD COLUMN IF NOT EXISTS parent_step_id text").format(sql.Identifier(self.schema)))
            cur.execute(sql.SQL("ALTER TABLE {}.task_runs ADD COLUMN IF NOT EXISTS task_kind text NOT NULL DEFAULT 'root'").format(sql.Identifier(self.schema)))
            cur.execute(sql.SQL("ALTER TABLE {}.task_runs ADD COLUMN IF NOT EXISTS task_depth integer NOT NULL DEFAULT 0").format(sql.Identifier(self.schema)))
            cur.execute(sql.SQL("CREATE INDEX IF NOT EXISTS {} ON {}.task_runs(parent_task_id, parent_step_id)").format(sql.Identifier(f"idx_{self.schema}_task_runs_parent"), sql.Identifier(self.schema)))
            cur.execute(sql.SQL("""
                CREATE TABLE IF NOT EXISTS {}.task_history (
                    history_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                    primary_task_id text NOT NULL UNIQUE,
                    task_date date NOT NULL,
                    title text NOT NULL,
                    status text NOT NULL,
                    original_request text NOT NULL,
                    outcome text NOT NULL,
                    lessons text NOT NULL,
                    future_note text NOT NULL,
                    source_task_ids jsonb NOT NULL DEFAULT '[]'::jsonb,
                    validated boolean NOT NULL DEFAULT false,
                    archived_at timestamptz NOT NULL DEFAULT now()
                )
            """).format(sql.Identifier(self.schema)))
            cur.execute(sql.SQL("ALTER TABLE {}.task_history ADD COLUMN IF NOT EXISTS task_kind text NOT NULL DEFAULT 'root'").format(sql.Identifier(self.schema)))
            cur.execute(sql.SQL("ALTER TABLE {}.task_history ADD COLUMN IF NOT EXISTS parent_task_id text").format(sql.Identifier(self.schema)))
            cur.execute(sql.SQL("ALTER TABLE {}.task_history ADD COLUMN IF NOT EXISTS parent_step_id text").format(sql.Identifier(self.schema)))
            cur.execute(sql.SQL("ALTER TABLE {}.task_history ADD COLUMN IF NOT EXISTS task_depth integer NOT NULL DEFAULT 0").format(sql.Identifier(self.schema)))
            cur.execute(sql.SQL("CREATE INDEX IF NOT EXISTS {} ON {}.task_history(task_date DESC, validated)").format(
                sql.Identifier(f"idx_{self.schema}_task_history_date"), sql.Identifier(self.schema)
            ))
            cur.execute(sql.SQL("CREATE INDEX IF NOT EXISTS {} ON {}.task_summaries(task_id, created_at DESC)").format(
                sql.Identifier(f"idx_{self.schema}_task_summaries_task"), sql.Identifier(self.schema)
            ))
            cur.execute(sql.SQL("""
                CREATE TABLE IF NOT EXISTS {}.runtime_state (
                    state_key text PRIMARY KEY,
                    value jsonb NOT NULL,
                    updated_at timestamptz NOT NULL DEFAULT now()
                )
            """).format(sql.Identifier(self.schema)))
            cur.execute(sql.SQL("""
                CREATE TABLE IF NOT EXISTS {}.runtime_maintenance_notes (
                    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                    phase text NOT NULL,
                    task_id text,
                    note text NOT NULL,
                    details jsonb NOT NULL DEFAULT '{{}}'::jsonb,
                    created_at timestamptz NOT NULL DEFAULT now()
                )
            """).format(sql.Identifier(self.schema)))
            cur.execute(sql.SQL("""
                CREATE TABLE IF NOT EXISTS {}.background_memory_snapshots (
                    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                    summary text NOT NULL,
                    source_through timestamptz NOT NULL,
                    created_at timestamptz NOT NULL DEFAULT now()
                )
            """).format(sql.Identifier(self.schema)))
            for table in ("task_steps", "task_evidence", "task_step_segments", "task_thinking_segments", "task_recovery_notes", "task_evidence_archive"):
                cur.execute(sql.SQL("ALTER TABLE {}.{} ADD COLUMN IF NOT EXISTS node_uuid uuid").format(sql.Identifier(self.schema), sql.Identifier(table)))
            cur.execute(sql.SQL("ALTER TABLE {}.task_runs ADD COLUMN IF NOT EXISTS parent_task_uuid uuid").format(sql.Identifier(self.schema)))
            cur.execute(sql.SQL("ALTER TABLE {}.task_runs ADD COLUMN IF NOT EXISTS parent_node_uuid uuid").format(sql.Identifier(self.schema)))
            self._migrate_uuid_identity(cur)

    @staticmethod
    def _node_kind(legacy_step_id: str, task_kind: str = "root") -> str:
        sid = str(legacy_step_id or "")
        if task_kind == "recovery":
            return "recovery"
        if "verify" in sid:
            return "verification"
        if sid in {"plan-breakdown", "deferred-plan"}:
            return "planning"
        return "action"

    def _task_uuid_cur(self, cur, task_id: str) -> str:
        cur.execute(sql.SQL("SELECT task_uuid::text FROM {}.task_runs WHERE task_id=%s OR task_uuid::text=%s").format(sql.Identifier(self.schema)), (task_id, task_id))
        row = cur.fetchone()
        if not row:
            raise KeyError(f"Unknown task identity: {task_id}")
        return str(row[0])

    def _node_uuid_cur(self, cur, task_id: str, step_id: str) -> str:
        cur.execute(sql.SQL("SELECT node_id::text FROM {}.task_nodes WHERE task_id=%s AND legacy_step_id=%s").format(sql.Identifier(self.schema)), (task_id, step_id))
        row = cur.fetchone()
        if not row:
            raise KeyError(f"Unknown node identity: {task_id}/{step_id}")
        return str(row[0])

    def task_uuid(self, task_id: str) -> str:
        with self._connect() as conn, conn.cursor() as cur:
            return self._task_uuid_cur(cur, task_id)

    def node_uuid(self, task_id: str, step_id: str) -> str:
        with self._connect() as conn, conn.cursor() as cur:
            return self._node_uuid_cur(cur, task_id, step_id)

    def _sync_plan_nodes(self, cur, task_id: str, task_uuid: str, plan: dict, task_kind: str = "root") -> dict:
        steps = list(plan.get("steps") or []) if isinstance(plan, dict) else []
        cur.execute(sql.SQL("SELECT legacy_step_id,node_id::text FROM {}.task_nodes WHERE task_id=%s").format(sql.Identifier(self.schema)), (task_id,))
        existing = {str(a): str(b) for a,b in cur.fetchall()}
        node_by_legacy: dict[str,str] = {}
        current_ids: list[str] = []
        for ordinal, item in enumerate(steps):
            legacy = str(item.get("id") or "").strip()
            if not legacy:
                continue
            current_ids.append(legacy)
            candidate = str(item.get("node_id") or existing.get(legacy) or uuid.uuid4())
            try:
                uuid.UUID(candidate)
            except Exception:
                candidate = str(uuid.uuid4())
            node_by_legacy[legacy] = candidate
            item["node_id"] = candidate
            item["ordinal"] = ordinal
            cur.execute(sql.SQL("""
                INSERT INTO {}.task_nodes(node_id,task_uuid,task_id,legacy_step_id,node_kind,ordinal,name,active)
                VALUES (%s::uuid,%s::uuid,%s,%s,%s,%s,%s,true)
                ON CONFLICT(task_id,legacy_step_id) DO UPDATE SET
                    task_uuid=EXCLUDED.task_uuid,node_kind=EXCLUDED.node_kind,ordinal=EXCLUDED.ordinal,name=EXCLUDED.name,active=true
            """).format(sql.Identifier(self.schema)), (candidate,task_uuid,task_id,legacy,self._node_kind(legacy,task_kind),ordinal,str(item.get("name") or "")))
        cur.execute(sql.SQL("UPDATE {}.task_nodes SET active=false WHERE task_id=%s AND NOT (legacy_step_id = ANY(%s))").format(sql.Identifier(self.schema)), (task_id,current_ids or ["__none__"]))
        for item in steps:
            deps=[str(v) for v in (item.get("depends_on") or [])]
            item["depends_on_node_ids"]=[node_by_legacy[d] for d in deps if d in node_by_legacy]
        plan["task_uuid"] = task_uuid
        plan["steps"] = steps
        cur.execute(sql.SQL("UPDATE {}.task_runs SET plan=%s WHERE task_id=%s").format(sql.Identifier(self.schema)), (Jsonb(plan),task_id))
        desired_edges: set[tuple[str,str,str]] = set()
        for item in steps:
            target=node_by_legacy.get(str(item.get("id") or ""))
            if not target: continue
            edge_type='verification' if 'verify' in str(item.get("id") or "") else 'sequence'
            for source in item.get("depends_on_node_ids") or []:
                desired_edges.add((str(source),str(target),edge_type))
        cur.execute(sql.SQL("SELECT edge_id::text,source_node_id::text,target_node_id::text,edge_type FROM {}.task_dependency_edges WHERE target_task_uuid=%s::uuid AND edge_type IN ('sequence','verification')").format(sql.Identifier(self.schema)), (task_uuid,))
        existing_edges={(str(source),str(target),str(kind)):str(edge_id) for edge_id,source,target,kind in cur.fetchall() if source and target}
        for key,edge_id in existing_edges.items():
            if key not in desired_edges:
                cur.execute(sql.SQL("DELETE FROM {}.task_dependency_edges WHERE edge_id=%s::uuid").format(sql.Identifier(self.schema)), (edge_id,))
        for source,target,edge_type in sorted(desired_edges):
            if (source,target,edge_type) in existing_edges:
                continue
            cur.execute(sql.SQL("INSERT INTO {}.task_dependency_edges(edge_id,source_task_uuid,source_node_id,target_task_uuid,target_node_id,edge_type) VALUES (%s::uuid,%s::uuid,%s::uuid,%s::uuid,%s::uuid,%s)").format(sql.Identifier(self.schema)), (str(uuid.uuid4()),task_uuid,source,task_uuid,target,edge_type))
        return plan

    def _migrate_uuid_identity(self, cur) -> None:
        cur.execute(sql.SQL("SELECT task_id,task_uuid::text,plan,task_kind,parent_task_id,parent_step_id FROM {}.task_runs ORDER BY started_at").format(sql.Identifier(self.schema)))
        rows=cur.fetchall()
        for task_id,task_uuid,plan,task_kind,parent_task_id,parent_step_id in rows:
            data=plan if isinstance(plan,dict) else {}
            self._sync_plan_nodes(cur,str(task_id),str(task_uuid),data,str(task_kind or 'root'))
        for table in ("task_steps", "task_evidence", "task_step_segments", "task_thinking_segments", "task_recovery_notes", "task_evidence_archive"):
            cur.execute(sql.SQL("SELECT DISTINCT x.task_id,x.step_id,tr.task_uuid::text,tr.task_kind FROM {}.{} x JOIN {}.task_runs tr ON tr.task_id=x.task_id LEFT JOIN {}.task_nodes n ON n.task_id=x.task_id AND n.legacy_step_id=x.step_id WHERE n.node_id IS NULL").format(sql.Identifier(self.schema),sql.Identifier(table),sql.Identifier(self.schema),sql.Identifier(self.schema)))
            for legacy_task,legacy_step,task_uuid,task_kind in cur.fetchall():
                cur.execute(sql.SQL("SELECT COALESCE(MAX(ordinal),-1)+1 FROM {}.task_nodes WHERE task_id=%s").format(sql.Identifier(self.schema)), (legacy_task,))
                ordinal=int(cur.fetchone()[0])
                cur.execute(sql.SQL("INSERT INTO {}.task_nodes(node_id,task_uuid,task_id,legacy_step_id,node_kind,ordinal,name,active) VALUES (%s::uuid,%s::uuid,%s,%s,%s,%s,%s,true)").format(sql.Identifier(self.schema)), (str(uuid.uuid4()),task_uuid,legacy_task,legacy_step,self._node_kind(str(legacy_step),str(task_kind or 'root')),ordinal,str(legacy_step)))
            cur.execute(sql.SQL("UPDATE {}.{} x SET node_uuid=n.node_id FROM {}.task_nodes n WHERE x.node_uuid IS NULL AND x.task_id=n.task_id AND x.step_id=n.legacy_step_id").format(sql.Identifier(self.schema),sql.Identifier(table),sql.Identifier(self.schema)))
        cur.execute(sql.SQL("""
            UPDATE {}.task_runs c SET
                parent_task_uuid=p.task_uuid,
                parent_node_uuid=(SELECT n.node_id FROM {}.task_nodes n WHERE n.task_id=p.task_id AND n.legacy_step_id=c.parent_step_id LIMIT 1)
            FROM {}.task_runs p
            WHERE c.parent_task_id=p.task_id AND (c.parent_task_uuid IS NULL OR c.parent_node_uuid IS NULL)
        """).format(sql.Identifier(self.schema),sql.Identifier(self.schema),sql.Identifier(self.schema)))
        cur.execute(sql.SQL("SELECT task_uuid::text,parent_task_uuid::text,parent_node_uuid::text,task_kind FROM {}.task_runs WHERE parent_task_uuid IS NOT NULL").format(sql.Identifier(self.schema)))
        for child_uuid,parent_uuid,parent_node,task_kind in cur.fetchall():
            cur.execute(sql.SQL("SELECT node_id::text FROM {}.task_nodes WHERE task_uuid=%s::uuid AND active ORDER BY ordinal LIMIT 1").format(sql.Identifier(self.schema)), (child_uuid,))
            r=cur.fetchone(); target_node=str(r[0]) if r else None
            edge_type='recovery' if str(task_kind)=='recovery' else 'child'
            cur.execute(sql.SQL("SELECT 1 FROM {}.task_dependency_edges WHERE source_task_uuid=%s::uuid AND source_node_id IS NOT DISTINCT FROM %s::uuid AND target_task_uuid=%s::uuid AND edge_type=%s LIMIT 1").format(sql.Identifier(self.schema)), (parent_uuid,parent_node,child_uuid,edge_type))
            if not cur.fetchone():
                cur.execute(sql.SQL("INSERT INTO {}.task_dependency_edges(edge_id,source_task_uuid,source_node_id,target_task_uuid,target_node_id,edge_type) VALUES (%s::uuid,%s::uuid,%s::uuid,%s::uuid,%s::uuid,%s)").format(sql.Identifier(self.schema)), (str(uuid.uuid4()),parent_uuid,parent_node,child_uuid,target_node,edge_type))

    def record_dependency_edge(self, source_task_id: str, source_step_id: str, target_task_id: str, target_step_id: str = "", edge_type: str = "sequence", metadata: dict | None = None) -> str:
        with self._connect() as conn, conn.cursor() as cur:
            su=self._task_uuid_cur(cur,source_task_id); tu=self._task_uuid_cur(cur,target_task_id)
            sn=self._node_uuid_cur(cur,source_task_id,source_step_id) if source_step_id else None
            tn=self._node_uuid_cur(cur,target_task_id,target_step_id) if target_step_id else None
            eid=str(uuid.uuid4())
            cur.execute(sql.SQL("INSERT INTO {}.task_dependency_edges(edge_id,source_task_uuid,source_node_id,target_task_uuid,target_node_id,edge_type,metadata) VALUES (%s::uuid,%s::uuid,%s::uuid,%s::uuid,%s::uuid,%s,%s)").format(sql.Identifier(self.schema)), (eid,su,sn,tu,tn,str(edge_type),Jsonb(metadata or {})))
            return eid

    def running_tasks(self, limit: int = 20) -> list[dict]:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(sql.SQL("""
                SELECT task_id,title,status,started_at,updated_at
                FROM {}.task_runs WHERE status='running'
                ORDER BY updated_at DESC LIMIT %s
            """).format(sql.Identifier(self.schema)), (max(1, int(limit)),))
            return [{
                "task_id": str(r[0]), "title": str(r[1]), "status": str(r[2]),
                "started_at": r[3].isoformat(), "updated_at": r[4].isoformat(),
            } for r in cur.fetchall()]

    def start_task(self, plan: TaskPlan) -> None:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(sql.SQL("SELECT task_uuid::text FROM {}.task_runs WHERE task_id=%s").format(sql.Identifier(self.schema)), (plan.task_id,))
            existing = cur.fetchone()
            if existing and str(existing[0]) != str(plan.task_uuid):
                raise ValueError(f"task identity changed for legacy task id {plan.task_id}: {existing[0]} != {plan.task_uuid}")
            cur.execute(sql.SQL("""
                INSERT INTO {}.task_runs(task_id, task_uuid, title, status, plan)
                VALUES (%s, %s::uuid, %s, 'running', %s)
                ON CONFLICT (task_id) DO UPDATE
                SET title = EXCLUDED.title, plan = EXCLUDED.plan, status = 'running', updated_at = now()
            """).format(sql.Identifier(self.schema)), (plan.task_id, plan.task_uuid, plan.title, Jsonb(plan.as_dict())))
            cur.execute(sql.SQL("SELECT task_kind FROM {}.task_runs WHERE task_id=%s").format(sql.Identifier(self.schema)), (plan.task_id,))
            kind_row=cur.fetchone(); task_kind=str(kind_row[0] or 'root') if kind_row else 'root'
            self._sync_plan_nodes(cur,plan.task_id,plan.task_uuid,plan.as_dict(),task_kind)

    def start_child_task(self, plan: TaskPlan, parent_task_id: str, parent_step_id: str, original_request: str = "", task_kind: str = "child") -> None:
        parent_task_id = str(parent_task_id or "").strip()
        parent_step_id = str(parent_step_id or "").strip()
        task_kind = str(task_kind or "child").strip().lower()
        if task_kind not in {"child", "recovery"}:
            raise ValueError("task_kind must be child or recovery")
        if not parent_task_id or not parent_step_id:
            raise ValueError("child task requires parent task and step ids")
        self.start_task(plan)
        plan_data = plan.as_dict(); plan_data["original_request"] = str(original_request or "").strip()
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(sql.SQL("SELECT task_depth,task_uuid::text FROM {}.task_runs WHERE task_id=%s").format(sql.Identifier(self.schema)), (parent_task_id,))
            row = cur.fetchone()
            if not row: raise KeyError(f"Unknown parent task: {parent_task_id}")
            depth = int(row[0] or 0) + 1; parent_uuid=str(row[1])
            parent_node=self._node_uuid_cur(cur,parent_task_id,parent_step_id)
            cur.execute(sql.SQL("UPDATE {}.task_runs SET plan=%s,parent_task_id=%s,parent_step_id=%s,parent_task_uuid=%s::uuid,parent_node_uuid=%s::uuid,task_kind=%s,task_depth=%s,updated_at=now() WHERE task_id=%s").format(sql.Identifier(self.schema)), (Jsonb(plan_data),parent_task_id,parent_step_id,parent_uuid,parent_node,task_kind,depth,plan.task_id))
            self._sync_plan_nodes(cur,plan.task_id,plan.task_uuid,plan_data,task_kind)
            cur.execute(sql.SQL("SELECT node_id::text FROM {}.task_nodes WHERE task_id=%s AND active ORDER BY ordinal LIMIT 1").format(sql.Identifier(self.schema)), (plan.task_id,))
            first=cur.fetchone(); target_node=str(first[0]) if first else None
            cur.execute(sql.SQL("INSERT INTO {}.task_dependency_edges(edge_id,source_task_uuid,source_node_id,target_task_uuid,target_node_id,edge_type,metadata) VALUES (%s::uuid,%s::uuid,%s::uuid,%s::uuid,%s::uuid,%s,%s)").format(sql.Identifier(self.schema)), (str(uuid.uuid4()),parent_uuid,parent_node,plan.task_uuid,target_node,task_kind,Jsonb({"legacy_parent_task_id":parent_task_id,"legacy_parent_step_id":parent_step_id})))

    def task_lineage(self, task_id: str) -> dict | None:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(sql.SQL("SELECT task_kind,parent_task_id,parent_step_id,task_depth FROM {}.task_runs WHERE task_id=%s").format(sql.Identifier(self.schema)), (task_id,))
            row = cur.fetchone()
        if not row:
            return None
        return {"task_kind": str(row[0] or "root"), "parent_task_id": str(row[1] or ""), "parent_step_id": str(row[2] or ""), "task_depth": int(row[3] or 0)}

    def is_child_task(self, task_id: str) -> bool:
        lineage = self.task_lineage(task_id)
        return bool(lineage and lineage.get("task_kind") == "child")

    def checkpoint_step(self, result: StepResult) -> None:
        with self._connect() as conn, conn.cursor() as cur:
            node_uuid = str(result.node_id or self._node_uuid_cur(cur, result.task_id, result.step_id))
            cur.execute(sql.SQL("""
                INSERT INTO {}.task_steps(
                    task_id, step_id, node_uuid, name, status, summary, artifacts,
                    verification, error, started_at, completed_at
                ) VALUES (%s,%s,%s::uuid,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (task_id, step_id) DO UPDATE SET
                    node_uuid=EXCLUDED.node_uuid,name=EXCLUDED.name, status=EXCLUDED.status, summary=EXCLUDED.summary,
                    artifacts=EXCLUDED.artifacts, verification=EXCLUDED.verification,
                    error=EXCLUDED.error, started_at=EXCLUDED.started_at,
                    completed_at=EXCLUDED.completed_at
            """).format(sql.Identifier(self.schema)), (
                result.task_id, result.step_id, node_uuid, result.name, result.status.value,
                result.summary, Jsonb(list(result.artifacts)), result.verification,
                result.error, result.started_at, result.completed_at,
            ))
            cur.execute(sql.SQL("UPDATE {}.task_runs SET updated_at=now(), status=%s WHERE task_id=%s").format(
                sql.Identifier(self.schema)
            ), ("failed" if result.status.value == "failed" else "cancelled" if result.status.value == "cancelled" else "running", result.task_id))

    def record_step_segment(
        self,
        task_id: str,
        step_id: str,
        content: str,
        resume_summary: str,
        *,
        item_start: int | None = None,
        item_end: int | None = None,
    ) -> int:
        content = str(content or "")
        if not content:
            raise ValueError("step segment content must not be blank")
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(sql.SQL("""
                SELECT COALESCE(MAX(segment_index),0)+1
                FROM {}.task_step_segments
                WHERE task_id=%s AND step_id=%s
            """).format(sql.Identifier(self.schema)), (task_id, step_id))
            segment_index = int(cur.fetchone()[0])
            node_uuid=self._node_uuid_cur(cur,task_id,step_id)
            cur.execute(sql.SQL("""
                INSERT INTO {}.task_step_segments(
                    task_id,step_id,node_uuid,segment_index,content,resume_summary,item_start,item_end
                ) VALUES (%s,%s,%s::uuid,%s,%s,%s,%s,%s)
            """).format(sql.Identifier(self.schema)), (
                task_id, step_id, node_uuid, segment_index, content, str(resume_summary or ""),
                item_start, item_end,
            ))
            cur.execute(sql.SQL("UPDATE {}.task_runs SET updated_at=now() WHERE task_id=%s").format(
                sql.Identifier(self.schema)
            ), (task_id,))
        return segment_index

    def step_segments(self, task_id: str, step_id: str) -> list[dict]:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(sql.SQL("""
                SELECT segment_index,content,resume_summary,item_start,item_end,created_at
                FROM {}.task_step_segments
                WHERE task_id=%s AND step_id=%s
                ORDER BY segment_index
            """).format(sql.Identifier(self.schema)), (task_id, step_id))
            rows = cur.fetchall()
        return [
            {
                "segment_index": int(r[0]), "content": str(r[1] or ""),
                "resume_summary": str(r[2] or ""), "item_start": r[3], "item_end": r[4],
                "created_at": r[5].isoformat(),
            }
            for r in rows
        ]

    def record_thinking_segment(self, task_id: str, step_id: str, raw_content: str, *, eval_count: int = 0, condensed_note: str = "") -> int:
        raw = str(raw_content or "")
        if not raw.strip():
            raise ValueError("thinking segment must not be blank")
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(sql.SQL("SELECT COALESCE(MAX(thinking_index),0)+1 FROM {}.task_thinking_segments WHERE task_id=%s AND step_id=%s").format(sql.Identifier(self.schema)), (task_id, step_id))
            idx = int(cur.fetchone()[0])
            node_uuid=self._node_uuid_cur(cur,task_id,step_id)
            cur.execute(sql.SQL("INSERT INTO {}.task_thinking_segments(task_id,step_id,node_uuid,thinking_index,raw_content,condensed_note,eval_count) VALUES (%s,%s,%s::uuid,%s,%s,%s,%s)").format(sql.Identifier(self.schema)), (task_id, step_id, node_uuid, idx, raw, str(condensed_note or ""), int(eval_count or 0)))
            cur.execute(sql.SQL("UPDATE {}.task_runs SET updated_at=now() WHERE task_id=%s").format(sql.Identifier(self.schema)), (task_id,))
        return idx

    def update_thinking_condensed(self, task_id: str, step_id: str, thinking_index: int, condensed_note: str) -> None:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(sql.SQL("UPDATE {}.task_thinking_segments SET condensed_note=%s WHERE task_id=%s AND step_id=%s AND thinking_index=%s").format(sql.Identifier(self.schema)), (str(condensed_note or ""), task_id, step_id, int(thinking_index)))

    def thinking_segments(self, task_id: str, step_id: str) -> list[dict]:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(sql.SQL("SELECT thinking_index,raw_content,condensed_note,eval_count,created_at FROM {}.task_thinking_segments WHERE task_id=%s AND step_id=%s ORDER BY thinking_index").format(sql.Identifier(self.schema)), (task_id, step_id))
            rows = cur.fetchall()
        return [{"thinking_index": int(r[0]), "raw_content": str(r[1] or ""), "condensed_note": str(r[2] or ""), "eval_count": int(r[3] or 0), "created_at": r[4].isoformat()} for r in rows]

    def purge_raw_thinking(self, task_id: str) -> int:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                sql.SQL("UPDATE {}.task_thinking_segments SET raw_content='', purged_at=now() WHERE task_id=%s AND raw_content<>''").format(sql.Identifier(self.schema)),
                (task_id,),
            )
            return int(cur.rowcount or 0)

    def record_recovery_note(self, task_id: str, step_id: str, note: str, scope_items: list[int] | None = None) -> None:
        note = str(note or "").strip()
        if not note:
            raise ValueError("recovery note must not be blank")
        scope = [int(v) for v in (scope_items or [])]
        with self._connect() as conn, conn.cursor() as cur:
            node_uuid=self._node_uuid_cur(cur,task_id,step_id)
            cur.execute(sql.SQL("""
                INSERT INTO {}.task_recovery_notes(task_id,step_id,node_uuid,scope_items,note)
                VALUES (%s,%s,%s::uuid,%s,%s)
                ON CONFLICT(task_id,step_id) DO UPDATE SET
                    node_uuid=EXCLUDED.node_uuid,scope_items=EXCLUDED.scope_items,note=EXCLUDED.note,updated_at=now()
            """).format(sql.Identifier(self.schema)), (task_id, step_id, node_uuid, Jsonb(scope), note))

    def recovery_note(self, task_id: str, step_id: str = "work") -> str:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(sql.SQL("SELECT note FROM {}.task_recovery_notes WHERE task_id=%s AND step_id=%s").format(
                sql.Identifier(self.schema)
            ), (task_id, step_id))
            row = cur.fetchone()
        return str(row[0] or "") if row else ""

    def compact_task_evidence(self, task_id: str, max_result_chars: int = 2400) -> dict:
        archived = 0
        before = 0
        after = 0
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(sql.SQL("""
                SELECT id,task_id,step_id,node_uuid,tool,arguments,result,deterministic_verification,created_at
                FROM {}.task_evidence WHERE task_id=%s ORDER BY id
            """).format(sql.Identifier(self.schema)), (task_id,))
            rows = cur.fetchall()
            for row in rows:
                result = row[6] if isinstance(row[6], dict) else {}
                rendered = json.dumps(result, ensure_ascii=False)
                before += len(rendered)
                if len(rendered) <= max_result_chars:
                    after += len(rendered)
                    continue
                cur.execute(sql.SQL("""
                    INSERT INTO {}.task_evidence_archive
                    (source_evidence_id,task_id,step_id,node_uuid,tool,arguments,result,deterministic_verification,created_at)
                    VALUES (%s,%s,%s,%s::uuid,%s,%s,%s,%s,%s)
                    ON CONFLICT(source_evidence_id) DO NOTHING
                """).format(sql.Identifier(self.schema)), (
                    row[0],row[1],row[2],row[3],row[4],Jsonb(row[5] if isinstance(row[5],dict) else {}),
                    Jsonb(result),row[7],row[8],
                ))
                compact = {k: result.get(k) for k in ("ok","tool","path","size","sha256") if k in result}
                compact.update({
                    "summary": "Bulky evidence archived; compact metadata retained for live context.",
                    "content_omitted_from_live_context": True,
                    "archived_original_evidence_id": int(row[0]),
                })
                cur.execute(sql.SQL("UPDATE {}.task_evidence SET result=%s WHERE id=%s").format(
                    sql.Identifier(self.schema)
                ), (Jsonb(compact), row[0]))
                archived += 1
                after += len(json.dumps(compact, ensure_ascii=False))
        return {"archived_rows": archived, "before_chars": before, "after_chars": after}

    def record_evidence(self, task_id: str, step_id: str, evidence: list[dict]) -> None:
        if not evidence:
            return
        with self._connect() as conn, conn.cursor() as cur:
            node_uuid=self._node_uuid_cur(cur,task_id,step_id)
            rows = []
            for item in evidence:
                if not isinstance(item, dict):
                    continue
                arguments = item.get("arguments") if isinstance(item.get("arguments"), dict) else {}
                result = item.get("result") if isinstance(item.get("result"), dict) else {}
                rows.append((task_id,step_id,node_uuid,str(item.get("tool") or "unknown"),Jsonb(arguments),Jsonb(result),bool(item.get("deterministic_verification"))))
            if not rows:
                return
            cur.executemany(sql.SQL("""
                INSERT INTO {}.task_evidence(task_id, step_id, node_uuid, tool, arguments, result, deterministic_verification)
                VALUES (%s,%s,%s::uuid,%s,%s,%s,%s)
            """).format(sql.Identifier(self.schema)), rows)

    def save_summary(self, task_id: str, summary: str, final: bool = False) -> None:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(sql.SQL("INSERT INTO {}.task_summaries(task_id, summary) VALUES (%s, %s)").format(
                sql.Identifier(self.schema)
            ), (task_id, summary))
            if final:
                cur.execute(sql.SQL("UPDATE {}.task_runs SET status='completed', updated_at=now() WHERE task_id=%s").format(
                    sql.Identifier(self.schema)
                ), (task_id,))

    def finalize_task(self, task_id: str, summary: str, status: str) -> None:
        status = str(status).strip().lower()
        if status not in {"completed", "failed", "cancelled"}:
            raise ValueError(f"invalid terminal task status: {status}")
        summary = str(summary or "").strip()
        if not summary:
            raise ValueError("terminal task summary must not be blank")
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(sql.SQL("SELECT summary FROM {}.task_summaries WHERE task_id=%s ORDER BY created_at DESC LIMIT 1").format(sql.Identifier(self.schema)), (task_id,))
            row = cur.fetchone()
            if not row or str(row[0]) != summary:
                cur.execute(sql.SQL("INSERT INTO {}.task_summaries(task_id, summary) VALUES (%s, %s)").format(sql.Identifier(self.schema)), (task_id, summary))
            cur.execute(sql.SQL("UPDATE {}.task_runs SET status=%s, updated_at=now() WHERE task_id=%s").format(sql.Identifier(self.schema)), (status, task_id))
            if cur.rowcount != 1:
                raise KeyError(f"Unknown task: {task_id}")

    def task_terminal_context(self, task_id: str) -> dict | None:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(sql.SQL("""
                SELECT tr.status,tr.plan,tr.effectiveness_note,
                       (SELECT ts.summary FROM {}.task_summaries ts WHERE ts.task_id=tr.task_id ORDER BY ts.created_at DESC LIMIT 1)
                FROM {}.task_runs tr WHERE tr.task_id=%s
            """).format(sql.Identifier(self.schema), sql.Identifier(self.schema)), (task_id,))
            row = cur.fetchone()
        if not row:
            return None
        plan = row[1] if isinstance(row[1], dict) else {}
        return {
            "task_id": task_id, "status": str(row[0]),
            "original_request": self._original_request_from_plan(plan),
            "effectiveness_note": str(row[2] or ""),
            "summary": str(row[3] or ""),
        }

    def task_status(self, task_id: str) -> str | None:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(sql.SQL("SELECT status FROM {}.task_runs WHERE task_id=%s").format(sql.Identifier(self.schema)), (task_id,))
            row = cur.fetchone()
            return str(row[0]) if row else None

    def terminal_summary_verified(self, task_id: str) -> bool:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(sql.SQL("SELECT tr.status, EXISTS(SELECT 1 FROM {}.task_summaries ts WHERE ts.task_id=tr.task_id) FROM {}.task_runs tr WHERE tr.task_id=%s").format(sql.Identifier(self.schema), sql.Identifier(self.schema)), (task_id,))
            row = cur.fetchone()
            return bool(row and str(row[0]) in {"completed", "failed", "cancelled"} and row[1])

    def terminal_task_ids(self) -> set[str]:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(sql.SQL("SELECT task_id FROM {}.task_runs WHERE status IN ('completed','failed','cancelled')").format(sql.Identifier(self.schema)))
            return {str(row[0]) for row in cur.fetchall()}

    def ensure_terminal_summary(self, task_id: str) -> bool:
        status = self.task_status(task_id)
        if status not in {"completed", "failed", "cancelled"}:
            return False
        if self.latest_summary(task_id):
            return True
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(sql.SQL("SELECT step_id, name, status, summary, error FROM {}.task_steps WHERE task_id=%s ORDER BY completed_at DESC LIMIT 1").format(sql.Identifier(self.schema)), (task_id,))
            row = cur.fetchone()
        if row:
            step_id, name, step_status, step_summary, error = row
            detail = str(step_summary or "").strip() or str(error or "").strip() or "No step summary was recorded."
            summary = f"Task {status}. Last recorded step {step_id} ({name}) [{step_status}]: {detail}"
        else:
            summary = f"Task {status}. No step detail was recorded."
        self.finalize_task(task_id, summary, status)
        return True

    def set_runtime_state(self, key: str, value) -> None:
        key = str(key or "").strip()
        if not key:
            raise ValueError("runtime state key must not be blank")
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(sql.SQL("""
                INSERT INTO {}.runtime_state(state_key,value,updated_at) VALUES (%s,%s,now())
                ON CONFLICT (state_key) DO UPDATE SET value=EXCLUDED.value, updated_at=now()
            """).format(sql.Identifier(self.schema)), (key, Jsonb(value)))

    def get_runtime_state(self, key: str, default=None):
        key = str(key or "").strip()
        if not key:
            return default
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(sql.SQL("SELECT value FROM {}.runtime_state WHERE state_key=%s").format(sql.Identifier(self.schema)), (key,))
            row = cur.fetchone()
        return row[0] if row else default

    def runtime_state_snapshot(self) -> dict:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(sql.SQL("SELECT state_key,value,updated_at FROM {}.runtime_state ORDER BY state_key").format(sql.Identifier(self.schema)))
            return {str(k): {"value": v, "updated_at": at} for k,v,at in cur.fetchall()}

    def record_maintenance_note(self, phase: str, note: str, *, task_id: str | None = None, details: dict | None = None) -> None:
        phase = str(phase or "maintenance").strip() or "maintenance"
        note = str(note or "").strip()
        if not note:
            raise ValueError("maintenance note must not be blank")
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(sql.SQL("INSERT INTO {}.runtime_maintenance_notes(phase, task_id, note, details) VALUES (%s,%s,%s,%s)").format(sql.Identifier(self.schema)),
                        (phase, task_id, note, Jsonb(details or {})))

    def finalize_interrupted_task(self, task_id: str, reason: str) -> str:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(sql.SQL("SELECT title, status FROM {}.task_runs WHERE task_id=%s").format(sql.Identifier(self.schema)), (task_id,))
            task = cur.fetchone()
            if not task:
                raise KeyError(f"Unknown task: {task_id}")
            cur.execute(sql.SQL("SELECT step_id, name, status, summary, error FROM {}.task_steps WHERE task_id=%s ORDER BY completed_at DESC LIMIT 1").format(sql.Identifier(self.schema)), (task_id,))
            step = cur.fetchone()
        if str(task[1]) in {"completed", "failed", "cancelled"}:
            self.ensure_terminal_summary(task_id)
            return self.latest_summary(task_id) or f"Task {task[1]}."
        detail = "No durable step result was available."
        if step:
            detail = str(step[3] or step[4] or "").strip() or detail
            detail = f"Last step {step[0]} ({step[1]}) [{step[2]}]: {detail}"
        summary = f"Task interrupted and could not be resumed during runtime maintenance. Reason: {reason}. {detail}"
        self.finalize_task(task_id, summary, "failed")
        return summary

    def latest_summary(self, task_id: str) -> str | None:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(sql.SQL("SELECT summary FROM {}.task_summaries WHERE task_id=%s ORDER BY created_at DESC LIMIT 1").format(
                sql.Identifier(self.schema)
            ), (task_id,))
            row = cur.fetchone()
            return row[0] if row else None

    def dependencies_satisfied(self, task_id: str, step_id: str) -> bool:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(sql.SQL("SELECT plan FROM {}.task_runs WHERE task_id=%s").format(
                sql.Identifier(self.schema)
            ), (task_id,))
            row = cur.fetchone()
            if not row:
                raise KeyError(f"Unknown task: {task_id}")
            plan = row[0]
            target = next((s for s in plan.get("steps", []) if str(s.get("id")) == step_id), None)
            if target is None:
                raise KeyError(f"Unknown step {step_id} for task {task_id}")
            dependencies = [str(v) for v in target.get("depends_on", [])]
            if not dependencies:
                return True
            cur.execute(sql.SQL("SELECT step_id FROM {}.task_steps WHERE task_id=%s AND status='completed'").format(
                sql.Identifier(self.schema)
            ), (task_id,))
            completed = {str(r[0]) for r in cur.fetchall()}
            return all(dep in completed for dep in dependencies)

    def task_plan_snapshot(self, task_id: str) -> dict | None:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(sql.SQL("SELECT task_id,title,status,plan,task_uuid::text FROM {}.task_runs WHERE task_id=%s OR task_uuid::text=%s").format(
                sql.Identifier(self.schema)
            ), (task_id,task_id))
            row = cur.fetchone()
            if not row:
                return None
            resolved_task_id=str(row[0]); plan = row[3] if isinstance(row[3], dict) else {}
            cur.execute(sql.SQL("SELECT step_id,status FROM {}.task_steps WHERE task_id=%s").format(
                sql.Identifier(self.schema)
            ), (resolved_task_id,))
            statuses = {str(step_id): str(status) for step_id, status in cur.fetchall()}
        steps = []
        for item in plan.get("steps", []) if isinstance(plan, dict) else []:
            step_id = str(item.get("id") or "")
            if not step_id:
                continue
            steps.append({
                "id": step_id,
                "node_id": str(item.get("node_id") or ""),
                "name": str(item.get("name") or "")[:160],
                "status": statuses.get(step_id, "pending"),
            })
        return {
            "task_id": resolved_task_id,
            "task_uuid": str(row[4]),
            "title": str(row[1]),
            "status": str(row[2]),
            "original_request": self._original_request_from_plan(plan),
            "steps": steps,
        }

    def external_dependency_state(self, previous_task_id: str, previous_step_id: str = "", previous_task_uuid: str = "", previous_node_id: str = "") -> dict:
        """Gate appended follow-up work on a fully verified predecessor using opaque IDs when available."""
        previous_task_id = str(previous_task_id or "").strip()
        previous_step_id = str(previous_step_id or "").strip()
        previous_task_uuid = str(previous_task_uuid or "").strip()
        previous_node_id = str(previous_node_id or "").strip()
        if not previous_task_id and not previous_task_uuid:
            return {"state": "missing", "task_status": None, "step_status": None}
        with self._connect() as conn, conn.cursor() as cur:
            if previous_task_uuid:
                cur.execute(sql.SQL("SELECT task_id,status,task_uuid::text FROM {}.task_runs WHERE task_uuid=%s::uuid").format(sql.Identifier(self.schema)), (previous_task_uuid,))
            else:
                cur.execute(sql.SQL("SELECT task_id,status,task_uuid::text FROM {}.task_runs WHERE task_id=%s").format(sql.Identifier(self.schema)), (previous_task_id,))
            row = cur.fetchone()
            if not row:
                return {"state": "missing", "task_status": None, "step_status": None}
            resolved_task_id,task_status,resolved_task_uuid=str(row[0]),str(row[1]),str(row[2])
            step_status = None
            resolved_node_id = ""
            if previous_node_id:
                cur.execute(sql.SQL("SELECT ts.status,n.node_id::text FROM {}.task_nodes n LEFT JOIN {}.task_steps ts ON ts.task_id=n.task_id AND ts.step_id=n.legacy_step_id WHERE n.node_id=%s::uuid AND n.task_uuid=%s::uuid").format(sql.Identifier(self.schema),sql.Identifier(self.schema)), (previous_node_id,resolved_task_uuid))
                step_row=cur.fetchone()
                if step_row: step_status=str(step_row[0]) if step_row[0] is not None else None; resolved_node_id=str(step_row[1])
            elif previous_step_id:
                cur.execute(sql.SQL("SELECT ts.status,n.node_id::text FROM {}.task_nodes n LEFT JOIN {}.task_steps ts ON ts.task_id=n.task_id AND ts.step_id=n.legacy_step_id WHERE n.task_id=%s AND n.legacy_step_id=%s").format(sql.Identifier(self.schema),sql.Identifier(self.schema)), (resolved_task_id,previous_step_id))
                step_row=cur.fetchone()
                if step_row:
                    step_status=str(step_row[0]) if step_row[0] is not None else None; resolved_node_id=str(step_row[1])
                else:
                    cur.execute(sql.SQL("SELECT status FROM {}.task_steps WHERE task_id=%s AND step_id=%s").format(sql.Identifier(self.schema)), (resolved_task_id,previous_step_id))
                    legacy_step=cur.fetchone(); step_status=str(legacy_step[0]) if legacy_step else None
            if task_status in {"failed", "cancelled"}: state="failed"
            elif task_status != "completed": state="waiting"
            elif (previous_node_id or previous_step_id) and step_status != "completed": state="failed"
            else: state="satisfied"
            return {"state":state,"task_status":task_status,"step_status":step_status,"task_id":resolved_task_id,"task_uuid":resolved_task_uuid,"node_id":resolved_node_id}

    def completed_step_context(self, task_id: str, step_id: str, max_chars: int | None = 24000) -> str:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(sql.SQL("SELECT plan FROM {}.task_runs WHERE task_id=%s").format(sql.Identifier(self.schema)), (task_id,))
            row = cur.fetchone()
            if not row:
                return ""
            ordered = [str(s.get("id")) for s in row[0].get("steps", [])]
            try:
                target_index = ordered.index(step_id)
            except ValueError:
                return ""
            prior_ids = ordered[:target_index]
            if not prior_ids:
                return ""
            cur.execute(sql.SQL("SELECT step_id, name, summary, verification FROM {}.task_steps WHERE task_id=%s AND status='completed'").format(sql.Identifier(self.schema)), (task_id,))
            rows = {str(r[0]): (str(r[1]), str(r[2]), str(r[3])) for r in cur.fetchall()}
            cur.execute(sql.SQL("SELECT step_id, tool, result FROM {}.task_evidence WHERE task_id=%s AND step_id = ANY(%s) ORDER BY id").format(
                sql.Identifier(self.schema)
            ), (task_id, prior_ids))
            evidence_by_step: dict[str, list[str]] = {}
            for ev_step, tool, result in cur.fetchall():
                rendered = json.dumps(result or {}, ensure_ascii=False)
                if len(rendered) > 12000:
                    rendered = rendered[:12000] + "...[truncated]"
                evidence_by_step.setdefault(str(ev_step), []).append(f"{tool}: {rendered}")
        parts = []
        for prior_id in prior_ids:
            if prior_id not in rows and prior_id not in evidence_by_step:
                continue
            name, summary, verification = rows.get(prior_id, (prior_id, "", ""))
            part = f"[{prior_id}] {name}\nResult: {summary}\nVerification: {verification}"
            if evidence_by_step.get(prior_id):
                part += "\nPersisted tool evidence:\n" + "\n".join(evidence_by_step[prior_id])
            parts.append(part)
        context = "\n\n".join(parts)
        if max_chars is None or len(context) <= max_chars:
            return context
        kept: list[str] = []
        used = 0
        for part in reversed(parts):
            extra = len(part) + (2 if kept else 0)
            if used + extra > max_chars:
                continue
            kept.append(part)
            used += extra
        kept.reverse()
        omitted = len(parts) - len(kept)
        prefix = (
            f"[{omitted} earlier completed step result(s) omitted by intermediate-step context budget]\n\n"
            if omitted else ""
        )
        return prefix + "\n\n".join(kept)

    def task_resource_status(self, task_id: str) -> dict:
        statuses: list[dict] = []
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(sql.SQL("SELECT summary FROM {}.task_steps WHERE task_id=%s ORDER BY completed_at").format(
                sql.Identifier(self.schema)
            ), (task_id,))
            for (summary,) in cur.fetchall():
                try:
                    payload = json.loads(str(summary))
                except (TypeError, ValueError, json.JSONDecodeError):
                    continue
                status = payload.get("resource_status") if isinstance(payload, dict) else None
                if isinstance(status, dict):
                    statuses.append(status)
            cur.execute(sql.SQL("SELECT result FROM {}.task_evidence WHERE task_id=%s ORDER BY id").format(
                sql.Identifier(self.schema)
            ), (task_id,))
            for (result,) in cur.fetchall():
                if not isinstance(result, dict):
                    continue
                status = result.get("resource_status")
                if isinstance(status, dict):
                    statuses.append(status)
                    continue
                storage = result.get("storage_context")
                if isinstance(storage, dict) and isinstance(storage.get("context_status"), dict):
                    statuses.append(storage["context_status"])
        return merge_resource_status(*statuses)

    @staticmethod
    def _original_request_from_plan(plan: dict) -> str:
        if isinstance(plan, dict) and str(plan.get("original_request") or "").strip():
            return str(plan.get("original_request") or "").strip()
        for step in plan.get("steps", []) if isinstance(plan, dict) else []:
            description = str(step.get("description") or "")
            token = "AUTHORITATIVE ORIGINAL USER REQUEST:\n"
            if token not in description:
                continue
            tail = description.split(token, 1)[1]
            for stop in ("\n\nGenerate explicit", "\n\nNORM-GENERATED EXECUTION STEP:", "\n\nVERIFICATION REQUIREMENT:"):
                if stop in tail:
                    tail = tail.split(stop, 1)[0]
            if tail.strip():
                return tail.strip()
        return ""

    def set_effectiveness_note(self, task_id: str, note: str) -> None:
        note = str(note or "").strip()
        if not note:
            return
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(sql.SQL("UPDATE {}.task_runs SET effectiveness_note=%s, updated_at=now() WHERE task_id=%s").format(
                sql.Identifier(self.schema)
            ), (note[:1200], task_id))

    def needs_deep_history_consolidation(self, retention_days: int = 30, interval_days: int = 7) -> bool:
        cutoff = datetime.now(timezone.utc) - timedelta(days=max(1, int(retention_days)))
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(sql.SQL("SELECT EXISTS(SELECT 1 FROM {}.task_runs WHERE status IN ('completed','failed','cancelled') AND started_at < %s)").format(
                sql.Identifier(self.schema)
            ), (cutoff,))
            if not bool(cur.fetchone()[0]):
                return False
            cur.execute(sql.SQL("SELECT MAX(created_at) FROM {}.runtime_maintenance_notes WHERE phase='deep_history_consolidation' AND details->>'status'='success'").format(
                sql.Identifier(self.schema)
            ))
            row = cur.fetchone()
        last = row[0] if row else None
        return last is None or last <= datetime.now(timezone.utc) - timedelta(days=max(1, int(interval_days)))

    def deep_history_candidates(self, retention_days: int = 30, limit: int = 500) -> list[dict]:
        cutoff = datetime.now(timezone.utc) - timedelta(days=max(1, int(retention_days)))
        with self._connect() as conn, conn.cursor() as cur:
            s = sql.Identifier(self.schema)
            cur.execute(sql.SQL("""
                SELECT tr.task_id,tr.title,tr.status,tr.plan,tr.started_at,tr.updated_at,tr.effectiveness_note,
                       tr.task_kind,tr.parent_task_id,tr.parent_step_id,tr.task_depth,
                       (SELECT ts.summary FROM {}.task_summaries ts WHERE ts.task_id=tr.task_id ORDER BY ts.created_at DESC LIMIT 1)
                FROM {}.task_runs tr
                WHERE tr.status IN ('completed','failed','cancelled') AND tr.started_at < %s
                ORDER BY tr.started_at ASC LIMIT %s
            """).format(s, s), (cutoff, max(1, int(limit))))
            task_rows = cur.fetchall()
            task_ids = [str(row[0]) for row in task_rows]
            if not task_ids:
                return []
            cur.execute(sql.SQL("SELECT task_id,step_id,name,status,summary,error FROM {}.task_steps WHERE task_id=ANY(%s) ORDER BY task_id,completed_at").format(s), (task_ids,))
            step_rows = cur.fetchall()
            cur.execute(sql.SQL("SELECT task_id,step_id,tool,result FROM {}.task_evidence WHERE task_id=ANY(%s) ORDER BY task_id,id").format(s), (task_ids,))
            evidence_rows = cur.fetchall()
        steps: dict[str, list[dict]] = {}
        for task_id, step_id, name, status, summary, error in step_rows:
            steps.setdefault(str(task_id), []).append({
                "step_id": str(step_id), "name": str(name), "status": str(status),
                "summary": str(summary or "")[:1800], "error": str(error or "")[:800],
            })
        evidence: dict[str, list[dict]] = {}
        for task_id, step_id, tool, result in evidence_rows:
            bucket = evidence.setdefault(str(task_id), [])
            if len(bucket) >= 12:
                continue
            rendered = json.dumps(result or {}, ensure_ascii=False)
            bucket.append({"step_id": str(step_id), "tool": str(tool), "result": rendered[:1200]})
        out = []
        for task_id, title, status, plan, started_at, updated_at, effectiveness_note, task_kind, parent_task_id, parent_step_id, task_depth, summary in task_rows:
            out.append({
                "task_id": str(task_id), "title": str(title), "status": str(status),
                "task_kind": str(task_kind or "root"), "parent_task_id": str(parent_task_id or ""),
                "parent_step_id": str(parent_step_id or ""), "task_depth": int(task_depth or 0),
                "plan": plan if isinstance(plan, dict) else {},
                "original_request": self._original_request_from_plan(plan if isinstance(plan, dict) else {}),
                "started_at": started_at, "updated_at": updated_at,
                "terminal_summary": str(summary or ""), "effectiveness_note": str(effectiveness_note or ""),
                "steps": steps.get(str(task_id), []), "evidence": evidence.get(str(task_id), []),
            })
        return out

    def upsert_task_history(self, records: list[dict], *, validated: bool = False) -> list[str]:
        primary_ids: list[str] = []
        if not records:
            return primary_ids
        with self._connect() as conn, conn.cursor() as cur:
            s = sql.Identifier(self.schema)
            for record in records:
                primary = str(record["primary_task_id"])
                primary_ids.append(primary)
                cur.execute(sql.SQL("""
                    INSERT INTO {}.task_history(
                        primary_task_id,task_date,title,status,original_request,outcome,lessons,future_note,source_task_ids,validated,
                        task_kind,parent_task_id,parent_step_id,task_depth
                    ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT(primary_task_id) DO UPDATE SET
                        task_date=EXCLUDED.task_date,title=EXCLUDED.title,status=EXCLUDED.status,
                        original_request=EXCLUDED.original_request,outcome=EXCLUDED.outcome,lessons=EXCLUDED.lessons,
                        future_note=EXCLUDED.future_note,source_task_ids=EXCLUDED.source_task_ids,
                        task_kind=EXCLUDED.task_kind,parent_task_id=EXCLUDED.parent_task_id,parent_step_id=EXCLUDED.parent_step_id,task_depth=EXCLUDED.task_depth,
                        validated=EXCLUDED.validated,archived_at=now()
                """).format(s), (
                    primary, record["task_date"], str(record["title"]), str(record["status"]),
                    str(record["original_request"]), str(record["outcome"]), str(record["lessons"]),
                    str(record["future_note"]), Jsonb(list(record["source_task_ids"])), bool(validated),
                    str(record.get("task_kind") or "root"), str(record.get("parent_task_id") or "") or None,
                    str(record.get("parent_step_id") or "") or None, int(record.get("task_depth") or 0),
                ))
        return primary_ids

    def mark_task_history_validated(self, primary_task_ids: list[str]) -> None:
        if not primary_task_ids:
            return
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(sql.SQL("UPDATE {}.task_history SET validated=true, archived_at=now() WHERE primary_task_id=ANY(%s)").format(
                sql.Identifier(self.schema)
            ), (primary_task_ids,))

    def delete_validated_archived_tasks(self, task_ids: list[str]) -> int:
        task_ids = [str(v) for v in dict.fromkeys(task_ids)]
        if not task_ids:
            return 0
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(sql.SQL("SELECT primary_task_id,source_task_ids FROM {}.task_history WHERE validated=true").format(sql.Identifier(self.schema)))
            covered: set[str] = set()
            for primary, source_ids in cur.fetchall():
                covered.add(str(primary))
                if isinstance(source_ids, list):
                    covered.update(str(v) for v in source_ids)
            missing = sorted(set(task_ids) - covered)
            if missing:
                raise RuntimeError(f"refusing to delete tasks not covered by validated task history: {missing[:5]}")
            cur.execute(sql.SQL("DELETE FROM {}.task_runs WHERE task_id=ANY(%s)").format(sql.Identifier(self.schema)), (task_ids,))
            return int(cur.rowcount)

    def delete_superseded_memories(self) -> int:
        with self._connect() as conn, conn.cursor() as cur:
            s = sql.Identifier(self.schema)
            cur.execute(sql.SQL("""
                DELETE FROM {}.memory_items old
                WHERE old.status='superseded' AND old.superseded_by IS NOT NULL
                  AND EXISTS(SELECT 1 FROM {}.memory_items newer WHERE newer.memory_id=old.superseded_by AND newer.status='active')
            """).format(s, s))
            return int(cur.rowcount)

    def prune_covered_conversation_history(self, retention_days: int = 30) -> dict:
        cutoff = datetime.now(timezone.utc) - timedelta(days=max(1, int(retention_days)))
        with self._connect() as conn, conn.cursor() as cur:
            s = sql.Identifier(self.schema)
            cur.execute(sql.SQL("""
                WITH deletable AS (
                    SELECT m.message_id FROM {}.messages m
                    WHERE m.created_at < %s
                      AND EXISTS(SELECT 1 FROM {}.message_threads mt0 WHERE mt0.message_id=m.message_id)
                      AND NOT EXISTS(
                        SELECT 1 FROM {}.message_threads mt
                        WHERE mt.message_id=m.message_id
                          AND NOT EXISTS(
                            SELECT 1 FROM {}.thread_summaries ts
                            JOIN {}.messages covered ON covered.message_id=ts.covers_through_message_id
                            WHERE ts.thread_id=mt.thread_id AND covered.created_at >= m.created_at
                          )
                      )
                )
                DELETE FROM {}.messages WHERE message_id IN (SELECT message_id FROM deletable)
            """).format(s, s, s, s, s, s), (cutoff,))
            messages_deleted = int(cur.rowcount)
            cur.execute(sql.SQL("""
                DELETE FROM {}.thread_summaries old
                WHERE old.created_at < %s
                  AND EXISTS(SELECT 1 FROM {}.thread_summaries newer WHERE newer.thread_id=old.thread_id AND newer.version>old.version)
            """).format(s, s), (cutoff,))
            summaries_deleted = int(cur.rowcount)
        return {"messages_deleted": messages_deleted, "thread_summaries_deleted": summaries_deleted}

    def keep_latest_background_snapshot(self) -> int:
        with self._connect() as conn, conn.cursor() as cur:
            s = sql.Identifier(self.schema)
            cur.execute(sql.SQL("DELETE FROM {}.background_memory_snapshots WHERE id <> COALESCE((SELECT id FROM {}.background_memory_snapshots ORDER BY created_at DESC LIMIT 1), -1)").format(s, s))
            return int(cur.rowcount)

    def create_sql_backup(self, backup_dir: str) -> str:
        target_dir = Path(backup_dir)
        target_dir.mkdir(parents=True, exist_ok=True)
        pg_dump_candidates = [
            Path(r"C:\Program Files\PostgreSQL\17\bin\pg_dump.exe"),
            Path(r"C:\Program Files\PostgreSQL\16\bin\pg_dump.exe"),
        ]
        pg_dump = next((p for p in pg_dump_candidates if p.is_file()), None)
        if pg_dump is None:
            raise RuntimeError("pg_dump.exe not found; destructive history maintenance refused")
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%SZ")
        out = target_dir / f"norm_runtime-pre-prune-{stamp}.sql"
        cmd = [
            str(pg_dump), f"--dbname={self.conninfo}", f"--schema={self.schema}",
            "--format=p", "--no-password", "--file=" + str(out),
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        if proc.returncode != 0 or not out.is_file() or out.stat().st_size < 1024:
            try:
                out.unlink(missing_ok=True)
            except OSError:
                pass
            raise RuntimeError("SQL backup failed; destructive history maintenance refused: " + proc.stderr.strip()[:600])
        return str(out)

    @staticmethod
    def delete_sql_backup(path: str) -> None:
        Path(path).unlink(missing_ok=True)

    def relevant_task_history(self, query: str, limit: int = 6, *, include_unvalidated: bool = False) -> list[dict]:
        query = str(query or "").strip()
        if not query:
            return []
        validated_clause = sql.SQL("") if include_unvalidated else sql.SQL("AND validated=true")
        vector = "to_tsvector('english', coalesce(title,'') || ' ' || coalesce(original_request,'') || ' ' || coalesce(outcome,'') || ' ' || coalesce(lessons,'') || ' ' || coalesce(future_note,''))"
        with self._connect() as conn, conn.cursor() as cur:
            statement = sql.SQL(f"""
                SELECT primary_task_id,task_date,title,status,original_request,outcome,lessons,future_note,source_task_ids,validated,
                       task_kind,parent_task_id,parent_step_id,task_depth,
                       ts_rank_cd({vector}, websearch_to_tsquery('english', %s)) AS rank
                FROM {{}}.task_history
                WHERE {vector} @@ websearch_to_tsquery('english', %s) {{}}
                ORDER BY rank DESC, task_date DESC LIMIT %s
            """).format(sql.Identifier(self.schema), validated_clause)
            cur.execute(statement, (query, query, max(1, int(limit))))
            rows = cur.fetchall()
        return [{
            "task_id": str(r[0]), "date": r[1].isoformat(), "title": str(r[2]), "status": str(r[3]),
            "request": str(r[4]), "outcome": str(r[5]), "lessons": str(r[6]), "future_note": str(r[7]),
            "source_task_ids": list(r[8] or []), "validated": bool(r[9]),
            "task_kind": str(r[10] or "root"), "parent_task_id": str(r[11] or ""),
            "parent_step_id": str(r[12] or ""), "task_depth": int(r[13] or 0), "rank": float(r[14] or 0.0),
        } for r in rows]

    def background_memory_source(self) -> list[dict]:
        records: list[dict] = []
        with self._connect() as conn, conn.cursor() as cur:
            s = sql.Identifier(self.schema)
            cur.execute(sql.SQL("""
                SELECT ts.created_at, ts.task_id, tr.title, tr.status, ts.summary
                FROM {}.task_summaries ts JOIN {}.task_runs tr ON tr.task_id=ts.task_id
                WHERE tr.task_kind <> 'child'
                ORDER BY ts.created_at
            """).format(s, s))
            for created_at, task_id, title, status, summary in cur.fetchall():
                records.append({"at": created_at, "source": "task_summary", "text": f"Task {task_id} [{status}] {title}: {summary}"})
            cur.execute(sql.SQL("""
                SELECT tr.updated_at,tr.task_id,tr.title,tr.status,tr.effectiveness_note,tr.parent_task_id,tr.parent_step_id,tr.task_depth
                FROM {}.task_runs tr
                WHERE tr.task_kind='child' AND tr.effectiveness_note IS NOT NULL AND btrim(tr.effectiveness_note) <> ''
                ORDER BY tr.updated_at
            """).format(s))
            for updated_at, task_id, title, status, note, parent_task_id, parent_step_id, task_depth in cur.fetchall():
                records.append({
                    "at": updated_at, "source": "child_effectiveness",
                    "text": f"Child task {task_id} [{status}] {title}; parent={parent_task_id}/{parent_step_id}; depth={int(task_depth or 0)}; lesson={note}",
                })
            cur.execute(sql.SQL("""
                SELECT archived_at,primary_task_id,task_date,title,status,original_request,outcome,lessons,future_note,source_task_ids,
                       task_kind,parent_task_id,parent_step_id,task_depth
                FROM {}.task_history WHERE validated=true ORDER BY task_date,archived_at
            """).format(s))
            for archived_at, task_id, task_date, title, status, request, outcome, lessons, future_note, source_ids, task_kind, parent_task_id, parent_step_id, task_depth in cur.fetchall():
                lineage = f"; kind={task_kind or 'root'}; depth={int(task_depth or 0)}"
                if parent_task_id:
                    lineage += f"; parent={parent_task_id}/{parent_step_id or ''}"
                records.append({
                    "at": archived_at, "source": "task_history",
                    "text": f"Archived task {task_id} ({task_date}) [{status}] {title}{lineage}; request={request}; outcome={outcome}; lessons={lessons}; next_time={future_note}; source_task_ids={list(source_ids or [])}",
                })
            cur.execute(sql.SQL("""
                SELECT m.created_at, m.message_id, m.role, m.content
                FROM {}.messages m ORDER BY m.created_at
            """).format(s))
            for created_at, message_id, role, content in cur.fetchall():
                records.append({"at": created_at, "source": "conversation_message", "text": f"Message {message_id} [{role}]: {content}"})
            cur.execute(sql.SQL("""
                SELECT DISTINCT ON (ts.thread_id) ts.created_at, ts.thread_id, t.title, ts.summary
                FROM {}.thread_summaries ts JOIN {}.threads t ON t.thread_id=ts.thread_id
                ORDER BY ts.thread_id, ts.version DESC
            """).format(s, s))
            for created_at, thread_id, title, summary in cur.fetchall():
                records.append({"at": created_at, "source": "thread_summary", "text": f"Thread {thread_id} ({title}): {summary}"})
            cur.execute(sql.SQL("""
                SELECT updated_at, memory_id, memory_type, status, content, superseded_by
                FROM {}.memory_items ORDER BY updated_at
            """).format(s))
            for updated_at, memory_id, memory_type, status, content, superseded_by in cur.fetchall():
                suffix = f"; superseded_by={superseded_by}" if superseded_by else ""
                records.append({"at": updated_at, "source": "memory_item", "text": f"Memory {memory_id} [{memory_type}/{status}]{suffix}: {content}"})
            cur.execute(sql.SQL("""
                SELECT completed_at, task_id, step_id, name, status, summary, verification, error
                FROM {}.task_steps
                WHERE status IN ('failed','cancelled') OR error IS NOT NULL
                ORDER BY completed_at
            """).format(s))
            for at, task_id, step_id, name, status, summary, verification, error in cur.fetchall():
                records.append({"at": at, "source": "task_failure", "text": f"Task {task_id} {step_id} {name} [{status}]: {summary}; verification={verification}; error={error or ''}"})
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(sql.SQL("SELECT created_at, phase, task_id, note, details FROM {}.runtime_maintenance_notes ORDER BY created_at").format(sql.Identifier(self.schema)))
            for created_at, phase, task_id, note, details in cur.fetchall():
                task_suffix = f" task={task_id}" if task_id else ""
                records.append({"at": created_at, "source": "runtime_maintenance",
                                "text": f"Runtime maintenance [{phase}]{task_suffix}: {note}; details={details or {}}"})
        records.sort(key=lambda item: item["at"])
        return records

    def latest_background_snapshot(self) -> dict | None:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(sql.SQL("SELECT summary, source_through, created_at FROM {}.background_memory_snapshots ORDER BY created_at DESC LIMIT 1").format(sql.Identifier(self.schema)))
            row = cur.fetchone()
            if not row:
                return None
            return {"summary": str(row[0]), "source_through": row[1], "created_at": row[2]}

    def save_background_snapshot(self, summary: str, source_through) -> None:
        if not summary.strip():
            raise ValueError("background memory summary must not be blank")
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(sql.SQL("INSERT INTO {}.background_memory_snapshots(summary, source_through) VALUES (%s,%s)").format(sql.Identifier(self.schema)), (summary.strip(), source_through))

    def needs_background_consolidation(self, days: int = 7) -> bool:
        snapshot = self.latest_background_snapshot()
        if snapshot is None:
            return True
        from datetime import datetime, timezone, timedelta
        return snapshot["created_at"] <= datetime.now(timezone.utc) - timedelta(days=max(1, int(days)))

    def background_memory_context(self, max_chars: int = 32000) -> str:
        snapshot = self.latest_background_snapshot()
        records = self.background_memory_source()
        parts: list[str] = []
        source_through = None
        if snapshot:
            parts.append("CONSOLIDATED BACKGROUND MEMORY:\n" + snapshot["summary"])
            source_through = snapshot["source_through"]
        delta = [r for r in records if source_through is None or r["at"] > source_through]
        if delta:
            parts.append("NEWER POSTGRESQL MEMORY SINCE CONSOLIDATION:\n" + "\n".join(r["text"] for r in delta))
        text = "\n\n".join(parts)
        if len(text) <= max_chars:
            return text
        return "[older background detail compacted/truncated for prompt budget]\n" + text[-max_chars:]

    def all_steps_completed(self, task_id: str) -> bool:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(sql.SQL("SELECT plan FROM {}.task_runs WHERE task_id=%s").format(
                sql.Identifier(self.schema)
            ), (task_id,))
            row = cur.fetchone()
            if not row:
                return False
            expected = {str(s.get("id")) for s in row[0].get("steps", [])}
            if not expected:
                return False
            cur.execute(sql.SQL("SELECT step_id FROM {}.task_steps WHERE task_id=%s AND status='completed'").format(
                sql.Identifier(self.schema)
            ), (task_id,))
            completed = {str(r[0]) for r in cur.fetchall()}
            return expected.issubset(completed)
