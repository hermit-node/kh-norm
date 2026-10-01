from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SOURCE_ROOT = Path(__file__).resolve().parents[1]
CORE_ROOT = SOURCE_ROOT / "core"
if str(CORE_ROOT) not in sys.path:
    sys.path.insert(0, str(CORE_ROOT))

import psycopg

from norm_runtime.conversation_store import ConversationStore
from norm_runtime.durable_log import PostgresTaskLog
from norm_runtime.settings import load_network_settings, load_ports, load_secrets, resolve_network_host


def postgres_target(runtime_root: Path) -> tuple[str, str]:
    network = load_network_settings(runtime_root)
    ports = load_ports(runtime_root)
    secrets = load_secrets(runtime_root)
    user = secrets.get("NORM_POSTGRES_USER", "").strip()
    password = secrets.get("NORM_POSTGRES_PASSWORD", "").strip()
    database = secrets.get("NORM_POSTGRES_DB", "postgres").strip() or "postgres"
    schema = secrets.get("NORM_POSTGRES_SCHEMA", "norm_runtime").strip() or "norm_runtime"
    if not user or not password:
        raise ValueError("NORM_POSTGRES_USER and NORM_POSTGRES_PASSWORD are required")
    host = resolve_network_host(network, "postgres_host")
    conninfo = psycopg.conninfo.make_conninfo(
        host=host, port=ports["postgres"], dbname=database, user=user, password=password
    )
    return conninfo, schema


def recent_repairs(conninfo: str, schema: str) -> list[dict]:
    with psycopg.connect(conninfo) as conn, conn.cursor() as cur:
        cur.execute(
            psycopg.sql.SQL("""
                SELECT phase,note,details,created_at
                FROM {}.runtime_maintenance_notes
                WHERE phase IN ('task_lineage_repair','memory_thread_repair','deep_history_prune_lineage_repair')
                ORDER BY created_at DESC LIMIT 12
            """).format(psycopg.sql.Identifier(schema))
        )
        return [
            {"phase": str(r[0]), "note": str(r[1]), "details": r[2], "created_at": r[3].isoformat()}
            for r in cur.fetchall()
        ]


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Repair Norm task lineage and memory-thread integrity using the current portable-source repair logic."
    )
    parser.add_argument(
        "--runtime-root", default=r"C:\Norm",
        help=r"Existing Norm installation whose config/secrets identify the PostgreSQL state to repair (default: C:\Norm).",
    )
    args = parser.parse_args()
    runtime_root = Path(args.runtime_root).expanduser().resolve()
    conninfo, schema = postgres_target(runtime_root)

    durable = PostgresTaskLog(conninfo, schema)
    durable.ensure_schema()
    store = ConversationStore(conninfo, schema)
    store.ensure_schema()

    output = {
        "status": "ok",
        "runtime_root": str(runtime_root),
        "schema": schema,
        "recent_integrity_repairs": recent_repairs(conninfo, schema),
    }
    print(json.dumps(output, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
