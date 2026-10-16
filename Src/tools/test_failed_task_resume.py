from __future__ import annotations

import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "core"))

if "redis" not in sys.modules:
    redis_stub = types.ModuleType("redis")
    redis_stub.Redis = object
    redis_stub.ResponseError = RuntimeError
    sys.modules["redis"] = redis_stub
if "psycopg" not in sys.modules:
    psycopg_stub = types.ModuleType("psycopg")
    psycopg_stub.Error = RuntimeError
    sys.modules["psycopg"] = psycopg_stub

from norm_runtime.prompt_worker import PromptWorker


class Queue:
    consumer = "test"
    def __init__(self):
        self.reset = []
        self.restored = []
    def reset_task_rounds(self, task_id):
        self.reset.append(task_id)
    def restore_task_jobs(self, payload):
        self.restored.append(payload)
        return ["1-0"]
    def cleanup_task(self, task_id):
        return {"work": 0, "retry": 0, "escalation": 0, "dead": 0, "round_key": 0, "budget_key": 0}


class Live:
    def __init__(self):
        self.cleaned = []
    def cleanup(self, task_id):
        self.cleaned.append(task_id)


class Durable:
    def __init__(self):
        self.status = "failed"
        self.checkpoints = []
    def recent_failed_tasks(self, limit=25):
        return [{
            "task_id": "chat-abcdef",
            "title": "Long refactor",
            "resumable": True,
            "resume_count": 2,
            "step_id": "step-07",
        }]
    def resume_failed_task(self, task_id):
        assert task_id == "chat-abcdef"
        self.status = "running"
        return {
            "queue": {"jobs": [{"job": {"message_id": "node-1"}}]},
            "checkpoint": "compact durable continuation",
            "failed_step_id": "step-07",
            "failed_step_name": "verify_and_report",
        }
    def re_fail_task_after_resume_error(self, task_id):
        self.status = "failed"
    def checkpoint_step(self, result):
        self.checkpoints.append(result)


worker = PromptWorker(Queue(), object(), Live(), Durable())
listed = worker.recent_failed_tasks()
assert listed["count"] == 1
assert listed["tasks"][0]["task_id"] == "chat-abcdef"

result = worker.resume_failed_task("0")
assert result["status"] == "ok"
assert result["resumed"] is True
assert result["task_id"] == "chat-abcdef"
assert result["resume_count"] == 3
assert result["round_budget"] == "reset"
assert worker.queue.reset == ["chat-abcdef"]
assert len(worker.queue.restored) == 1
assert worker.live.cleaned == ["chat-abcdef"]
assert len(worker.durable.checkpoints) == 1
assert worker.durable.checkpoints[0].status.value == "running"

# Exact id and a unique prefix are also valid selectors.
assert worker._resolve_failed_selector(listed["tasks"], "chat-abcdef")["task_id"] == "chat-abcdef"
assert worker._resolve_failed_selector(listed["tasks"], "chat-ab")["task_id"] == "chat-abcdef"

print("PASS failed task list/manual resume")
