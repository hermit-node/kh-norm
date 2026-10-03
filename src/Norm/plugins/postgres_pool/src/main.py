from __future__ import annotations

import importlib
import sys
from pathlib import Path


def _pool_class():
    runtime_root = Path(__file__).resolve().parents[3]
    root_text = str(runtime_root)
    if root_text not in sys.path:
        sys.path.insert(0, root_text)
    return importlib.import_module("_pool").PostgresPool


def postgres_pool(action: str = "status", connection: str = "norm") -> dict:
    """Use Norm's bounded PostgreSQL pool. Actions: status, health, connections."""
    pool = _pool_class()
    if not pool.configured():
        pool.configure_from_runtime(Path(__file__).resolve().parents[3])
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
