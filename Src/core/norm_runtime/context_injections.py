"""Durable, task-scoped operator context with auditable retirement.

Historical injections (created before this schema) remain persistent. New injections
may opt into retirement *after a verifier accepts evidence of fulfillment*.
"""
from __future__ import annotations

import uuid
from psycopg import sql


class ContextInjections:
    def __init__(self, durable):
        self.durable = durable

    def ensure_schema(self):
        schema = sql.Identifier(self.durable.schema)
        with self.durable._connect() as conn, conn.cursor() as cur:
            cur.execute("SET LOCAL lock_timeout = '5s'")
            cur.execute("SET LOCAL statement_timeout = '120s'")
            cur.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (f"norm:schema:{self.durable.schema}",))
            cur.execute(sql.SQL('''CREATE TABLE IF NOT EXISTS {}.task_context_injections (
                injection_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                request_id uuid NOT NULL UNIQUE,
                root_task_id text NOT NULL REFERENCES {}.task_runs(task_id) ON DELETE CASCADE,
                content text NOT NULL CHECK(length(content) BETWEEN 1 AND 8000),
                created_at timestamptz NOT NULL DEFAULT now(),
                retire_when_satisfied boolean NOT NULL DEFAULT true,
                status text NOT NULL DEFAULT 'active',
                status_updated_at timestamptz,
                retired_by_step text,
                retirement_reason text
            )''').format(schema, schema))
            # Safe in-place migration: do not implicitly change legacy injection semantics.
            for declaration in (
                "retire_when_satisfied boolean NOT NULL DEFAULT false",
                "status text NOT NULL DEFAULT 'active'",
                "status_updated_at timestamptz",
                "retired_by_step text",
                "retirement_reason text",
            ):
                cur.execute(sql.SQL("ALTER TABLE {}.task_context_injections ADD COLUMN IF NOT EXISTS ").format(schema) + sql.SQL(declaration))
            cur.execute(sql.SQL('''CREATE INDEX IF NOT EXISTS task_context_active_v1
                ON {}.task_context_injections(root_task_id, injection_id)
                WHERE status='active' ''').format(schema))

    def save(self, task_id, content, request_id, retire_when_satisfied=True):
        content = str(content or '').strip()
        if not content or len(content) > 8000:
            raise ValueError('Context must contain 1–8000 characters')
        request_id = str(uuid.UUID(str(request_id)))
        root = self.durable.root_task_id(task_id)
        if not root:
            raise ValueError('Unknown task ID')
        with self.durable._connect() as conn, conn.cursor() as cur:
            cur.execute(sql.SQL('SELECT status FROM {}.task_runs WHERE task_id=%s FOR SHARE').format(sql.Identifier(self.durable.schema)), (root,))
            row = cur.fetchone()
            if not row or row[0] in ('completed', 'failed', 'cancelled'):
                raise ValueError('Task tree is already terminal; choose an unfinished task')
            cur.execute(sql.SQL('''INSERT INTO {}.task_context_injections
                (request_id,root_task_id,content,retire_when_satisfied)
                VALUES(%s::uuid,%s,%s,%s) ON CONFLICT(request_id) DO NOTHING
                RETURNING injection_id''').format(sql.Identifier(self.durable.schema)),
                (request_id, root, content, bool(retire_when_satisfied)))
            inserted = cur.fetchone()
            if inserted:
                injection_id = inserted[0]
            else:
                cur.execute(sql.SQL('''SELECT injection_id,root_task_id,content,retire_when_satisfied
                    FROM {}.task_context_injections WHERE request_id=%s::uuid''').format(sql.Identifier(self.durable.schema)), (request_id,))
                existing = cur.fetchone()
                if existing[1:] != (root, content, bool(retire_when_satisfied)):
                    raise ValueError('Request ID was already used for different context')
                injection_id = existing[0]
        return {'status': 'ok', 'task_id': root, 'injection_id': injection_id,
                'retire_when_satisfied': bool(retire_when_satisfied),
                'delivery': 'next_model_boundary', 'task_resumed': False}

    def read(self, task_id, after=0):
        """All currently active injections for a task tree, not stale job-local copies.

        ``after`` only filters fresh delivery; use 0 at each worker model
        boundary so retirement is reflected even in already-running jobs.
        """
        root = self.durable.root_task_id(task_id)
        if not root:
            return []
        with self.durable._connect() as conn, conn.cursor() as cur:
            cur.execute(sql.SQL('''SELECT injection_id,content,retire_when_satisfied
                FROM {}.task_context_injections
                WHERE root_task_id=%s AND status='active' AND injection_id>%s
                ORDER BY injection_id''').format(sql.Identifier(self.durable.schema)), (root, int(after)))
            return [{'id': row[0], 'content': row[1],
                     'retire_when_satisfied': bool(row[2])} for row in cur.fetchall()]

    def list(self, task_id):
        root = self.durable.root_task_id(task_id)
        if not root:
            raise ValueError('Unknown task ID')
        with self.durable._connect() as conn, conn.cursor() as cur:
            cur.execute(sql.SQL('''SELECT injection_id,content,retire_when_satisfied,status,
                  status_updated_at,retired_by_step,retirement_reason
                  FROM {}.task_context_injections WHERE root_task_id=%s ORDER BY injection_id''').format(sql.Identifier(self.durable.schema)), (root,))
            rows = cur.fetchall()
        return {'task_id': root, 'injections': [{
            'id': r[0], 'content': r[1], 'retire_when_satisfied': bool(r[2]),
            'status': r[3], 'status_updated_at': r[4].isoformat() if r[4] else None,
            'retired_by_step': r[5], 'retirement_reason': r[6],
        } for r in rows]}

    def retire(self, task_id, injection_id, *, step_id='', reason='operator', verified=False):
        root = self.durable.root_task_id(task_id)
        if not root:
            raise ValueError('Unknown task ID')
        iid = int(injection_id)
        if iid <= 0:
            raise ValueError('Invalid injection ID')
        status = 'satisfied' if verified else 'superseded'
        with self.durable._connect() as conn, conn.cursor() as cur:
            cur.execute(sql.SQL('''UPDATE {}.task_context_injections
                 SET status=%s,status_updated_at=now(),retired_by_step=%s,retirement_reason=%s
                 WHERE injection_id=%s AND root_task_id=%s AND status='active'
                   AND (%s = false OR retire_when_satisfied = true)
                 RETURNING injection_id''').format(sql.Identifier(self.durable.schema)),
                (status, str(step_id)[:160], str(reason)[:400], iid, root, bool(verified)))
            result = cur.fetchone()
        return {'task_id': root, 'injection_id': iid, 'retired': bool(result), 'status': status if result else 'unchanged'}

    def restore(self, task_id, injection_id):
        root = self.durable.root_task_id(task_id)
        if not root:
            raise ValueError('Unknown task ID')
        with self.durable._connect() as conn, conn.cursor() as cur:
            cur.execute(sql.SQL('''UPDATE {}.task_context_injections
                SET status='active',status_updated_at=now(),retired_by_step=NULL,retirement_reason=NULL
                WHERE injection_id=%s AND root_task_id=%s AND status IN ('satisfied','superseded')
                RETURNING injection_id''').format(sql.Identifier(self.durable.schema)), (int(injection_id), root))
            result = cur.fetchone()
        return {'task_id': root, 'injection_id': int(injection_id), 'restored': bool(result)}
