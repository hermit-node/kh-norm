"""0.55.8: read-only offline regression of N1 review routing, progress & repetition.

Uses real source methods, fake IO, no Ollama/Redis/Postgres or Windows calls.
"""
from __future__ import annotations
import ast
import json
import logging
import sys
import types
import unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'core'))
from norm_runtime.n1_gatekeeper import N1Gatekeeper
if 'redis' not in sys.modules:
    redis_stub=types.ModuleType('redis');redis_stub.Redis=object;redis_stub.ResponseError=RuntimeError
    sys.modules['redis']=redis_stub
if 'psycopg' not in sys.modules:
    pg_stub=types.ModuleType('psycopg');pg_stub.Error=RuntimeError
    sys.modules['psycopg']=pg_stub
from norm_runtime.prompt_queue import PromptJob
from norm_runtime.prompt_worker import PromptWorker

class FakeLive:
    def validation_candidates(self,**kw):return []
    def invalidate_validation_target(self,target):return 0
class DummyClient:
    def generate(self,*args,**kw):raise AssertionError('unexpected review model request')

class GateTests(unittest.TestCase):
    def test_git_log_format_variations_trigger_same_bounded_loop_guard(self):
        gate=N1Gatekeeper(DummyClient(),FakeLive())
        variants=[
            'git log --oneline --all',
            'git log --all --oneline --pretty=format:"%h %s"',
            'git log --oneline --all --format="%h %s"',
        ]
        for index,cmd in enumerate(variants):
            outcome=gate.before_tool(task_id='task',step_id='step',name='run_command',
                                     arguments={'command':cmd,'cwd':'D:/project'})
            self.assertEqual(outcome.execute,index<2,(index,cmd,outcome.result))
            if outcome.execute:
                gate.after_tool(task_id='task',step_id='step',name='run_command',outcome=outcome,
                                result={'ok':True,'stdout':'one tag'})
            else:
                self.assertTrue(outcome.result.get('n1_loop_blocked'))
                self.assertIn('git log',outcome.reset_instruction)
        # A semantically distinct git log question may still run.
        outcome=gate.before_tool(task_id='task',step_id='step',name='run_command',
                                 arguments={'command':'git log --grep="different commit"','cwd':'D:/project'})
        self.assertTrue(outcome.execute)
        # Generic commands are not normalized into repeat classes.
        self.assertEqual(gate._inspection_signature('task','step','run_command',{'command':'del file.txt'}),'')

    def test_identical_reads_are_bounded_different_chunks_remain_allowed(self):
        gate=N1Gatekeeper(DummyClient(),FakeLive())
        for i in range(3):
            o=gate.before_tool(task_id='t',step_id='s',name='read_file',arguments={'path':'D:/project/log.txt','start_byte':0})
            self.assertEqual(o.execute,i<2)
        o=gate.before_tool(task_id='t',step_id='s',name='read_file',arguments={'path':'D:/project/log.txt','start_byte':1000})
        self.assertTrue(o.execute)
        # Successful mutation clears information-loop counters, not security rules.
        write=gate.before_tool(task_id='t',step_id='s',name='write_file',arguments={'path':'D:/project/log.txt','content':'updated'})
        self.assertTrue(write.execute)
        gate.after_tool(task_id='t',step_id='s',name='write_file',outcome=write,result={'ok':True,'path':'D:/project/log.txt'})
        o=gate.before_tool(task_id='t',step_id='s',name='read_file',arguments={'path':'D:/project/log.txt','start_byte':0})
        self.assertTrue(o.execute)

    def test_stalled_review_has_no_automatic_child_generation(self):
        class Client:
            def generate(self,prompt,**kwargs):
                self.prompt=prompt;self.kwargs=kwargs
                return json.dumps({'decompose':False,'reason':'tag evidence now advancing','next_action':'inspect relevant tag diff'})
        client=Client(); gate=N1Gatekeeper(client,FakeLive())
        result=gate.review_stalled_step(task_id='task',step_id='step',original_request='find old installers',
                                       step_request='read mixed history',checkpoint='NO dedicated commits found',round_count=32)
        self.assertFalse(result['decompose'])
        self.assertIn('tag diff',result['next_action'])
        self.assertIn('NO dedicated commits',client.prompt)
        self.assertTrue(client.kwargs['think'])

class ContextRetentionTests(unittest.TestCase):
    def test_full_current_slice_archived_and_previous_checkpoint_injected(self):
        class Queue:consumer='test'
        class Durable:
            def __init__(self):self.records={};self.next=0
            def record_context_slice(self,task_id,step_id,messages,**details):
                self.next+=1;self.records[self.next]={'messages':json.loads(json.dumps(messages))}
                return self.next
            def context_slice(self,task_id,step_id,slice_index):return self.records[slice_index]
        class Gate:
            def distill_continuation_checkpoint(self,**kwargs):
                self.received=kwargs
                return 'PRESERVED FACTS and recorded next action'
        class Chunks:
            def retained_summary(self):return 'file A scanned'
            def snapshot_streams(self):return []
        durable=Durable();gate=Gate()
        worker=PromptWorker(Queue(),DummyClient(),FakeLive(),durable,n1_gatekeeper=gate)
        worker._runtime_config=lambda:{'context_budget':{'checkpoint_archive_messages':8}}
        job=PromptJob(message_id='1-0',task_id='task',step_id='step',prompt='inspect',
                      project_id='default',context='PREVIOUS NEGATIVE FACT: no dedicated commits',
                      attempt=0,created_at=0,metadata={'original_user_prompt':'reconstruct installers'},
                      chain_id='chain',previous_prompt_id='',next_prompt_id='',chain_index=0,recovery_attempted=False)
        messages=[{'role':'assistant','content':f'observed item {i}'} for i in range(14)]
        result,metadata=worker._distilled_continuation_checkpoint(
            job,'step prompt',messages,reason='step_round_limit',headroom={},chunk_manager=Chunks(),
            step_rounds=14,task_rounds=32)
        self.assertEqual(len(durable.records[1]['messages']),14)
        self.assertEqual(len(gate.received['archived_messages']),8)
        self.assertIn('PREVIOUS NEGATIVE FACT',gate.received['prior_checkpoint'])
        self.assertTrue(metadata['compact_resume_context'])
        self.assertEqual(metadata['postgres_context_slice_index'],1)
        self.assertIn('PRESERVED FACTS',result)

    def test_raw_duplicate_observations_not_counted_as_progress(self):
        class Queue:consumer='test'
        class Live(FakeLive):
            def task_evidence(self,task_id):
                observation={'tool':'read_file','arguments':{'path':'D:/file.txt'},
                             'result':{'ok':True,'path':'D:/file.txt','sha256':'same'}}
                return [observation,observation]
        worker=PromptWorker(Queue(),DummyClient(),Live(),None)
        job=PromptJob(message_id='1-0',task_id='task',step_id='step',prompt='inspect',
                      project_id='default',context='',attempt=0,created_at=0,metadata={},chain_id='chain',
                      previous_prompt_id='',next_prompt_id='',chain_index=0,recovery_attempted=False)
        metrics,_,_=worker._task_progress_snapshot(job)
        self.assertEqual(metrics['evidence_count'],2)
        self.assertEqual(metrics['distinct_observations'],1)
        self.assertEqual(metrics['repeated_observations'],1)

class ActualPlanRoutingTests(unittest.TestCase):
    def test_structured_plan_verifier_uses_gatekeeper_client_not_n2(self):
        source=(ROOT/'core/norm_runtime/conversation_service.py').read_text('utf-8')
        parent=next(n for n in ast.parse(source).body if isinstance(n,ast.ClassDef) and n.name=='ConversationService')
        method=next(n for n in parent.body if isinstance(n,ast.FunctionDef) and n.name=='_structured_generate')
        definition=ast.fix_missing_locations(ast.Module(body=[ast.ClassDef(name='Harness',bases=[],keywords=[],body=[method],decorator_list=[])],type_ignores=[]))
        ns={'json':json,'invocation_header':lambda *a:'HEADER','load_active_voice_context':lambda p:'',
            'OllamaClient':object}
        exec(compile(definition,'<real-structured-generate>','exec'),ns)
        class Client:
            def __init__(self):self.called=0
            def generate(self,prompt,**kw):self.called+=1;return '{"accepted":true,"issues":[]}'
            def parse_json(self,raw):return json.loads(raw)
        n2=Client();n1=Client();s=ns['Harness']();s.ollama=n2;s.n1_gatekeeper=type('Gate',(),{'client':n1})();s.runtime_root=None
        out=s._structured_generate('review plan',{'type':'object'},num_predict=1600,think=True,invoke_source='n2',invoke_target='n1')
        self.assertTrue(out['accepted']);self.assertEqual(n1.called,1);self.assertEqual(n2.called,0)
        s._structured_generate('draft',{'type':'object'},num_predict=1600,invoke_target='n2')
        self.assertEqual(n2.called,1)

if __name__=='__main__':unittest.main(verbosity=2)
