"""Small references to existing durable evidence and artifacts; never copy bulk history."""
from __future__ import annotations

from dataclasses import dataclass

from ._base import ContractError, as_payload, require_text


@dataclass(frozen=True, slots=True)
class EvidenceReference:
    """WHEN: N2 cites observed tool output and N1 needs proof for a specific claim.

    WHY: Refer to persisted Redis/PostgreSQL evidence by stable locator, without
    injecting whole histories into model context. The reference itself is NOT
    proof: N1 must read/check the record, including its trust and freshness.
    """
    task_id: str
    step_id: str
    record_locator: str
    claim: str
    observed_sha256: str = ""
    tool_name: str = ""

    def __post_init__(self) -> None:
        for attr in ("task_id", "step_id", "record_locator", "claim"):
            require_text(getattr(self, attr), attr)

    def to_dict(self) -> dict:
        return as_payload(self)


@dataclass(frozen=True, slots=True)
class ArtifactReference:
    """WHEN: N2 proposes a produced/checked file in a step or final submission.

    WHY: Pair the path with an observed hash and explicit verification state.
    The caller cannot elevate 'verified' by asserting it in natural language;
    the existing file-tool/N1 evidence path is authoritative.
    """
    path: str
    sha256: str = ""
    verified: bool = False
    evidence_locator: str = ""

    def __post_init__(self) -> None:
        require_text(self.path, "path")
        if self.verified and not (self.sha256 and self.evidence_locator):
            raise ContractError("verified artifact requires observed SHA-256 and evidence locator")

    def to_dict(self) -> dict:
        return as_payload(self)
