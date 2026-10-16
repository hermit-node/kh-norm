"""Offline, focused tests of r11r4 N1 observation and task-scoped Celery cancellation."""
import ast
import json
import logging
import sys
import types
import uuid
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'core'))
import norm_runtime  # make the package available for isolated module stubs

def function(path, function_name, ns, class_name=None):
    tree = ast.parse((ROOT / path).read_text('utf8'))
    body = next(t.body for t in tree.body if isinstance(t, ast.ClassDef) and t.name == class_name) if class_name else tree.body
    node = next(x for x in body if isinstance(x, ast.FunctionDef) and x.name == function_name)
    node.decorator_list = []
    ns = dict(ns)
    exec(compile(ast.fix_missing_locations(ast.Module(body=[node], type_ignores=[])), path, 'exec'), ns)
    return ns[function_name]

class RedisFake:
    def __init__(self): self.data={};self.closed=False
    def get(self, name): return self.data.get(name)
    def set(self, name, value, **opts): self.data[name]=value;return True
    def exists(self, name): return name in self.data
    def close(self): self.closed=True
    def scan_iter(self, match, count=None):
        import fnmatch
        return (k for k in self.data if fnmatch.fnmatch(k, match))

class AsyncFake:
    def __init__(self, ready):self._ready=ready
    def ready(self):return self._ready

class Tests(unittest.TestCase):
    def test_n1_may_end_wait_at_fifteen_minutes(self):
        bridge = function('core/norm_runtime/async_n1_delivery.py', '_estimate_next',
            {'HARD_DEADLINE_SECONDS':3600, 'logging':logging}, 'N1AsyncBridge')
        class Gate:
            called=0
            def estimate_async_next_check(self, **kwargs):
                self.called+=1
                assert kwargs['elapsed_seconds']==900
                return {'decision':'stop', 'reason':'worker unresponsive', 'check_after_seconds':180}
        gate=Gate()
        self_obj=types.SimpleNamespace(broker=types.SimpleNamespace(inspect=lambda jobid:{'state':'PENDING','execution':'submitted'}),
                         owner=types.SimpleNamespace(n1_gatekeeper=gate), job=types.SimpleNamespace(task_id='t',step_id='s'))
        item={'id':'uuid','name':'list_directory','arguments':{},'submitted_at':1000}
        self.assertEqual(bridge(self_obj,item,1900),1900)
        self.assertEqual(item['n1_stop_reason'],'worker unresponsive')
        self.assertEqual(gate.called,1)

    def test_n1_can_continue_observation_without_redispach(self):
        bridge=function('core/norm_runtime/async_n1_delivery.py','_estimate_next',
            {'HARD_DEADLINE_SECONDS':3600,'logging':logging}, 'N1AsyncBridge')
        class Gate:
            def estimate_async_next_check(self, **kwargs):return {'decision':'continue','reason':'worker alive','check_after_seconds':600}
        obj=types.SimpleNamespace(broker=types.SimpleNamespace(inspect=lambda x:{'state':'STARTED','execution':'executing'}),
                      owner=types.SimpleNamespace(n1_gatekeeper=Gate()),job=types.SimpleNamespace(task_id='t',step_id='s'))
        item={'id':'id','name':'postgres_query','arguments':{},'submitted_at':1000}
        self.assertEqual(bridge(obj,item,1900),2500)
        self.assertNotIn('n1_stop_reason', item)

    def test_n1_model_decision_is_returned_to_bridge(self):
        gate_fn=function('core/norm_runtime/n1_gatekeeper.py', 'estimate_async_next_check',
            {'redact':lambda x:x, 'json':json,
             'OllamaClient':types.SimpleNamespace(parse_json=lambda x:x)}, 'N1Gatekeeper')
        class Gate:
            enabled=True
            def _profile(self,*args,**kwargs):return {'think':False,'temperature':0,'num_predict':120,'timeout_seconds':10}
            def _review_generate(self,prompt,**kwargs):
                assert 'N1' in prompt and 'N2' in prompt
                return {'decision':'stop','check_after_seconds':180,'reason':'missing heartbeat'}
        decision=gate_fn(Gate(),task_id='t',step_id='s',tool='list_directory',arguments={},
                          dispatch_id='d',elapsed_seconds=900,remaining_seconds=2700,
                          worker_status={'state':'PENDING'},prior_estimates=0)
        self.assertEqual(decision['decision'],'stop')
        self.assertEqual(decision['reason'],'missing heartbeat')

    def test_n1_stopped_dispatch_produces_unresolved_receipt(self):
        bridge_fn=function('core/norm_runtime/async_n1_delivery.py','deliver',
            {'time':types.SimpleNamespace(time=lambda:1900),
             'logging':logging,'HARD_DEADLINE_SECONDS':3600},'N1AsyncBridge')
        class Bridge:
            pending=[{'id':'dispatch','name':'list_directory','submitted_at':1000,'next_check_at':1900}]
            broker=types.SimpleNamespace(poll=lambda x:None)
            job=types.SimpleNamespace(task_id='t')
            def _schedule_next(self,item,now):item['n1_stop_reason']='worker lost'
            def _complete(self,item,result,messages,evidence,**kw):
                assert kw.get('unresolved') is True
                self.result=result
                self.pending=[]
        obj=Bridge()
        count=bridge_fn(obj,[],[],now=1900)
        self.assertEqual(count,1)
        self.assertEqual(obj.result['status'],'timed_out')
        self.assertEqual(obj.result['n1_decision'],'stop_waiting')

    def test_task_scoped_cancel_kills_only_verified_worker(self):
        import subprocess
        from unittest.mock import Mock
        ident1=str(uuid.uuid4()); ident2=str(uuid.uuid4())
        active=RedisFake(); receipts=RedisFake()
        pfx='norm:async:v1'
        receipts.data['norm:async-link:child:step-01']=json.dumps([{'id':ident1},{'id':ident2}])
        receipts.data['norm:async-link:other:step-01']=json.dumps([{'id':str(uuid.uuid4())}])
        active.data[pfx+':approved:'+ident1]=json.dumps({'approved':{'id':ident1,'task_id':'child'}})
        active.data[pfx+':approved:'+ident2]=json.dumps({'approved':{'id':ident2,'task_id':'child'}})
        active.data[pfx+':execution:'+ident1]=json.dumps({'pid':777,'state':'executing'})
        active.data[pfx+':execution:'+ident2]=json.dumps({'pid':888,'state':'executing'})
        revoked=[];killed=[]
        class App:
            def AsyncResult(self,x):return AsyncFake(False)
            control=types.SimpleNamespace(revoke=lambda x,terminate=False:revoked.append((x,terminate)))
        fake_subproc=types.SimpleNamespace(run=lambda command, **kw: (killed.append(command),types.SimpleNamespace(returncode=0))[1],
                                           CREATE_NO_WINDOW=0, DEVNULL=subprocess.DEVNULL, Popen=lambda *a,**k:None)
        module=types.ModuleType('norm_runtime.celery_shutdown')
        module._owned_workers=lambda root:[{'pid':777,'number':1}]
        module.stop_task_celery_worker=lambda root,pid,jobid,client,prefix:(killed.append(['taskkill.exe',str(pid)]),True)[1]
        original=sys.modules.get('norm_runtime.celery_shutdown')
        sys.modules['norm_runtime.celery_shutdown']=module
        try:
            f=function('core/norm_runtime/celery_tool_executor.py','cancel_task_dispatches',
              {'Path':Path,'root':lambda:'/does-not-exist','redis':lambda:active,
               'subprocess':fake_subproc,'os':types.SimpleNamespace(name='posix'),
               'app':App(),'json':json,'uuid':uuid,'CFG':{'prefix':pfx},'cancellation_key':lambda id:pfx+':cancelled:'+id,
               'key':lambda id:pfx+':approved:'+id,'logging':logging})
            result=f(receipts, ['child'])
            self.assertEqual(result['cancelled'],2)
            self.assertEqual(result['workers_stopped'],1)
            self.assertEqual(len(killed),1)
            self.assertIn('777', killed[0]);self.assertNotIn('888',killed[0])
            self.assertTrue(active.closed)
            self.assertEqual(set(revoked),{(ident1,False),(ident2,False)})
        finally:
            if original is None:sys.modules.pop('norm_runtime.celery_shutdown',None)
            else:sys.modules['norm_runtime.celery_shutdown']=original

    def test_completed_celery_job_is_not_cancelled(self):
        from unittest.mock import Mock
        ident=str(uuid.uuid4()); active=RedisFake();receipt=RedisFake();pfx='norm:async:v1'
        receipt.data['norm:async-link:child:step-01']=json.dumps([{'id':ident}])
        active.data[pfx+':approved:'+ident]=json.dumps({'approved':{'id':ident,'task_id':'child'}})
        module=types.ModuleType('norm_runtime.celery_shutdown')
        module._owned_workers=lambda root:[{'pid':2,'number':1}]
        module.stop_task_celery_worker=lambda *args:False
        old=sys.modules.get('norm_runtime.celery_shutdown');sys.modules['norm_runtime.celery_shutdown']=module
        try:
            f=function('core/norm_runtime/celery_tool_executor.py','cancel_task_dispatches',
            {'Path':Path,'root':lambda:'.','redis':lambda:active,'subprocess':Mock(),'os':types.SimpleNamespace(name='posix'),
             'app':types.SimpleNamespace(AsyncResult=lambda x:AsyncFake(True)), 'json':json,'uuid':uuid,
             'CFG':{'prefix':pfx},'key':lambda x:pfx+':approved:'+x,
             'cancellation_key':lambda x:pfx+':cancelled:'+x,'logging':logging})
            res=f(receipt,['child'])
            self.assertEqual(res['found'],1)
            self.assertEqual(res['cancelled'],0)
            self.assertNotIn(pfx+':cancelled:'+ident,active.data)
        finally:
            if old is None:sys.modules.pop('norm_runtime.celery_shutdown',None)
            else:sys.modules['norm_runtime.celery_shutdown']=old

if __name__=='__main__':unittest.main(verbosity=2)
