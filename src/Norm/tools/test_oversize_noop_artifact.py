from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "core"))

from norm_runtime.prompt_queue import PromptJob
from norm_runtime.prompt_worker import PromptWorker
from norm_runtime.protocol import PROTOCOL_VERSION


class DummyLive:
    def start_task(self, *args, **kwargs):
        return None

    def step_started(self, *args, **kwargs):
        return None

    def step_completed(self, *args, **kwargs):
        return None


class DummyDurable:
    def start_child_task(self, *args, **kwargs):
        return None

    def checkpoint_step(self, *args, **kwargs):
        return None


def parent_job() -> PromptJob:
    command = {
        "schema_version": PROTOCOL_VERSION,
        "kind": "command",
        "category": "task",
        "intent": "Update documentation files if they are stale.",
        "requires_file_mutation": True,
        "requires_external_research": False,
        "input_has_media": False,
        "output_requires_media": False,
        "expected_output": {"type": "artifact_and_response", "format": "text"},
    }
    return PromptJob(
        message_id="parent-node",
        task_id="parent-task",
        step_id="step-01",
        prompt="Update documentation files if they are stale.",
        project_id="default",
        context="",
        attempt=0,
        created_at=time.time(),
        metadata={
            "command_envelope": command,
            "original_user_prompt": "Update the docs.",
            "task_user_prompt": "Update the docs.",
            "subtask_depth": 0,
        },
        chain_id="parent-chain",
        previous_prompt_id="",
        next_prompt_id="",
        chain_index=0,
        recovery_attempted=False,
        request_type="task",
        task_uuid="parent-task-uuid",
        node_id="parent-node",
        chain_uuid="parent-task-uuid",
        previous_node_id="",
        next_node_id="",
    )


def main() -> None:
    assert PromptWorker._final_artifact_state({
        "command": {"expected_output": {"type": "answer"}},
        "artifacts": [],
    }) is True, "answer-only no-op must not require a manufactured artifact"

    assert PromptWorker._final_artifact_state({
        "command": {"expected_output": {"type": "artifact"}},
        "artifacts": [],
    }) is False, "root artifact request must still require an artifact"

    assert PromptWorker._final_artifact_state({
        "command": {"expected_output": {"type": "answer"}},
        "artifacts": [{"path": "x", "sha256": "abc", "verified": False}],
    }) is False, "an answer-only unit that writes must still verify the write"

    assert PromptWorker._final_artifact_state({
        "command": {"expected_output": {"type": "artifact_and_response"}},
        "artifacts": [{"path": "x", "sha256": "abc", "verified": True}],
    }) is True, "verified artifact must remain acceptable"

    unverified = PromptWorker._artifact_snapshot([
        {"tool": "write_file", "result": {"ok": True, "path": "x", "sha256": "abc"}},
    ])
    assert unverified == [{"path": "x", "sha256": "abc", "verified": False}]

    verified = PromptWorker._artifact_snapshot([
        {"tool": "write_file", "result": {"ok": True, "path": "x", "sha256": "abc"}},
        {"tool": "read_file", "result": {"ok": True, "path": "x", "sha256": "abc"}},
    ])
    assert verified == [{"path": "x", "sha256": "abc", "verified": True}]

    worker = PromptWorker.__new__(PromptWorker)
    worker.live = DummyLive()
    worker.durable = DummyDurable()
    worker._runtime_config = lambda: {"worker": {"max_subtask_depth": 4}}

    _, unit_job = worker._single_runtime_child(
        parent_job(),
        title="Inspect one documentation file",
        instruction="Inspect the file. Edit only if a current-state claim is wrong; otherwise report no change required.",
        verify="If edited, reread the file; otherwise verify the no-op finding from observed evidence.",
        context="",
        prompt_origin="runtime_oversize_recovery_unit",
        allow_subtask=True,
        scope_items=[1],
    )
    expected = unit_job.metadata["command_envelope"]["expected_output"]
    assert expected == {"type": "answer", "format": "text"}, expected

    _, analysis_job = worker._single_runtime_child(
        parent_job(),
        title="Analyze oversized step",
        instruction="Return bounded recovery analysis.",
        verify="Return structured recovery analysis.",
        context="",
        prompt_origin="runtime_oversize_recovery_analysis",
        allow_subtask=False,
        scope_items=[1],
    )
    expected = analysis_job.metadata["command_envelope"]["expected_output"]
    assert expected == {"type": "answer", "format": "json"}, expected

    print("PASS: oversize no-op units do not inherit root artifact requirements")
    print("PASS: any reported write still requires deterministic post-write verification")


if __name__ == "__main__":
    main()
