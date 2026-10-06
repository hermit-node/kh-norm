from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SOURCE_ROOT = Path(__file__).resolve().parents[1]
CORE_ROOT = SOURCE_ROOT / "core"
if str(CORE_ROOT) not in sys.path:
    sys.path.insert(0, str(CORE_ROOT))

from psycopg import sql

from runtime_bootstrap import build_postgres_pool
from norm_runtime.conversation_store import ConversationStore
from norm_runtime.durable_log import PostgresTaskLog
from norm_runtime.settings import load_postgres_settings


def postgres_target(runtime_root: Path):
    return build_postgres_pool(runtime_root), str(load_postgres_settings(runtime_root)["schema"])


def recent_repairs(pool, schema: str) -> list[dict]:
    with pool.connection("norm") as conn, conn.cursor() as cur:
        cur.execute(
            sql.SQL("""
                SELECT phase,note,details,created_at
                FROM {}.runtime_maintenance_notes
                WHERE phase IN ('task_lineage_repair','memory_thread_repair','deep_history_prune_lineage_repair')
                ORDER BY created_at DESC LIMIT 12
            """).format(sql.Identifier(schema))
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
    pool, schema = postgres_target(runtime_root)

    durable = PostgresTaskLog(pool, schema)
    durable.ensure_schema()
    store = ConversationStore(pool, schema)
    store.ensure_schema()

    output = {
        "status": "ok",
        "runtime_root": str(runtime_root),
        "schema": schema,
        "recent_integrity_repairs": recent_repairs(pool, schema),
    }
    print(json.dumps(output, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
