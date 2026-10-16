"""Offline permission-gate tests against the actual source method ASTs."""
from __future__ import annotations
import dataclasses
import json
import sys
import types
import unittest
from pathlib import Path
from test_recovery_suppress_celery_r11r3 import method, FakeQueue, FakeJob, FakeRedis, FakePipeline

ROOT = Path(__file__).resolve().parents[1]

@dataclasses.dataclass
class J:
    task_id: str = 't1'
    step_id: str = 'step-03'
    message_id: str = 'n03'
    metadata: dict = dataclasses.field(default_factory=lambda: {
        'approval_request': {'question': 'Approve the file move?', 'proposed_action': 'Move files', 'why_approval_needed':'irreversible', 'authorized':False}})
    chain_id: str = 'chain'
    chain_index: int = 3
    context: str = ''

class ApprovalPipeline(FakePipeline):
    def __init__(self,store): super().__init__(store)
    def __enter__(self): return self
    def __exit__(self,*args): return False
    def watch(self,key): return None
    def zrange(self,key,start,end): return list(self.store.sorted.get(key,{}))
    def unwatch(self):return None
    def multi(self):return None
    def xack(self,*a):self.commands.append(('xack',a[0],a[-1])); return self
    def xdel(self,*a):self.commands.append(('xdel',a[0],a[-1])); return self
    def zrem(self,key,*values):self.commands.append(('zrem',key,values));return self
    def execute(self):
        out=[]
        for kind,key,v in self.commands:
            if kind=='zadd': self.store.sorted.setdefault(key,{}).update(v);out.append(1)
            elif kind=='zrem':
                for one in v: self.store.sorted.get(key,{}).pop(one,None)
                out.append(len(v))
            elif kind=='xadd':
                rows=self.store.streams.setdefault(key,[])
                rows.append((f'{len(rows)+1}-0',v));out.append('1-0')
            elif kind=='xack':out.append(1)
            elif kind=='xdel':
                self.store.streams[key]=[(i,fields) for i,fields in self.store.streams.get(key,[]) if i!=v]
                out.append(1)
        return out

class ApprovalRedis(FakeRedis):
    def pipeline(self,transaction=True):return ApprovalPipeline(self)
    def zrem(self,key,payload):
        return bool(self.sorted.get(key,{}).pop(payload,None) is not None)

class Q(FakeQueue):
    group='consumer'
    def __init__(self):self.r=ApprovalRedis()
    def _approval_wait_key(self): return 'work:approval-wait'
    def _job_to_fields(self, job):
        data=dataclasses.asdict(job)
        data['metadata']=json.dumps(data['metadata'])
        return data
    def _fields_to_job(self, mid, fields):
        data=dict(fields)
        if isinstance(data.get('metadata'),str): data['metadata']=json.loads(data['metadata'])
        return J(**data)
    def _task_suppressed(self,task_id): return False

class Tests(unittest.TestCase):
    def test_approval_hold_then_wake_same_step_not_next(self):
        rel='core/norm_runtime/prompt_queue.py'
        park=method(rel,'RedisPromptQueue','park_for_user_approval',{'json':json,'PromptJob':J})
        wake=method(rel,'RedisPromptQueue','resume_approval_on_injection',{'json':json})
        summary=method(rel,'RedisPromptQueue','awaiting_approval',{'json':json})
        q=Q();job=J()
        q.r.streams['work']=[('redis03',q._job_to_fields(job))]
        park(q,'redis03',job,'Awaiting explicit user approval')
        self.assertEqual(q.r.streams['work'],[])
        self.assertEqual([r['step_id'] for r in summary(q)],['step-03'])
        sys.modules['redis'] = types.SimpleNamespace(WatchError=type('WatchError',(Exception,),{}))
        try:
            self.assertEqual(wake(q,['unrelated']),0)
            self.assertEqual(wake(q,['t1']),1)
        finally:
            sys.modules.pop('redis',None)
        self.assertEqual(len(q.r.streams['work']),1)
        self.assertEqual(q.r.streams['work'][0][1]['step_id'],'step-03')
        self.assertEqual(summary(q),[])
        sys.modules['redis'] = types.SimpleNamespace(WatchError=type('WatchError',(Exception,),{}))
        try:
            self.assertEqual(wake(q,['t1']),0)
        finally:
            sys.modules.pop('redis',None)

    def test_approval_hold_survives_suppression_restore(self):
        rel='core/norm_runtime/prompt_queue.py'
        snapshot=method(rel,'RedisPromptQueue','snapshot_task_jobs',{'asdict':dataclasses.asdict,'json':json,'time':__import__('time')})
        restore=method(rel,'RedisPromptQueue','restore_task_jobs',{'PromptJob':J,'json':json})
        q=Q(); job=J()
        payload=json.dumps(q._job_to_fields(job),ensure_ascii=False,sort_keys=True)
        q.r.sorted[q._approval_wait_key()]={payload:0}
        saved=snapshot(q,'t1')
        self.assertEqual(saved['jobs'][0]['stream'],'approval-wait')
        q.r.sorted.clear()
        self.assertEqual(len(restore(q,saved)),1)
        self.assertIn(q._approval_wait_key(),q.r.sorted)
        self.assertEqual(q.r.streams,{})

    def test_injection_handler_keeps_target_and_wake_hook(self):
        s=(ROOT/'core/norm_main.py').read_text()
        self.assertIn('result = injections.save(target, content, request_id',s)
        self.assertIn('result["woken_steps"] = prompt_queue_obj.resume_approval_on_injection(targets)',s)
        self.assertLess(s.index('result = injections.save(target, content, request_id'),s.index('result["woken_steps"] ='))

if __name__=='__main__':unittest.main(verbosity=2)
