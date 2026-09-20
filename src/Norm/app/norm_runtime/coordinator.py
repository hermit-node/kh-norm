from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from threading import Event, Thread
from time import monotonic
from typing import Callable, Protocol

from .models import StepResult, StepStatus, TaskPlan, TaskStep


class LiveLog(Protocol):
    def start_task(self, task_id: str, title: str, plan: dict) -> None: ...
    def step_started(self, task_id: str, step_id: str, name: str) -> None: ...
    def heartbeat(self, task_id: str, step_id: str, elapsed_seconds: int) -> None: ...
    def step_completed(self, task_id: str, step_id: str, summary: str, verification: str = "") -> None: ...
    def step_failed(self, task_id: str, step_id: str, error: str) -> None: ...
    def finish(self, task_id: str, summary: str) -> None: ...


class DurableLog(Protocol):
    def start_task(self, plan: TaskPlan) -> None: ...
    def checkpoint_step(self, result: StepResult) -> None: ...
    def save_summary(self, task_id: str, summary: str, final: bool = False) -> None: ...


@dataclass(frozen=True)
class StepOutcome:
    summary: str
    artifacts: tuple[str, ...] = ()


class TaskCoordinator:
    def __init__(self, live: LiveLog, durable: DurableLog | None = None, heartbeat_seconds: int = 300) -> None:
        if heartbeat_seconds < 1:
            raise ValueError("heartbeat_seconds must be positive")
        self.live = live
        self.durable = durable
        self.heartbeat_seconds = heartbeat_seconds
        self.plan: TaskPlan | None = None

    def start(self, plan: TaskPlan) -> None:
        self.plan = plan
        self.live.start_task(plan.task_id, plan.title, plan.as_dict())
        if self.durable:
            self.durable.start_task(plan)

    def checkpoint_summary(self, summary: str, final: bool = False) -> None:
        plan = self._require_plan()
        if self.durable:
            self.durable.save_summary(plan.task_id, summary, final=final)
        if final:
            self.live.finish(plan.task_id, summary)

    def execute_step(
        self,
        step: TaskStep,
        action: Callable[[], StepOutcome],
        verify: Callable[[StepOutcome], str] | None = None,
    ) -> StepResult:
        plan = self._require_plan()
        started = datetime.now(timezone.utc)
        started_clock = monotonic()
        self.live.step_started(plan.task_id, step.id, step.name)

        stop = Event()
        heartbeat = Thread(
            target=self._heartbeat_loop,
            args=(stop, plan.task_id, step.id, started_clock),
            daemon=True,
        )
        heartbeat.start()
        try:
            outcome = action()
            verification = verify(outcome) if verify else ""
            result = StepResult(
                task_id=plan.task_id,
                step_id=step.id,
                name=step.name,
                status=StepStatus.COMPLETED,
                summary=outcome.summary,
                artifacts=outcome.artifacts,
                verification=verification,
                started_at=started,
                completed_at=datetime.now(timezone.utc),
            )
            if self.durable:
                self.durable.checkpoint_step(result)
            self.live.step_completed(plan.task_id, step.id, result.summary, result.verification)
            return result
        except Exception as exc:
            result = StepResult(
                task_id=plan.task_id,
                step_id=step.id,
                name=step.name,
                status=StepStatus.FAILED,
                summary="Step failed",
                error=f"{type(exc).__name__}: {exc}",
                started_at=started,
                completed_at=datetime.now(timezone.utc),
            )
            if self.durable:
                self.durable.checkpoint_step(result)
            self.live.step_failed(plan.task_id, step.id, result.error or "Unknown error")
            raise
        finally:
            stop.set()
            heartbeat.join(timeout=1)

    def _heartbeat_loop(self, stop: Event, task_id: str, step_id: str, started_clock: float) -> None:
        while not stop.wait(self.heartbeat_seconds):
            elapsed = int(monotonic() - started_clock)
            self.live.heartbeat(task_id, step_id, elapsed)

    def _require_plan(self) -> TaskPlan:
        if not self.plan:
            raise RuntimeError("TaskCoordinator.start() must be called first")
        return self.plan
