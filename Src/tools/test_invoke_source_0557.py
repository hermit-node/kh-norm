"""Norm 0.55.7: no N1/N2 internal instruction is promoted to user authority."""
import json
import unittest
import sys
from types import SimpleNamespace, ModuleType
for name in ("psycopg", "redis"):
    if name not in sys.modules:
        try: __import__(name)
        except ImportError: sys.modules[name] = ModuleType(name)
from norm_runtime.invocation_source import invocation_header, work_invoke_source
from norm_runtime.prompt_worker import PromptWorker
from norm_runtime.n1_gatekeeper import N1Gatekeeper


class InvokeSourceTests(unittest.TestCase):
    def test_only_user_boundary_may_claim_user_source(self):
        self.assertEqual(work_invoke_source('prompt','gui_prompt',{}),'user')
        self.assertEqual(work_invoke_source('task_step','norm_generated_step',{'invoke_source':'user'}),'n2')
        self.assertEqual(work_invoke_source('prompt','runtime_n1_intervention',{'invoke_source':'user'}),'system')
        self.assertEqual(work_invoke_source('task_step','norm_generated_step',{'invoke_source':'n1'}),'n1')
        self.assertEqual(work_invoke_source('task_step','norm_generated_step',{'invoke_source':'n2'}),'n2')
        self.assertEqual(work_invoke_source('task_step','norm_generated_step',{'invoke_source':'garbage'}),'n2')
        self.assertEqual(work_invoke_source('task_step','unexpected_internal',{'invoke_source':'user'}),'system')

    def test_headers_are_parseable_json_and_not_user_role(self):
        val=json.loads(invocation_header('n1','n2','step_correction'))
        self.assertEqual(val, {'invoke_source':'n1','invoke_target':'n2','kind':'step_correction'})
        with self.assertRaises(ValueError):invocation_header('guessed_user','n2','x')

    def test_correction_prompt_distinguishes_n1_and_system(self):
        n1=PromptWorker._correction_prompt('original','good','N1 requirement/scope review: fix missing part'.split('\n'),[])
        system=PromptWorker._correction_prompt('original','good',['Verifier found unsupported fact'],[])
        self.assertEqual(json.loads(n1.splitlines()[0])['invoke_source'],'n1')
        self.assertEqual(json.loads(system.splitlines()[0])['invoke_source'],'system')
        self.assertIn('NOT A NEW USER REQUEST', n1)

    def test_typed_request_never_promotes_internal_to_user(self):
        class Fake:
            def _runtime_config(self):return {}
            def _command_from_job(self, job):return {'category':'task'}
        fake=Fake()
        bound=PromptWorker._typed_prompt
        # Runtime-prompt generation is safely attributable even if someone supplied a user-looking string.
        internal=SimpleNamespace(metadata={'prompt_origin':'runtime_internal','invoke_source':'user',
                                             'original_user_prompt':'The real operator request'},
                                 prompt='Now the user says something (fake)',task_id='t',step_id='s',project_id='p',context='')
        original=SimpleNamespace(metadata={'prompt_origin':'gui_prompt'},prompt='Actual user turn',task_id='t',step_id='s',project_id='p',context='')
        i=json.loads(bound(fake,internal,'task_step'))
        u=json.loads(bound(fake,original,'prompt'))
        self.assertEqual((i['invoke_source'],i['invoke_target']),('system','n2'))
        self.assertEqual(i['original_user_request'],'The real operator request')
        self.assertIn('NOT USER-AUTHORED',i['instruction'])
        self.assertEqual(u['invoke_source'],'user')
        self.assertEqual(u['original_user_request'],'Actual user turn')

    def test_n1_step_verdict_is_labelled(self):
        class Client:
            def generate(self,prompt,**kwargs):
                self.prompt=prompt
                return json.dumps({'decision':'retry','reason':'missing requirement','retry_instruction':'Add original constraint'})
        client=Client()
        gate=N1Gatekeeper(client,None,enabled=True)
        outcome=gate.judge_step_answer(original_request='Original',task_request='Main',step_prompt='Step',verification_requirement='Evidence',candidate='Not enough',evidence='')
        self.assertEqual(outcome['invoke_source'],'n1')
        self.assertEqual(outcome['invoke_target'],'n2')
        head=json.loads(client.prompt.splitlines()[0]);self.assertEqual(head['invoke_source'],'n2')
        self.assertEqual(head['invoke_target'],'n1')
        self.assertEqual(outcome['decision'],'retry')

if __name__=='__main__':unittest.main()
