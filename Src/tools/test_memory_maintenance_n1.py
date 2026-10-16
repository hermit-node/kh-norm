from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CORE = ROOT / "core"
if str(CORE) not in sys.path:
    sys.path.insert(0, str(CORE))

from norm_runtime.history_maintenance import DeepHistoryMaintainer


class TextClient:
    def __init__(self):
        self.calls = 0

    def generate_complete_text(self, prompt, **kwargs):
        self.calls += 1
        return "A" * (2500 if self.calls == 1 else 1700)


class StructuredClient:
    @staticmethod
    def parse_json(raw):
        return json.loads(raw)

    def generate(self, prompt, **kwargs):
        return json.dumps({
            "disposition": "destroy",
            "reason": "superseded implementation chatter",
            "outcome": "",
            "lessons": "",
            "future_note": "",
        })


class DestroyThenKeepClient:
    def __init__(self):
        self.calls = 0

    @staticmethod
    def parse_json(raw):
        return json.loads(raw)

    def generate(self, prompt, **kwargs):
        self.calls += 1
        if self.calls == 1:
            return json.dumps({
                "disposition": "destroy",
                "reason": "looks obsolete",
                "outcome": "",
                "lessons": "",
                "future_note": "",
            })
        return json.dumps({
            "disposition": "keep",
            "reason": "N1 required preservation",
            "outcome": "Current durable outcome",
            "lessons": "Reusable lesson",
            "future_note": "Useful future note",
        })


class RejectDestroyGate:
    def __init__(self):
        self.calls = []

    def judge_memory_condensation(self, **kwargs):
        self.calls.append(kwargs)
        if kwargs.get("proposed_disposition") == "destroy":
            return {"decision": "retry", "reason": "still useful", "retry_instruction": "keep the durable lesson"}
        return {"decision": "retry", "reason": "could be tighter", "retry_instruction": "tighten without deleting"}


class Gate:
    def __init__(self):
        self.calls = []

    def judge_memory_condensation(self, **kwargs):
        self.calls.append(kwargs)
        if kwargs.get("proposed_disposition") == "destroy":
            return {"decision": "accept", "reason": "safe to discard", "retry_instruction": ""}
        if kwargs["attempt"] == 1:
            return {"decision": "retry", "reason": "too verbose", "retry_instruction": "compress low-value detail"}
        return {"decision": "accept", "reason": "good", "retry_instruction": ""}


def _maintainer(client, gate):
    obj = DeepHistoryMaintainer.__new__(DeepHistoryMaintainer)
    obj.client = client
    obj.n1_gatekeeper = gate
    obj.config = {
        "consolidation_batch_target_chars": 1800,
        "consolidation_batch_accept_chars": 3400,
        "consolidation_max_attempts": 4,
    }
    return obj


def test_reductive_retry_then_accept():
    client = TextClient()
    gate = Gate()
    m = _maintainer(client, gate)
    text, judgments = m._condense_plain_with_n1(
        prompt="condense", source_text="source", mode="reductive",
        ideal_chars=1800, acceptable_chars=3400, num_predict=900, max_attempts=4,
    )
    assert len(text) == 1700
    assert client.calls == 2
    assert [j["decision"] for j in judgments] == ["retry", "accept"]


def test_destructive_discard_requires_n1_accept():
    gate = Gate()
    m = _maintainer(StructuredClient(), gate)
    group = [{
        "task_id": "t1", "status": "completed", "title": "old implementation",
        "original_request": "implement old thing", "task_kind": "root",
        "parent_task_id": "", "parent_step_id": "", "task_depth": 0,
        "terminal_summary": "superseded and complete", "effectiveness_note": "",
        "steps": [], "evidence": [], "started_at": datetime(2026, 9, 1, tzinfo=timezone.utc),
        "updated_at": datetime(2026, 9, 2, tzinfo=timezone.utc),
    }]
    result = m._summarize_group(group, destructive=True)
    assert result is None
    assert gate.calls[-1]["proposed_disposition"] == "destroy"


def test_rejected_destroy_falls_back_to_safe_keep():
    gate = RejectDestroyGate()
    client = DestroyThenKeepClient()
    m = _maintainer(client, gate)
    group = [{
        "task_id": "t2", "status": "completed", "title": "mixed-value implementation",
        "original_request": "implement thing", "task_kind": "root",
        "parent_task_id": "", "parent_step_id": "", "task_depth": 0,
        "terminal_summary": "mostly stale, but one lesson remains", "effectiveness_note": "",
        "steps": [], "evidence": [], "started_at": datetime(2026, 9, 3, tzinfo=timezone.utc),
        "updated_at": datetime(2026, 9, 4, tzinfo=timezone.utc),
    }]
    result = m._summarize_group(group, destructive=True)
    assert result is not None
    assert result["outcome"] == "Current durable outcome"
    assert client.calls == 4
    assert any(call.get("proposed_disposition") == "destroy" for call in gate.calls)


if __name__ == "__main__":
    for fn in (test_reductive_retry_then_accept, test_destructive_discard_requires_n1_accept, test_rejected_destroy_falls_back_to_safe_keep):
        fn()
        print(f"PASS {fn.__name__}")
