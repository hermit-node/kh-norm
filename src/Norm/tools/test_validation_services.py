"""Integration test for the compact shared validation pool.

Usage: python tools/test_validation_services.py --services-root <installation>
Uses an isolated Redis namespace and PostgreSQL schema; never starts a Norm worker.
"""
from __future__ import annotations

import argparse
import json
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
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
    prefix="norm:test:validation:"+tag
    schema="norm_test_"+tag
    pool=build_postgres_pool(args.services_root)
    pg=PostgresTaskLog(pool,schema)
    live=RedisTaskLog(client,prefix=prefix+":task",validation_prefix=prefix+":validation")

    class Clock:
        value=datetime(2026,10,4,12,tzinfo=timezone.utc)
        @classmethod
        def now(cls,tz=None): return cls.value

    try:
        client.ping(); pg.ensure_schema()
        target=r"D:\Example\Norm.temp"
        desc="complete recursive inventory with file sizes and dates"
        with patch("norm_runtime.live_log.datetime",Clock):
            first=live.record_global_validations("a","s",[{"record_id":"new","tool":"run_command","target":target,"description":desc,"value":"inventory-v1"}])[0]
            rid=first["record_id"]
            assert first["num_checks"]==1 and first["previous_value"]=="" and first["changed_at"]==""
            # Wording variation must find the same target/fact candidate instead of
            # forcing a new model-authored semantic key.
            candidates=live.validation_candidates(tool="run_command",target=target.replace("\\","/"),description="recursive file inventory including sizes and modification dates")
            assert candidates and candidates[0]["record_id"]==rid and candidates[0]["description_match"]>=0.45,candidates
            second=live.record_global_validations("b","s",[{"record_id":rid,"tool":"run_command","target":target,"description":"wording changed","value":"inventory-v1"}])[0]
            assert second["num_checks"]==2
            Clock.value+=timedelta(hours=25)
            # A same-value recheck does not reset Redis age; the record is eligible
            # on the next migration wave because age is based on when the hot epoch began.
            third=live.record_global_validations("c","s",[{"record_id":rid,"tool":"run_command","target":target,"description":desc,"value":"inventory-v1"}])[0]
            assert third["num_checks"]==3
            stale=live.stale_validation_records(older_than_seconds=86400)
            assert [r["record_id"] for r in stale]==[rid]
        print("PASS one Redis hash record, wording-tolerant reuse, age based on epoch start")

        saved=pg.merge_validation_pool_record(stale[0])
        assert saved["num_checks"]==3
        assert live.acknowledge_validation_records([rid])==1
        assert live.global_validation(rid) is None

        # A later Redis wave with the same fact/value may have a different generated
        # id because Redis was emptied. PostgreSQL still combines it by target+description.
        with patch("norm_runtime.live_log.datetime",Clock):
            Clock.value+=timedelta(hours=1)
            later=live.record_global_validations("d","s",[{"record_id":"new","tool":"list_directory","target":target,"description":"recursive inventory of files, sizes, and dates","value":"inventory-v1"}])[0]
            Clock.value+=timedelta(hours=25)
            stale2=live.stale_validation_records(older_than_seconds=86400)
        assert stale2
        merged=pg.merge_validation_pool_record(stale2[0])
        live.acknowledge_validation_records([stale2[0]["record_id"]])
        assert merged["value"]=="inventory-v1" and merged["num_checks"]==4,merged
        print("PASS PostgreSQL combines same-value counts across separate Redis migration waves")

        # A changed value begins a fresh count at 1 and carries previous value/change time.
        with patch("norm_runtime.live_log.datetime",Clock):
            changed=live.record_global_validations("e","s",[{"record_id":"new","tool":"run_command","target":target,"description":desc,"value":"inventory-v1"}])[0]
            changed=live.record_global_validations("e","s",[{"record_id":changed["record_id"],"tool":"run_command","target":target,"description":desc,"value":"inventory-v2"}])[0]
        assert changed["num_checks"]==1 and changed["previous_value"]=="inventory-v1" and changed["changed_at"]
        print("PASS changed value resets num_checks=1 and records previous value/change timestamp")

        # PostgreSQL is queried only when Norm explicitly calls verification_history.
        worker=PromptWorker.__new__(PromptWorker)
        worker.live=live; worker.durable=pg
        worker._runtime_config=lambda:{"validation_pool":{"enabled":True,"redis_min_age_seconds":86400,"migration_interval_seconds":43200,"history_retention_seconds":1209600}}
        state={}
        pre=worker._execute_validation_tool(type("J",(),{"task_id":"t"})(),"verification_preflight",{"tool":"run_command","target":target,"description":desc},state)
        assert pre["ok"]
        history=worker._execute_validation_tool(type("J",(),{"task_id":"t"})(),"verification_history",{},state)
        assert history["ok"] and history["history"]["records"]
        print("PASS Redis preflight first; PostgreSQL history remains an explicit optional second decision")

        # Simulate old exploded keys and confirm upgrade collapse removes them.
        legacy_prefix=prefix+":legacy"
        legacy=RedisTaskLog(client,prefix=prefix+":legacy-task",validation_prefix=legacy_prefix)
        old={"value":"x","generation_checks":6,"generation_started_at":"2026-10-01T00:00:00+00:00","last_checked_at":"2026-10-02T00:00:00+00:00","last_source":"run_command","identity":{"where":target,"what":desc}}
        client.hset(legacy_prefix+":facts","wordy-key",json.dumps(old))
        client.zadd(legacy_prefix+":recent",{"wordy-key":1})
        client.zadd(legacy_prefix+":observations:deadbeef",{"{}":1})
        result=legacy.migrate_legacy_validation_layout()
        assert result["migrated"]==1
        assert client.exists(legacy_prefix+":pool")
        assert not list(client.scan_iter(match=legacy_prefix+":observations:*"))
        assert not client.exists(legacy_prefix+":facts") and not client.exists(legacy_prefix+":recent")
        print("PASS legacy exploded Redis validation keys collapse into one pool hash")
    finally:
        keys=list(client.scan_iter(match=prefix+":*"))
        if keys: client.delete(*keys)
        with pool.connection("norm") as conn:
            conn.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(schema)))
        pool.close()
        print("PASS isolated Redis/PostgreSQL validation test cleanup")


if __name__=="__main__":
    main()
