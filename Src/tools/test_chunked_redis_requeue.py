from __future__ import annotations

import copy
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
    sys.modules["psycopg"] = types.ModuleType("psycopg")

from norm_runtime.n1_gatekeeper import N1Gatekeeper
from norm_runtime.prompt_queue import PromptJob
from norm_runtime.prompt_worker import PromptWorker


class Queue:
    def __init__(self):
        self.consumer = "test"
        self._rounds = 0
    def task_rounds(self, _task_id):
        return self._rounds
    def add_task_rounds(self, _task_id, value):
        self._rounds += int(value)
        return self._rounds


class Live:
    def __init__(self):
        self.rows = []
        self.replays = {}
        self.seq = 0
    def validation_candidates(self, **kwargs):
        target = kwargs.get("target")
        rows = [copy.deepcopy(row) for row in self.rows if row.get("target") == target]
        for row in rows:
            row["description_match"] = 1.0
        return rows
    def validation_replay(self, record_id):
        return copy.deepcopy(self.replays.get(record_id))
    def record_global_validations(self, _task_id, _step_id, items):
        saved = []
        for item in copy.deepcopy(items):
            self.seq += 1
            rid = item.get("record_id")
            if not rid or rid == "new":
                rid = f"r{self.seq}"
            replay = item.get("replay_result")
            if isinstance(replay, dict):
                self.replays[rid] = copy.deepcopy(replay)
            row = {
                "record_id": rid, "tool": item.get("tool"), "target": item.get("target"),
                "description": item.get("description"), "scope": item.get("scope", ""),
                "value": item.get("value"), "num_checks": 1, "previous_value": "", "changed_at": "",
                "last_verified_at": "2026-10-06T20:00:00+00:00", "age_seconds": 30,
                "replay_available": isinstance(replay, dict), "replay_bytes": int(item.get("replay_bytes") or 0),
                "description_match": 1.0,
            }
            self.rows = [old for old in self.rows if old.get("record_id") != rid]
            self.rows.insert(0, row)
            saved.append(copy.deepcopy(row))
        return saved
    def invalidate_validation_target(self, target):
        doomed = {row["record_id"] for row in self.rows if row.get("target") == target}
        self.rows = [row for row in self.rows if row.get("target") != target]
        for rid in doomed:
            self.replays.pop(rid, None)
        return len(doomed)


class N1Client:
    timeout_seconds = 30
    def __init__(self):
        self.reuse_record_id = ""
    def generate(self, prompt, **_kwargs):
        if "information gatekeeper" in prompt:
            if self.reuse_record_id:
                return json.dumps({"decision": "reuse", "record_ids": [self.reuse_record_id], "reason": "prior chunk is still sufficient"})
            return json.dumps({"decision": "execute", "record_ids": [], "reason": "no replay yet"})
        if "passively watching N2" in prompt:
            return json.dumps({"loop": False, "reason": "", "instruction": ""})
        raise AssertionError(prompt[:180])


class Tools:
    read_chunk_max_bytes = 20_000
    def __init__(self):
        self.execute_count = 0
    def instructions(self):
        return "Use read_file for evidence."
    def schemas(self):
        return [{
            "type": "function",
            "function": {
                "name": "read_file", "description": "read",
                "parameters": {"type": "object", "properties": {"path": {"type": "string"}}},
            },
        }]
    def execute(self, name, arguments):
        assert name == "read_file"
        self.execute_count += 1
        source = ("abcdefghijklmnopqrstuvwxyz\n" * 5000).encode()
        start = int(arguments.get("start_byte") or 0)
        width = int(arguments.get("max_bytes") or 0)
        raw = source[start:start + width]
        return {
            "ok": True, "tool": "read_file", "path": r"C:\tmp\big.txt", "mode": "text",
            "source_fingerprint": "fp-stable", "source_size_bytes": len(source), "size": len(source),
            "returned_bytes": len(raw), "start_byte": start, "next_byte": start + len(raw),
            "truncated": start + len(raw) < len(source), "content": raw.decode(),
        }


class N2Client:
    system_instructions = "system"
    context_length = 65536
    model = "norm"
    def __init__(self):
        self.calls = 0
        self.stream_id = ""
    def effective_context_length(self, model=None):
        return 65536
    def chat_with_tools(self, messages, _schemas, **_kwargs):
        self.calls += 1
        if self.calls == 1:
            return {
                "content": "", "thinking": "", "done_reason": "stop", "eval_count": 10,
                "tool_calls": [{"function": {"name": "read_file", "arguments": {
                    "path": r"C:\tmp\big.txt", "n1_target": r"C:\tmp\big.txt", "n1_need": "piece one contents"
                }}}],
            }
        if self.calls == 2:
            # This is a fresh requeued slice. Request piece 1 from the restored stream;
            # N1 must replay Redis instead of invoking Tools again.
            assert self.stream_id
            return {
                "content": "", "thinking": "", "done_reason": "stop", "eval_count": 12,
                "tool_calls": [{"function": {"name": "read_file", "arguments": {
                    "path": r"C:\tmp\big.txt", "stream_id": self.stream_id, "piece": 1,
                    "n1_target": r"C:\tmp\big.txt", "n1_need": "piece one contents"
                }}}],
            }
        if self.calls == 3:
            payload = json.loads(messages[-1]["content"])
            assert payload["content"].startswith("{0:1}\n")
            assert payload["chunk_stream"]["piece"] == 1
            return {"content": "done", "thinking": "", "done_reason": "stop", "eval_count": 14, "tool_calls": []}
        raise AssertionError(self.calls)


queue = Queue()
live = Live()
n1_client = N1Client()
gate = N1Gatekeeper(n1_client, live)
n2 = N2Client()
worker = PromptWorker(queue, n2, live, None, n1_gatekeeper=gate)
limit = {"value": 1}
worker._runtime_config = lambda: {
    "worker": {"step_round_limit": limit["value"], "task_round_limit": 128},
    "context_budget": {"enabled": True, "raw_evidence_fraction": 0.20, "safety_fraction": 0.08,
        "compact_state_max_fraction": 0.30, "estimated_chars_per_token": 3.5,
        "estimated_text_bytes_per_token": 3.0, "min_raw_evidence_tokens": 1024,
        "max_raw_evidence_tokens": 32768, "min_safety_tokens": 2048,
        "max_retained_chars_per_piece": 5000},
}
tools = Tools()
job1 = PromptJob(message_id="1", task_id="t", step_id="s", prompt="inspect", project_id="default", context="", attempt=0,
    created_at=time.time(), metadata={}, chain_id="c", previous_prompt_id="", next_prompt_id="x", chain_index=0, recovery_attempted=False)
out1 = worker._run_tool_slice(job1, "inspect", tools)
assert out1["yielded"] is True and out1["reason"] == "step_round_limit"
assert tools.execute_count == 1
streams = out1["resume_metadata"]["chunked_evidence_streams"]
assert len(streams) == 1
n2.stream_id = next(iter(streams))
assert live.rows and live.rows[0]["replay_available"]
n1_client.reuse_record_id = live.rows[0]["record_id"]

limit["value"] = 2
job2 = PromptJob(message_id="2", task_id="t", step_id="s", prompt=out1["checkpoint"], project_id="default", context=out1["checkpoint"], attempt=0,
    created_at=time.time(), metadata={"chunked_evidence_streams": streams}, chain_id="c", previous_prompt_id="", next_prompt_id="x", chain_index=0, recovery_attempted=False)
out2 = worker._run_tool_slice(job2, out1["checkpoint"], tools)
assert out2["yielded"] is False and out2["answer"] == "done"
assert tools.execute_count == 1, "Redis replay should satisfy the requeued piece without touching source again"
print("PASS chunk stream survives requeue and N1 transparently replays the verified Redis payload")
