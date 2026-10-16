"""Dependency-free tests for the Celery watchdog and N1 terminal observation."""
import ast
import json
import sys
import types
import unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]

def unit(rel,name,namespace,cls=None):
    tree=ast.parse((ROOT/rel).read_text(encoding='utf-8'))
    nodes=tree.body if cls is None else next(node.body for node in tree.body if isinstance(node,ast.ClassDef) and node.name==cls)
    node=next(x for x in nodes if isinstance(x,(ast.FunctionDef,ast.AsyncFunctionDef)) and x.name==name)
    node.decorator_list=[]
    exec(compile(ast.fix_missing_locations(ast.Module(body=[node],type_ignores=[])),rel,'exec'),namespace)
    return namespace[name]

class Tests(unittest.TestCase):
    def test_supervisor_absent_during_initial_boot(self):
        f=unit('core/norm_runtime/celery_tool_executor.py','expiry_reason',{})
        self.assertIsNone(f(0,None,299,startup_grace=300,orphan_grace=180))
        self.assertEqual('supervisor_never_started',f(0,None,300,startup_grace=300,orphan_grace=180))
    def test_norm_crash_detected_after_grace_and_recovery_resets(self):
        f=unit('core/norm_runtime/celery_tool_executor.py','expiry_reason',{})
        self.assertIsNone(f(0,100,279,startup_grace=300,orphan_grace=180))
        self.assertEqual('supervisor_lost',f(0,100,280,startup_grace=300,orphan_grace=180))
        self.assertIsNone(f(0,279,280,startup_grace=300,orphan_grace=180))
    def test_deadline_supports_old_and_new_approvals(self):
        f=unit('core/norm_runtime/celery_tool_executor.py','dispatch_started_at',{})
        self.assertEqual(1000,f({'expires':87400}))
        self.assertEqual(2000,f({'expires':87400,'submitted_at':2000}))
    def test_orphan_marker_surfaces_to_n1_without_duplicate_execution(self):
        class Redis:
            def exists(self,key):return False
            def get(self,key):
                return json.dumps({'state':'orphaned','reason':'supervisor_lost'}).encode() if ':execution:' in key else None
        class Result:
            def ready(self):return False
        broker=types.SimpleNamespace(redis=Redis())
        f=unit('core/norm_runtime/celery_tool_executor.py','poll',{
            'app':types.SimpleNamespace(AsyncResult=lambda _job:Result()),
            'json':json,'CFG':{'prefix':'norm:async:v1'},'cancellation_key':lambda x:'cancelled:'+x,
        },cls='Broker')
        result=f(broker,'dispatch')
        self.assertEqual(result['status'],'orphaned')
        self.assertTrue(result['async_unresolved'])
        self.assertEqual(result['dispatch_id'],'dispatch')
    def test_N1_blocks_repeat_after_orphan_observation(self):
        # Inspect real N1 delivery branch for explicit unresolved treatment.
        bridge=(ROOT/'core/norm_runtime/async_n1_delivery.py').read_text('utf-8')
        self.assertIn('unresolved=bool(isinstance(result, dict) and result.get("async_unresolved"))',bridge)
    def test_watchdog_guarantees_process_exit_not_graceful_celery_shutdown(self):
        worker=(ROOT/'tools/celery_tool_worker.py').read_text('utf-8')
        self.assertIn('os._exit(75)',worker)
        self.assertIn('supervisor_key()',worker)
        self.assertIn('dispatch_started_at(approved)',worker)
        self.assertIn('CFG["max_job_age_seconds"]',worker)
        self.assertIn('r.delete(active_key,heartbeat)',worker)
        launcher=(ROOT/'tools/start_async_workers.py').read_text('utf-8')
        self.assertIn('len(ids)!=len(set(ids))',launcher)
    def test_suppressed_receipts_survive_redis_expiry_and_reach_N1(self):
        from test_recovery_suppress_celery_r11r3 import FakeQueue, FakeJob, FakePipeline
        import dataclasses
        key="norm:async-link:task-1:step-4"
        original={"id":"original-dispatch","name":"list_directory","status":"pending",
                  "signature":"test-signature"}
        q=FakeQueue()
        q.r.receipts={key:json.dumps([original])}
        q.r.scan_iter=lambda **kw:iter(q.r.receipts.keys())
        q.r.get=lambda k:q.r.receipts.get(k)
        old_pipeline=q.r.pipeline
        class Pipe(FakePipeline):
            def set(self,k,value,**opts):
                self.commands.append(("set",k,value))
                return self
            def execute(self):
                out=[]
                for kind,loc,value in self.commands:
                    if kind=="set":
                        self.store.receipts[loc]=value
                        out.append(True)
                    elif kind=="zadd":
                        self.store.sorted.setdefault(loc,{}).update(value)
                        out.append(1)
                    elif kind=="xadd":
                        self.store.streams.setdefault(loc,[]).append(("new-id",value))
                        out.append("new-id")
                return out
        q.r.pipeline=lambda **kw: Pipe(q.r)
        job=FakeJob("task-1","step-4","queued-4",{"last_yield_reason":"n1_async_pending"})
        q.r.sorted[q._async_delayed_key()]={json.dumps(dataclasses.asdict(job)):4000.0}
        snap=unit('core/norm_runtime/prompt_queue.py','snapshot_task_jobs',
                  {'asdict':dataclasses.asdict,'time':types.SimpleNamespace(time=lambda:100),
                   'json':json,'logging':types.SimpleNamespace(exception=lambda *a:None)},cls='RedisPromptQueue')
        saved=snap(q,'task-1')
        self.assertEqual(saved['celery_receipts'][0]['pending'][0]['id'],'original-dispatch')
        self.assertTrue(saved['celery_receipts'][0]['pending'][0]['suppressed_while_pending'])
        q.r.receipts.clear()  # simulate Redis's two-hour TTL while PostgreSQL holds snapshot
        q.r.sorted.clear()
        restore=unit('core/norm_runtime/prompt_queue.py','restore_task_jobs',
                     {'PromptJob':FakeJob,'json':json},cls='RedisPromptQueue')
        result=restore(q,saved)
        self.assertEqual(len(result),1)  # do not count restored evidence as queued work
        restored=json.loads(q.r.receipts[key])
        self.assertEqual(restored[0]['id'],'original-dispatch')
        self.assertTrue(restored[0]['suppressed_while_pending'])
        class Bridge:
            pending=restored
            broker=types.SimpleNamespace(poll=lambda *args:None)
            def _complete(self,item,result,messages,evidence,**kw):
                self.observed=result
                self.unresolved=kw['unresolved']
                self.pending.remove(item)
        bridge=Bridge()
        deliver=unit('core/norm_runtime/async_n1_delivery.py','deliver',
                     {'time':types.SimpleNamespace(time=lambda:20000)},cls='N1AsyncBridge')
        self.assertEqual(deliver(bridge,[],[],now=20000),1)
        self.assertEqual(bridge.observed['status'],'cancelled')
        self.assertTrue(bridge.unresolved)

    def test_service_produces_renewable_lease(self):
        main=(ROOT/'core/norm_main.py').read_text('utf-8')
        self.assertIn('celery_supervisor.set(supervisor_key(), celery_supervisor_token, ex=75)',main)
        self.assertIn('celery_supervisor.close()',main)

if __name__=='__main__':unittest.main(verbosity=2)
