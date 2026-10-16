"""Offline regressions for 0.55.8 r5. No Windows, Redis server, Ollama or Postgres required."""
from __future__ import annotations

import ast
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "core"))
for module_name in ("redis", "psycopg"):
    if module_name not in sys.modules:
        try:
            __import__(module_name)
        except ImportError:
            sys.modules[module_name] = types.ModuleType(module_name)
from norm_runtime.output_overflow import OverflowStore, write_full_report, report_link
from norm_runtime.ollama_client import ModelOutputTruncated
from norm_runtime.prompt_worker import PromptWorker
from norm_runtime.prompt_queue import PromptJob

class FakeRedis:
    def __init__(self): self.values = {}
    def setex(self, key, ttl, value): self.values[key] = (ttl, value); return True
    def get(self, key): return self.values.get(key, (0, None))[1]

class FakeClient:
    def __init__(self, outputs): self.outputs = iter(outputs); self.calls = []
    def generate(self, prompt, **kwargs):
        self.calls.append((prompt, kwargs))
        outcome = next(self.outputs)
        if isinstance(outcome, BaseException): raise outcome
        return outcome

class FakeQueue:
    def __init__(self): self.r = FakeRedis()


def make_job():
    return PromptJob(message_id="m",task_id="task-r5",step_id="final",prompt="Work",
        project_id="default",context="",attempt=0,created_at=0,metadata={},
        chain_id="chain",previous_prompt_id="",next_prompt_id="",chain_index=0,
        recovery_attempted=False,request_type="conclusion")

class OverflowTests(unittest.TestCase):
    def test_receipt_preserves_complete_generated_part_and_locator(self):
        redis = FakeRedis()
        store = OverflowStore(redis)
        content = "αβgreeting\n" * 600
        receipt = store.save("a", "b", "n2", content)
        self.assertEqual(store.load(receipt.key), content)
        self.assertIn("FULL GENERATED TEXT IN REDIS", receipt.handoff)
        self.assertIn(receipt.key, receipt.handoff)
        self.assertEqual(redis.values[receipt.key][0], 86400)
        self.assertIn("generation may be incomplete", receipt.handoff)

    def test_full_md_is_unique_unchanged_and_locally_linked(self):
        with tempfile.TemporaryDirectory() as tmp:
            content = "# Report\n" + "really long body\n" * 200
            a = write_full_report(Path(tmp), "task/a", "final", content)
            b = write_full_report(Path(tmp), "task/a", "final", content)
            self.assertNotEqual(a, b)
            self.assertEqual(a.read_text(encoding="utf-8"), content)
            self.assertTrue(a.is_file())
            self.assertIn("file:", report_link(a))

    def test_overlong_final_report_condenses_working_copy_three_times_then_shortest(self):
        with tempfile.TemporaryDirectory() as tmp:
            w = PromptWorker.__new__(PromptWorker)
            w.queue = FakeQueue()
            w.client = FakeClient(["z"*19000, "y"*18000, "x"*17500])
            w._runtime_config = lambda: {"output_overflow": {"final_reply_max_chars": 16000}}
            with patch("norm_runtime.prompt_worker.load_path_settings", return_value={"state_root": Path(tmp)}):
                response = w._prepare_final_report(make_job(), "# Full original\n" + "E"*20000)
            self.assertIn("x" * 17500, response)
            self.assertNotIn("y" * 18000, response)
            self.assertIn("Full original report:", response)
            reports = list(Path(tmp).glob("reports/final-overflow/*.md"))
            self.assertEqual(len(reports), 1)
            self.assertEqual(reports[0].read_text(encoding="utf-8"), "# Full original\n"+"E"*20000)
            self.assertEqual(len(w.client.calls), 3)
            self.assertIn("# Full original", w.client.calls[0][0])
            self.assertTrue(any("final-condense-3" in key for key in w.queue.r.values))

    def test_short_enough_summary_keeps_original(self):
        with tempfile.TemporaryDirectory() as tmp:
            w=PromptWorker.__new__(PromptWorker)
            w.queue=FakeQueue(); w.client=FakeClient(["Concise and accurate."])
            w._runtime_config = lambda: {"output_overflow":{"final_reply_max_chars":5000}}
            with patch("norm_runtime.prompt_worker.load_path_settings",return_value={"state_root":Path(tmp)}):
                response=w._prepare_final_report(make_job(), "A"*7000)
            self.assertIn("Concise and accurate.",response)
            self.assertEqual(len(w.client.calls),1)

    def test_truncated_condensation_is_not_presented_as_complete(self):
        with tempfile.TemporaryDirectory() as tmp:
            w=PromptWorker.__new__(PromptWorker)
            w.queue=FakeQueue(); w.client=FakeClient([ModelOutputTruncated("unfinished"*100,6000)]*3)
            w._runtime_config=lambda: {"output_overflow":{"final_reply_max_chars":5000}}
            with patch("norm_runtime.prompt_worker.load_path_settings",return_value={"state_root":Path(tmp)}):
                response=w._prepare_final_report(make_job(), "original verified content"*400)
            self.assertNotIn("unfinished", response)
            self.assertIn("report was preserved", response)
            self.assertEqual(len(list(Path(tmp).glob("reports/final-overflow/*.md"))),1)
            self.assertEqual(len(w.client.calls),3)

    def test_complete_text_accumulates_partial_and_redis_preserves_it(self):
        w=PromptWorker.__new__(PromptWorker)
        w.queue=FakeQueue()
        w._runtime_config=lambda: {}
        w.client=FakeClient([ModelOutputTruncated("first",38000),ModelOutputTruncated("second",38000)])
        with self.assertRaises(ModelOutputTruncated) as caught:
            w._generate_complete_text("prompt",think=True,num_predict=38000,
                                      temperature=.2,max_segments=2,job=make_job())
        self.assertEqual(caught.exception.partial,"firstsecond")
        self.assertTrue(any(b"firstsecond"==value for _,value in w.queue.r.values.values()))

    def test_requested_budgets_and_untouched_no_tools_branch(self):
        tree = ast.parse((ROOT/"core/norm_runtime/prompt_worker.py").read_text())
        cls = next(x for x in tree.body if isinstance(x,ast.ClassDef) and x.name=='PromptWorker')
        methods = {x.name:x for x in cls.body if isinstance(x,ast.FunctionDef)}
        source = (ROOT/"core/norm_runtime/prompt_worker.py").read_text()
        self.assertIn('74000 if long_form else 38000', source)
        self.assertIn('num_predict=int(metadata.get("num_predict") or 16000)', source)
        self.assertEqual(source.count('num_predict=87400,'),2)
        self.assertEqual(len(methods['_prepare_final_report'].body)>0,True)

if __name__=='__main__': unittest.main()
