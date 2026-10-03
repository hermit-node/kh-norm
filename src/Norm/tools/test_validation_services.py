"""Integration tests using an isolated PostgreSQL schema and Redis namespace.

Usage: python tools/test_validation_services.py --services-root <installation>
Reads endpoint credentials from that installation; never starts a Norm worker.
"""
from __future__ import annotations
import argparse
import json
import sys
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"core"))
from runtime_bootstrap import build_postgres_pool, load_config
from norm_runtime.live_log import RedisTaskLog
from norm_runtime.durable_log import PostgresTaskLog
from norm_runtime.prompt_worker import PromptWorker
import redis
from psycopg import sql


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--services-root",type=Path,required=True)
    args=parser.parse_args()
    cfg=load_config(args.services_root)
    rp=cfg["redis"]
    client=redis.Redis(host=rp["host"],port=int(rp["port"]),db=int(rp.get("db",0)),decode_responses=True,socket_timeout=5,socket_connect_timeout=5)
    tag=uuid.uuid4().hex
    prefix="norm:test:maintenance:"+tag
    schema="norm_test_"+tag
    pool=build_postgres_pool(args.services_root)
    pg=PostgresTaskLog(pool,schema)
    live=RedisTaskLog(client,prefix=prefix+":task",validation_prefix=prefix+":validation")
    class Clock:
        value=datetime(2026,10,2,12,tzinfo=timezone.utc)
        @classmethod
        def now(cls,tz=None):return cls.value
    def record(value="v", task="a"):
        return live.record_global_validations(task,"work",[{"subject":"x","value":value,"source":"test"}])[0]
    try:
        client.ping()
        pg.ensure_schema()
        with patch("norm_runtime.live_log.datetime",Clock):
            first=record()
            Clock.value+=timedelta(hours=23)
            second=record(task="unrelated")
            assert second["generation_checks"]==2 and second["recent_checks"]==2
            assert second["generation_started_at"]==first["generation_started_at"]
            Clock.value+=timedelta(hours=2)
            third=record()
            assert third["recent_checks"]==2 and third["generation_checks"]==3
            Clock.value+=timedelta(seconds=1)
            changed=record("new")
            assert changed["generation_checks"]==changed["recent_checks"]==1
            assert changed["generation_started_at"]==changed["last_checked_at"]
            assert changed["generation_started_at"]!=first["generation_started_at"]
            assert changed["_previous_generation"]["generation_checks"]==3
        print("PASS rolling 24h, cross-task reuse, same-value generation and changed-value reset")
        # No hard ceiling and no lost concurrent increments.
        with ThreadPoolExecutor(max_workers=4) as workers:
            list(workers.map(lambda _:record("new"),range(40)))
        row=live.global_validation("x")
        assert row["generation_checks"]==41,row
        print("PASS concurrent generation increments and advisory counts beyond reuse threshold")
        pg.archive_global_validation_generation(changed["_previous_generation"])
        pg.upsert_global_validation(changed)
        pg.upsert_global_validation(first)  # Out-of-order snapshot must not roll back the value.
        assert pg.global_validation("x")["current_value"]=="new"
        assert len(pg.global_validation_history("x"))==1
        print("PASS durable history, schema formatting, out-of-order snapshot protection")
        # Exercise worker checkpoint throttling without creating live task rows.
        durable=SimpleNamespace(global_validations=pg.global_validations,upsert_global_validation=pg.upsert_global_validation,archive_global_validation_generation=pg.archive_global_validation_generation)
        worker=PromptWorker.__new__(PromptWorker)
        worker.live=live
        worker.durable=durable
        worker._runtime_config=lambda:{"validation_pool":{"durable_snapshot_interval_seconds":43200}}
        job=SimpleNamespace(task_id="worker-test",step_id="work")
        item=[{"subject":"worker","value":"a","source":"test"}]
        worker._record_global_validations(job,item)
        worker._record_global_validations(job,item)
        assert live.global_validation("worker")["generation_checks"]==2
        assert pg.global_validation("worker")["generation_check_count"]==1
        with pool.connection("norm") as conn:
            conn.execute(sql.SQL("UPDATE {}.global_validations SET snapshot_at=now()-interval '13 hours' WHERE subject='worker'").format(sql.Identifier(schema)))
        worker._record_global_validations(job,item)
        assert pg.global_validation("worker")["generation_check_count"]==3
        item[0]["value"]="b"
        worker._record_global_validations(job,item)
        assert pg.global_validation("worker")["generation_check_count"]==1
        assert len(pg.global_validation_history("worker"))==1
        assert not live.pending_validation_generations()
        changed_again=live.record_global_validations("other","work",[{"subject":"worker","value":"c","source":"test"}])[0]
        assert len(live.pending_validation_generations())==1
        def unavailable(*args):raise RuntimeError("simulated PostgreSQL history outage")
        durable.archive_global_validation_generation=unavailable
        item[0]["value"]="c"
        with patch("norm_runtime.prompt_worker.logging.exception"):
            worker._record_global_validations(job,item)
        assert len(live.pending_validation_generations())==1
        durable.archive_global_validation_generation=pg.archive_global_validation_generation
        worker._record_global_validations(job,item)
        assert not live.pending_validation_generations()
        assert len(pg.global_validation_history("worker"))==2
        print("PASS completed generation retained during PostgreSQL outage and retried")
        print("PASS worker snapshot lag, due checkpoint, and changed-generation archival")
        live.cleanup("worker-test")
        assert live.global_validation("worker") is not None
    finally:
        keys=list(client.scan_iter(match=prefix+":*"))
        if keys:client.delete(*keys)
        assert not list(client.scan_iter(match=prefix+":*"))
        with pool.connection("norm") as conn:
            conn.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(schema)))
            assert conn.execute("SELECT 1 FROM pg_namespace WHERE nspname=%s",(schema,)).fetchone() is None
        pool.close()
        print("PASS temporary Redis keys and PostgreSQL schema removed")


if __name__=="__main__":
    main()
