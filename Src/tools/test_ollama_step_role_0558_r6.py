"""Offline regression tests for restored operator-role context and step execution.

Never contacts Ollama, Redis, PostgreSQL or remote filesystems.
"""
from __future__ import annotations
from pathlib import Path
from unittest.mock import patch
import pytest
from test_n2_voice_provenance_0553 import make_worker, Tools, PromptWorker


def _typed(worker, job, request_type, **kwargs):
    with patch.object(PromptWorker, "_runtime_config", return_value={}):
        return worker._typed_prompt(job, request_type, **kwargs)


def _run(worker, job, prompt, patches):
    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
        return worker._run_tool_slice(job, prompt, Tools())


def test_generated_step_uses_unchanged_human_user_request_and_current_step():
    worker, job, patches = make_worker()
    job.request_type = 'step'
    job.prompt = 'NORM-GENERATED EXECUTION STEP: inspect 1.6.10 files'
    job.metadata.update(generated_step_index=2, generated_step_count=6,
                        verification='List files with source references')
    prompt = _typed(worker, job, 'step', prior='Verified step 1 complete')
    result = _run(worker, job, prompt, patches)
    assert result['answer']
    packet = worker.client.calls[0]
    assert [m['role'] for m in packet] == ['system','user']
    assert packet[1]['content'] == "How's your mother?"
    system = packet[0]['content']
    assert 'Step 2/6' in system
    assert 'PROSECUTE the CURRENT work_request.instruction' in system
    assert 'Planning and N1 plan verification have ALREADY occurred' in system
    assert 'Verified step 1 complete' in system
    assert 'inspect 1.6.10 files' in system
    assert 'regenerate the task plan' in system
    assert 'execute_scoped_step' in system


def test_final_conclusion_keeps_original_user_and_synthesizes_not_replans():
    worker, job, patches = make_worker()
    job.request_type = 'conclusion'
    job.metadata.update(generated_step_index=6, generated_step_count=6)
    _run(worker, job, _typed(worker, job,'conclusion'), patches)
    packet=worker.client.calls[0]
    assert packet[1]['content'] == "How's your mother?"
    assert 'conclusion step, synthesize' in packet[0]['content']


def test_direct_turn_is_not_misidentified_as_generated_step():
    worker, job, patches = make_worker()
    job.request_type='prompt'
    job.prompt='Ahoy!'
    job.metadata = {'prompt_origin': 'user_prompt', 'original_user_prompt': 'Ahoy!'}
    _run(worker, job, _typed(worker, job,'prompt'), patches)
    packet=worker.client.calls[0]
    assert packet[1]['content']=='Ahoy!'
    assert '[NORM DIRECT EXECUTION PHASE]' in packet[0]['content']


def test_generated_step_legacy_request_from_text_is_recovered():
    worker, job, patches = make_worker()
    job.request_type='step'
    job.metadata={'prompt_origin':'norm_generated_step'}
    job.prompt=('PROMPT ORIGIN: norm_generated_step\nAUTHORITATIVE PRIMARY TASK REQUEST:\n'
               'Write a friendly greeting.\n\nNORM-GENERATED EXECUTION STEP:\n'
               'Produce hello.\n\nVERIFICATION REQUIREMENT:\nCheck tone.')
    assert PromptWorker._tool_slice_initial_messages(job)[1]['content']=='Write a friendly greeting.'


def test_missing_original_fails_before_sending_misattributed_internal_text():
    worker, job, patches=make_worker()
    job.request_type='step'
    job.metadata={'prompt_origin':'norm_generated_step'}
    job.prompt='Only generated internal instruction, original user was lost'
    with pytest.raises(RuntimeError, match='original user request'):
        _run(worker, job, _typed(worker, job,'step'), patches)
    assert not worker.client.calls


def test_planning_and_execution_have_separate_prompts():
    # The planner module imports production database bindings that are not
    # installed in this offline regression environment. Check its source-level
    # contracts, and the executed worker stage in the tool-call tests above.
    source = (Path(__file__).resolve().parents[1] / 'core' / 'norm_runtime' / 'conversation_service.py').read_text()
    assert '[NORM PLANNING PHASE — PLAN ONLY, DO NOT EXECUTE]' in source
    assert 'do not run tools, modify files' in source
    assert 'EXECUTION (NOT PLANNING)' in source
    assert 'This plan and its N1 verification are already complete.' in source
    assert 'Actively carry out this exact step' in source
