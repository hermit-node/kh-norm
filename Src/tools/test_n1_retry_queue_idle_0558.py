"""Read-only isolated regression tests for bounded N1 review and /queue-full.

No Ollama, network, PostgreSQL, Redis or Windows GPU tools are contacted.
"""
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'core'))
for dependency in ('psycopg', 'redis'):
    if dependency not in sys.modules:
        try:
            __import__(dependency)
        except ImportError:
            sys.modules[dependency] = types.ModuleType(dependency)

from norm_runtime.prompt_worker import PromptWorker, VerifierProtocolError
from norm_runtime.prompt_queue import PromptJob
from norm_runtime.queue_visibility import collect_queue_full
from norm_runtime import busy_status
from norm_runtime.n1_gatekeeper import N1Gatekeeper


def job():
    return PromptJob(message_id='redis1', task_id='task-1', step_id='step-1', prompt='Do original task',
        project_id='p', context='', attempt=0, created_at=0, metadata={
            'original_user_prompt': 'Do the task, preserve constraints',
            'task_user_prompt': 'Do the task', 'verification': 'All requirements and evidence verified',
        }, chain_id='chain1', previous_prompt_id='', next_prompt_id='', chain_index=0,
        recovery_attempted=False, request_type='step')


class RetryTests(unittest.TestCase):
    def worker(self, verdicts):
        worker = PromptWorker.__new__(PromptWorker)
        gate = Mock()
        gate.judge_step_answer.side_effect = verdicts
        worker.n1_gatekeeper = gate
        worker.durable = Mock()
        archived = {}
        def archive(task_id, step_id, messages, **kwargs):
            archived['messages'] = messages
            archived['reason'] = kwargs['reason']
            return 7
        worker.durable.record_context_slice.side_effect = archive
        worker.durable.context_slice.side_effect = lambda *args: {'messages': archived['messages']}
        worker.durable.completed_step_context.return_value = 'Previously completed verified step: prepare dataset'
        worker._evidence_text = Mock(return_value='deterministic verification passed')
        return worker, gate

    def test_failed_n1_checkpoints_verified_candidate_then_retries_once(self):
        w, gate = self.worker([
            {'processing_failed': True, 'reason': 'ModelOutputTruncated'},
            {'processing_failed': False, 'decision': 'accept', 'retry_instruction': ''},
        ])
        j = job()
        self.assertEqual(w._n1_step_alignment(j, 'unchanged candidate', [], verified_by_n2=True), (True, []))
        self.assertEqual(gate.judge_step_answer.call_count, 2)
        saved = w.durable.checkpoint_step.call_args.args[0]
        self.assertEqual(saved.status.value, 'running')
        self.assertIn('"context_slice_index": 7', saved.summary)
        self.assertEqual(saved.verification, 'n1-step-review:processing-retry')
        self.assertIn('unchanged candidate', w.durable.record_context_slice.call_args.args[2][2]['content'])
        self.assertIn('prepare dataset', w.durable.record_context_slice.call_args.args[2][4]['content'])
        self.assertEqual(w.durable.record_context_slice.call_args.kwargs['reason'], 'n1_step_review_processing_retry')
        self.assertEqual(w.durable.context_slice.call_count, 1)
        self.assertNotIn('review_handoff', gate.judge_step_answer.call_args_list[0].kwargs)
        handoff = gate.judge_step_answer.call_args_list[1].kwargs['review_handoff']
        self.assertIn('Previously completed verified step', handoff)
        self.assertIn('slice_index=7', handoff)
        self.assertIn('attempt 2', handoff)
        self.assertNotIn('n1_step_review_status', j.metadata)

    def test_failed_twice_passes_only_preverified_candidate_with_unavailable_status(self):
        w, gate = self.worker([
            {'processing_failed': True, 'reason': 'ModelOutputTruncated'},
            {'processing_failed': True, 'reason': 'TimeoutError'},
        ])
        j = job()
        self.assertEqual(w._n1_step_alignment(j, 'unchanged', [], verified_by_n2=True), (True, []))
        self.assertEqual(gate.judge_step_answer.call_count, 2)
        self.assertEqual(w.durable.checkpoint_step.call_count, 1)
        self.assertEqual(j.metadata['n1_step_review_status'], 'unavailable_after_2_attempts')

    def test_full_lossless_evidence_and_context_archived_before_retry(self):
        w, gate = self.worker([
            {'processing_failed': True, 'reason': 'ModelOutputTruncated'},
            {'processing_failed': False, 'decision': 'accept'},
        ])
        j = job()
        j.context = 'working context ' * 1500
        evidence = [{'tool': 'inspect', 'result': {'content': 'large result ' * 10000}}]
        w._n1_step_alignment(j, 'original verified candidate', evidence, verified_by_n2=True)
        saved = w.durable.record_context_slice.call_args.args[2]
        self.assertIn('large result ' * 10000, saved[3]['content'])
        self.assertIn('working context ' * 1500, saved[4]['content'])
        self.assertIn('original verified candidate', saved[2]['content'])
        self.assertEqual(gate.judge_step_answer.call_count, 2)
        self.assertLess(len(gate.judge_step_answer.call_args.kwargs['review_handoff']), 5500)

    def test_checkpoint_readback_failure_does_not_fake_success(self):
        w, gate = self.worker([{'processing_failed': True, 'reason': 'output truncated'}])
        w.durable.context_slice.side_effect = None
        w.durable.context_slice.return_value = {'messages': []}
        with self.assertRaisesRegex(VerifierProtocolError, 'readback did not match'):
            w._n1_step_alignment(job(), 'candidate', [], verified_by_n2=True)
        self.assertEqual(gate.judge_step_answer.call_count, 1)

    def test_n1_real_rejection_after_processing_retry_returns_specific_issues(self):
        w, gate = self.worker([
            {'processing_failed': True, 'reason': 'timeout'},
            {'processing_failed': False, 'decision': 'retry', 'retry_instruction': 'Verify item 3'},
        ])
        ok, issues = w._n1_step_alignment(job(), 'draft', [], verified_by_n2=True)
        self.assertFalse(ok)
        self.assertIn('Verify item 3', issues[0])

    def test_no_failed_open_when_n2_not_verified(self):
        w, gate = self.worker([{'processing_failed': True, 'reason': 'timeout'}])
        with self.assertRaises(VerifierProtocolError):
            w._n1_step_alignment(job(), 'draft', [], verified_by_n2=False)
        self.assertEqual(gate.judge_step_answer.call_count, 1)
        w.durable.checkpoint_step.assert_not_called()

    def test_default_n1_budget_is_bounded_and_reasoning_on(self):
        class Client:
            def __init__(self):self.kwargs=[]
            def generate(self, prompt, **kwargs):
                self.kwargs.append(kwargs)
                return '{"decision":"accept","reason":"ok","retry_instruction":""}'
        c=Client()
        gate=N1Gatekeeper(c, Mock(), profiles={'step_review':{'num_predict':86000,'timeout_seconds':86400,'think':True}})
        result=gate.judge_step_answer(original_request='Task',task_request='Task',step_prompt='Step',verification_requirement='Done',candidate='Done')
        self.assertEqual(result['decision'],'accept')
        self.assertEqual(c.kwargs[0]['num_predict'], 10000)
        self.assertEqual(c.kwargs[0]['timeout_seconds'], 300.0)
        self.assertTrue(c.kwargs[0]['think'])

    def test_checkpoint_summary_reaches_fresh_n1_model_call(self):
        class Client:
            def __init__(self): self.prompts = []
            def generate(self, prompt, **kwargs):
                self.prompts.append(prompt)
                return '{"decision":"accept","reason":"rechecked","retry_instruction":""}'
        client = Client()
        gate = N1Gatekeeper(client, Mock())
        result = gate.judge_step_answer(
            original_request='Do task', task_request='Task', step_prompt='Step',
            verification_requirement='Verify it', candidate='Already checked',
            evidence='Evidence', review_handoff='Lossless archive at slice_index=7; earlier work completed',
        )
        self.assertEqual(result['decision'], 'accept')
        self.assertIn('Lossless archive at slice_index=7', client.prompts[0])
        self.assertIn('ORIGINAL USER PROMPT:\nDo task', client.prompts[0])

    def test_budget_extension_900_is_separate_profile_and_configurable(self):
        gate = N1Gatekeeper(Mock(), Mock(), profiles={'task_budget_review': {'num_predict': 3000, 'think': True}})
        profile = gate._profile('task_budget_review', num_predict=900, timeout_seconds=300)
        self.assertEqual(profile['num_predict'], 3000)
        self.assertTrue(profile['think'])


class QueueTests(unittest.TestCase):
    def test_all_revivable_locations_are_visible(self):
        class Redis:
            def xrange(self, stream, **kwargs):
                return {'work':[('1',{'task_id':'a','step_id':'s','attempt':'0'})],
                        'retry':[('2',{'task_id':'b','step_id':'s','attempt':'1'})],
                        'esc':[('3',{'task_id':'c','step_id':'s','attempt':'3'})],
                        'dead':[('4',{'task_id':'d','step_id':'s','attempt':'4'})]}[stream]
            def xpending_range(self, *args): return [{'message_id':'1'}]
        queue=types.SimpleNamespace(r=Redis(),stream='work',retry_stream='retry',escalation_stream='esc',dead_letter_stream='dead',group='g')
        durable=Mock()
        durable.running_tasks.return_value=[{'task_id':'a','title':'Running A'},{'task_id':'e','title':'Orphan running E'}]
        durable.recent_failed_tasks.return_value=[{'task_id':'f','title':'Recoverable F','resumable':True},{'task_id':'h','resumable':False}]
        durable.suppressed_tasks.return_value=[{'task_id':'g','title':'Suppressed G'}]
        found=collect_queue_full(prompt_queue=queue,durable=durable,maintenance={'active':False})
        self.assertEqual(found['status'],'ok')
        by_id={row['task_id']:row for row in found['tasks']}
        self.assertEqual(set(by_id),set('abcdefgh'))
        self.assertEqual(by_id['f']['state'],'failed-resumable')
        self.assertIn('worker:parked-retry',by_id['b']['locations'])
        self.assertIn('postgres:suppressed',by_id['g']['locations'])
        self.assertEqual(by_id['a']['state'],'running')

    def test_partial_backend_failure_not_reported_as_empty_success(self):
        result=collect_queue_full(prompt_queue=None,durable=None)
        self.assertEqual(result['status'],'partial')
        self.assertEqual(len(result['errors']),2)


class ResourceTests(unittest.TestCase):
    def test_gpu_memory_controller_activity_counts_when_model_present(self):
        prev={'monotonic':0.0,'cpu_times':{1:0}}
        cur={'monotonic':0.25,'cpu_times':{1:0},'gpu_pct':2.0,
             'gpu_memory_activity_pct':35.0,'model_processes':[{'pid':1,'name':'ollama.exe'}]}
        vote=busy_status._interval_vote(prev,cur)
        self.assertTrue(vote['gpu_active'])
        self.assertTrue(vote['active'])

    def test_short_single_spike_is_not_sustained_resource_activity(self):
        snapshots=iter([{'v':i} for i in range(4)])
        votes=iter([{'active':True},{'active':False},{'active':False}])
        with patch.object(busy_status,'_resource_snapshot',side_effect=lambda: next(snapshots)),\
             patch.object(busy_status,'_interval_vote',side_effect=lambda *args: next(votes)),\
             patch.object(busy_status.time,'sleep',return_value=None):
            result=busy_status.sample_resource_activity()
        self.assertEqual(result['sample_count'],3)
        self.assertFalse(result['busy'])


if __name__=='__main__':unittest.main()
