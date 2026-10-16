"""Common vocabulary for Norm's proposed inter-component contracts.

WHEN: Imported by the contract classes in this package.
WHY: Makes a child's expected result, the task's identity and review decisions
     machine-distinguishable instead of relying on prose.

This module does not replace norm_runtime.protocol.PROTOCOL_VERSION (currently 2),
nor does importing it modify Norm's runtime or register a new executor.
"""
from __future__ import annotations

from dataclasses import asdict, is_dataclass
from enum import StrEnum
from typing import Any

CONTRACT_DRAFT_VERSION = 1


class ContractError(ValueError):
    """WHEN: An object is missing required fields or crosses a forbidden boundary.

    WHY: Reject the wrong kind of payload before asking N1 to assess its content.
    """


class TaskKind(StrEnum):
    """WHEN: A task or child assignment is created; WHY: keep recovery out of fresh-task ingress."""
    USER = "user"
    RECOVERY = "recovery"
    OVERSIZE_ANALYSIS = "oversize_analysis"
    RECOVERY_UNIT = "recovery_unit"


class ResultKind(StrEnum):
    """WHEN: N1/N2 exchange results; WHY: a recovery plan is not a final user answer."""
    STEP_RESULT = "step_result"
    RECOVERY_ANALYSIS = "recovery_analysis"
    FINAL_CANDIDATE = "final_candidate"


class ReviewKind(StrEnum):
    """WHEN: N1 reviews a result; WHY: acceptance applies only to the requested result kind."""
    STEP = "step"
    RECOVERY = "recovery"
    FINAL = "final"


class Decision(StrEnum):
    """WHEN: N1 issues a review outcome; WHY: distinguish approval from a requested correction."""
    ACCEPT = "accept"
    RETRY = "retry"


def require_text(value: str, field: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ContractError(f"{field} must be a nonempty string")


def as_payload(value: Any) -> dict[str, Any]:
    """Convert immutable draft dataclasses/enums to plain JSON-safe Python data."""
    if not is_dataclass(value) or isinstance(value, type):
        raise ContractError("payload must be a dataclass instance")
    return asdict(value)


def require_keys(value: dict[str, Any], required: set[str], *, context: str) -> None:
    if not isinstance(value, dict):
        raise ContractError(f"{context} must be an object")
    missing = required.difference(value)
    if missing:
        raise ContractError(f"{context} missing {sorted(missing)}")
