from __future__ import annotations

import sys
from pathlib import Path
from threading import Event

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "core"))

from norm_runtime.prompt_queue import PromptJob
from norm_runtime.prompt_worker import PromptWorker


class Queue:
    def __init__(self):
        self.rounds = 0
    def task_rounds(self, _task_id):
        return self.rounds
    def add_task_rounds(self, _task_id, amount):
        self.rounds += amount
        return self.rounds


class Live:
    def __init__(self):
        self.count = 45
    def global_validation(self, subject, **_kwargs):
        rows = self.recent_global_validations()
        return rows[0] if subject == "PE version" else None
    def recent_global_validations(self, **_kwargs):
        return [{
            "subject": "PE version", "value": "0.53.1",
            "recent_checks": self.count, "generation_checks": self.count,
            "source_counts": {"tool": self.count}, "storage": "redis_live",
            "generation_started_at": "2026-10-01T00:00:00+00:00",
            "last_checked_at": "2026-10-02T12:00:00+00:00",
        }]


class Tools:
    def __init__(self, live):
        self.live = live
    def instructions(self):
        return "TOOLS"
    def schemas(self):
        return [{"type": "function", "function": {"name": "lookup", "parameters": {"type": "object"}}}]
    def execute(self, name, arguments):
        assert name == "lookup"
        self.live.count = 46
        return {"ok": True, "value": "0.53.1"}


class Client:
    def __init__(self):
        self.prompts = []
    def chat_with_tools(self, messages, schemas, **_kwargs):
        self.prompts.append(messages[0]["content"])
        if len(self.prompts) == 1:
            return {"content": "", "thinking": "", "done_reason": "", "eval_count": 1,
                    "tool_calls": [{"function": {"name": "lookup", "arguments": {}}}]}
        return {"content": "done", "thinking": "", "done_reason": "stop", "eval_count": 1, "tool_calls": []}


w = PromptWorker.__new__(PromptWorker)
w.queue = Queue()
w.live = Live()
w.durable = None
w.client = Client()
w._drain = Event()
w._slice_limits = lambda: (4, 8)
w._validation_pool_config = lambda: {
    "enabled": True, "recent_window_seconds": 86400, "observation_retention_seconds": 604800,
    "emergency_row_cap": 512, "context_token_budget": 2500, "context_char_budget": 10000,
    "reuse_after_checks": 3, "durable_snapshot_interval_seconds": 43200,
}
w._consume_context_injections = lambda _job: []
w._suppression_requested = lambda _task_id: False
w._verify_pending = lambda *_args, **_kwargs: []
w._persist_thinking_now = lambda *_args, **_kwargs: 0
w._persist_evidence_now = lambda *_args, **_kwargs: None

job = PromptJob(
    message_id="m", task_id="t", step_id="s", prompt="check", project_id="p", context="",
    attempt=0, created_at=0.0, metadata={}, chain_id="", previous_prompt_id="", next_prompt_id="",
    chain_index=0, recovery_attempted=False,
)
result = w._run_tool_slice(job, "Find PE version only if another lookup is worth it.", Tools(w.live))
assert result["answer"] == "done"
assert "PE version = 0.53.1" in w.client.prompts[0]
assert "recent_24h=45" in w.client.prompts[0]
assert "recent_24h=46" in w.client.prompts[1], w.client.prompts[1]
print("PASS: one global Redis verification pool is refreshed before every tool-decision round")
