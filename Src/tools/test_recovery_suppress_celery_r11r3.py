"""Dependency-free source-isolated regressions for r11r3 queue/Celery repairs.

Python AST isolation allows testing the actual method bodies without importing
production Redis/Postgres/Celery clients. No network services or user state used.
"""
import ast
import dataclasses
import json
import sys
import threading
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def method(rel, class_name, name, globals_dict):
    tree = ast.parse((ROOT / rel).read_text(encoding="utf8"))
    node = next(x for x in tree.body if isinstance(x, ast.ClassDef) and x.name == class_name)
    f = next(x for x in node.body if isinstance(x, (ast.FunctionDef, ast.AsyncFunctionDef)) and x.name == name)
    # Prevent decorator execution and unresolved annotations in isolated unit scope.
    f.decorator_list = []
    mod = ast.fix_missing_locations(ast.Module(body=[f], type_ignores=[]))
    ns = dict(globals_dict)
    exec(compile(mod, str(ROOT / rel), "exec"), ns)
    return ns[name]


@dataclasses.dataclass
class FakeJob:
    task_id: str
    step_id: str
    message_id: str
    metadata: dict = dataclasses.field(default_factory=dict)


class FakePipeline:
    def __init__(self, store):
        self.store = store
        self.commands = []
    def xadd(self, stream, data):
        self.commands.append(("xadd", stream, data))
        return self
    def zadd(self, key, values):
        self.commands.append(("zadd", key, values))
        return self
    def execute(self):
        out = []
        for kind, where, val in self.commands:
            if kind == "xadd":
                self.store.streams.setdefault(where, []).append((str(len(self.store.streams.get(where, [])) + 1), val))
                out.append("redis-stream-id")
            else:
                self.store.sorted.setdefault(where, {}).update(val)
                out.append(len(val))
        return out


class FakeRedis:
    def __init__(self):
        self.streams = {}
        self.sorted = {}
    def xrange(self, stream, min="-", max="+"):
        return list(self.streams.get(stream, []))
    def zrange(self, key, begin, end, withscores=False):
        items = list(self.sorted.get(key, {}).items())
        return items if withscores else [row[0] for row in items]
    def pipeline(self, transaction=True):
        return FakePipeline(self)


class FakeQueue:
    stream = "work"
    retry_stream = "retry"
    escalation_stream = "escalation"
    def __init__(self):
        self.r = FakeRedis()
    def _async_delayed_key(self):
        return "work:celery-delayed"
    def _approval_wait_key(self):
        return "work:awaiting-user-approval"
    def _fields_to_job(self, message_id, fields):
        return FakeJob(**fields)
    def _job_to_fields(self, job):
        return dataclasses.asdict(job)
    def contains_message_id(self, message_id):
        return False


class Tests(unittest.TestCase):
    def test_delayed_celery_continuation_survives_suppress_resume(self):
        rel = "core/norm_runtime/prompt_queue.py"
        fn = method(rel, "RedisPromptQueue", "snapshot_task_jobs", {"asdict": dataclasses.asdict, "json": json, "time": types.SimpleNamespace(time=lambda: 100)})
        restore = method(rel, "RedisPromptQueue", "restore_task_jobs", {"PromptJob": FakeJob, "json": json})
        q = FakeQueue()
        delayed = FakeJob("task-1", "step-4", "queued-4", {"last_yield_reason": "n1_async_pending"})
        q.r.sorted[q._async_delayed_key()] = {json.dumps(dataclasses.asdict(delayed)): 10042.0}
        payload = fn(q, "task-1")
        self.assertEqual(len(payload["jobs"]), 1)
        self.assertEqual(payload["jobs"][0]["stream"], "celery-delayed")
        self.assertEqual(payload["jobs"][0]["due_at"], 10042.0)
        q.r.sorted.clear()  # normal suppression cleanup
        result = restore(q, payload)
        self.assertEqual(len(result), 1)
        self.assertEqual(list(q.r.sorted[q._async_delayed_key()].values()), [10042.0])
        self.assertEqual(q.r.streams, {})

    def test_normal_queue_resume_unchanged(self):
        rel = "core/norm_runtime/prompt_queue.py"
        snapshot = method(rel, "RedisPromptQueue", "snapshot_task_jobs", {"asdict": dataclasses.asdict, "json": json, "time": types.SimpleNamespace(time=lambda: 100)})
        restore = method(rel, "RedisPromptQueue", "restore_task_jobs", {"PromptJob": FakeJob, "json": json})
        q = FakeQueue()
        job = FakeJob("task-2", "step-1", "queued-1")
        q.r.streams["work"] = [("1-0", dataclasses.asdict(job))]
        payload = snapshot(q, "task-2")
        q.r.streams.clear()
        self.assertEqual(len(restore(q, payload)), 1)
        self.assertEqual(q.r.streams["work"][0][1]["task_id"], "task-2")

    def test_resuming_requires_original_worker_to_release_claim(self):
        guard = method("core/norm_runtime/prompt_worker.py", "PromptWorker",
                       "suppression_transition_active", {})
        owner = types.SimpleNamespace(_state_lock=threading.Lock(), _active_task_id="task-3", _suppress_requested=set())
        self.assertTrue(guard(owner, ["task-3"]))
        owner._active_task_id = ""
        self.assertFalse(guard(owner, ["task-3"]))
        owner._suppress_requested = {"task-3"}
        self.assertTrue(guard(owner, ["task-3"]))

    def test_celery_worker_toolkit_cache_per_process(self):
        # Execute actual decorated implementation (isolated from module imports).
        tree = ast.parse((ROOT / "core/norm_runtime/celery_tool_executor.py").read_text())
        fn = next(x for x in tree.body if isinstance(x, ast.FunctionDef) and x.name == "_worker_tool_executor")
        import functools
        counts = []
        fake_tools = object()
        fake_module = types.SimpleNamespace(build_conversation_service=lambda root, ensure_schema: (counts.append(root), types.SimpleNamespace(file_tools=fake_tools))[1])
        old = sys.modules.get("runtime_bootstrap")
        sys.modules["runtime_bootstrap"] = fake_module
        try:
            ns = {"lru_cache": functools.lru_cache, "root": lambda: "fake-runtime"}
            exec(compile(ast.fix_missing_locations(ast.Module(body=[fn], type_ignores=[])), "cached_executor", "exec"), ns)
            self.assertIs(ns["_worker_tool_executor"](), fake_tools)
            self.assertIs(ns["_worker_tool_executor"](), fake_tools)
            self.assertEqual(counts, ["fake-runtime"])
        finally:
            if old is None: sys.modules.pop("runtime_bootstrap", None)
            else: sys.modules["runtime_bootstrap"] = old

    def test_no_speculative_n1_eta_for_celery_pending(self):
        fn = method("core/norm_runtime/async_n1_delivery.py", "N1AsyncBridge", "_estimate_next",
                    {"HARD_DEADLINE_SECONDS": 3600, "time": types.SimpleNamespace(time=lambda: 0), "logging": types.SimpleNamespace(exception=lambda *a, **k: None)})
        class Gate:
            def estimate_async_next_check(self, **kwargs):
                raise AssertionError("N1 should not infer a completion time for unstarted work")
        bridge = types.SimpleNamespace(broker=types.SimpleNamespace(inspect=lambda jobid: {"state": "PENDING", "execution": "submitted"}),
                                       owner=types.SimpleNamespace(n1_gatekeeper=Gate()),
                                       job=types.SimpleNamespace(task_id="t", step_id="s"))
        item = {"id": "abc", "submitted_at": 1000, "name": "inspect_file", "arguments": {}}
        self.assertEqual(fn(bridge, item, 1000 + 900), 2080)


    def test_recovery_scope_request_n1_approves_once_and_deduplicates(self):
        import json as json_module
        fn = method("core/norm_runtime/prompt_worker.py", "PromptWorker", "_request_scope_reconsideration",
                    {"json": json_module, "PromptJob": FakeJob, "logging": types.SimpleNamespace(exception=lambda *a, **k: None)})
        class Durable:
            def root_task_id(self, task_id):
                return "root" if task_id in {"child", "prior"} else "elsewhere"
            def task_plan_snapshot(self, task_id):
                return {"steps": [{"id": "step-03", "name": "Move files", "status": "completed"}]}
            def record_maintenance_note(self, *args, **kwargs):
                self.last_note = kwargs
        class Gate:
            enabled = True
            count = 0
            def judge_scope_reconsideration(self, **kwargs):
                self.count += 1
                return {"decision": "approve", "reason": "new evidence proves missing move and user approved"}
        gate = Gate()
        worker = types.SimpleNamespace(durable=Durable(), n1_gatekeeper=gate,
                   _recovery_scope_exception_allowed=lambda job: True,
                   _ensure_original_user_prompt=lambda job: "Reorganize Switch library")
        job = types.SimpleNamespace(task_id="child", step_id="step-04", prompt="Verify move", metadata={"recovery_mode": True})
        args = {"prior_task_id": "prior", "prior_step_id": "step-03",
                "missing_work": "Move unrelocated files", "proposed_action": "Move manifest files",
                "new_evidence": "Paths remain in source", "authorization_evidence": "Explicit user go"}
        first = fn(worker, job, args)
        self.assertTrue(first["approved"])
        self.assertEqual(fn(worker, job, args)["approval_id"], first["approval_id"])
        self.assertEqual(gate.count, 1)
        denied = fn(worker, job, {**args, "prior_task_id": "unrelated"})
        self.assertFalse(denied["approved"])
        self.assertEqual(gate.count, 1)

    def test_manual_resume_acknowledges_enqueue_without_waiting_for_completion(self):
        fn = method("core/norm_runtime/conversation_service.py", "ConversationService", "_resume_suppressed_and_wait",
                    {"full_context_status": lambda: {}, "logging": types.SimpleNamespace(info=lambda *a, **k: None,
                     exception=lambda *a, **k: None)})
        class Durable:
            def suppression_payload(self, task_id):
                return {"jobs": [{"stream": "work", "job": {"task_id": task_id, "metadata": {}}}]}
            def resume_suppressed_task(self, task_id):
                self.resumed = task_id
        class Queue:
            def restore_task_jobs(self, payload):
                self.payload = payload
                return ["42-0"]
        db, queue = Durable(), Queue()
        service = types.SimpleNamespace(durable=db, prompt_queue=queue,
                                        suppression_transition_active=lambda members: False,
                                        file_tools=None)
        task_id, msg, _ = fn(service, "task-1", thread_ids=["thread-a"],
                             primary_thread_id="thread-a", user_message_id="user-1")
        self.assertEqual(db.resumed, "task-1")
        self.assertIn("restored 1 queued job(s)", msg)
        self.assertEqual(queue.payload["jobs"][0]["job"]["metadata"]["resumed_from_suppressed"], True)
        self.assertEqual(task_id, "task-1")


if __name__ == "__main__":
    unittest.main(verbosity=2)
