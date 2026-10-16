from __future__ import annotations

import json
import sys
import time
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "core"))

# The artifact-test container intentionally does not carry runtime Redis/Postgres
# drivers. PromptWorker only needs their module names for this isolated flow test.
if "redis" not in sys.modules:
    redis_stub = types.ModuleType("redis")
    redis_stub.Redis = object
    redis_stub.ResponseError = RuntimeError
    sys.modules["redis"] = redis_stub
if "psycopg" not in sys.modules:
    sys.modules["psycopg"] = types.ModuleType("psycopg")

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
    pass


class Tools:
    read_chunk_max_bytes = 24_000
    def instructions(self):
        return "Use read_file. Large reads are chunked."
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
        assert name == "read_file"
        start = int(arguments.get("start_byte") or 0)
        width = int(arguments.get("max_bytes") or 0)
        source = ("0123456789abcdef" * 10_000).encode("utf-8")
        raw = source[start:start + width]
        return {
            "ok": True,
            "tool": "read_file",
            "path": r"C:\\tmp\\big.txt",
            "source_fingerprint": "same-source",
            "source_size_bytes": len(source),
            "size": len(source),
            "mode": "text",
            "returned_bytes": len(raw),
            "start_byte": start,
            "next_byte": start + len(raw),
            "truncated": start + len(raw) < len(source),
            "content": raw.decode("utf-8"),
        }


class Client:
    system_instructions = "system"
    context_length = 65_536
    model = "norm"
    def __init__(self):
        self.calls = 0
        self.first_stream_id = ""
        self.first_chunk_width = 0
    def effective_context_length(self, model=None):
        return 65_536
    def chat_with_tools(self, messages, tools, **kwargs):
        self.calls += 1
        if self.calls == 1:
            return {
                "content": "",
                "thinking": "",
                "tool_calls": [{"function": {"name": "read_file", "arguments": {"path": r"C:\\tmp\\big.txt"}}}],
                "done_reason": "stop",
                "eval_count": 10,
            }
        if self.calls == 2:
            payload = json.loads(messages[-1]["content"])
            meta = payload["chunk_stream"]
            assert payload["content"].startswith("{0:1}\n")
            assert payload["content"].endswith("\n{1:2}")
            self.first_stream_id = meta["stream_id"]
            self.first_chunk_width = meta["chunk_bytes"]
            return {
                "content": "",
                "thinking": "",
                "tool_calls": [{"function": {"name": "read_file", "arguments": {
                    "path": r"C:\\tmp\\big.txt",
                    "stream_id": self.first_stream_id,
                    "piece": 3,
                    "retain": "piece 1 establishes the header format",
                }}}],
                "done_reason": "stop",
                "eval_count": 20,
            }
        if self.calls == 3:
            # Exactly one raw chunk is active. Piece 1 must now be a compact stub,
            # while piece 3 is the only content-bearing chunk.
            chunk_payloads = []
            evicted = []
            for message in messages:
                if message.get("role") != "tool":
                    continue
                payload = json.loads(message["content"])
                if payload.get("chunk_evicted"):
                    evicted.append(payload)
                elif isinstance(payload.get("chunk_stream"), dict) and "content" in payload:
                    chunk_payloads.append(payload)
            assert len(evicted) == 1, evicted
            assert evicted[0]["retained_facts"] == "piece 1 establishes the header format"
            assert len(chunk_payloads) == 1, len(chunk_payloads)
            assert chunk_payloads[0]["chunk_stream"]["piece"] == 3
            assert chunk_payloads[0]["content"].startswith("{2:3}\n")
            return {
                "content": "done",
                "thinking": "",
                "tool_calls": [],
                "done_reason": "stop",
                "eval_count": 30,
            }
        raise AssertionError("unexpected extra model call")


queue = Queue()
client = Client()
worker = PromptWorker(queue, client, Live(), None, n1_gatekeeper=None)
worker._runtime_config = lambda: {
    "worker": {"step_round_limit": 16, "task_round_limit": 128},
    "context_budget": {
        "enabled": True,
        "raw_evidence_fraction": 0.20,
        "safety_fraction": 0.08,
        "compact_state_max_fraction": 0.30,
        "estimated_chars_per_token": 3.5,
        "estimated_text_bytes_per_token": 3.0,
        "min_raw_evidence_tokens": 1024,
        "max_raw_evidence_tokens": 32768,
        "min_safety_tokens": 2048,
        "max_retained_chars_per_piece": 5000,
    },
}
job = PromptJob(
    message_id="1-0", task_id="t", step_id="s", prompt="inspect the large file",
    project_id="default", context="", attempt=0, created_at=time.time(), metadata={},
    chain_id="c", previous_prompt_id="", next_prompt_id="x", chain_index=0,
    recovery_attempted=False,
)
outcome = worker._run_tool_slice(job, "inspect the large file", Tools())
assert outcome["yielded"] is False
assert outcome["answer"] == "done"
assert client.calls == 3
assert client.first_chunk_width <= Tools.read_chunk_max_bytes
print("PASS chunked worker flow")
