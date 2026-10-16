"""r9 regressions: real N2 terminal routing; purpose-built recovery task, not restart.

No Redis/PostgreSQL/Ollama network or filesystem mutation of the live install.
"""
import json
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import Mock
import types
for name in ('redis','psycopg'):
    try:
        __import__(name)
    except ImportError:
        m=types.ModuleType(name)
        if name=='redis':
            m.Redis=object; m.ResponseError=RuntimeError
        if name=='psycopg':
            m.Error=RuntimeError
        sys.modules[name]=m
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'core'))
from norm_runtime.prompt_queue import PromptJob
from norm_runtime.prompt_worker import PromptWorker

class Redis:
    def __init__(self): self.items={}
    def setex(self,k,ttl,v): self.items[k]=json.loads(v)

class Q:
    def __init__(self): self.r=Redis();self.saved=[]
    def contains_message_id(self,v):return any(x.message_id==v for x in self.saved)
    def enqueue_chain(self,jobs):self.saved.extend(jobs)

class Durable:
    def __init__(self): self.child=None;self.status=None;self.notes=[];self.checkpoints=[]
    def task_lineage(self,v):return {'task_kind':'root','task_depth':0}
    def task_plan_snapshot(self,v):return {'original_request':'Create original important document, without duplication.','steps':[{'id':'step-01','name':'Create document','status':'completed'}, {'id':'step-02','name':'Verify and report','status':'failed'}]}
    def task_status(self,v):return self.status
    def start_child_task(self,p,original_id,original_step,**kwargs):
        assert kwargs['task_kind']=='recovery';self.child=(p,original_id,original_step,kwargs);self.status='running'
    def checkpoint_step(self,record):self.checkpoints.append(record)
    def record_maintenance_note(self,*args,**kwargs):self.notes.append((args,kwargs))

class Live:
    def __init__(self):self.starts=[]
    def start_task(self,*args):self.starts.append(args)
    def step_started(self,*args):pass
    def step_completed(self,*args):pass

j=PromptJob(message_id='j',task_id='orig-task',step_id='step-02',prompt='Internal broken operation',
    project_id='default',context='Large stale context',attempt=5,created_at=0,metadata={
        'original_user_prompt':'Create original important document, without duplication.',
        'is_final_candidate':True,'generated_step_count':9,'prompt_origin':'norm_generated_step',
    },chain_id='chain',previous_prompt_id='',next_prompt_id='',chain_index=6,
    recovery_attempted=True,request_type='conclusion')
w=PromptWorker.__new__(PromptWorker)
w.durable=Durable();w.live=Live();w.queue=Q();w._task_suppressed=lambda x:False
w._runtime_config=lambda:{'worker':{'max_subtask_depth':4}}
assert not hasattr(w, 'fake_ollama_generation')
child_id=w._handoff_failed_task_to_recovery(j,stage='terminal-exception',error='generation crashed',checkpoint='piece 04 done')
assert child_id.startswith('recovery-')
plan,old,failed,params=w.durable.child
assert (old,failed)==('orig-task','step-02')
assert plan.original_request=='Create original important document, without duplication.'
assert w.queue.r.items['norm:recovery:handoff:'+child_id]['saved_checkpoint']=='piece 04 done'
assert len(w.queue.saved)==5
assert [x.request_type for x in w.queue.saved]==['step','step','step','step','conclusion']
assert all(x.metadata['recovery_mode'] for x in w.queue.saved)
assert all(x.metadata['original_user_prompt']==plan.original_request for x in w.queue.saved)
assert all(x.metadata['compact_resume_context'] is False for x in w.queue.saved)
assert all('Large stale context' not in x.context for x in w.queue.saved)
assert 'RECOVERY MODE' in w.queue.saved[0].prompt
assert w.queue.saved[-1].metadata['is_final_candidate'] is True
assert all(x.metadata['is_final_candidate'] is False for x in w.queue.saved[:-1])
assert w.queue.saved[-1].metadata['final_verification_node_id']==plan.steps[-1].node_id
assert w.queue.saved[0].context.startswith('{"kind": "recovery_handoff"')
assert all(x.metadata['prompt_origin']=='runtime_recovery_attempt' for x in w.queue.saved)
assert w._handoff_failed_task_to_recovery(j,stage='terminal-exception',error='same failure',checkpoint='piece 04 done') == child_id
assert len(w.queue.saved) == 5  # duplicate retry must NOT spawn a second recovery sequence
assert w.queue.saved[0].node_id == plan.steps[2].node_id

# No N2 inference from Celery. Full N2 execution still has exact user-role reference.
handoff_user_text = PromptWorker._tool_slice_initial_messages(w.queue.saved[0])[1]['content']
assert plan.original_request in handoff_user_text
assert '[ORIGINAL USER PROMPT: REFERENCE ONLY' in handoff_user_text
assert '[CURRENT ASSIGNMENT:' in handoff_user_text
assert handoff_user_text != plan.original_request
assert 'CURRENT assignment determines' in PromptWorker._tool_slice_execution_instructions(w.queue.saved[0])
print('PASS r9 dedicated recovery, original prompt provenance, compact context, normal N2 jobs')

# A PASSED_TO parent is an archive, NOT a cleanup target: no soft deletion,
# model-buffer removal, SOS deletion, or live Redis history purge.
cleaned=[]
w4=PromptWorker.__new__(PromptWorker)
w4.durable=SimpleNamespace(terminal_summary_verified=lambda t: True,
                           task_status=lambda t: 'passed_to')
w4.queue=SimpleNamespace(cleanup_task=lambda task: cleaned.append(task))
w4.live=SimpleNamespace(cleanup=lambda task: (_ for _ in ()).throw(AssertionError('live evidence erased')))
w4._ensure_effectiveness_note=lambda task: (_ for _ in ()).throw(AssertionError('unsupervised summary generated'))
w4._soft_delete_task_assets=lambda *a,**k: (_ for _ in ()).throw(AssertionError('recovery artifacts deleted'))
w4._clear_processed_sos=lambda task: (_ for _ in ()).throw(AssertionError('SOS erased'))
w4._clear_terminal_redis('orig-task')
assert cleaned == ['orig-task']
print('PASS passed_to retains old Redis history, SOS, artifacts and checkpoints')
