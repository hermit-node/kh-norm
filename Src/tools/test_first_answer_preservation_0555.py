"""0.55.5 regression: preserve first N2 answer until evidence-backed rejection.

No actual Ollama or network required. This covers both generation branches and the
existing step-verification correction loop.
"""
from __future__ import annotations
import json
import sys
import types
from pathlib import Path
from unittest.mock import Mock, patch
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'core'))
for dependency in ('redis','psycopg'):
    if dependency not in sys.modules:
        try: __import__(dependency)
        except ImportError: sys.modules[dependency]=types.ModuleType(dependency)
from norm_runtime.prompt_queue import PromptJob
from norm_runtime.prompt_worker import PromptWorker

FIRST="Alright, thanks — ready to dig in. What's on your mind?"
SECOND="Hey there! I'm doing well, thanks for asking — ready to dig into whatever you've got going. What can I help you with today?"

def _job():
    return PromptJob(message_id='m',task_id='t',step_id='step-01',
        prompt='Hello, how are you?', project_id='p',context='',attempt=0,
        created_at=0,metadata={'think':True,'original_user_prompt':'Hello, how are you?'},
        chain_id='c',previous_prompt_id='',next_prompt_id='',chain_index=0,
        recovery_attempted=False,request_type='task')

def _run(*,tool_enabled,verdicts):
    worker=PromptWorker.__new__(PromptWorker)
    worker.client=Mock()
    worker.client.chat_with_tools=Mock()
    worker.live=Mock()
    worker.live.task_evidence.return_value=[]
    source=Mock()
    source.side_effect=[{'yielded':False,'answer':FIRST,'evidence':[]},
                        {'yielded':False,'answer':SECOND,'evidence':[]}]
    generator=Mock(return_value=FIRST)
    verdict=Mock(side_effect=verdicts)
    with patch.object(PromptWorker,'_active_voice_instructions',return_value='Cockney system prior'), \
         patch.object(PromptWorker,'_prompt_with_context_injections',side_effect=lambda _job,prompt,**kw:prompt), \
         patch.object(PromptWorker,'_worker_tools',return_value=Mock() if tool_enabled else None), \
         patch.object(PromptWorker,'_run_tool_slice',source), \
         patch.object(PromptWorker,'_generate_complete_text',generator), \
         patch.object(PromptWorker,'_request_verdict',verdict), \
         patch.object(PromptWorker,'_record_verifier_attempt'), \
         patch.object(PromptWorker,'_retire_verified_injections'):
        result=worker._generate_with_budget('redis-id',_job(), 'Hello, how are you?',None,'n2',0.0)
    return result,source,generator,verdict

def test_first_answer_kept_when_structured_verifier_accepts_with_tools():
    response, source, gen, verdict=_run(tool_enabled=True, verdicts=[(True,[],json.dumps({'verdict':'accept'}))])
    assert response==FIRST
    assert response!=SECOND
    assert source.call_count==1, 'an unconditional completion review would call N2 twice'
    assert verdict.call_count==1
    assert gen.call_count==0

def test_first_answer_kept_when_structured_verifier_accepts_without_tools():
    response, source, gen, verdict=_run(tool_enabled=False, verdicts=[(True,[],json.dumps({'verdict':'accept'}))])
    assert response==FIRST
    assert source.call_count==0
    assert gen.call_count==1, 'unconditional review would create a second generation'
    assert verdict.call_count==1

def test_rejected_answer_corrected_only_after_explicit_structured_issue():
    response, source, gen, verdict=_run(tool_enabled=True, verdicts=[
        (False,['Missing required detail'],json.dumps({'verdict':'revise','issues':['Missing required detail']})),
        (True,[],json.dumps({'verdict':'accept'}))])
    assert response==SECOND
    assert source.call_count==2, 'a rejection must still permit one concrete correction'
    assert verdict.call_count==2

def test_no_tool_rejection_still_reaches_existing_correction_generator():
    worker=PromptWorker.__new__(PromptWorker)
    with patch.object(PromptWorker,'_request_verdict',side_effect=[
         (False,['Incorrect fact'], '{}'),(True,[],'{}')]) as v, \
         patch.object(PromptWorker,'_record_verifier_attempt'), \
         patch.object(PromptWorker,'_generate_complete_text',return_value='Fixed answer') as generate:
        answer=worker._verify_and_correct_without_tools(_job(),'question', FIRST,system_context='Cockney')
    assert answer=='Fixed answer'
    assert v.call_count==2
    assert generate.call_count==1
