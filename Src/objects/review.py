"""Typed N2 submissions and N1 decisions; rejection must be actionable."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ._base import ContractError, Decision, ResultKind, ReviewKind, as_payload, require_text
from .evidence import ArtifactReference, EvidenceReference


@dataclass(frozen=True, slots=True)
class StepSubmission:
    """WHEN: N2 self-checks a step and submits it for N1 review.

    WHY: The returned kind must match the StepAssignment.expected_result.
    In particular, an analysis child's submission cannot silently become
    a final user reply; narrative claims do not count as tool evidence.
    """
    task_id: str
    step_id: str
    result_kind: ResultKind
    content: str
    self_verified: bool
    evidence_refs: tuple[EvidenceReference, ...] = ()

    def __post_init__(self) -> None:
        for attr in ("task_id", "step_id", "content"):
            require_text(getattr(self, attr), attr)
        if not isinstance(self.result_kind, ResultKind):
            raise ContractError("result_kind must be ResultKind")
        if not self.self_verified:
            raise ContractError("N2 must self-verify before submitting to N1")
        if self.result_kind == ResultKind.FINAL_CANDIDATE:
            raise ContractError("use FinalCandidate for final user-facing answers")
        if self.result_kind == ResultKind.RECOVERY_ANALYSIS:
            # Deterministic format check BEFORE any N1 model review.
            import json
            from .recovery import RecoveryAnalysis
            try:
                RecoveryAnalysis.from_parent_dict(json.loads(self.content))
            except (ValueError, TypeError, ContractError) as exc:
                raise ContractError(f"recovery-analysis child must return parseable structured JSON: {exc}") from exc

    def to_dict(self) -> dict:
        return as_payload(self)


@dataclass(frozen=True, slots=True)
class ReviewDecision:
    """WHEN: N1 accepts/rejects an N2 submission for its assigned result kind.

    WHY: Eliminate the 'ask for recovery JSON, judge against final PDF' mismatch.
    A RETRY must identify a concrete issue and action. A result-kind mismatch
    is rejected *before* semantic evaluation; unsupported evidence may be
    fetched from durable storage rather than rerunning a side-effecting tool.
    """
    task_id: str
    step_id: str
    reviewed_result_kind: ResultKind
    review_kind: ReviewKind
    decision: Decision
    reason: str
    retry_instruction: str = ""
    evidence_needed: tuple[str, ...] = ()
    issues: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for attr in ("task_id", "step_id", "reason"):
            require_text(getattr(self, attr), attr)
        if self.reviewed_result_kind == ResultKind.RECOVERY_ANALYSIS and self.review_kind != ReviewKind.RECOVERY:
            raise ContractError("recovery-analysis output requires recovery-specific review")
        if (self.review_kind == ReviewKind.FINAL) != (self.reviewed_result_kind == ResultKind.FINAL_CANDIDATE):
            raise ContractError("final review applies if and only if the result is a final_candidate")
        if self.decision == Decision.RETRY:
            require_text(self.retry_instruction, "retry_instruction")
            if not self.issues and not self.evidence_needed:
                raise ContractError("retry must say what failed or what evidence is needed")
        elif self.decision == Decision.ACCEPT:
            if self.retry_instruction or self.issues or self.evidence_needed:
                raise ContractError("accepted review must have no unresolved issues")
        else:
            raise ContractError("decision must be accept or retry")

    def to_dict(self) -> dict:
        return as_payload(self)


@dataclass(frozen=True, slots=True)
class FinalCandidate:
    """WHEN: Ordinary N2 work has finished and N2 submits the user-facing answer.

    WHY: Separate 'N2 believes it is complete' from N1 final acceptance.
    N1 must still review scope, constraints and artifact evidence; this object
    never itself marks the durable task completed.
    """
    task_id: str
    step_id: str
    user_reply: str
    self_verified: bool
    artifacts: tuple[ArtifactReference, ...] = ()
    evidence_refs: tuple[EvidenceReference, ...] = ()
    kind: ResultKind = ResultKind.FINAL_CANDIDATE

    def __post_init__(self) -> None:
        for attr in ("task_id", "step_id", "user_reply"):
            require_text(getattr(self, attr), attr)
        if not self.self_verified:
            raise ContractError("final answer must pass N2 self-verification first")
        if self.kind != ResultKind.FINAL_CANDIDATE:
            raise ContractError("only a final_candidate may be sent to final review")

    def to_dict(self) -> dict:
        return as_payload(self)


def validate_assignment_submission(assignment, submission: StepSubmission) -> None:
    """WHEN: Immediately before N1 step review; WHY: enforce the child's contract.

    Reject a correct-looking answer of the WRONG result kind before model review.
    This is the missing boundary in the r9 recovery-analysis deadlock.
    """
    from .task import StepAssignment
    if not isinstance(assignment, StepAssignment) or not isinstance(submission, StepSubmission):
        raise ContractError("assignment and submission must be typed contract objects")
    if (assignment.task_id, assignment.step_id) != (submission.task_id, submission.step_id):
        raise ContractError("submission task/step identity does not match assignment")
    if assignment.expected_result != submission.result_kind:
        raise ContractError(
            f"expected {assignment.expected_result}, received {submission.result_kind}"
        )
