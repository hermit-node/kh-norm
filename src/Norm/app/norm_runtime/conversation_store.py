from __future__ import annotations

import hashlib
import re
import uuid
from contextlib import contextmanager
from typing import Iterator

import psycopg
from psycopg import sql

_SCHEMA_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_MEMORY_TYPES = {"fact", "preference", "decision", "constraint", "task", "assumption"}


class ConversationStore:
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
            cur.execute(sql.SQL("CREATE INDEX IF NOT EXISTS {} ON {}.threads(project_id, updated_at DESC)").format(
                sql.Identifier(f"idx_{self.schema}_threads_project"), s))
            cur.execute(sql.SQL("CREATE INDEX IF NOT EXISTS {} ON {}.message_threads(thread_id)").format(
                sql.Identifier(f"idx_{self.schema}_message_threads_thread"), s))
            cur.execute(sql.SQL("CREATE INDEX IF NOT EXISTS {} ON {}.memory_items(project_id, status)").format(
                sql.Identifier(f"idx_{self.schema}_memory_project_status"), s))

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
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(sql.SQL("SELECT summary FROM {}.thread_summaries WHERE thread_id=%s ORDER BY version DESC LIMIT 1").format(
                sql.Identifier(self.schema)), (thread_id,))
            row = cur.fetchone()
            return row[0] if row else ""

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