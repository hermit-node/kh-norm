"""0.55.8 r4: offline regressions for durable progress and resume scoping.

No Ollama, Redis or PostgreSQL connections. Exercises production worker paths
with in-memory fakes and ensures existing multi-tool calling is untouched.
"""
from __future__ import annotations
import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'core'))
for name in ('redis', 'psycopg'):
    if name not in sys.modules:
        try:
            __import__(name)
        except ImportError:
            sys.modules[name] = types.ModuleType(name)
from norm_runtime.prompt_worker import PromptWorker
from norm_runtime.prompt_queue import PromptJob


def job(**overrides):
    meta = {'prompt_origin': 'norm_generated_step',
            'original_user_prompt': 'Restore every installer release from the original repository'}
    meta.update(overrides.pop('metadata', {}))
    return PromptJob(message_id='m', task_id='task', step_id='step-02',
                     prompt='Compare only distinct revisions in the manifest',
                     project_id='default', context='', attempt=0, created_at=0,
                     metadata=meta, chain_id='chain', previous_prompt_id='',
                     next_prompt_id='', chain_index=0, recovery_attempted=False,
                     request_type='step', **overrides)


class Queue:
    def __init__(self):
        self.yields = []
    def yield_job(self, redis_id, entry, checkpoint, *, reason, step_rounds, task_rounds):
        meta = dict(entry.metadata)
        meta['last_yield_reason'] = reason
        entry.metadata = meta
        entry.context = checkpoint
        self.yields.append((checkpoint, meta))


class Durable:
    def __init__(self):
        self.calls = []
    def dependencies_satisfied(self, *args):
        return True
    def completed_step_context(self, task_id, step_id, max_chars=None):
        self.calls.append((task_id, step_id, max_chars))
        return '[step-01] Manifest already saved and verified; do not regenerate it'
    def background_memory_context(self, **kwargs):
        return ''


class Live:
    def working_memory(self, task_id):
        return '{"task_id":"task","state":{},"events":[]}'
    def step_started(self, *_):
        pass


class Client:
    def set_crash_context(self, *_):
        pass
    def clear_crash_context(self):
        pass


def test_postgres_verified_prior_step_not_shadowed_by_redis_envelope():
    worker = PromptWorker.__new__(PromptWorker)
    worker.durable, worker.live, worker.client = Durable(), Live(), Client()
    worker.queue = Queue()
    worker._task_suppressed = lambda *_: False
    worker._heartbeat_loop = lambda *_: None
    worker._runtime_config = lambda: {}
    worker._progress_oversize_recovery = lambda *args: {'parked': False, 'context': ''}
    seen = {}
    def capture_typed(j, request_type, **kwargs):
        seen.update(kwargs)
        return 'typed prompt'
    worker._typed_prompt = capture_typed
    worker._generate_with_budget = lambda *args: None  # end before any Ollama request
    worker._process('redis-1', job())
    assert seen['task_memory'].startswith('{"task_id"')
    assert 'Manifest already saved' in seen['prior']
    assert worker.durable.calls == [('task', 'step-02', 36000)]


def test_compact_checkpoint_is_one_resume_not_forever():
    worker = PromptWorker.__new__(PromptWorker)
    worker.durable = None
    worker.queue = Queue()
    worker._runtime_config = lambda: {}
    entry = job(metadata={'compact_resume_context': True,
                          'last_yield_reason': 'context_headroom_checkpoint',
                          'context_checkpoint_remaining_fraction': .03})
    entry.context = 'distilled predecessor result and exact next action'
    assert worker._compact_resume_requested(entry)
    # This step completes another 16 tool rounds, and yields a NEW normal checkpoint.
    worker._checkpoint_yield('redis-1', entry, 'Inspect old versions', None,
                            {'checkpoint': 'new step round evidence',
                             'reason': 'step_round_limit', 'step_rounds': 16,
                             'task_rounds': 32})
    assert not worker._compact_resume_requested(entry)
    assert 'compact_resume_context' not in entry.metadata
    assert 'context_checkpoint_remaining_fraction' not in entry.metadata
    assert entry.context == 'new step round evidence'
    # Another genuine HEADROOM checkpoint can still compact once.
    worker._checkpoint_yield('redis-2', entry, 'Inspect old versions', None,
                            {'checkpoint': 'new distilled state',
                             'reason': 'context_headroom_checkpoint', 'step_rounds': 9,
                             'task_rounds': 41, 'resume_metadata': {
                                 'compact_resume_context': True,
                                 'context_checkpoint_remaining_fraction': .04}})
    assert worker._compact_resume_requested(entry)


def test_old_headroom_marker_and_manual_failure_resume_remain_supported():
    entry = job(metadata={'last_yield_reason': 'context_headroom_checkpoint',
                          'context_checkpoint_remaining_fraction': .01})
    entry.context = 'legacy distilled state'
    assert PromptWorker._compact_resume_requested(entry)
    entry.metadata['last_yield_reason'] = 'step_round_limit'
    assert not PromptWorker._compact_resume_requested(entry)
    entry.metadata.update({'manual_failure_resume': True, 'compact_resume_context': True})
    assert PromptWorker._compact_resume_requested(entry)


def test_internal_step_retains_user_goal_as_reference_not_new_user_turn():
    worker = PromptWorker.__new__(PromptWorker)
    worker._runtime_config = lambda: {}
    entry = job()
    typed = worker._typed_prompt(entry, 'step', prior='[step-01] done')
    assert 'Restore every installer release' in typed
    assert 'Compare only distinct revisions' in typed
    assert '[step-01] done' in typed
    # Ollama-message role behavior is integration-tested in voice/provenance tests.
