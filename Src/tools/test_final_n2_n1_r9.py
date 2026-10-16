"""Offline r9 final-answer contract: genuine N2 job -> independent N1 acceptance.
No Ollama, Redis or PostgreSQL calls.
"""
import json, sys, types
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'core'))
for name in ('redis','psycopg'):
 try: __import__(name)
 except ImportError:
  m=types.ModuleType(name)
  if name=='redis':m.Redis=object;m.ResponseError=RuntimeError
  if name=='psycopg':m.Error=RuntimeError
  sys.modules[name]=m
from norm_runtime.prompt_worker import PromptWorker, VerifierProtocolError
from norm_runtime.prompt_queue import PromptJob
from norm_runtime.protocol import PROTOCOL_VERSION, normalize_command

command=normalize_command({'category':'task','intent':'Write a verified report','requires_file_mutation':False,
    'expected_output':{'type':'answer','format':'markdown'}},'Write report')
candidate={'schema_version':PROTOCOL_VERSION,'kind':'final_candidate','task_id':'task-123',
 'step_id':'step-03','status':'completed','command':command,'user_reply':'Distinctive Norm voice and Nabler output',
 'artifacts':[],'requirements':{},'evidence_count':1}
original=PromptJob(message_id='verify-node',task_id='task-123',step_id='final-verify',
 prompt=json.dumps(candidate),project_id='default',context='',attempt=0,created_at=0,
 metadata={'verification_cycle':1,'n2_conclusion_step_id':'step-03',
           'task_user_prompt':'Write a verified report','original_user_prompt':'Write a verified report',
           'task_uuid':'b169ed7c-2c53-4c35-bf2b-f4e4ac277542'},
 chain_id='b169ed7c-2c53-4c35-bf2b-f4e4ac277542',previous_prompt_id='',next_prompt_id='',chain_index=7,
 recovery_attempted=False,request_type='final_verification')
class D:
 def __init__(self):self.checkpoints=[];self.finalized=[]
 def checkpoint_step(self,v):self.checkpoints.append(v)
 def all_steps_completed(self,*a):return True
 def finalize_task(self,*a):self.finalized.append(a)
 def terminal_summary_verified(self,*a):return True
class L:
 def __init__(self):self.finished=[]
 def step_completed(self,*a):pass
 def finish(self,*a):self.finished.append(a)
class Q:
 def __init__(self):self.acked=[];self.requeued=[]
 def ack(self,v):self.acked.append(v)
 def enqueue(self,v):self.requeued.append(v)

def worker():
 w=PromptWorker.__new__(PromptWorker);w.durable=D();w.live=L();w.queue=Q();w.n1_gatekeeper=object()
 w._runtime_config=lambda:{'worker':{'n1_final_verification_cycle_limit':8}}
 w._clear_terminal_redis=lambda *_:None
 return w

def verdict(text):
 accepted=text=='accept'
 return {'schema_version':2,'kind':'final_verification','verdict':text,'schema_complete':True,
   'requirements_complete':accepted,'artifact_verified':True,
   'issues':[] if accepted else ['Missing evidence'], 'repair_scope':'none' if accepted else 'response'}

from copy import deepcopy
w=worker();j=deepcopy(original)
w._n1_final_verification=lambda *_:(verdict('revise'),False)
w._process_final_verification('r0',j,'Final review',None)
assert w.durable.finalized == []
assert w.queue.acked==['r0'] and len(w.queue.requeued)==1
n2=w.queue.requeued[0]
assert n2.request_type=='conclusion' and n2.step_id=='step-03'
assert n2.metadata['prompt_origin']=='runtime_final_n2_repair'
assert n2.metadata['is_final_candidate'] is True
assert n2.metadata['verification_cycle']==2
assert 'N1 issues' in n2.prompt
assert n2.metadata['original_user_prompt']=='Write a verified report'
assert PromptWorker._tool_slice_initial_messages(n2)[1]['content']=='Write a verified report'

w2=worker();j2=deepcopy(original)
w2._n1_final_verification=lambda *_:(verdict('accept'),False)
w2._process_final_verification('r1',j2,'Final review',None)
assert w2.queue.requeued==[]
assert w2.durable.finalized==[('task-123',candidate['user_reply'],'completed')]
assert w2.live.finished[0][1]==candidate['user_reply']

w3=worker();j3=deepcopy(original)
w3._n1_final_verification=lambda *_:(None,False)
try:
 w3._process_final_verification('r2',j3,'Final review',None)
except VerifierProtocolError:pass
else:raise AssertionError('missing N1 must not accept final response')
assert not w3.durable.finalized
print('PASS r9 N1 rejects -> real N2 repair; N1 accepts -> completes; missing N1 blocks')
