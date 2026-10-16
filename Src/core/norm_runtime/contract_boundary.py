"""Small adapter from existing N1/N2 work to portable typed contract objects.

No new supervisor, process, database, task type or model calls. This only
checks assignment/result identity BEFORE N1 semantic evaluation.
"""
from __future__ import annotations
import json
import sys
from pathlib import Path


def _types(root: Path):
    root = str(Path(root).resolve())
    if root not in sys.path:
        sys.path.append(root)
    from objects import (ResultKind, TaskKind, UserPromptReference, StepAssignment,
                         StepSubmission, RecoveryHandoff, validate_assignment_submission)
    return ResultKind, TaskKind, UserPromptReference, StepAssignment, StepSubmission, RecoveryHandoff, validate_assignment_submission


def validate_recovery_analysis_child(root, *, task_id, step_id, instruction,
                                     criterion, original_prompt, parent_task_id,
                                     analysis):
    result_kind, task_kind, user_ref, assign_cls, submit_cls, _, check = _types(Path(root))
    assignment = assign_cls(
        task_id=str(task_id), step_id=str(step_id),
        task_kind=task_kind.OVERSIZE_ANALYSIS,
        expected_result=result_kind.RECOVERY_ANALYSIS,
        instruction=str(instruction), acceptance_criteria=str(criterion),
        original=user_ref(original_prompt=str(original_prompt or instruction),
                          source_task_id=str(parent_task_id)),
        parent_task_id=str(parent_task_id),
    )
    submission = submit_cls(
        task_id=str(task_id), step_id=str(step_id),
        result_kind=result_kind.RECOVERY_ANALYSIS,
        content=json.dumps(analysis, ensure_ascii=False),
        self_verified=True,
    )
    check(assignment, submission)
    return submission.content


def validate_recovery_handoff(root, *, parent_task_id, parent_step_id,
                              successor_task_id, original_prompt, summary):
    _, task_kind, user_ref, _, _, handoff_cls, _ = _types(Path(root))
    return handoff_cls(
        from_task_id=str(parent_task_id), from_step_id=str(parent_step_id),
        new_task_id=str(successor_task_id), reason="terminal recovery handoff",
        original=user_ref(original_prompt=str(original_prompt),
                          source_task_id=str(parent_task_id)),
        compact_summary=str(summary)[:12000],
        prior_evidence_locations=(f"PostgreSQL task_evidence:{parent_task_id}",),
        task_kind=task_kind.RECOVERY,
    ).marker
