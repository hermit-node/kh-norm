from __future__ import annotations

import ast
import json
import logging
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "core" / "norm_runtime" / "conversation_service.py").read_text(encoding="utf-8")


class ModelOutputTruncated(RuntimeError):
    pass


class ModelDegenerateOutput(RuntimeError):
    pass


def _harness_class():
    tree = ast.parse(SOURCE)
    service = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "ConversationService")
    wanted = {"_conservative_primary_request", "_summary_violates_current_state", "_summary_fallback", "_refresh_summary"}
    methods = [node for node in service.body if isinstance(node, ast.FunctionDef) and node.name in wanted]
    assert {node.name for node in methods} == wanted
    cls = ast.ClassDef(name="Harness", bases=[], keywords=[], body=methods, decorator_list=[])
    module = ast.Module(body=[cls], type_ignores=[])
    ast.fix_missing_locations(module)
    ns = {
        "json": json,
        "logging": logging,
        "re": re,
        "ModelOutputTruncated": ModelOutputTruncated,
        "ModelDegenerateOutput": ModelDegenerateOutput,
    }
    exec(compile(module, "<runtime-summary-test>", "exec"), ns)
    return ns["Harness"]


class FakeStore:
    def __init__(self) -> None:
        self.saved = []
        self.messages_since_calls = []
        self.active_calls = []

    def latest_summary_state(self, thread_id: str) -> dict:
        return {
            "summary": "Durable facts: Norm v0.51.8 is current. On 9/23 this superseded 9/19.",
            "covers_through_message_id": "old-covered-message",
            "version": 17,
            "created_at": "2026-09-23T12:00:00-04:00",
        }

    def messages_since(self, thread_id: str, after_message_id: str | None, *, through_message_id: str | None = None) -> list[dict]:
        self.messages_since_calls.append((thread_id, after_message_id, through_message_id))
        return [
            {
                "message_id": "u-new",
                "role": "user",
                "content": "Norm is now v0.53.13; the old 0.51.8 state is obsolete.",
                "created_at": "2026-10-03T22:00:00-04:00",
            },
            {
                "message_id": through_message_id,
                "role": "assistant",
                "content": "Verified current runtime is Norm v0.53.13.",
                "created_at": "2026-10-03T22:01:00-04:00",
            },
        ]

    def active_memories(self, project_id: str, thread_ids: list[str], limit: int = 40) -> list[dict]:
        self.active_calls.append((project_id, tuple(thread_ids), limit))
        return [
            {
                "memory_id": "m-new",
                "type": "fact",
                "content": "Norm current deployed version is 0.53.13.",
                "updated_at": "2026-10-03T22:01:00-04:00",
            },
            {
                "memory_id": "m-rule",
                "type": "constraint",
                "content": "Use the sanctioned PostgreSQL pool.",
                "updated_at": "2026-10-03T21:00:00-04:00",
            },
        ]

    def save_summary(self, thread_id: str, summary: str, covers_through_message_id: str | None = None) -> None:
        self.saved.append((thread_id, summary, covers_through_message_id))


def _instance(store: FakeStore):
    harness = _harness_class()()
    harness.store = store
    harness.recent_message_limit = 12
    return harness


def main() -> int:
    store = FakeStore()
    service = _instance(store)
    calls = []

    def first_recreates_bad_ledger(prompt, schema, **kwargs):
        calls.append((prompt, schema, kwargs))
        if len(calls) == 1:
            return {
                "summary": (
                    "Current state: Norm v0.53.13 is deployed.\n\n"
                    "Superseded information:\n- Norm v0.51.8 was current on 9/19.\n\n"
                    "Recent messages:\n- user asked for the current state"
                )
            }
        return {"summary": "Current state: Norm v0.53.13 is deployed. Use the sanctioned PostgreSQL pool."}

    service._structured_generate = first_recreates_bad_ledger
    service._refresh_summary("default", ["thread-1"], "thread-1", "a-new")
    assert store.messages_since_calls == [("thread-1", "old-covered-message", "a-new")]
    assert store.saved[-1][2] == "a-new"
    assert "0.53.13" in store.saved[-1][1] and "0.51.8" not in store.saved[-1][1]
    assert calls[0][2]["emit_stream"] is False
    assert "There is no target chunk size" in calls[0][0]



    # Prompt interpretation may extract intent/side facts, but ordinary task sentences
    # are never dropped just because the classifier labels them as memory-worthy.
    original = "Fix the end of task summary. It should preserve the exact user qualifier and prune stale state."
    misclassified = [{
        "verbatim": "It should preserve the exact user qualifier and prune stale state.",
        "destination": "memory_constraint",
        "also_current_task": False,
    }]
    assert service._conservative_primary_request(original, misclassified) == original

    with_aside = "Fix the end of task summary. By the way, remember that I prefer concise operator logs."
    sidecar = [{
        "verbatim": "By the way, remember that I prefer concise operator logs.",
        "destination": "memory_preference",
        "also_current_task": False,
    }]
    assert service._conservative_primary_request(with_aside, sidecar) == "Fix the end of task summary."

    store_bad = FakeStore()
    service_bad = _instance(store_bad)

    def always_bad_ledger(*args, **kwargs):
        return {
            "summary": (
                "Durable facts: Norm v0.51.8 is current.\n\n"
                "Superseded information:\n- 9/23 superseded 9/19.\n\n"
                "Recent messages:\n- embedded transcript"
            )
        }

    service_bad._structured_generate = always_bad_ledger
    service_bad._refresh_summary("default", ["thread-1"], "thread-1", "a-new")
    bad_fallback = store_bad.saved[-1][1]
    assert "Superseded information" not in bad_fallback
    assert "Recent messages" not in bad_fallback
    assert "0.53.13" in bad_fallback and "0.51.8" not in bad_fallback

    store2 = FakeStore()
    service2 = _instance(store2)

    def always_fail(*args, **kwargs):
        raise ModelDegenerateOutput("synthetic")

    service2._structured_generate = always_fail
    service2._refresh_summary("default", ["thread-1"], "thread-1", "a-new")
    fallback = store2.saved[-1][1]
    assert "0.53.13" in fallback and "0.51.8" not in fallback and "9/23" not in fallback
    assert store2.saved[-1][2] == "a-new"

    assert "Summary refresh deferred after two output-budget hits; preserving prior summary" not in SOURCE
    assert "_conservative_primary_request" in SOURCE
    runtime = json.loads((ROOT / "config" / "runtime.json").read_text(encoding="utf-8-sig"))
    assert runtime["memory"]["consolidation_batch_chars"] == 14000

    print("PASS runtime summary advances by coverage cursor and prunes stale prior state")
    print("PASS append-only Superseded information / Recent messages shapes are rejected")
    print("PASS double summary-model failure cannot freeze the old summary")
    print("PASS executable wording preserves ordinary task sentences and only removes explicit exact asides")
    print("PASS unrelated consolidation_batch_chars remains 14000")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
