from __future__ import annotations

import hashlib
import re
import uuid

from psycopg import sql

from .integrity_repair import repair_memory_threads_cur
_SCHEMA_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_MEMORY_TYPES = {"fact", "preference", "decision", "constraint", "task", "assumption"}


class ConversationStore:
    def __init__(self, pool, schema: str = "norm_runtime", connection_name: str = "norm") -> None:
        if pool is None or not hasattr(pool, "connection"):
            raise ValueError("ConversationStore requires the configured postgres_pool tool")
        if not _SCHEMA_RE.fullmatch(schema):
            raise ValueError("Unsafe PostgreSQL schema name")
        self.pool = pool
        self.connection_name = str(connection_name or "norm")
        self.schema = schema

    def _connect(self):
        return self.pool.connection(self.connection_name)

    def ensure_schema(self) -> None:
        with self._connect() as conn, conn.cursor() as cur:
            # Startup migrations must never wait forever behind a stale transaction.
            cur.execute("SET LOCAL lock_timeout = '5s'")
            cur.execute("SET LOCAL statement_timeout = '120s'")
            cur.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (f"norm:schema:{self.schema}",))
            s = sql.Identifier(self.schema)
            cur.execute(sql.SQL("CREATE SCHEMA IF NOT EXISTS {}").format(s))
            cur.execute(sql.SQL("""CREATE TABLE IF NOT EXISTS {}.projects (
                project_id text PRIMARY KEY, name text NOT NULL,
                created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now()
            )""").format(s))
            cur.execute(sql.SQL("""CREATE TABLE IF NOT EXISTS {}.threads (
                thread_id text PRIMARY KEY, project_id text NOT NULL REFERENCES {}.projects(project_id) ON DELETE CASCADE,
                parent_thread_id text REFERENCES {}.threads(thread_id) ON DELETE SET NULL,
                title text NOT NULL, status text NOT NULL DEFAULT 'active',
                created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now()
            )""").format(s, s, s))
            cur.execute(sql.SQL("""CREATE TABLE IF NOT EXISTS {}.messages (
                message_id text PRIMARY KEY, role text NOT NULL CHECK (role IN ('user','assistant','system')),
                content text NOT NULL, created_at timestamptz NOT NULL DEFAULT now()
            )""").format(s))
            cur.execute(sql.SQL("""CREATE TABLE IF NOT EXISTS {}.message_threads (
                message_id text NOT NULL REFERENCES {}.messages(message_id) ON DELETE CASCADE,
                thread_id text NOT NULL REFERENCES {}.threads(thread_id) ON DELETE CASCADE,
                is_primary boolean NOT NULL DEFAULT false, relevance double precision NOT NULL DEFAULT 1.0,
                PRIMARY KEY (message_id, thread_id)
            )""").format(s, s, s))
            cur.execute(sql.SQL("""CREATE TABLE IF NOT EXISTS {}.thread_summaries (
                summary_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                thread_id text NOT NULL REFERENCES {}.threads(thread_id) ON DELETE CASCADE,
                version integer NOT NULL, summary text NOT NULL, covers_through_message_id text,
                created_at timestamptz NOT NULL DEFAULT now(), UNIQUE(thread_id, version)
            )""").format(s, s))
            cur.execute(sql.SQL("""CREATE TABLE IF NOT EXISTS {}.memory_items (
                memory_id text PRIMARY KEY, project_id text NOT NULL REFERENCES {}.projects(project_id) ON DELETE CASCADE,
                memory_type text NOT NULL CHECK (memory_type IN ('fact','preference','decision','constraint','task','assumption')),
                content text NOT NULL, content_hash text NOT NULL, status text NOT NULL DEFAULT 'active' CHECK (status IN ('active','superseded')),
                source_message_id text REFERENCES {}.messages(message_id) ON DELETE SET NULL,
                superseded_by text REFERENCES {}.memory_items(memory_id) ON DELETE SET NULL,
                created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(),
                UNIQUE(project_id, memory_type, content_hash)
            )""").format(s, s, s, s))
            cur.execute(sql.SQL("""CREATE TABLE IF NOT EXISTS {}.memory_threads (
                memory_id text NOT NULL REFERENCES {}.memory_items(memory_id) ON DELETE CASCADE,
                thread_id text NOT NULL REFERENCES {}.threads(thread_id) ON DELETE CASCADE,
                relevance double precision NOT NULL DEFAULT 1.0, PRIMARY KEY(memory_id, thread_id)
            )""").format(s, s, s))
            cur.execute(sql.SQL("""CREATE TABLE IF NOT EXISTS {}.unresolved_bits (
                bit_id text PRIMARY KEY,
                project_id text NOT NULL REFERENCES {}.projects(project_id) ON DELETE CASCADE,
                source_message_id text REFERENCES {}.messages(message_id) ON DELETE SET NULL,
                last_source_message_id text REFERENCES {}.messages(message_id) ON DELETE SET NULL,
                detail_type text NOT NULL DEFAULT 'ingrained_detail',
                verbatim text NOT NULL,
                normalized text NOT NULL,
                content_hash text NOT NULL,
                why_separate text NOT NULL DEFAULT '',
                suggested_destination text NOT NULL DEFAULT '',
                confidence double precision NOT NULL DEFAULT 0.0,
                source_thread_ids text[] NOT NULL DEFAULT ARRAY[]::text[],
                mention_count integer NOT NULL DEFAULT 1,
                created_at timestamptz NOT NULL DEFAULT now(),
                updated_at timestamptz NOT NULL DEFAULT now(),
                last_considered_at timestamptz,
                UNIQUE(project_id, content_hash)
            )""").format(s, s, s, s))
            cur.execute(sql.SQL("""CREATE TABLE IF NOT EXISTS {}.unresolved_bit_trials (
                trial_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                bit_id text NOT NULL REFERENCES {}.unresolved_bits(bit_id) ON DELETE CASCADE,
                task_id text NOT NULL,
                task_domain text NOT NULL DEFAULT 'unknown',
                outcome text NOT NULL,
                reason text NOT NULL DEFAULT '',
                useful boolean NOT NULL DEFAULT false,
                considered_at timestamptz NOT NULL DEFAULT now()
            )""").format(s, s))
            cur.execute(sql.SQL("CREATE INDEX IF NOT EXISTS {} ON {}.threads(project_id, updated_at DESC)").format(
                sql.Identifier(f"idx_{self.schema}_threads_project"), s))
            cur.execute(sql.SQL("CREATE INDEX IF NOT EXISTS {} ON {}.message_threads(thread_id)").format(
                sql.Identifier(f"idx_{self.schema}_message_threads_thread"), s))
            cur.execute(sql.SQL("CREATE INDEX IF NOT EXISTS {} ON {}.memory_items(project_id, status)").format(
                sql.Identifier(f"idx_{self.schema}_memory_project_status"), s))
            cur.execute(sql.SQL("CREATE INDEX IF NOT EXISTS {} ON {}.unresolved_bits(project_id, last_considered_at, created_at)").format(
                sql.Identifier(f"idx_{self.schema}_unresolved_bits_project"), s))
            cur.execute(sql.SQL("CREATE INDEX IF NOT EXISTS {} ON {}.unresolved_bit_trials(bit_id, considered_at)").format(
                sql.Identifier(f"idx_{self.schema}_unresolved_trials_bit"), s))
            repair_memory_threads_cur(cur, self.schema)

    def ensure_project(self, project_id: str, name: str | None = None) -> None:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(sql.SQL("""INSERT INTO {}.projects(project_id,name) VALUES (%s,%s)
                ON CONFLICT(project_id) DO UPDATE SET name=EXCLUDED.name, updated_at=now()""").format(sql.Identifier(self.schema)),
                (project_id, name or project_id))

    def create_thread(self, project_id: str, title: str, parent_thread_id: str | None = None, thread_id: str | None = None) -> str:
        thread_id = thread_id or str(uuid.uuid4())
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(sql.SQL("INSERT INTO {}.threads(thread_id,project_id,parent_thread_id,title) VALUES (%s,%s,%s,%s)").format(
                sql.Identifier(self.schema)), (thread_id, project_id, parent_thread_id, title))
        return thread_id

    def thread_exists(self, project_id: str, thread_id: str) -> bool:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                sql.SQL("SELECT 1 FROM {}.threads WHERE project_id=%s AND thread_id=%s AND status='active'").format(
                    sql.Identifier(self.schema)
                ),
                (project_id, thread_id),
            )
            return cur.fetchone() is not None

    def list_threads(self, project_id: str, limit: int = 12) -> list[dict]:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(sql.SQL("""SELECT t.thread_id,t.title,t.parent_thread_id,t.updated_at,
                (SELECT ts.summary FROM {}.thread_summaries ts WHERE ts.thread_id=t.thread_id ORDER BY version DESC LIMIT 1)
                FROM {}.threads t WHERE t.project_id=%s AND t.status='active' ORDER BY t.updated_at DESC LIMIT %s""").format(
                sql.Identifier(self.schema), sql.Identifier(self.schema)), (project_id, limit))
            return [{"thread_id":r[0],"title":r[1],"parent_thread_id":r[2],"updated_at":r[3].isoformat(),"summary":r[4] or ""} for r in cur.fetchall()]

    def add_message(self, role: str, content: str, thread_ids: list[str], primary_thread_id: str) -> str:
        message_id = str(uuid.uuid4())
        with self._connect() as conn, conn.cursor() as cur:
            s = sql.Identifier(self.schema)
            cur.execute(sql.SQL("INSERT INTO {}.messages(message_id,role,content) VALUES (%s,%s,%s)").format(s), (message_id, role, content))
            for thread_id in dict.fromkeys(thread_ids):
                cur.execute(sql.SQL("INSERT INTO {}.message_threads(message_id,thread_id,is_primary) VALUES (%s,%s,%s)").format(s),
                    (message_id, thread_id, thread_id == primary_thread_id))
                cur.execute(sql.SQL("UPDATE {}.threads SET updated_at=now() WHERE thread_id=%s").format(s), (thread_id,))
        return message_id

    def recent_messages(self, thread_id: str, limit: int = 12) -> list[dict]:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(sql.SQL("""SELECT m.message_id,m.role,m.content,m.created_at FROM {}.messages m
                JOIN {}.message_threads mt ON mt.message_id=m.message_id WHERE mt.thread_id=%s
                ORDER BY m.created_at DESC LIMIT %s""").format(sql.Identifier(self.schema), sql.Identifier(self.schema)), (thread_id, limit))
            rows = cur.fetchall()[::-1]
            return [{"message_id":r[0],"role":r[1],"content":r[2],"created_at":r[3].isoformat()} for r in rows]

    def latest_summary(self, thread_id: str) -> str:
        state = self.latest_summary_state(thread_id)
        return str(state.get("summary") or "")

    def latest_summary_state(self, thread_id: str) -> dict:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(sql.SQL(
                "SELECT summary,covers_through_message_id,version,created_at "
                "FROM {}.thread_summaries WHERE thread_id=%s ORDER BY version DESC LIMIT 1"
            ).format(sql.Identifier(self.schema)), (thread_id,))
            row = cur.fetchone()
        if not row:
            return {
                "summary": "",
                "covers_through_message_id": None,
                "version": 0,
                "created_at": None,
            }
        return {
            "summary": str(row[0] or ""),
            "covers_through_message_id": str(row[1]) if row[1] else None,
            "version": int(row[2] or 0),
            "created_at": row[3].isoformat() if row[3] else None,
        }

    def messages_since(
        self,
        thread_id: str,
        after_message_id: str | None,
        *,
        through_message_id: str | None = None,
    ) -> list[dict]:
        """Return exact thread messages after the last successfully covered message.

        This is intentionally cursor-based rather than a fixed "last N" window so a
        failed summary refresh cannot permanently lose later updates.
        """
        with self._connect() as conn, conn.cursor() as cur:
            s = sql.Identifier(self.schema)
            lower = None
            upper = None
            if after_message_id:
                cur.execute(sql.SQL(
                    "SELECT m.created_at,m.message_id FROM {}.messages m "
                    "JOIN {}.message_threads mt ON mt.message_id=m.message_id "
                    "WHERE mt.thread_id=%s AND m.message_id=%s LIMIT 1"
                ).format(s, s), (thread_id, after_message_id))
                lower = cur.fetchone()
            if through_message_id:
                cur.execute(sql.SQL(
                    "SELECT m.created_at,m.message_id FROM {}.messages m "
                    "JOIN {}.message_threads mt ON mt.message_id=m.message_id "
                    "WHERE mt.thread_id=%s AND m.message_id=%s LIMIT 1"
                ).format(s, s), (thread_id, through_message_id))
                upper = cur.fetchone()

            clauses = [sql.SQL("mt.thread_id=%s")]
            params: list = [thread_id]
            if lower:
                clauses.append(sql.SQL("(m.created_at,m.message_id) > (%s,%s)"))
                params.extend([lower[0], lower[1]])
            if upper:
                clauses.append(sql.SQL("(m.created_at,m.message_id) <= (%s,%s)"))
                params.extend([upper[0], upper[1]])
            statement = sql.SQL(
                "SELECT m.message_id,m.role,m.content,m.created_at FROM {}.messages m "
                "JOIN {}.message_threads mt ON mt.message_id=m.message_id WHERE "
            ).format(s, s) + sql.SQL(" AND ").join(clauses) + sql.SQL(
                " ORDER BY m.created_at,m.message_id"
            )
            cur.execute(statement, tuple(params))
            rows = cur.fetchall()
        return [
            {
                "message_id": str(r[0]),
                "role": str(r[1]),
                "content": str(r[2]),
                "created_at": r[3].isoformat(),
            }
            for r in rows
        ]

    def save_summary(self, thread_id: str, summary: str, covers_through_message_id: str | None = None) -> None:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(sql.SQL("SELECT COALESCE(MAX(version),0)+1 FROM {}.thread_summaries WHERE thread_id=%s").format(
                sql.Identifier(self.schema)), (thread_id,))
            version = cur.fetchone()[0]
            cur.execute(sql.SQL("INSERT INTO {}.thread_summaries(thread_id,version,summary,covers_through_message_id) VALUES (%s,%s,%s,%s)").format(
                sql.Identifier(self.schema)), (thread_id, version, summary, covers_through_message_id))
            cur.execute(sql.SQL("DELETE FROM {}.thread_summaries WHERE thread_id=%s AND version < %s").format(
                sql.Identifier(self.schema)), (thread_id, version))

    def active_memories(self, project_id: str, thread_ids: list[str] | None = None, limit: int = 40) -> list[dict]:
        with self._connect() as conn, conn.cursor() as cur:
            s = sql.Identifier(self.schema)
            if thread_ids:
                cur.execute(sql.SQL("""SELECT DISTINCT mi.memory_id,mi.memory_type,mi.content,mi.updated_at FROM {}.memory_items mi
                    JOIN {}.memory_threads mt ON mt.memory_id=mi.memory_id
                    WHERE mi.project_id=%s AND mi.status='active' AND mt.thread_id=ANY(%s)
                    ORDER BY mi.updated_at DESC LIMIT %s""").format(s, s), (project_id, thread_ids, limit))
            else:
                cur.execute(sql.SQL("SELECT memory_id,memory_type,content,updated_at FROM {}.memory_items WHERE project_id=%s AND status='active' ORDER BY updated_at DESC LIMIT %s").format(s),
                    (project_id, limit))
            return [{"memory_id":r[0],"type":r[1],"content":r[2],"updated_at":r[3].isoformat()} for r in cur.fetchall()]

    def upsert_memory(self, project_id: str, memory_type: str, content: str, thread_ids: list[str], source_message_id: str | None = None) -> str:
        if memory_type not in _MEMORY_TYPES:
            raise ValueError(f"Unsupported memory type: {memory_type}")
        digest = hashlib.sha256(content.strip().lower().encode("utf-8")).hexdigest()
        memory_id = str(uuid.uuid4())
        with self._connect() as conn, conn.cursor() as cur:
            s = sql.Identifier(self.schema)
            cur.execute(sql.SQL("""INSERT INTO {}.memory_items(memory_id,project_id,memory_type,content,content_hash,source_message_id)
                VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT(project_id,memory_type,content_hash) DO UPDATE
                SET content=EXCLUDED.content,status='active',source_message_id=EXCLUDED.source_message_id,updated_at=now()
                RETURNING memory_id""").format(s), (memory_id, project_id, memory_type, content, digest, source_message_id))
            memory_id = cur.fetchone()[0]
            for thread_id in dict.fromkeys(thread_ids):
                cur.execute(sql.SQL("INSERT INTO {}.memory_threads(memory_id,thread_id) VALUES (%s,%s) ON CONFLICT DO NOTHING").format(s), (memory_id, thread_id))
        return memory_id

    def supersede_memories(self, memory_ids: list[str], superseded_by: str) -> None:
        if not memory_ids:
            return
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(sql.SQL("UPDATE {}.memory_items SET status='superseded',superseded_by=%s,updated_at=now() WHERE memory_id=ANY(%s)").format(
                sql.Identifier(self.schema)), (superseded_by, memory_ids))

    def add_unresolved_bit(
        self,
        project_id: str,
        *,
        verbatim: str,
        normalized: str,
        why_separate: str = "",
        suggested_destination: str = "",
        confidence: float = 0.0,
        source_message_id: str | None = None,
        thread_ids: list[str] | None = None,
    ) -> str:
        normalized = str(normalized or verbatim or "").strip()
        verbatim = str(verbatim or normalized).strip()
        if not normalized:
            raise ValueError("unresolved bit requires non-empty content")
        digest = hashlib.sha256(normalized.lower().encode("utf-8")).hexdigest()
        bit_id = str(uuid.uuid4())
        clean_threads = [str(v) for v in dict.fromkeys(thread_ids or []) if str(v).strip()]
        with self._connect() as conn, conn.cursor() as cur:
            s = sql.Identifier(self.schema)
            cur.execute(sql.SQL("""
                INSERT INTO {}.unresolved_bits AS ub(
                    bit_id,project_id,source_message_id,last_source_message_id,detail_type,
                    verbatim,normalized,content_hash,why_separate,suggested_destination,
                    confidence,source_thread_ids
                ) VALUES (%s,%s,%s,%s,'ingrained_detail',%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT(project_id,content_hash) DO UPDATE SET
                    last_source_message_id=EXCLUDED.last_source_message_id,
                    verbatim=EXCLUDED.verbatim,normalized=EXCLUDED.normalized,
                    why_separate=EXCLUDED.why_separate,
                    suggested_destination=EXCLUDED.suggested_destination,
                    confidence=GREATEST(ub.confidence,EXCLUDED.confidence),
                    source_thread_ids=ARRAY(
                        SELECT DISTINCT value
                        FROM unnest(ub.source_thread_ids || EXCLUDED.source_thread_ids) AS merged(value)
                    ),
                    mention_count=ub.mention_count+1,updated_at=now()
                RETURNING bit_id
            """).format(s), (
                bit_id, project_id, source_message_id, source_message_id, verbatim, normalized,
                digest, str(why_separate or "")[:1200], str(suggested_destination or "")[:120],
                max(0.0, min(float(confidence or 0.0), 1.0)), clean_threads,
            ))
            return str(cur.fetchone()[0])

    def unresolved_candidates(
        self, project_id: str, *, exclude_message_id: str | None = None, limit: int = 12
    ) -> list[dict]:
        with self._connect() as conn, conn.cursor() as cur:
            s = sql.Identifier(self.schema)
            cur.execute(sql.SQL("""
                SELECT b.bit_id,b.verbatim,b.normalized,b.why_separate,b.suggested_destination,
                       b.confidence,b.source_message_id,b.last_source_message_id,b.source_thread_ids,
                       b.mention_count,b.created_at,b.last_considered_at,
                       COUNT(t.trial_id)::int AS trial_count,
                       COUNT(DISTINCT NULLIF(t.task_domain,''))::int AS distinct_domains,
                       COALESCE(SUM(CASE WHEN t.useful THEN 1 ELSE 0 END),0)::int AS useful_count
                FROM {}.unresolved_bits b
                LEFT JOIN {}.unresolved_bit_trials t ON t.bit_id=b.bit_id
                WHERE b.project_id=%s
                  AND (CAST(%s AS text) IS NULL OR b.last_source_message_id IS DISTINCT FROM CAST(%s AS text))
                GROUP BY b.bit_id
                ORDER BY b.last_considered_at NULLS FIRST, b.updated_at ASC
                LIMIT %s
            """).format(s, s), (project_id, exclude_message_id, exclude_message_id, max(1, int(limit))))
            rows = cur.fetchall()
        return [{
            "bit_id": str(r[0]), "verbatim": str(r[1]), "normalized": str(r[2]),
            "why_separate": str(r[3] or ""), "suggested_destination": str(r[4] or ""),
            "confidence": float(r[5] or 0.0), "source_message_id": str(r[6] or ""),
            "last_source_message_id": str(r[7] or ""), "source_thread_ids": list(r[8] or []),
            "mention_count": int(r[9] or 1), "created_at": r[10].isoformat() if r[10] else None,
            "last_considered_at": r[11].isoformat() if r[11] else None,
            "trial_count": int(r[12] or 0), "distinct_domains": int(r[13] or 0),
            "useful_count": int(r[14] or 0),
        } for r in rows]

    def record_unresolved_trial(
        self, bit_id: str, *, task_id: str, task_domain: str, outcome: str, reason: str = "", useful: bool = False
    ) -> None:
        with self._connect() as conn, conn.cursor() as cur:
            s = sql.Identifier(self.schema)
            cur.execute(sql.SQL("""
                INSERT INTO {}.unresolved_bit_trials(bit_id,task_id,task_domain,outcome,reason,useful)
                VALUES (%s,%s,%s,%s,%s,%s)
            """).format(s), (
                bit_id, str(task_id), str(task_domain or "unknown")[:120], str(outcome or "ambiguous")[:80],
                str(reason or "")[:1200], bool(useful),
            ))
            cur.execute(sql.SQL("UPDATE {}.unresolved_bits SET last_considered_at=now(),updated_at=now() WHERE bit_id=%s").format(s), (bit_id,))

    def delete_unresolved_bit(self, bit_id: str) -> bool:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(sql.SQL("DELETE FROM {}.unresolved_bits WHERE bit_id=%s").format(sql.Identifier(self.schema)), (bit_id,))
            return bool(cur.rowcount)

    def prune_unresolved_bit_if_exhausted(
        self, bit_id: str, *, min_trials: int = 15, min_domains: int = 3
    ) -> bool:
        with self._connect() as conn, conn.cursor() as cur:
            s = sql.Identifier(self.schema)
            cur.execute(sql.SQL("""
                SELECT b.mention_count,COUNT(t.trial_id)::int,
                       COUNT(DISTINCT NULLIF(t.task_domain,''))::int,
                       COALESCE(SUM(CASE WHEN t.useful THEN 1 ELSE 0 END),0)::int
                FROM {}.unresolved_bits b
                LEFT JOIN {}.unresolved_bit_trials t ON t.bit_id=b.bit_id
                WHERE b.bit_id=%s GROUP BY b.bit_id
            """).format(s, s), (bit_id,))
            row = cur.fetchone()
            if not row:
                return False
            mentions, trials, domains, useful = (int(row[0] or 0), int(row[1] or 0), int(row[2] or 0), int(row[3] or 0))
            if mentions > 1 or trials < max(1, int(min_trials)) or domains < max(1, int(min_domains)) or useful > 0:
                return False
            cur.execute(sql.SQL("DELETE FROM {}.unresolved_bits WHERE bit_id=%s").format(s), (bit_id,))
            return bool(cur.rowcount)
