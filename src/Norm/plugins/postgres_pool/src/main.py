from __future__ import annotations

import importlib
import sys
from pathlib import Path
from typing import Any

from psycopg import sql


def _runtime_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _bootstrap():
    root = _runtime_root()
    core = root / "core"
    core_text = str(core)
    if core_text not in sys.path:
        sys.path.insert(0, core_text)
    return importlib.import_module("runtime_bootstrap")


def _shared_pool():
    """Return Norm's canonical process-global PostgreSQL pool object."""
    return _bootstrap().build_postgres_pool(_runtime_root())


def _runtime_schema() -> str:
    config = _bootstrap().load_config(_runtime_root())
    schema = str((config.get("postgres") or {}).get("schema") or "norm_runtime").strip()
    if not schema:
        raise ValueError("configured PostgreSQL runtime schema is blank")
    return schema


def postgres_pool(action: str = "status", connection: str = "norm") -> dict:
    """Inspect Norm's shared bounded PostgreSQL pool. Actions: status, health, connections."""
    pool = _shared_pool()
    selected = str(action or "status").strip().lower()
    if selected == "status":
        return pool.status(connection if str(connection or "").strip() else None)
    if selected == "health":
        return pool.health(connection)
    if selected == "connections":
        return {
            "allowed_connections": pool.status().get("allowed_connections", []),
            "note": "Connection targets are configured by Norm; host/user/password/dbname are not tool parameters.",
        }
    raise ValueError("action must be one of: status, health, connections")


def postgres_query(
    query: str,
    connection: str = "norm",
    params: list | dict | None = None,
    max_rows: int = 200,
    timeout_seconds: float = 30.0,
) -> dict:
    """Run one read-only SQL query through Norm's existing shared pool.

    Only the configured logical connections (norm/stocks) are accepted by the
    canonical pool.  The transaction is forced READ ONLY and results are bounded.
    """
    statement = str(query or "").strip()
    if not statement:
        raise ValueError("query cannot be empty")
    limit = max(1, min(int(max_rows), 1000))
    timeout = max(1.0, min(float(timeout_seconds), 120.0))
    pool = _shared_pool()

    with pool.connection(connection, timeout=timeout) as conn:
        with conn.transaction():
            with conn.cursor() as cur:
                cur.execute("SET TRANSACTION READ ONLY")
                cur.execute(f"SET LOCAL statement_timeout = {int(timeout * 1000)}")
                cur.execute(statement, params)
                columns = [str(item.name) for item in cur.description] if cur.description else []
                rows = cur.fetchmany(limit + 1) if cur.description else []
                truncated = len(rows) > limit
                if truncated:
                    rows = rows[:limit]

    rendered_rows: list[dict[str, Any] | list[Any]]
    if columns:
        rendered_rows = [dict(zip(columns, row)) for row in rows]
    else:
        rendered_rows = [list(row) for row in rows]
    return {
        "connection": str(connection or "norm"),
        "columns": columns,
        "rows": rendered_rows,
        "row_count": len(rendered_rows),
        "truncated": truncated,
        "max_rows": limit,
        "read_only": True,
    }


def postgres_execute(
    action: str,
    table: str,
    values: dict | None = None,
    where: dict | None = None,
    returning: list[str] | None = None,
    max_rows: int = 200,
    timeout_seconds: float = 30.0,
) -> dict:
    """Apply a bounded INSERT/UPDATE/DELETE inside Norm's configured runtime schema.

    This intentionally has no connection parameter: writes always use the approved
    norm connection and the configured Norm runtime schema. UPDATE and DELETE
    require an explicit equality/IS NULL filter so the model cannot accidentally
    mutate an entire table.
    """
    op = str(action or "").strip().lower()
    if op not in {"insert", "update", "delete"}:
        raise ValueError("action must be one of: insert, update, delete")
    table_name = str(table or "").strip()
    if not table_name or "." in table_name:
        raise ValueError("table must be one unqualified table name inside the Norm runtime schema")

    values = dict(values or {})
    where = dict(where or {})
    returning = [str(name).strip() for name in (returning or []) if str(name).strip()]
    if op in {"insert", "update"} and not values:
        raise ValueError(f"{op} requires non-empty values")
    if op in {"update", "delete"} and not where:
        raise ValueError(f"{op} requires a non-empty where filter")

    schema = _runtime_schema()
    target = sql.SQL("{}.{}").format(sql.Identifier(schema), sql.Identifier(table_name))
    params: list[Any] = []

    if op == "insert":
        columns = list(values)
        statement = sql.SQL("INSERT INTO {} ({}) VALUES ({})").format(
            target,
            sql.SQL(", ").join(sql.Identifier(name) for name in columns),
            sql.SQL(", ").join(sql.Placeholder() for _ in columns),
        )
        params.extend(values[name] for name in columns)
    elif op == "update":
        columns = list(values)
        statement = sql.SQL("UPDATE {} SET {}").format(
            target,
            sql.SQL(", ").join(
                sql.SQL("{} = {}").format(sql.Identifier(name), sql.Placeholder())
                for name in columns
            ),
        )
        params.extend(values[name] for name in columns)
    else:
        statement = sql.SQL("DELETE FROM {}").format(target)

    if where:
        predicates = []
        for name, value in where.items():
            if value is None:
                predicates.append(sql.SQL("{} IS NULL").format(sql.Identifier(str(name))))
            else:
                predicates.append(
                    sql.SQL("{} = {}").format(sql.Identifier(str(name)), sql.Placeholder())
                )
                params.append(value)
        statement += sql.SQL(" WHERE ") + sql.SQL(" AND ").join(predicates)

    if returning:
        statement += sql.SQL(" RETURNING ") + sql.SQL(", ").join(
            sql.Identifier(name) for name in returning
        )

    limit = max(1, min(int(max_rows), 1000))
    timeout = max(1.0, min(float(timeout_seconds), 120.0))
    pool = _shared_pool()
    with pool.connection("norm", timeout=timeout) as conn:
        with conn.transaction():
            with conn.cursor() as cur:
                cur.execute(f"SET LOCAL statement_timeout = {int(timeout * 1000)}")
                cur.execute(statement, params)
                affected = max(0, int(cur.rowcount if cur.rowcount is not None else 0))
                columns = [str(item.name) for item in cur.description] if cur.description else []
                rows = cur.fetchmany(limit + 1) if cur.description else []
                truncated = len(rows) > limit
                if truncated:
                    rows = rows[:limit]

    rendered = [dict(zip(columns, row)) for row in rows] if columns else []
    return {
        "connection": "norm",
        "schema": schema,
        "table": table_name,
        "action": op,
        "affected_rows": affected,
        "columns": columns,
        "rows": rendered,
        "truncated": truncated,
        "max_rows": limit,
    }
