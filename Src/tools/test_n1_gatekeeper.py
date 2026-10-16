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
        self.call_kwargs = []
        self.reuse_record_id = ""
        self.fail_ingress = False
        self.fail_routing = False
        self.fail_final = False
        self.final_decision = "accept"
        self.routing_decision = "accept"

    def generate(self, prompt, **kwargs):
        self.calls.append(prompt)
        self.call_kwargs.append(dict(kwargs))
        if "user-ingress boundary" in prompt:
            if self.fail_ingress:
                raise RuntimeError("ingress boom")
            return json.dumps({"guidance": "Preserve the exact named constraint.", "reason": "constraint is easy to lose"})
        if "final-answer gatekeeper" in prompt:
            if self.fail_final:
                raise RuntimeError("final boom")
            if self.final_decision == "retry":
                return json.dumps({"decision": "retry", "reason": "material defect", "retry_instruction": "Fix the unsupported completion claim."})
            return json.dumps({"decision": "accept", "reason": "complete and supported", "retry_instruction": ""})
        if "subject decomposition before Norm stores" in prompt:
            if self.fail_routing:
                raise RuntimeError("routing boom")
            if self.routing_decision == "retry":
                return json.dumps({"decision": "retry", "reason": "weak thread placement", "retry_instruction": "Create a new subject thread instead of using the weak match."})
            return json.dumps({"decision": "accept", "reason": "subjects and placements are clean", "retry_instruction": ""})
        if "information gatekeeper" in prompt:
            if self.reuse_record_id:
                return json.dumps({"decision": "reuse", "record_ids": [self.reuse_record_id], "reason": "prior evidence remains sufficient"})
            return json.dumps({"decision": "execute", "record_ids": [], "reason": "fresh check needed"})
        if "passively watching N2" in prompt:
            return json.dumps({
                "loop": True,
                "reason": "same reasoning and tool path repeated without progress",
                "instruction": "Stop repeating this path; use existing evidence and choose a materially different next action.",
            })
        if "rabbit hole" in prompt:
            return json.dumps({"instruction": "Use the result already supplied and continue; do not repeat this request."})
        if "maintenance gatekeeper" in prompt:
            return json.dumps({"decision": "retry", "reason": "candidate can be tighter", "retry_instruction": "Keep important state and compress low-value detail."})
        if "fresh information tool just returned" in prompt:
            return json.dumps({"value": "0.53.14"})
        raise AssertionError(prompt[:200])


class FakeLive:
    def __init__(self):
        self.candidates = []
        self.recorded = []
        self.invalidated = []
        self.replays = {}
        self._counter = 0

    def validation_candidates(self, **kwargs):
        return copy.deepcopy(self.candidates)

    def validation_replay(self, record_id):
        return copy.deepcopy(self.replays.get(record_id))

    def record_global_validations(self, task_id, step_id, items):
        copied = copy.deepcopy(items)
        self.recorded.append((task_id, step_id, copied))
        saved = []
        for item in copied:
            self._counter += 1
            record_id = str(item.get("record_id") or "")
            if not record_id or record_id == "new":
                record_id = f"generated-{self._counter}"
            replay = item.get("replay_result")
            if isinstance(replay, dict):
                self.replays[record_id] = copy.deepcopy(replay)
            row = {
                "record_id": record_id, "tool": item.get("tool"), "target": item.get("target"),
                "description": item.get("description"), "scope": item.get("scope", ""),
                "value": item.get("value"), "num_checks": 1, "previous_value": "", "changed_at": "",
                "last_verified_at": "2026-10-06T20:00:00+00:00", "age_seconds": 0,
                "replay_available": isinstance(replay, dict), "replay_bytes": int(item.get("replay_bytes") or 0),
                "description_match": 1.0,
            }
            self.candidates = [r for r in self.candidates if r.get("record_id") != record_id]
            self.candidates.insert(0, row)
            saved.append(copy.deepcopy(row))
        return saved

    def invalidate_validation_target(self, target):
        self.invalidated.append(target)
        doomed = [r.get("record_id") for r in self.candidates if r.get("target") == target]
        self.candidates = [r for r in self.candidates if r.get("target") != target]
        for rid in doomed:
            self.replays.pop(rid, None)
        return len(doomed)


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


def test_subject_routing_review_uses_ingress_budget_and_can_retry():
    client = FakeClient()
    client.routing_decision = "retry"
    gate = N1Gatekeeper(
        client, FakeLive(), ingress_num_predict=4321, ingress_review_timeout_seconds=654,
    )
    result = gate.judge_subject_routing(
        request="Fix Norm. Also my RAID controller is weird.",
        proposal={"subjects": [{"label": "Norm", "content": "Fix Norm", "thread_id": "a", "match_confidence": 0.9, "create_thread_title": None, "primary": True}]},
        routing_context=[{"thread_id": "a", "title": "Norm"}],
        threshold=0.68,
    )
    assert result["decision"] == "retry"
    assert "new subject thread" in result["retry_instruction"]
    assert client.call_kwargs[-1]["num_predict"] == 4321
    assert client.call_kwargs[-1]["timeout_seconds"] == 654
    stats = gate.boundary_stats()
    assert stats["routing"]["total"] == 1
    assert stats["routing"]["retry"] == 1


def test_redis_reuse_skips_execution():
    client = FakeClient()
    client.reuse_record_id = "abc"
    live = FakeLive()
    live.candidates = [{
        "record_id": "abc", "tool": "read_file", "target": r"C:\\Norm\\Norm.exe",
        "description": "deployed version", "scope": 'read_file:{"path":"C:\\\\Norm\\\\Norm.exe"}',
        "value": "0.53.14", "num_checks": 6, "previous_value": "0.53.13",
        "changed_at": "2026-10-04T10:00:00+00:00", "last_verified_at": "2026-10-06T19:50:00+00:00",
        "age_seconds": 600, "replay_available": True, "replay_bytes": 100, "description_match": 0.96,
    }]
    live.replays["abc"] = {"ok": True, "tool": "read_file", "path": r"C:\\Norm\\Norm.exe", "content": "version=0.53.14"}
    gate = N1Gatekeeper(client, live)
    outcome = gate.before_tool(
        task_id="t", step_id="s", name="read_file",
        arguments={"path": r"C:\\Norm\\Norm.exe", "n1_target": r"C:\\Norm\\Norm.exe", "n1_need": "deployed version"},
    )
    assert outcome.execute is False
    assert outcome.result["content"] == "version=0.53.14"
    assert "n1_reused" not in outcome.result
    assert outcome.arguments == {"path": r"C:\\Norm\\Norm.exe"}


def test_chunked_read_can_replay_redis_payload_without_loop_reset():
    client = FakeClient()
    client.reuse_record_id = "chunk-1"
    live = FakeLive()
    live.candidates = [{
        "record_id": "chunk-1", "tool": "read_file", "target": r"C:\\tmp\\big.txt",
        "description": "requested file/archive contents or metadata",
        "scope": 'read_file:{"max_bytes":20000,"mode":"text","path":"C:\\\\tmp\\\\big.txt","start_byte":0}',
        "value": '{"source_fingerprint":"fp","start_byte":0,"next_byte":20000}',
        "num_checks": 1, "previous_value": "", "changed_at": "",
        "last_verified_at": "2026-10-06T20:00:00+00:00", "age_seconds": 600,
        "replay_available": True, "replay_bytes": 22000, "description_match": 1.0,
    }]
    raw = {
        "ok": True, "tool": "read_file", "path": r"C:\\tmp\\big.txt",
        "mode": "text", "start_byte": 0, "next_byte": 20000, "returned_bytes": 20000,
        "source_fingerprint": "fp", "source_size_bytes": 60000, "truncated": True,
        "content": "chunk one",
    }
    live.replays["chunk-1"] = raw
    gate = N1Gatekeeper(client, live)
    outcome = gate.before_tool(
        task_id="t", step_id="s", name="read_file",
        arguments={
            "path": r"C:\\tmp\\big.txt", "start_byte": 0, "max_bytes": 20000, "mode": "text",
            "_norm_chunked_evidence": True,
        },
    )
    assert outcome.execute is False
    assert outcome.streamed_read is True
    assert outcome.result == raw
    assert outcome.reset_instruction == ""
    decision_prompt = next(item for item in client.calls if "information gatekeeper" in item)
    assert '"start_byte": 0' in decision_prompt
    assert '"scope"' in decision_prompt


def test_multiple_small_prior_results_can_jointly_satisfy_one_request():
    class MultiClient(FakeClient):
        def generate(self, prompt, **kwargs):
            self.calls.append(prompt)
            self.call_kwargs.append(dict(kwargs))
            if "information gatekeeper" in prompt:
                return json.dumps({"decision": "reuse", "record_ids": ["a", "b"], "reason": "the two prior observations jointly answer it"})
            return super().generate(prompt, **kwargs)

    client = MultiClient()
    live = FakeLive()
    live.candidates = [
        {"record_id": "a", "tool": "list_directory", "target": "x", "description": "inventory", "scope": "one", "value": "A", "num_checks": 1, "previous_value": "", "changed_at": "", "last_verified_at": "now", "age_seconds": 10, "replay_available": True, "replay_bytes": 20, "description_match": 1.0},
        {"record_id": "b", "tool": "list_directory", "target": "x", "description": "inventory", "scope": "two", "value": "B", "num_checks": 1, "previous_value": "", "changed_at": "", "last_verified_at": "now", "age_seconds": 10, "replay_available": True, "replay_bytes": 20, "description_match": 0.99},
    ]
    live.replays["a"] = {"ok": True, "items": ["a"]}
    live.replays["b"] = {"ok": True, "items": ["b"]}
    gate = N1Gatekeeper(client, live, max_replay_records=3)
    outcome = gate.before_tool(
        task_id="t", step_id="s", name="list_directory",
        arguments={"path": "x", "n1_target": "x", "n1_need": "inventory"},
    )
    assert outcome.execute is False
    assert outcome.result["ok"] is True
    assert [row["items"][0] for row in outcome.result["results"]] == ["a", "b"]


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
    assert live.recorded[0][2][0]["replay_result"] == raw_before
    client.reuse_record_id = live.candidates[0]["record_id"]

    second = gate.before_tool(task_id="t", step_id="s", name="read_file", arguments=args)
    assert second.execute is False
    assert second.result == raw_before
    assert second.reset_instruction == ""

    third = gate.before_tool(task_id="t", step_id="s", name="read_file", arguments=args)
    assert third.execute is False
    assert third.result == raw_before
    assert third.reset_instruction == ""


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
    client.reuse_record_id = live.candidates[0]["record_id"]
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


def test_reasoning_loop_observer_leaves_information_repeats_to_verification_pool():
    gate = N1Gatekeeper(
        FakeClient(), FakeLive(), reasoning_loop_threshold=3, reasoning_similarity_floor=0.70
    )
    calls = [{"function": {"name": "read_file", "arguments": {"path": "x.txt", "n1_need": "contents", "n1_target": "x.txt"}}}]
    for _ in range(4):
        assert gate.observe_n2_turn(task_id="t", step_id="s", content="I need to inspect x again.", thinking="Checking the same evidence.", calls=calls) == ""


def test_reasoning_loop_observer_can_still_reset_noninformation_loop():
    gate = N1Gatekeeper(
        FakeClient(), FakeLive(), reasoning_loop_threshold=3, reasoning_similarity_floor=0.70
    )
    calls = [{"function": {"name": "write_file", "arguments": {"path": "x.txt", "content": "same"}}}]
    assert gate.observe_n2_turn(task_id="t", step_id="s", content="write it again", thinking="same path", calls=calls) == ""
    assert gate.observe_n2_turn(task_id="t", step_id="s", content="write it again", thinking="same path", calls=calls) == ""
    reset = gate.observe_n2_turn(task_id="t", step_id="s", content="write it again", thinking="same path", calls=calls)
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
    client.reuse_record_id = live.candidates[0]["record_id"]
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

def test_memory_condensation_judgment_and_final_bound():
    gate = N1Gatekeeper(FakeClient(), FakeLive())
    first = gate.judge_memory_condensation(
        mode="reductive", source_text="important source", candidate="x" * 2200,
        attempt=1, max_attempts=4, ideal_chars=1800, acceptable_chars=3400,
    )
    assert first["decision"] == "retry"
    final = gate.judge_memory_condensation(
        mode="destructive", source_text="obsolete state", candidate="{\"disposition\":\"destroy\"}",
        proposed_disposition="destroy", attempt=4, max_attempts=4, ideal_chars=1800, acceptable_chars=3400,
    )
    assert final["decision"] == "retry"
    stats = gate.maintenance_judgment_stats()
    assert stats["total"] == 2
    assert stats["retry"] == 2
    assert stats["accept"] == 0
    assert stats["destructive_accept"] == 0



def test_memory_condensation_uses_configurable_budget_and_timeout():
    client = FakeClient()
    gate = N1Gatekeeper(
        client, FakeLive(), maintenance_num_predict=4321,
        maintenance_review_timeout_seconds=654,
    )
    gate.judge_memory_condensation(
        mode="reductive", source_text="source", candidate="candidate",
        attempt=1, max_attempts=4, ideal_chars=1800, acceptable_chars=3400,
    )
    kwargs = client.call_kwargs[-1]
    assert kwargs["num_predict"] == 4321
    assert kwargs["timeout_seconds"] == 654.0


def test_ingress_review_is_real_and_configurable_without_rewriting_user_text():
    client = FakeClient()
    gate = N1Gatekeeper(
        client, FakeLive(), ingress_num_predict=3456, ingress_review_timeout_seconds=654,
    )
    user = "  Keep this exact request.  "
    review = gate.review_user_ingress(user)
    assert review["reviewed"] is True
    assert "constraint" in review["guidance"].lower()
    assert gate.forward_user(user) == user
    kwargs = client.call_kwargs[-1]
    assert kwargs["num_predict"] == 3456
    assert kwargs["timeout_seconds"] == 654.0


def test_final_answer_review_accept_retry_and_processing_failure_are_distinct():
    client = FakeClient()
    gate = N1Gatekeeper(
        client, FakeLive(), final_num_predict=4567, final_review_timeout_seconds=765,
    )
    accepted = gate.judge_final_answer(request="do it", candidate="done")
    assert accepted["decision"] == "accept"
    assert accepted["processing_failed"] is False
    kwargs = client.call_kwargs[-1]
    assert kwargs["num_predict"] == 4567
    assert kwargs["timeout_seconds"] == 765.0

    client.final_decision = "retry"
    retry = gate.judge_final_answer(request="do it", candidate="maybe done")
    assert retry["decision"] == "retry"
    assert "unsupported" in retry["retry_instruction"].lower()
    assert retry["processing_failed"] is False

    client.fail_final = True
    failed = gate.judge_final_answer(request="do it", candidate="odd structure")
    assert failed["processing_failed"] is True
    gate.record_final_review_failure_pass()
    stats = gate.boundary_stats()
    assert stats["ingress"]["total"] == 0
    assert stats["final"]["processing_failed"] == 1
    assert stats["final"]["review_failure_pass"] == 1


def test_ingress_failure_forwards_exact_text_fail_open():
    client = FakeClient()
    client.fail_ingress = True
    gate = N1Gatekeeper(client, FakeLive())
    text = "User request must survive."
    review = gate.review_user_ingress(text)
    assert review["reviewed"] is False
    assert review["guidance"] == ""
    assert gate.forward_user(text) == text
    assert gate.boundary_stats()["ingress"]["failed"] == 1



def test_sensitive_value_probe_gets_opaque_database_redirect_and_blocks_probe_call():
    gate = N1Gatekeeper(FakeClient(), FakeLive())
    calls = [{
        "function": {
            "name": "read_file",
            "arguments": {
                "path": r"D:\code\db_connect.py",
                "n1_target": r"D:\code\db_connect.py",
                "n1_need": "read more of db_connect.py to understand password usage",
            },
        }
    }]
    redirect = gate.observe_n2_turn(
        task_id="t", step_id="s",
        thinking="I need to find the actual hardcoded password value in this file.",
        content="", calls=calls,
    )
    assert redirect == "The Bird says No. The Worm won't Go. I can query the database directly."

    blocked = gate.before_tool(
        task_id="t", step_id="s", name="read_file",
        arguments={
            "path": r"D:\code\db_connect.py",
            "n1_target": r"D:\code\db_connect.py",
            "n1_need": "read more of db_connect.py to understand password usage",
        },
    )
    assert blocked.execute is False
    assert blocked.result["n1_redirected"] is True
    assert blocked.result["error"] == redirect
    assert blocked.reset_instruction == redirect


def test_database_pool_remains_allowed_after_sensitive_probe():
    gate = N1Gatekeeper(FakeClient(), FakeLive())
    gate.observe_n2_turn(
        task_id="t", step_id="s",
        thinking="I need the actual password value.",
        calls=[{"function": {"name": "read_file", "arguments": {"path": "db_connect.py"}}}],
    )
    outcome = gate.before_tool(
        task_id="t", step_id="s", name="plugin_postgres_pool__main__postgres_query",
        arguments={"query": "select 1", "n1_target": "postgres", "n1_need": "verify database access"},
    )
    assert outcome.execute is True
    assert outcome.reset_instruction == ""


def test_repeated_sensitive_probe_gets_firmer_redirect():
    gate = N1Gatekeeper(FakeClient(), FakeLive())
    first = gate.observe_n2_turn(
        task_id="t", step_id="s", thinking="Find the actual password value.", calls=[]
    )
    second = gate.observe_n2_turn(
        task_id="t", step_id="s", thinking="That password didn't work; I need the real credential.", calls=[]
    )
    assert first == "The Bird says No. The Worm won't Go. I can query the database directly."
    assert second == "The Bird says No. The Worm won't Go. Use the database access I already offered."


def test_accepting_database_redirect_does_not_retrigger_bird():
    gate = N1Gatekeeper(FakeClient(), FakeLive())
    reset = gate.observe_n2_turn(
        task_id="t", step_id="s",
        thinking="I do not need the actual password; I can query the database directly instead.",
        calls=[{"function": {"name": "plugin_postgres_pool__main__postgres_query", "arguments": {"query": "select 1"}}}],
    )
    assert reset == ""


def test_password_usage_analysis_is_not_itself_blocked():
    gate = N1Gatekeeper(FakeClient(), FakeLive())
    outcome = gate.before_tool(
        task_id="t", step_id="s", name="read_file",
        arguments={
            "path": "db_connect.py", "n1_target": "db_connect.py",
            "n1_need": "understand how the password is passed into the DatabaseManager and where the connection is used",
        },
    )
    assert outcome.execute is True


def test_manual_database_credential_attempt_is_redirected_but_pool_is_not():
    gate = N1Gatekeeper(FakeClient(), FakeLive())
    manual = gate.before_tool(
        task_id="t", step_id="s", name="run_command",
        arguments={"command": "python -c \"import psycopg; psycopg.connect('host=x password=[REDACTED]')\""},
    )
    assert manual.execute is False
    assert manual.result["n1_redirected"] is True
    assert manual.result["error"].startswith("The Bird says No.")

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
        test_reasoning_loop_observer_leaves_information_repeats_to_verification_pool,
        test_reasoning_loop_observer_can_still_reset_noninformation_loop,
        test_redis_reuse_skips_execution,
        test_chunked_read_can_replay_redis_payload_without_loop_reset,
        test_multiple_small_prior_results_can_jointly_satisfy_one_request,
        test_fresh_result_is_not_mutated_and_is_reused_raw,
        test_mutations_pass_without_n1_verification_metadata,
        test_mutation_invalidates_prior_raw_read,
        test_file_mutation_invalidates_parent_directory_cache,
        test_memory_condensation_judgment_and_final_bound,
        test_memory_condensation_uses_configurable_budget_and_timeout,
        test_ingress_review_is_real_and_configurable_without_rewriting_user_text,
        test_final_answer_review_accept_retry_and_processing_failure_are_distinct,
        test_ingress_failure_forwards_exact_text_fail_open,
        test_sensitive_value_probe_gets_opaque_database_redirect_and_blocks_probe_call,
        test_database_pool_remains_allowed_after_sensitive_probe,
        test_repeated_sensitive_probe_gets_firmer_redirect,
        test_accepting_database_redirect_does_not_retrigger_bird,
        test_password_usage_analysis_is_not_itself_blocked,
        test_manual_database_credential_attempt_is_redirected_but_pool_is_not,
        test_third_repeated_noncacheable_call_is_blocked,
    ):
        fn()
        print(f"PASS {fn.__name__}")

