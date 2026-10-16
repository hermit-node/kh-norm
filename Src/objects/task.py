"""Canonical task-intent reference and N2 step-assignment contracts (DRAFT)."""
from __future__ import annotations

from dataclasses import dataclass, field

from ._base import ContractError, ResultKind, TaskKind, as_payload, require_text


@dataclass(frozen=True, slots=True)
class UserPromptReference:
    """WHEN: N1 plans work or a task/recovery child receives original intent.

    WHY: Preserve the operator's original words/constraints *verbatim* through
    all steps without executing that request again. The current assignment
    establishes the execution purpose, not an exploration ceiling: N2 may
    investigate related discoveries using existing N1/tool authorization.
    A recovery child must not reinterpret the original request as a fresh user message.
    """
    original_prompt: str
    source_task_id: str
    verbatim_constraints: tuple[str, ...] = ()
    execution_role: str = "reference_only"

    def __post_init__(self) -> None:
        require_text(self.original_prompt, "original_prompt")
        require_text(self.source_task_id, "source_task_id")
        if self.execution_role != "reference_only":
            raise ContractError("post-planning user prompt must remain reference_only")
        if not all(isinstance(x, str) and x.strip() for x in self.verbatim_constraints):
            raise ContractError("verbatim_constraints must contain only nonempty strings")

    def to_dict(self) -> dict:
        return as_payload(self)


@dataclass(frozen=True, slots=True)
class StepAssignment:
    """WHEN: N1 dispatches a specific N2 step, including a recovery child.

    WHY: Give N2 an explicit execution purpose and expected result format;
    this is NOT a limit on exploration or initiative. N2 may investigate
    unforeseen dependencies, propose improvements, and repair faults under
    the existing tool/N1 authorization process. N1 judges the returned result
    against *this assignment*, not against unrelated final-deliverable criteria.
    """
    task_id: str
    step_id: str
    task_kind: TaskKind
    expected_result: ResultKind
    instruction: str
    acceptance_criteria: str
    original: UserPromptReference
    parent_task_id: str = ""
    parent_step_id: str = ""
    evidence_locations: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for attr in ("task_id", "step_id", "instruction", "acceptance_criteria"):
            require_text(getattr(self, attr), attr)
        if not isinstance(self.original, UserPromptReference):
            raise ContractError("original must be a UserPromptReference")
        if self.task_kind == TaskKind.OVERSIZE_ANALYSIS:
            if self.expected_result != ResultKind.RECOVERY_ANALYSIS:
                raise ContractError("oversize analysis child must return recovery_analysis")
            require_text(self.parent_task_id, "parent_task_id")
        elif self.expected_result == ResultKind.RECOVERY_ANALYSIS:
            raise ContractError("recovery_analysis is reserved for an analysis assignment")
        if self.task_kind in (TaskKind.RECOVERY, TaskKind.RECOVERY_UNIT):
            require_text(self.parent_task_id, "parent_task_id")

    def to_dict(self) -> dict:
        return as_payload(self)
