"""Consistent classification for PostgreSQL failures (no automatic replay).

This module deliberately cannot retry an operation. SQL execution may follow a
filesystem action that already succeeded; task-level recovery must reconcile
external effects before any retry.
"""
from __future__ import annotations


def classify_sqlstate(sqlstate: str | None) -> str:
    """Classify for observability; not proof that a retry is safe."""
    code = str(sqlstate or '').strip().upper()
    if code in {'40001', '40P01', '55P03'}:
        return 'transient_transaction'
    if code.startswith(('08', '53')) or code in {'57P01', '57P02', '57P03'}:
        return 'connection_or_resource'
    if code.startswith(('22', '23', '28', '42')):
        return 'deterministic_sql_or_data'
    return 'unclassified'
