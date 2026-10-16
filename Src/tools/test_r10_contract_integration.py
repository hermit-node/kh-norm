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

    @staticmethod
    def _job(metadata=None):
        from norm_runtime.prompt_queue import PromptJob
        return PromptJob(
            message_id="job-01",task_id="child-01",step_id="work",
            prompt="GENERATED INSTRUCTION ONLY",project_id="default",
            context="",attempt=0,created_at=1.0,
            metadata=metadata or {},chain_id="chain-01",
            previous_prompt_id="",next_prompt_id="",chain_index=0,
            recovery_attempted=False,request_type="step")

    def test_child_restores_root_prompt_not_generated_instruction(self):
        worker=PromptWorker.__new__(PromptWorker)
        durable=Mock()
        durable.root_task_id.return_value="root-01"
        durable.task_plan_snapshot.return_value={
            "original_request":"Create a PDF from the supplied research."}
        worker.durable=durable
        job=self._job({"original_user_prompt":"GENERATED INSTRUCTION ONLY",
                       "parent_task_id":"root-01","task_user_prompt":"Inspect the PDF"})
        self.assertEqual(worker._ensure_original_user_prompt(job),
                         "Create a PDF from the supplied research.")
        self.assertEqual(job.original_user_prompt,
                         "Create a PDF from the supplied research.")
        self.assertEqual(job.metadata["task_user_prompt"],"Inspect the PDF")
        self.assertEqual(job.metadata["original_prompt_source"],"durable_root")

    def test_missing_original_fails_without_substituting_generated_step(self):
        worker=PromptWorker.__new__(PromptWorker)
        worker.durable=None
        job=self._job({"parent_task_id":"root-01"})
        with self.assertRaisesRegex(RuntimeError,"no authoritative original user prompt"):
            worker._ensure_original_user_prompt(job)
        self.assertFalse(job.original_user_prompt)

    def test_redis_roundtrip_carries_explicit_prompt(self):
        from norm_runtime.prompt_queue import RedisPromptQueue
        job=self._job({"original_user_prompt":"Actual original user request"})
        queue=RedisPromptQueue.__new__(RedisPromptQueue)
        fields=queue._job_to_fields(job)
        self.assertEqual(fields["original_user_prompt"],"Actual original user request")
        restored=queue._fields_to_job("redis-id",fields)
        self.assertEqual(restored.original_user_prompt,"Actual original user request")
        self.assertEqual(restored.metadata["original_user_prompt"],
                         "Actual original user request")

    def test_legacy_redis_metadata_restores_explicit_field(self):
        from norm_runtime.prompt_queue import RedisPromptQueue
        import dataclasses
        queue=RedisPromptQueue.__new__(RedisPromptQueue)
        job=self._job({"original_user_prompt":"Original user turn"})
        fields=queue._job_to_fields(job)
        fields.pop("original_user_prompt",None)
        restored=queue._fields_to_job("redis-id",fields)
        self.assertEqual(restored.original_user_prompt,"Original user turn")

    def test_conflicting_prompt_rejected_before_redis_enqueue(self):
        from norm_runtime.prompt_queue import RedisPromptQueue
        job=self._job({"original_user_prompt":"One user request"})
        job.metadata["original_user_prompt"]="A conflicting request"
        queue=RedisPromptQueue.__new__(RedisPromptQueue)
        with self.assertRaisesRegex(ValueError,"Conflicting original user prompts"):
            queue._job_to_fields(job)

    def test_child_task_plan_does_not_mislabel_scoped_assignment(self):
        from norm_runtime.models import TaskPlan,TaskStep
        plan=TaskPlan(task_id="child",title="Analysis child",
            steps=(TaskStep("work","Inspect","Inspect existing files"),),
            original_request="Inspect existing files",
            original_user_prompt="Build the full user PDF.")
        data=plan.as_dict()
        self.assertEqual(data["original_request"],"Inspect existing files")
        self.assertEqual(data["original_user_prompt"],"Build the full user PDF.")
        old=TaskPlan(task_id="legacy",title="Old",steps=(),
                     original_request="Legacy scoped assignment").as_dict()
        self.assertEqual(old["original_user_prompt"],"")

    def test_step_seven_does_not_reissue_original_prompt(self):
        job=self._job({
            "original_user_prompt":"Make an entire document and all seven components.",
            "task_user_prompt":"Review accumulated work and finish the final section.",
            "prompt_origin":"norm_generated_step",
            "generated_step_index":7,"generated_step_count":7,
            "verification":"Final section references verified earlier results.",
        })
        job.step_id="step-07"
        job.prompt="Finalize section seven from existing verified files."
        msgs=PromptWorker._tool_slice_initial_messages(job)
        self.assertEqual(len(msgs),2)
        user=msgs[1]["content"]
        self.assertIn("step 7/7",user)
        self.assertIn("Finalize section seven",user)
        self.assertIn("[ORIGINAL USER PROMPT: REFERENCE ONLY",user)
        self.assertIn("Make an entire document and all seven components.",user)
        self.assertNotEqual(user,job.metadata["original_user_prompt"])
        self.assertIn("Do not restart",user)
        self.assertIn("Preserve established progress",user)

    def test_direct_user_prompt_still_executes_as_direct_turn(self):
        job=self._job({"original_user_prompt":"What time is it?",
                       "prompt_origin":"user_prompt"})
        job.request_type="prompt"
        msgs=PromptWorker._tool_slice_initial_messages(job)
        self.assertEqual(msgs[1]["content"],"What time is it?")

    def test_final_json_never_strips_original_user_prompt(self):
        from norm_runtime.protocol import validate_final_candidate,PROTOCOL_VERSION
        payload={"schema_version":PROTOCOL_VERSION,"kind":"final_candidate",
                 "task_id":"child-1","step_id":"work","status":"completed",
                 "original_user_prompt":"Create a research report.",
                 "task_user_prompt":"Finalize the current section.",
                 "command":{"category":"task","intent":"Finalize the current section.",
                            "requires_file_mutation":False,
                            "requires_external_research":False,
                            "input_has_media":False,"output_requires_media":False,
                            "expected_output":{"type":"answer","format":"text"}},
                 "user_reply":"The verified section is complete.","artifacts":[],
                 "requirements":{},"evidence_count":0}
        cleaned=validate_final_candidate(payload,require_original_prompt=True)
        self.assertEqual(cleaned["original_user_prompt"],payload["original_user_prompt"])
        self.assertEqual(cleaned["task_user_prompt"],payload["task_user_prompt"])
        payload.pop("original_user_prompt")
        with self.assertRaisesRegex(ValueError,"original_user_prompt"):
            validate_final_candidate(payload,require_original_prompt=True)

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