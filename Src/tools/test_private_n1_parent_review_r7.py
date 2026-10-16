"""Offline behavioral contracts for 0.55.10r7 N1/N2 review ownership."""
from __future__ import annotations
import json
import sys
import types
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'core'))
for dep in ('psycopg','redis'):
    if dep not in sys.modules:
        try: __import__(dep)
        except ImportError: sys.modules[dep] = types.ModuleType(dep)
from norm_runtime.n1_gatekeeper import N1Gatekeeper
from norm_runtime.prompt_worker import PromptWorker

class ReviewModel:
    def __init__(self,reply):
        self.reply=reply
        self.prompts=[]
    def generate(self,prompt,**kwargs):
        self.prompts.append(prompt)
        return json.dumps(self.reply)

def _worker(*, private='Understand actual intended result', child=False, reply=None):
    w=PromptWorker.__new__(PromptWorker)
    client=ReviewModel(reply or {'decision':'accept','reason':'observed result matches intent','retry_instruction':''})
    w.n1_gatekeeper=N1Gatekeeper(client,Mock())
    durable=Mock()
    durable.n1_step_intent.return_value=private
    durable.child_step_evidence.return_value=[{'tool':'durable_child_result_pointer','arguments':{'child_task_id':'child-A'},'result':{'ok':True,'evidence_locator':'PostgreSQL task_evidence task_id=child-A'}}]
    w.durable=durable
    meta={'original_user_prompt':'Review archive for deep changes','task_user_prompt':'Evaluate the release',
          'n1_private_prepared':True,'verification':'DO NOT LEAK PLANNER RIGID CHECKLIST'}
    if child:
        meta.update(parent_task_id='parent',parent_step_id='step-01',internal_child_result=True,prompt_origin='runtime_child_subtask')
    job=SimpleNamespace(task_id='test-task',step_id='step-01',prompt='Review the release source',metadata=meta)
    return w,client,durable,job

def test_n1_private_interpretation_never_in_n2_planner_checklist():
    model=ReviewModel({'interpretations':[{'step_id':'step-01','meaning':'Judge whether the release was genuinely reviewed'}]})
    gate=N1Gatekeeper(model,Mock())
    got=gate.prepare_private_step_intents(original_request='Review the release',task_request='Review',steps=[{'id':'step-01','name':'Read code','description':'Read current code'}])
    assert got=={'step-01':'Judge whether the release was genuinely reviewed'}
    assert 'Read current code' in model.prompts[0]
    assert 'N2 SUBMISSION' not in model.prompts[0]
    assert 'DO NOT LEAK' not in model.prompts[0]

def test_finished_parent_submission_reviewed_with_private_intent_and_child_pointer():
    w,model,durable,job=_worker()
    accepted,issues=w._n1_step_alignment(job,'Reviewed 12 modules and documented ten suggestions',[])
    assert (accepted,issues)==(True,[])
    assert durable.child_step_evidence.call_count==1
    assert 'N1 PRIVATE PRE-EXECUTION UNDERSTANDING' in model.prompts[0]
    assert 'durable_child_result_pointer' in model.prompts[0]
    assert 'DO NOT LEAK PLANNER RIGID CHECKLIST' not in model.prompts[0]
    assert 'Reviewed 12 modules' in model.prompts[0]

def test_n1_returns_only_actionable_correction():
    w,model,durable,job=_worker(reply={'decision':'retry','reason':'Claim is unsupported','retry_instruction':'Check the produced file and correct the sha256 claim'})
    accepted,issues=w._n1_step_alignment(job,'Created report.txt with sha abc',[])
    assert not accepted
    assert issues==['N1 requirement/scope review: Check the produced file and correct the sha256 claim']
    assert 'N1 PRIVATE PRE-EXECUTION UNDERSTANDING' not in issues[0]

def test_internal_child_skips_independent_n1_review():
    w,model,durable,job=_worker(child=True)
    accepted,issues=w._n1_step_alignment(job,'Completed my child component',[])
    assert (accepted,issues)==(True,[])
    assert model.prompts==[]
    durable.child_step_evidence.assert_not_called()

def test_compared_to_r6_existing_n2_step_selfcheck_is_identical():
    import zipfile
    p=ROOT/'core/norm_runtime/prompt_worker.py'
    now=p.read_text(encoding='utf8')
    source=Path('/mnt/data/Norm-0.55.10r6-chat-style-pdf-portable-source.zip')
    if not source.exists():
        # A portable source ZIP can run this test without its build baseline.
        return
    with zipfile.ZipFile(source) as z:
        before=z.read('core/norm_runtime/prompt_worker.py').decode('utf8')
    def take(s,start,end):
        i=s.index('    def '+start+'(')
        j=s.index('    def '+end+'(',i)
        return s[i:j]
    for start,end in [('_request_verdict','_n1_step_alignment'),('_verify_and_correct_without_tools','_verify_and_correct'),('_verify_and_correct','_context_headroom_state')]:
        assert take(before,start,end)==take(now,start,end),start
