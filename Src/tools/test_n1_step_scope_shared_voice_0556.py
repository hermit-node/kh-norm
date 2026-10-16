"""0.55.6: N1 scope review of EACH step plus one shared active voice profile."""
from __future__ import annotations
import json, sys, types, unittest
from pathlib import Path
from unittest.mock import Mock, patch
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'core'))
for dep in ('psycopg','redis'):
    if dep not in sys.modules:
        try: __import__(dep)
        except ImportError: sys.modules[dep] = types.ModuleType(dep)
from norm_runtime.n1_gatekeeper import N1Gatekeeper
from norm_runtime.prompt_worker import PromptWorker, VerifierProtocolError
from norm_runtime.prompt_queue import PromptJob
from norm_runtime.ollama_client import OllamaClient

class FakeClient:
    def __init__(self, responses):
        self.responses=list(responses); self.prompts=[]
    def generate(self, prompt, **kwargs):
        self.prompts.append((prompt,kwargs))
        return json.dumps(self.responses.pop(0))

def job():
    return PromptJob(message_id='x',task_id='t',step_id='step-02',
        prompt='[NORM GENERATED STEP] Update one config file only', project_id='p',
        context='',attempt=0,created_at=0,metadata={
            'original_user_prompt':'Fix X without changing Y',
            'task_user_prompt':'Fix X',
            'verification':'X fixed; Y untouched'},
        chain_id='c',previous_prompt_id='',next_prompt_id='',chain_index=0,
        recovery_attempted=False,request_type='step')

class AlignmentTests(unittest.TestCase):
    def test_n1_compares_all_three_layers_and_rejects_missing_requirement(self):
        client=FakeClient([{'decision':'retry','reason':'Y altered','retry_instruction':'Restore Y and re-verify it'}])
        gate=N1Gatekeeper(client, Mock())
        result=gate.judge_step_answer(original_request='Fix X without changing Y',task_request='Fix X',
            step_prompt='Change X only',verification_requirement='Y must remain untouched',
            candidate='X changed and Y overwritten',evidence='changed Y')
        self.assertEqual(result['decision'],'retry')
        prompt=client.prompts[0][0]
        for part in ['Fix X without changing Y','Fix X','Change X only']:
            self.assertIn(part,prompt)
        # N1 must judge the original user's constraint independently.
        # This N2-generated verification string is historical only and never
        # an authoritative acceptance checklist.
        self.assertNotIn('Y must remain untouched',prompt)
        self.assertIn('Restore Y',result['retry_instruction'])

    def test_n1_accepts_good_step_without_rewriting_it(self):
        gate=N1Gatekeeper(FakeClient([{'decision':'accept','reason':'all good','retry_instruction':''}]), Mock())
        worker=PromptWorker.__new__(PromptWorker)
        worker.n1_gatekeeper=gate
        worker._evidence_text=Mock(return_value='verified X, Y unchanged')
        self.assertEqual(worker._n1_step_alignment(job(),'Original text preserved',[{'result':{'ok':True}}]),(True,[]))

    def test_n1_returns_specific_repair_to_existing_step_verifier_loop(self):
        gate=N1Gatekeeper(FakeClient([{'decision':'retry','reason':'Y changed','retry_instruction':'Restore Y'}]),Mock())
        worker=PromptWorker.__new__(PromptWorker);worker.n1_gatekeeper=gate
        worker._evidence_text=Mock(return_value='Y modified')
        worker._request_verdict=Mock(return_value=(True,[],json.dumps({'verdict':'accept','issues':[]})))
        worker._record_verifier_attempt=Mock()
        worker._generate_complete_text=Mock(return_value='X fixed. Y restored.')
        worker._correction_prompt=Mock(return_value='Repair Y only')
        # First rejected by N1, second accepted by N1 (replace client response)
        gate.client.responses=[{'decision':'retry','reason':'Y changed','retry_instruction':'Restore Y'},
                               {'decision':'accept','reason':'Y restored','retry_instruction':''}]
        answer=worker._verify_and_correct_without_tools(job(),'Fix X without touching Y','X fixed, Y changed')
        self.assertEqual(answer,'X fixed. Y restored.')
        self.assertEqual(worker._generate_complete_text.call_count,1)
        self.assertEqual(worker._request_verdict.call_count,2)

    def test_n1_transport_failure_cannot_accept_unreviewed_step(self):
        class Broken:
            def generate(self,*a,**k):raise RuntimeError('offline')
        gate=N1Gatekeeper(Broken(),Mock())
        worker=PromptWorker.__new__(PromptWorker);worker.n1_gatekeeper=gate
        worker._evidence_text=Mock(return_value='')
        with self.assertRaises(VerifierProtocolError):worker._n1_step_alignment(job(),'unverified',[])

    def test_same_validated_voice_flows_to_n1_review_as_system_not_user_message(self):
        client=OllamaClient('http://127.0.0.1:11434', model='norm')
        captured=[]
        class Transport:
            def __enter__(self):return iter([json.dumps({'response':json.dumps({'decision':'accept','reason':'ok','retry_instruction':''}),'done':True}).encode()+b'\n'])
            def __exit__(self,*args):return False
        def fake(req,**kwargs):
            captured.append(json.loads(req.data))
            return Transport()
        gate=N1Gatekeeper(client, Mock(), runtime_root='/fake/root')
        with patch('norm_runtime.n1_gatekeeper.load_active_voice_context', return_value='COCKNEY_STYLE_MARKER'), \
             patch.object(client,'_urlopen', side_effect=fake):
            outcome=gate.judge_step_answer(original_request='Fix X',task_request='Fix X',step_prompt='Do X',
                    verification_requirement='Test X',candidate='X done')
        self.assertEqual(outcome['decision'],'accept')
        self.assertIn('COCKNEY_STYLE_MARKER',captured[0]['system'])
        self.assertIn('Fix X',captured[0]['prompt'])
        self.assertNotIn('COCKNEY_STYLE_MARKER',captured[0]['prompt'])
        self.assertEqual(gate.runtime_root, '/fake/root')

if __name__=='__main__':unittest.main()
