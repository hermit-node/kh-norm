"""Offline regression for step-boundary evidence/budget isolation (no DB needed)."""
from __future__ import annotations

import ast
import copy
import json
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]


def extract_method(file: str, class_name: str, method: str, *, globals_: dict | None = None):
    tree = ast.parse((ROOT / file).read_text(encoding="utf-8-sig"))
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == class_name)
    node = next(n for n in cls.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == method)
    node = copy.deepcopy(node)
    node.decorator_list = []
    wrapper = ast.Module(body=[ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0),
                               ast.ClassDef(name="TestSubject", bases=[], keywords=[], body=[node], decorator_list=[])],
                         type_ignores=[])
    ast.fix_missing_locations(wrapper)
    namespace = dict(globals_ or {})
    exec(compile(wrapper, file, "exec"), namespace)
    return getattr(namespace["TestSubject"], method)


class RedisFake:
    def __init__(self):
        self.rows = [("1-0", {"event": "step_evidence", "step_id": "step-01",
                               "evidence": json.dumps([{"tool": "list_directory", "result": {"text": "old listing"}}])}),
                     ("2-0", {"event": "step_evidence", "step_id": "step-02",
                               "evidence": json.dumps([{"tool": "run_command", "result": {"ok": True, "path": "result.txt"}}])}),
                     ("3-0", {"event": "step_completed", "step_id": "step-01", "summary": "old"})]
    def xrange(self, key, **kwargs):
        assert kwargs == {"min": "-", "max": "+"}
        return list(self.rows)


class Live:
    _stream_key = lambda self, task: "norm:" + task
    client = RedisFake()


Live.step_evidence = extract_method("core/norm_runtime/live_log.py", "RedisTaskLog", "step_evidence", globals_={"json": json})
Live.task_evidence = extract_method("core/norm_runtime/live_log.py", "RedisTaskLog", "task_evidence", globals_={"json": json})
assert len(Live().task_evidence("task")) == 2
assert len(Live().step_evidence("task", "step-02")) == 1
assert Live().step_evidence("task", "step-02")[0]["result"]["path"] == "result.txt"
assert len(Live().client.rows) == 3, "Full Redis evidence must remain stored"
print("PASS: active-step evidence excludes completed-step listings; full Redis stream retained")


class Worker:
    pass


Worker._background_memory_budget = extract_method(
    "core/norm_runtime/prompt_worker.py", "PromptWorker", "_background_memory_budget",
    globals_={"normalize_request_type": lambda x: x},
)
w = Worker()
w._runtime_config = lambda: {"memory": {"background_context_chars": 32000},
                             "worker": {"step_boundary_background_chars": 8000}}
def job(kind, idx, final=False):
    return SimpleNamespace(request_type=kind, metadata={"generated_step_index": idx, "is_final_candidate": final},
                           task_id="task", step_id="step-02")
assert w._background_memory_budget(job("step", 1)) == 32000
assert w._background_memory_budget(job("step", 2)) == 8000
assert w._background_memory_budget(job("task_step", 8)) == 8000
assert w._background_memory_budget(job("conclusion", 8)) == 32000
assert w._background_memory_budget(job("step", 0)) == 32000
w._runtime_config = lambda: {"memory": {"background_context_chars": 6000},
                             "worker": {"step_boundary_background_chars": 8000}}
assert w._background_memory_budget(job("step", 3)) == 6000
print("PASS: later generated steps reload bounded general memory; first/conclusion unchanged")


Worker._generate_with_budget = extract_method(
    "core/norm_runtime/prompt_worker.py", "PromptWorker", "_generate_with_budget",
    globals_={"normalize_request_type": lambda x: x,
              "ModelContextExceeded": type("ModelContextExceeded", (Exception,), {})},
)
w = Worker()
w.live = Live()
w.client = SimpleNamespace(chat_with_tools=lambda *args, **kwargs: None)
w._active_voice_instructions = lambda: ""
w._prompt_with_context_injections = lambda j, p, **kw: p
w._worker_tools = lambda: SimpleNamespace(set_task_context=lambda *args: None)
w._run_tool_slice = lambda *args: {"yielded": False, "answer": "done", "evidence": []}
w._prepare_final_report = lambda j, answer, **kw: answer
received = []
w._verify_and_correct = lambda *args: received.append(args[3]) or "done"
assert w._generate_with_budget("redis-entry", job("step", 2), "current prompt", None, "step", 0) == "done"
assert len(received[-1]) == 1 and received[-1][0]["result"].get("path") == "result.txt"
assert w._generate_with_budget("redis-entry", job("conclusion", 8), "conclude", None, "conclusion", 0) == "done"
assert len(received[-1]) == 2, "Conclusion must retain task-wide evidence"
assert w._generate_with_budget("redis-entry", job("step", 2, final=True), "final step", None, "final", 0) == "done"
assert len(received[-1]) == 2, "Final candidate must retain task-wide evidence"
print("PASS: verifier gets current-step evidence only; conclusion/final gets whole-task evidence")
print("PASS all r22 isolated step-boundary tests")
