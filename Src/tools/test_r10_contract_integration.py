"""r10 isolated contract/console/injection regression tests; no remote services."""
import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
sys.path.insert(0,str(ROOT/"core"))
from norm_runtime.contract_boundary import validate_recovery_analysis_child,validate_recovery_handoff
from norm_runtime.prompt_worker import PromptWorker
from norm_runtime.context_injections import ContextInjections

GOOD={"useful_points":["old steps remain"],"completed_work":["step 1 verified"],
      "remaining_units":[{"name":"check","instruction":"inspect missing artifact",
                          "verify":"file exists","scope_items":[]}],
      "merge_requirements":["preserve previous work"]}

class R10Contracts(unittest.TestCase):
    def validate(self,data):
        return validate_recovery_analysis_child(ROOT,task_id="child-1",step_id="work",
            instruction="Assess surviving work",criterion="Return recovery JSON",
            original_prompt="Create requested PDF",parent_task_id="root-1",analysis=data)

    def test_analysis_kind_and_identity(self):
        self.assertEqual(json.loads(self.validate(GOOD)),GOOD)

    def test_missing_analysis_keys_rejected(self):
        with self.assertRaises(ValueError):
            self.validate({"remaining_units":[]})

    def test_handoff_marker_is_explicit(self):
        marker=validate_recovery_handoff(ROOT,parent_task_id="old-1",
            parent_step_id="step-07",successor_task_id="recovery-1",
            original_prompt="Create requested PDF",summary="Checkpoint exists")
        self.assertEqual(marker,"PASSED_TO:recovery-1")

    def test_final_n1_judges_child_assignment_not_root(self):
        worker=PromptWorker.__new__(PromptWorker)
        worker._runtime_root=lambda: ROOT
        worker.durable=None
        gate=Mock()
        gate.judge_final_answer.return_value={
            "decision":"accept","reason":"valid internal analysis",
            "retry_instruction":"","processing_failed":False}
        worker.n1_gatekeeper=gate
        job=SimpleNamespace(task_id="child-1",step_id="final-verify",
            request_type="final_verification",prompt=json.dumps(GOOD),
            metadata={"expected_result_kind":"recovery_analysis",
              "task_user_prompt":"Assess surviving work",
              "original_user_prompt":"Create requested PDF",
              "n2_conclusion_step_id":"work","verification":"Return recovery JSON",
              "parent_task_id":"root-1","recovery_scope_items":[]})
        candidate={"user_reply":json.dumps(GOOD),"command":{},"requirements":{},
                   "artifacts":[],"evidence_count":0,"resource_status":{}}
        decision,failed=worker._n1_final_verification(job,candidate,True)
        self.assertFalse(failed)
        self.assertEqual(decision["verdict"],"accept")
        self.assertEqual(gate.judge_final_answer.call_args.kwargs["request"],
                         "Assess surviving work")
        self.assertIn("INTERNAL_RESULT_KIND: recovery_analysis",
                      gate.judge_final_answer.call_args.kwargs["context"])

    def test_invalid_child_fails_before_n1(self):
        worker=PromptWorker.__new__(PromptWorker)
        worker._runtime_root=lambda:ROOT
        worker.durable=None
        worker.n1_gatekeeper=Mock()
        job=SimpleNamespace(task_id="child-1",step_id="final-verify",
             request_type="final_verification",prompt="invalid JSON",
             metadata={"expected_result_kind":"recovery_analysis",
             "task_user_prompt":"Assess surviving work","parent_task_id":"root-1",
             "original_user_prompt":"Create requested PDF",
             "n2_conclusion_step_id":"work","verification":"Return JSON"})
        verdict,failed=worker._n1_final_verification(job,{"user_reply":"not JSON"},True)
        self.assertFalse(failed)
        self.assertEqual(verdict["decision"],"retry")
        worker.n1_gatekeeper.judge_final_answer.assert_not_called()

    def test_verified_text_only_injection_retires_without_tool_result(self):
        worker=PromptWorker.__new__(PromptWorker)
        db=Mock()
        db.read.return_value=[{"id":14,"content":"Use concise English",
                               "retire_when_satisfied":True}]
        db.retire.return_value={"retired":True}
        worker.context_injections=db
        job=SimpleNamespace(task_id="root-1",step_id="step-02",metadata={})
        worker._retire_verified_injections(
            job,json.dumps({"verdict":"accept","fulfilled_injection_ids":[14]}),[])
        db.retire.assert_called_once_with(
            "root-1",14,step_id="step-02",
            reason="Accepted step verifier explicitly confirmed requirement fulfilled",
            verified=True)

    def test_injection_default_retirement_and_optout_flags(self):
        self.assertTrue(ContextInjections.save.__defaults__[0])
        script=(ROOT/"tools/norm_gui_prompt.py").read_text(encoding="utf8")
        self.assertIn('"--no_expire", "-no_expire"',script)
        self.assertIn('"--forever", "-forever"',script)
        self.assertIn('"--persist", "-persist"',script)
        self.assertNotIn("Retire this instruction when a completed step",script)

    def test_console_failed_commands_wired(self):
        common=(ROOT/"tools/norm_gui_common.py").read_text(encoding="utf8")
        gui=(ROOT/"tools/norm_gui_prompt.py").read_text(encoding="utf8")
        for key in ("failed_tasks","resume_failed","flush_failed"):
            self.assertIn(f'ep["{key}"]',gui)
            self.assertIn(f'"{key}":',common)

if __name__=="__main__":
    unittest.main(verbosity=2)