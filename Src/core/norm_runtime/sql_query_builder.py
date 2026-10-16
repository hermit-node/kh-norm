"""Narrow python-sql query builders for new, composable PostgreSQL queries.

Keep psycopg 3 for execution/connection pooling, DDL and legacy static SQL.
Never interpolate user values into SQL: python-sql returns the statement and
parameter tuple independently. Only trusted schema names enter Table().
"""
from __future__ import annotations

import re

_SCHEMA = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _runtime_state_table(schema: str):
    if not isinstance(schema, str) or not _SCHEMA.fullmatch(schema):
        raise ValueError("Unsafe PostgreSQL schema name")
    from sql import Table  # python-sql (Tryton), not psycopg.sql
    return Table("runtime_state", schema)


def runtime_state_get_query(schema: str, key: str) -> tuple[str, tuple]:
    table = _runtime_state_table(schema)
    return tuple(table.select(table.value, where=table.state_key == key))


def runtime_state_snapshot_query(schema: str) -> tuple[str, tuple]:
    table = _runtime_state_table(schema)
    return tuple(table.select(table.state_key, table.value, table.updated_at,
                              order_by=table.state_key))
