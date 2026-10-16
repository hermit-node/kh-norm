"""Separate 'what survived?' findings from 'do the remaining work' assignments."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ._base import ContractError, TaskKind, as_payload, require_keys, require_text
from .evidence import EvidenceReference
from .task import UserPromptReference


@dataclass(frozen=True, slots=True)
class RecoveryUnit:
    """WHEN: Accepted recovery analysis proposes a bounded remaining subtask.

    WHY: Make the scope explicit and reviewable before any N2 execution.
    This is a *proposal*, not an immediate tool instruction or new user task.
    """
    name: str
    instruction: str
    verify: str
    scope_items: tuple[int, ...] = ()

    def __post_init__(self) -> None:
        for attr in ("name", "instruction", "verify"):
            require_text(getattr(self, attr), attr)
        if any(not isinstance(x, int) or isinstance(x, bool) or x < 0 for x in self.scope_items):
            raise ContractError("scope_items must be nonnegative integer identifiers")

    def to_parent_dict(self) -> dict[str, Any]:
        """Existing _parse_recovery_analysis() field names, no runtime rewrite."""
        return {"name": self.name, "instruction": self.instruction,
                "verify": self.verify, "scope_items": list(self.scope_items)}


@dataclass(frozen=True, slots=True)
class RecoveryAnalysis:
    """WHEN: N2 finishes an oversize/recovery analysis child.

    WHY: N1 verifies the accuracy of the assessment, NOT whether the original
    user deliverable has been produced. A parent must parse/validate this
    structured object before considering the child complete. If no missing
    work remains, remaining_units is empty and no successor is necessary.
    """
    useful_points: tuple[str, ...] = ()
    completed_work: tuple[str, ...] = ()
    remaining_units: tuple[RecoveryUnit, ...] = ()
    merge_requirements: tuple[str, ...] = ()
    evidence_refs: tuple[EvidenceReference, ...] = ()
    kind: str = "recovery_analysis"

    def __post_init__(self) -> None:
        if self.kind != "recovery_analysis":
            raise ContractError("expected recovery_analysis kind")
        if not all(isinstance(x, RecoveryUnit) for x in self.remaining_units):
            raise ContractError("remaining_units must contain RecoveryUnit values")
        if not all(isinstance(x, EvidenceReference) for x in self.evidence_refs):
            raise ContractError("evidence_refs must contain EvidenceReference values")

    def to_parent_dict(self) -> dict[str, Any]:
        """Wire-compatible with existing _parse_recovery_analysis() expectations."""
        return {"useful_points": list(self.useful_points),
                "completed_work": list(self.completed_work),
                "remaining_units": [x.to_parent_dict() for x in self.remaining_units],
                "merge_requirements": list(self.merge_requirements)}

    @classmethod
    def from_parent_dict(cls, value: dict[str, Any]) -> RecoveryAnalysis:
        """Validate N2's structured child response *before* N1 acceptance."""
        require_keys(value, {"useful_points", "completed_work", "remaining_units", "merge_requirements"}, context="recovery_analysis")
        arrays = ("useful_points", "completed_work", "remaining_units", "merge_requirements")
        for key in arrays:
            if not isinstance(value[key], list):
                raise ContractError(f"recovery_analysis.{key} must be an array")
        units = []
        for row in value["remaining_units"]:
            require_keys(row, {"name", "instruction", "verify", "scope_items"}, context="recovery_unit")
            if not isinstance(row["scope_items"], list):
                raise ContractError("scope_items must be an array")
            units.append(RecoveryUnit(name=row["name"], instruction=row["instruction"],
                                      verify=row["verify"], scope_items=tuple(row["scope_items"])))
        return cls(useful_points=tuple(value["useful_points"]),
                   completed_work=tuple(value["completed_work"]),
                   remaining_units=tuple(units),
                   merge_requirements=tuple(value["merge_requirements"]))


@dataclass(frozen=True, slots=True)
class RecoveryHandoff:
    """WHEN: A failed task is concluded as PASSED_TO:<recovery task ID>.

    WHY: Distinguish a designated recovery attempt from a new user task. Keep
    the verbatim original user request for scope alignment, a *distilled*
    handoff and pointers to full records. The successor starts by verifying
    surviving work, not by re-executing the original request.
    """
    from_task_id: str
    new_task_id: str
    reason: str
    original: UserPromptReference
    compact_summary: str
    prior_evidence_locations: tuple[str, ...] = ()
    checkpoint_locations: tuple[str, ...] = ()
    from_step_id: str = ""
    task_kind: TaskKind = TaskKind.RECOVERY
    status: str = "passed_to"

    def __post_init__(self) -> None:
        for attr in ("from_task_id", "new_task_id", "reason", "compact_summary"):
            require_text(getattr(self, attr), attr)
        if self.from_task_id == self.new_task_id:
            raise ContractError("recovery successor must have a different task ID")
        if self.task_kind != TaskKind.RECOVERY or self.status != "passed_to":
            raise ContractError("handoff must be explicitly passed_to a recovery task")
        if not isinstance(self.original, UserPromptReference):
            raise ContractError("original must be a UserPromptReference")
        if len(self.compact_summary) > 12000:
            raise ContractError("handoff summary must be distilled; full history goes in evidence storage")

    @property
    def marker(self) -> str:
        return f"PASSED_TO:{self.new_task_id}"

    def to_dict(self) -> dict:
        return as_payload(self)
