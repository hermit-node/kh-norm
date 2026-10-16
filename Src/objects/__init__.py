"""Norm inter-component handoff contracts (DRAFT, not yet wired to runtime).

WHEN: Developers add/modify N1/N2, Redis, child-task or final-review boundaries.
WHY: Reuse named, validated objects rather than unrelated dicts / narrative JSON.
This package is inert on import; it does NOT start agents, change model calls,
mutate Redis/PostgreSQL or install tools.
"""
from ._base import CONTRACT_DRAFT_VERSION, ContractError, Decision, ResultKind, ReviewKind, TaskKind
from .task import StepAssignment, UserPromptReference
from .evidence import ArtifactReference, EvidenceReference
from .recovery import RecoveryAnalysis, RecoveryHandoff, RecoveryUnit
from .review import FinalCandidate, ReviewDecision, StepSubmission, validate_assignment_submission

__all__ = [
    "CONTRACT_DRAFT_VERSION", "ContractError", "Decision", "ResultKind", "ReviewKind", "TaskKind",
    "StepAssignment", "UserPromptReference", "ArtifactReference", "EvidenceReference",
    "RecoveryAnalysis", "RecoveryHandoff", "RecoveryUnit", "FinalCandidate", "ReviewDecision", "StepSubmission", "validate_assignment_submission",
]
