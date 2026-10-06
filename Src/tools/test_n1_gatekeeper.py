from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CORE = ROOT / "core"
if str(CORE) not in sys.path:
    sys.path.insert(0, str(CORE))

from norm_runtime.n1_gatekeeper import N1Gatekeeper


class FakeClient:
    def __init__(self):
        self.calls = []
        self.reuse_record_id = ""

    def generate(self, prompt, **kwargs):
        self.calls.append(prompt)
        if "Decide whether the existing LIVE Redis verification record" in prompt:
            if self.reuse_record_id:
                return json.dumps({"decision": "reuse", "record_id": self.reuse_record_id, "reason": "already established"})
            return json.dumps({"decision": "execute", "record_id": "", "reason": "fresh check needed"})
        if "passively watching N2" in prompt:
            return json.dumps({
                "loop": True,
                "reason": "same reasoning and tool path repeated without progress",
                "instruction": "Stop repeating this path; use existing evidence and choose a materially different next action.",
            })
        if "rabbit hole" in prompt:
            return json.dumps({"instruction": "Use the result already supplied and continue; do not repeat this request."})
        if "fresh information tool just returned" in prompt:
            return json.dumps({"value": "0.53.14"})
        raise AssertionError(prompt[:200])


class FakeLive:
    def __init__(self):
        self.candidates = []
        self.recorded = []
        self.invalidated = []

    def validation_candidates(self, **kwargs):
        return copy.deepcopy(self.candidates)

    def record_global_validations(self, task_id, step_id, items):
        self.recorded.append((task_id, step_id, copy.deepcopy(items)))
        return copy.deepcopy(items)

    def invalidate_validation_target(self, target):
        self.invalidated.append(target)
        return 1


def _read_schema():
    return [{
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "read",
            "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
        },
    }]


def _write_schema():
    return [{
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "write",
            "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
        },
    }]


def test_schema_instrumentation():
    gate = N1Gatekeeper(FakeClient(), FakeLive())
    read = gate.instrument_schemas(_read_schema())[0]["function"]["parameters"]
    assert "n1_need" in read["properties"]
    assert "n1_target" in read["properties"]
    assert "n1_need" in read["required"]
    assert "n1_target" in read["required"]
    write = gate.instrument_schemas(_write_schema())[0]["function"]["parameters"]
    assert "n1_need" not in write["properties"]
    assert "n1_target" not in write["properties"]


def test_redis_reuse_skips_execution():
    client = FakeClient()
    client.reuse_record_id = "abc"
    live = FakeLive()
    live.candidates = [{
        "record_id": "abc", "tool": "read_file", "target": r"C:\\Norm\\Norm.exe",
        "description": "deployed version", "value": "0.53.14", "num_checks": 6,
        "previous_value": "0.53.13", "changed_at": "2026-10-04T10:00:00+00:00", "description_match": 0.96,
    }]
    gate = N1Gatekeeper(client, live)
    outcome = gate.before_tool(
        task_id="t", step_id="s", name="read_file",
        arguments={"path": r"C:\\Norm\\Norm.exe", "n1_target": r"C:\\Norm\\Norm.exe", "n1_need": "deployed version"},
    )
    assert outcome.execute is False
    assert outcome.result["n1_reused"] is True
    assert outcome.result["value"] == "0.53.14"
    assert outcome.arguments == {"path": r"C:\\Norm\\Norm.exe"}


def test_fresh_result_is_not_mutated_and_is_reused_raw():
    client = FakeClient()
    live = FakeLive()
    gate = N1Gatekeeper(client, live, loop_repeat_threshold=3)
    args = {"path": r"C:\\Norm\\Norm.exe", "n1_target": r"C:\\Norm\\Norm.exe", "n1_need": "deployed version"}
    first = gate.before_tool(task_id="t", step_id="s", name="read_file", arguments=args)
    assert first.execute is True
    raw = {"ok": True, "tool": "read_file", "path": r"C:\\Norm\\Norm.exe", "content": "raw executor payload"}
    raw_before = copy.deepcopy(raw)
    meta = gate.after_tool(task_id="t", step_id="s", name="read_file", outcome=first, result=raw)
    assert raw == raw_before
    assert meta["recorded"] is True
    assert live.recorded[0][2][0]["value"] == "0.53.14"

    second = gate.before_tool(task_id="t", step_id="s", name="read_file", arguments=args)
    assert second.execute is False
    assert second.result == raw_before
    assert second.reset_instruction == ""

    third = gate.before_tool(task_id="t", step_id="s", name="read_file", arguments=args)
    assert third.execute is False
    assert third.result == raw_before
    assert "do not repeat" in third.reset_instruction.lower()


def test_mutations_pass_without_n1_verification_metadata():
    live = FakeLive()
    gate = N1Gatekeeper(FakeClient(), live)
    outcome = gate.before_tool(
        task_id="t", step_id="s", name="write_file",
        arguments={"path": "x.txt", "content": "hello"},
    )
    assert outcome.execute is True
    assert outcome.arguments == {"path": "x.txt", "content": "hello"}
    gate.after_tool(
        task_id="t", step_id="s", name="write_file", outcome=outcome,
        result={"ok": True, "tool": "write_file", "path": "x.txt"},
    )
    assert live.invalidated == ["x.txt"]


def test_mutation_invalidates_prior_raw_read():
    client = FakeClient()
    live = FakeLive()
    gate = N1Gatekeeper(client, live)
    read_args = {"path": "x.txt", "n1_target": "x.txt", "n1_need": "file contents"}
    read = gate.before_tool(task_id="t", step_id="s", name="read_file", arguments=read_args)
    raw = {"ok": True, "tool": "read_file", "path": "x.txt", "content": "old"}
    gate.after_tool(task_id="t", step_id="s", name="read_file", outcome=read, result=raw)
    assert gate.before_tool(task_id="t", step_id="s", name="read_file", arguments=read_args).execute is False

    write = gate.before_tool(task_id="t", step_id="s", name="write_file", arguments={"path": "x.txt", "content": "new"})
    gate.after_tool(task_id="t", step_id="s", name="write_file", outcome=write, result={"ok": True, "tool": "write_file", "path": "x.txt"})
    reread = gate.before_tool(task_id="t", step_id="s", name="read_file", arguments=read_args)
    assert reread.execute is True



def test_passthrough_is_identity():
    gate = N1Gatekeeper(FakeClient(), FakeLive())
    user = "  Keep my spacing\nexactly.  "
    answer = "N2 answer\nunchanged"
    assert gate.forward_user(user) == user
    assert gate.forward_to_user(answer) == answer


def test_reasoning_loop_observer_can_reset_repeated_turn():
    gate = N1Gatekeeper(
        FakeClient(), FakeLive(), reasoning_loop_threshold=3, reasoning_similarity_floor=0.70
    )
    calls = [{"function": {"name": "read_file", "arguments": {"path": "x.txt", "n1_need": "contents", "n1_target": "x.txt"}}}]
    assert gate.observe_n2_turn(task_id="t", step_id="s", content="I need to inspect x again.", thinking="Checking the same evidence.", calls=calls) == ""
    assert gate.observe_n2_turn(task_id="t", step_id="s", content="I need to inspect x again.", thinking="Checking the same evidence.", calls=calls) == ""
    reset = gate.observe_n2_turn(task_id="t", step_id="s", content="I need to inspect x again.", thinking="Checking the same evidence.", calls=calls)
    assert "different next action" in reset.lower()


def test_file_mutation_invalidates_parent_directory_cache():
    client = FakeClient()
    live = FakeLive()
    gate = N1Gatekeeper(client, live)
    directory = r"C:\work"
    file_path = r"C:\work\new.txt"
    list_args = {"path": directory, "n1_target": directory, "n1_need": "directory contents"}
    listing = gate.before_tool(task_id="t", step_id="s", name="list_directory", arguments=list_args)
    gate.after_tool(
        task_id="t", step_id="s", name="list_directory", outcome=listing,
        result={"ok": True, "tool": "list_directory", "path": directory, "items": []},
    )
    assert gate.before_tool(task_id="t", step_id="s", name="list_directory", arguments=list_args).execute is False

    write = gate.before_tool(
        task_id="t", step_id="s", name="write_file", arguments={"path": file_path, "content": "x"}
    )
    gate.after_tool(
        task_id="t", step_id="s", name="write_file", outcome=write,
        result={"ok": True, "tool": "write_file", "path": file_path, "file_mutation": True},
    )
    assert directory in live.invalidated
    assert gate.before_tool(task_id="t", step_id="s", name="list_directory", arguments=list_args).execute is True

def test_third_repeated_noncacheable_call_is_blocked():
    gate = N1Gatekeeper(FakeClient(), FakeLive(), loop_repeat_threshold=3)
    args = {"command": "echo hi"}
    assert gate.before_tool(task_id="t", step_id="s", name="run_command", arguments=args).execute is True
    assert gate.before_tool(task_id="t", step_id="s", name="run_command", arguments=args).execute is True
    third = gate.before_tool(task_id="t", step_id="s", name="run_command", arguments=args)
    assert third.execute is False
    assert third.result["n1_loop_blocked"] is True


if __name__ == "__main__":
    for fn in (
        test_schema_instrumentation,
        test_passthrough_is_identity,
        test_reasoning_loop_observer_can_reset_repeated_turn,
        test_redis_reuse_skips_execution,
        test_fresh_result_is_not_mutated_and_is_reused_raw,
        test_mutations_pass_without_n1_verification_metadata,
        test_mutation_invalidates_prior_raw_read,
        test_file_mutation_invalidates_parent_directory_cache,
        test_third_repeated_noncacheable_call_is_blocked,
    ):
        fn()
        print(f"PASS {fn.__name__}")
