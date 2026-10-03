"""Isolated Redis regression for suppression/resume queue invariants."""
from __future__ import annotations
import argparse
import sys
import threading
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "core"))
from runtime_bootstrap import load_config
from norm_runtime.prompt_queue import PromptJob, RedisPromptQueue
from norm_runtime.prompt_worker import PromptWorker
import redis

class DurableStub:
    def __init__(self):
        self.suppressed = set()
        self.payloads = {}
    def is_task_tree_suppressed(self, task_id): return task_id in self.suppressed
    def root_task_id(self, task_id): return task_id
    def task_status(self, task_id): return "suppressed" if task_id in self.suppressed else "queued"
    def task_tree(self, task_id):
        return [{"task_id": task_id, "status": self.task_status(task_id), "task_kind": "root", "task_depth": 0}]
    def suppress_task_tree(self, task_id, member_ids, reason, payload):
        self.suppressed.update(member_ids)
        self.payloads[task_id] = payload
        return {"task_id": task_id, "title": "test"}

def make_job(task_id: str) -> PromptJob:
    return PromptJob(
        message_id="m-" + task_id, task_id=task_id, step_id="work", prompt="test", project_id="default",
        context="", attempt=0, created_at=time.time(), metadata={}, chain_id="", previous_prompt_id="",
        next_prompt_id="", chain_index=0, recovery_attempted=False, request_type="step", task_uuid=task_id,
        node_id="m-" + task_id, chain_uuid="", previous_node_id="", next_node_id="",
    )

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--services-root", type=Path, required=True)
    args = ap.parse_args()
    cfg = load_config(args.services_root)
    rp = cfg["redis"]
    client = redis.Redis(
        host=rp["host"], port=int(rp["port"]), db=int(rp.get("db", 0)), decode_responses=True,
        socket_timeout=5, socket_connect_timeout=5,
    )
    tag = uuid.uuid4().hex
    prefix = "norm:test:suppress:" + tag
    durable = DurableStub()
    queue = RedisPromptQueue(
        client, prefix+":work", prefix+":group", prefix+":retry", prefix+":escalation",
        prefix+":dead", consumer="test", durable=durable,
    )
    queue.ensure_group()
    task_id = "task-" + tag
    job = make_job(task_id)
    try:
        queue.enqueue(job)
        worker = PromptWorker.__new__(PromptWorker)
        worker.queue = queue
        worker.durable = durable
        worker._state_lock = threading.RLock()
        worker._suppress_requested = set()
        worker._soft_delete_task_assets = lambda *a, **k: None
        worker.active_task_id = lambda: None
        result = worker.request_suppress_task(task_id, "regression")
        assert result["suppressed"] is True, result
        assert queue.snapshot_task_jobs(task_id)["jobs"] == []
        payload = durable.payloads[task_id]
        assert len(payload["tasks"][0]["queue"]["jobs"]) == 1
        print("PASS suppress captures once and removes live Redis queue state")

        queue.enqueue(job)
        claimed = queue.read_one(block_ms=1)
        assert claimed is not None
        moved = queue.requeue_pending_on_startup()
        assert moved == 0
        assert not queue.contains_message_id(job.message_id)
        print("PASS durably suppressed pending job is not requeued on startup")

        durable.suppressed.discard(task_id)
        restored = queue.restore_task_jobs(payload)
        assert len(restored) == 1, restored
        assert queue.contains_message_id(job.message_id)
        restored_again = queue.restore_task_jobs(payload)
        assert restored_again == [], restored_again
        matches = []
        for stream in (queue.stream, queue.retry_stream, queue.escalation_stream, queue.dead_letter_stream):
            matches.extend((stream, rid) for rid, fields in client.xrange(stream, min="-", max="+")
                           if str(fields.get("message_id") or "") == job.message_id)
        assert len(matches) == 1, matches
        print("PASS resume restores exactly one captured job and duplicate resume is idempotent")
    finally:
        keys = list(client.scan_iter(match=prefix+"*"))
        if keys:
            client.delete(*keys)
        assert not list(client.scan_iter(match=prefix+"*"))
        print("PASS temporary suppression/resume Redis keys removed")

if __name__ == "__main__":
    main()
