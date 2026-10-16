from __future__ import annotations

import json
import sys
import time
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
    def __init__(self):
        self.consumer = "test"
        self._rounds = 0
    def task_rounds(self, task_id):
        return self._rounds
    def add_task_rounds(self, task_id, value):
        self._rounds += int(value)
        return self._rounds


class Live:
    def __init__(self):
        self.evidence = []
    def record_evidence(self, task_id, step_id, evidence):
        self.evidence.extend(evidence)


class Durable:
    def __init__(self):
        self.rows = {}
        self.next_idx = 1
    def step_segments(self, task_id, step_id):
        return []
    def task_status(self, task_id):
        return "running"
    def record_context_slice(self, task_id, step_id, messages, **meta):
        idx = self.next_idx
        self.next_idx += 1
        self.rows[(task_id, step_id, idx)] = {"messages": json.loads(json.dumps(messages)), **meta}
        return idx
    def context_slice(self, task_id, step_id, idx):
        row = self.rows[(task_id, step_id, idx)]
        # Marker proves PromptWorker consumes the durable copy rather than only the in-memory list.
        messages = list(row["messages"])
        messages.append({"role": "system", "content": "FROM_POSTGRES"})
        return {"slice_index": idx, "messages": messages}
    def record_evidence(self, task_id, step_id, evidence):
        pass


class Gate:
    def __init__(self):
        self.seen = None
    def instructions(self):
        return "N1 boundary"
    def instrument_schemas(self, schemas):
        return schemas
    def observe_n2_turn(self, **kwargs):
        return ""
    def distill_continuation_checkpoint(self, **kwargs):
        self.seen = kwargs
        assert any(m.get("content") == "FROM_POSTGRES" for m in kwargs["archived_messages"])
        return (
            "NORM INTERNAL STEP CONTINUATION\n"
            "This is an internally requeued slice of the same step, not a new user request.\n"
            "Applicable facts:\n- keep this\nOpen work:\n- finish it\nNext action: continue"
        )


class Tools:
    read_chunk_max_bytes = 24000
    def __init__(self):
        self.executed = 0
    def instructions(self):
        return "Use tools when needed."
    def schemas(self):
        return [{
            "type": "function",
            "function": {
                "name": "read_file",
                "description": "read",
                "parameters": {"type": "object", "properties": {"path": {"type": "string"}}},
            },
        }]
    def execute(self, name, arguments):
        self.executed += 1
        raise AssertionError("5% checkpoint must yield before executing newly proposed tools")


class Client:
    system_instructions = "system"
    context_length = 1000
    model = "norm"
    def effective_context_length(self, model=None):
        return 1000
    def chat_with_tools(self, messages, tools, **kwargs):
        return {
            "content": "I have enough state to continue after a checkpoint.",
            "thinking": "",
            "tool_calls": [{"function": {"name": "read_file", "arguments": {"path": r"C:\\tmp\\next.txt"}}}],
            "done_reason": "stop",
            "prompt_eval_count": 940,
            "eval_count": 10,
        }


queue = Queue()
durable = Durable()
gate = Gate()
tools = Tools()
worker = PromptWorker(queue, Client(), Live(), durable, n1_gatekeeper=gate)
worker._runtime_config = lambda: {
    "worker": {"step_round_limit": 16, "task_round_limit": 128},
    "context_budget": {
        "enabled": True,
        "checkpoint_remaining_fraction": 0.05,
        "checkpoint_archive_messages": 8,
    },
}
job = PromptJob(
    message_id="1-0", task_id="task", step_id="step", prompt="original user task",
    project_id="default", context="", attempt=0, created_at=time.time(), metadata={
        "prompt_origin": "norm_internal",
        "original_user_prompt": "original user task",
    },
    chain_id="c", previous_prompt_id="", next_prompt_id="x", chain_index=0,
    recovery_attempted=False,
)
outcome = worker._run_tool_slice(job, "internal step prompt", tools)
assert outcome["yielded"] is True
assert outcome["reason"] == "context_headroom_checkpoint"
assert tools.executed == 0
assert "not a new user request" in outcome["checkpoint"]
assert outcome["resume_metadata"]["postgres_context_slice_index"] == 1
assert outcome["resume_metadata"]["compact_resume_context"] is True
assert gate.seen is not None
assert gate.seen["original_goal"] == "original user task"
row = durable.rows[("task", "step", 1)]
assert row["prompt_eval_count"] == 940
assert row["eval_count"] == 10
assert row["context_limit_tokens"] == 1000
assert row["messages"][-1]["role"] == "assistant"
assert row["messages"][-1]["tool_calls"][0]["function"]["name"] == "read_file"
# On the requeued slice the checkpoint occupies the continuation field; it does
# not replace or synthesize the authoritative user request/instruction.
job.context = outcome["checkpoint"]
payload = json.loads(worker._typed_prompt(job, "step"))
assert payload["context"]["continuation"] == outcome["checkpoint"]
assert payload["instruction_source"] == "norm_internal"
assert payload["original_user_request"] == "original user task"
print("PASS context checkpoint distillation")

# 0.54.2h did not write compact_resume_context, but it did persist these
# headroom-yield markers. An already-parked h task must compact on its first i resume.
legacy_job = PromptJob(
    message_id="legacy-1", task_id="legacy", step_id="step", prompt="finish",
    project_id="default", context="legacy distilled continuation", attempt=0, created_at=time.time(),
    metadata={"last_yield_reason": "context_headroom_checkpoint", "context_checkpoint_remaining_fraction": 0.038},
    chain_id="", previous_prompt_id="", next_prompt_id="", chain_index=0, recovery_attempted=False,
)
assert worker._compact_resume_requested(legacy_job) is True
legacy_job.context = ""
assert worker._compact_resume_requested(legacy_job) is False
print("PASS legacy 0.54.2h compact-resume migration")
