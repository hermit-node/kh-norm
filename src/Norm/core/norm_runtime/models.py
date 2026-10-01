from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any
import uuid


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class StepStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    SKIPPED = "skipped"


@dataclass(frozen=True)
class TaskStep:
    id: str
    name: str
    description: str
    verify: str = ""
    depends_on: tuple[str, ...] = ()
    node_id: str = field(default_factory=lambda: str(uuid.uuid4()))


@dataclass(frozen=True)
class TaskPlan:
    task_id: str
    title: str
    steps: tuple[TaskStep, ...]
    created_at: datetime = field(default_factory=utc_now)
    task_uuid: str = field(default_factory=lambda: str(uuid.uuid4()))
    original_request: str = ""
    source_prompt_id: str = ""
    source_user_message_id: str = ""
    ingrained_detail_count: int = 0
    ingrained_task_context: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        node_by_legacy = {s.id: s.node_id for s in self.steps}
        return {
            "task_id": self.task_id,
            "task_uuid": self.task_uuid,
            "title": self.title,
            "created_at": self.created_at.isoformat(),
            "original_request": self.original_request,
            "source_prompt_id": self.source_prompt_id,
            "source_user_message_id": self.source_user_message_id,
            "ingrained_detail_count": int(self.ingrained_detail_count or 0),
            "ingrained_task_context": list(self.ingrained_task_context),
            "steps": [
                {
                    "id": s.id,
                    "node_id": s.node_id,
                    "ordinal": index,
                    "name": s.name,
                    "description": s.description,
                    "verify": s.verify,
                    "depends_on": list(s.depends_on),
                    "depends_on_node_ids": [node_by_legacy[d] for d in s.depends_on if d in node_by_legacy],
                }
                for index, s in enumerate(self.steps)
            ],
        }


@dataclass(frozen=True)
class StepResult:
    task_id: str
    step_id: str
    name: str
    status: StepStatus
    summary: str
    artifacts: tuple[str, ...] = ()
    verification: str = ""
    error: str | None = None
    started_at: datetime = field(default_factory=utc_now)
    completed_at: datetime = field(default_factory=utc_now)
    task_uuid: str = ""
    node_id: str = ""
