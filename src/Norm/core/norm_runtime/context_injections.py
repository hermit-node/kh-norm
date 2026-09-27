"""Durable operator context scoped to an existing task tree."""
import uuid
from psycopg import sql


class ContextInjections:
    def __init__(self, durable):
        self.durable = durable

    def ensure_schema(self):
        with self.durable._connect() as conn, conn.cursor() as cur:
            cur.execute(sql.SQL('''CREATE TABLE IF NOT EXISTS {}.task_context_injections (
                injection_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                request_id uuid NOT NULL UNIQUE,
                root_task_id text NOT NULL REFERENCES {}.task_runs(task_id) ON DELETE CASCADE,
                content text NOT NULL CHECK(length(content) BETWEEN 1 AND 8000),
                created_at timestamptz NOT NULL DEFAULT now()
            )''').format(sql.Identifier(self.durable.schema), sql.Identifier(self.durable.schema)))

    def save(self, task_id, content, request_id):
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
            cur.execute(sql.SQL('''INSERT INTO {}.task_context_injections(request_id,root_task_id,content)
                VALUES(%s::uuid,%s,%s) ON CONFLICT(request_id) DO NOTHING
                RETURNING injection_id''').format(sql.Identifier(self.durable.schema)), (request_id, root, content))
            inserted = cur.fetchone()
            if inserted:
                injection_id = inserted[0]
            else:
                cur.execute(sql.SQL('SELECT injection_id,root_task_id,content FROM {}.task_context_injections WHERE request_id=%s::uuid').format(sql.Identifier(self.durable.schema)), (request_id,))
                existing = cur.fetchone()
                if existing[1:] != (root, content):
                    raise ValueError('Request ID was already used for different context')
                injection_id = existing[0]
        return {'status': 'ok', 'task_id': root, 'injection_id': injection_id,
                'delivery': 'next_model_boundary', 'task_resumed': False}

    def read(self, task_id, after=0):
        root = self.durable.root_task_id(task_id)
        if not root:
            return []
        with self.durable._connect() as conn, conn.cursor() as cur:
            cur.execute(sql.SQL('SELECT injection_id,content FROM {}.task_context_injections WHERE root_task_id=%s AND injection_id>%s ORDER BY injection_id').format(sql.Identifier(self.durable.schema)), (root, after))
            return [{'id': row[0], 'content': row[1]} for row in cur.fetchall()]
