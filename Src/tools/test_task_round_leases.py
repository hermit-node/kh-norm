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

from norm_runtime.prompt_queue import PromptJob
from norm_runtime.prompt_worker import PromptWorker


class Queue:
    consumer = "test"
    def __init__(self):
        self.state = {}
    def set_task_round_budget_state(self, task_id, state):
        self.state[task_id] = dict(state)


class Live:
    def task_evidence(self, task_id):
        return [
            {"tool": "write_file", "arguments": {"path": "a.py"}, "result": {"ok": True, "path": "a.py", "file_mutation": True}},
            {"tool": "read_file", "arguments": {"path": "a.py"}, "result": {"ok": True, "path": "a.py"}, "deterministic_verification": True},
        ]


class Durable:
    def task_plan_snapshot(self, task_id):
        return {"steps": [{"step_id": "s1", "status": "completed"}, {"step_id": "s2", "status": "running"}]}


class Gate:
    def __init__(self):
        self.calls = []
    def review_task_round_progress(self, **kwargs):
        self.calls.append(kwargs)
        return {"extend": True, "reason": "verified forward progress", "processing_failed": False}


class Client:
    pass


job = PromptJob(
    message_id="1-0", task_id="task", step_id="s2", prompt="step",
    project_id="default", context="", attempt=0, created_at=0.0, metadata={},
    chain_id="", previous_prompt_id="", next_prompt_id="", chain_index=0,
    recovery_attempted=False,
)
queue = Queue()
gate = Gate()
worker = PromptWorker(queue, Client(), Live(), Durable(), n1_gatekeeper=gate)
policy = {"step_limit": 16, "base_limit": 128, "first_review": 96, "extension": 36, "hard_limit": 384}
state = {"granted_limit": 128, "next_review": 96, "reviews": 0, "denied": False, "last_review_round": 0}

limit, state = worker._maybe_extend_task_round_budget(job, 95, 128, state, policy)
assert limit == 128
assert gate.calls == []

limit, state = worker._maybe_extend_task_round_budget(job, 96, 128, state, policy)
assert limit == 164
assert state["next_review"] == 132
assert state["reviews"] == 1
assert len(gate.calls) == 1
assert gate.calls[0]["progress_delta"]["completed_steps"] == 1
assert gate.calls[0]["progress_delta"]["mutations"] == 1

limit, state = worker._maybe_extend_task_round_budget(job, 132, 164, state, policy)
assert limit == 200
assert state["next_review"] == 168
assert state["reviews"] == 2

# Hard cap never grows, regardless of N1 willingness.
limit, state = worker._maybe_extend_task_round_budget(job, 384, 384, state, policy)
assert limit == 384
assert len(gate.calls) == 2

print("PASS adaptive task round leases")
