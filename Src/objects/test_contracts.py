"""Tests for proposed typed boundaries. No Redis, Postgres or Ollama required."""
from __future__ import annotations

import json
import unittest

from objects import (
    ContractError, Decision, RecoveryAnalysis, RecoveryHandoff, RecoveryUnit,
    ResultKind, ReviewDecision, ReviewKind, StepAssignment, StepSubmission,
    TaskKind, UserPromptReference, EvidenceReference, ArtifactReference, FinalCandidate,
    validate_assignment_submission,
)


class ObjectContractsTests(unittest.TestCase):
    def setUp(self):
        self.original = UserPromptReference(
            original_prompt="Generate a PDF from the selected conversation.",
            source_task_id="chat-1", verbatim_constraints=("Use that conversation only",),
        )

    def test_original_request_is_reference_not_reexecuted(self):
        self.assertEqual(self.original.execution_role, "reference_only")
        with self.assertRaises(ContractError):
            UserPromptReference("Do original task", "chat-1", execution_role="execute")

    def test_recovery_analysis_child_expects_structured_result(self):
        a = StepAssignment("child-1", "work", TaskKind.OVERSIZE_ANALYSIS,
                           ResultKind.RECOVERY_ANALYSIS, "Inspect archived state",
                           "Report surviving work, do not produce PDF", self.original,
                           parent_task_id="chat-1")
        self.assertEqual(a.expected_result, ResultKind.RECOVERY_ANALYSIS)
        with self.assertRaises(ContractError):
            StepAssignment("child-1", "work", TaskKind.OVERSIZE_ANALYSIS,
                           ResultKind.FINAL_CANDIDATE, "Inspect state", "Return JSON",
                           self.original, parent_task_id="chat-1")

    def test_legacy_recovery_analysis_roundtrip_and_no_work(self):
        a = RecoveryAnalysis(
            useful_points=("PDF converter exists",), completed_work=("script checked",),
            remaining_units=(RecoveryUnit("Retrieve PDF input", "Acquire transcript", "Source validated", (1,)),),
            merge_requirements=("Preserve original output requirements",),
        )
        wire = a.to_parent_dict()
        self.assertEqual(RecoveryAnalysis.from_parent_dict(wire).to_parent_dict(), wire)
        self.assertEqual(RecoveryAnalysis().to_parent_dict()["remaining_units"], [])

    def test_missing_recovery_json_is_rejected_before_n1(self):
        with self.assertRaises(ContractError):
            StepSubmission("child-1", "work", ResultKind.RECOVERY_ANALYSIS,
                           "The PDF pipeline is working.", self_verified=True)
        self.assertEqual(
            StepSubmission("child-1", "work", ResultKind.RECOVERY_ANALYSIS,
                           json.dumps(RecoveryAnalysis().to_parent_dict()), True).result_kind,
            ResultKind.RECOVERY_ANALYSIS,
        )

    def test_assignment_result_mismatch_rejected(self):
        a = StepAssignment("child-1", "work", TaskKind.OVERSIZE_ANALYSIS,
                           ResultKind.RECOVERY_ANALYSIS, "Inventory archived state",
                           "Return structured JSON", self.original, parent_task_id="chat-1")
        normal = StepSubmission("child-1", "work", ResultKind.STEP_RESULT, "I am done", True)
        with self.assertRaises(ContractError):
            validate_assignment_submission(a, normal)
        correct = StepSubmission("child-1", "work", ResultKind.RECOVERY_ANALYSIS,
                                 json.dumps(RecoveryAnalysis().to_parent_dict()), True)
        validate_assignment_submission(a, correct)

    def test_n1_cannot_review_analysis_as_final(self):
        with self.assertRaises(ContractError):
            ReviewDecision("child-1", "work", ResultKind.RECOVERY_ANALYSIS,
                           ReviewKind.FINAL, Decision.ACCEPT, "Looks good")
        ok = ReviewDecision("child-1", "work", ResultKind.RECOVERY_ANALYSIS,
                            ReviewKind.RECOVERY, Decision.ACCEPT, "Assessment supported")
        self.assertEqual(ok.decision, Decision.ACCEPT)

    def test_rejection_is_actionable(self):
        with self.assertRaises(ContractError):
            ReviewDecision("task", "step", ResultKind.STEP_RESULT,
                           ReviewKind.STEP, Decision.RETRY, "Evidence not clear", "Try again")
        rejected = ReviewDecision("task", "step", ResultKind.STEP_RESULT,
                                  ReviewKind.STEP, Decision.RETRY, "Missing evidence",
                                  "Fetch stored evidence record #31", ("record-31",))
        self.assertEqual(rejected.evidence_needed, ("record-31",))

    def test_recovery_handoff_explicit_kind_and_distilled_summary(self):
        h = RecoveryHandoff("chat-1", "recovery-2", "model crashed", self.original,
                            "Two files verified, one missing", ("postgres:task_evidence/1",),
                            ("redis:sos/1",))
        self.assertEqual(h.marker, "PASSED_TO:recovery-2")
        self.assertEqual(h.task_kind, TaskKind.RECOVERY)
        with self.assertRaises(ContractError):
            RecoveryHandoff("chat-1", "chat-1", "crashed", self.original, "Summary")

    def test_evidence_pointer_is_not_proof(self):
        ref = EvidenceReference("chat-1", "step-1", "postgres:task_evidence/10", "File exists")
        self.assertEqual(ref.to_dict()["claim"], "File exists")
        with self.assertRaises(ContractError):
            ArtifactReference("output.pdf", verified=True)

    def test_n2_self_check_before_final_submission(self):
        with self.assertRaises(ContractError):
            FinalCandidate("chat-1", "final", "Completed", self_verified=False)
        f = FinalCandidate("chat-1", "final", "Completed", self_verified=True)
        self.assertEqual(f.kind, ResultKind.FINAL_CANDIDATE)


if __name__ == "__main__":
    unittest.main()
