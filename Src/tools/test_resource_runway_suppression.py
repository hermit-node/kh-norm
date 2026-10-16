"""Regression: resource-only timeout cannot resurrect operator-suppressed ingress.

Uses an isolated fake Redis and clock; never contacts Ollama/DB3/live Norm.
"""
from __future__ import annotations

import ast
import importlib.util
import json
import sys
import threading
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if 'redis' not in sys.modules:
    try:
        import redis
    except ImportError:
        redis = ModuleType('redis')
        redis.Redis = object
        redis.ResponseError = type('ResponseError', (Exception,), {})
        redis.exceptions = SimpleNamespace(LockError=RuntimeError)
        sys.modules['redis'] = redis

spec = importlib.util.spec_from_file_location('norm_ingress_runway_test', ROOT / 'core/norm_runtime/prompt_ingress.py')
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


class Clock:
    def __init__(self):
        self.now = 0.0
    def monotonic(self):
        return self.now


class Event:
    def __init__(self, clock):
        self.clock = clock
    def clear(self):
        pass
    def wait(self, seconds):
        self.clock.now += seconds


class RedisFake:
    def __init__(self):
        self.blocked = set()
        self.dispatching = {}
        self.stream = {'1-0': {'kind': 'prompt', 'prompt_id': 'uuid-1', 'message': '64'}}
        self.pending = {'1-0'}
        self.acks = []
        self.deletes = []
        self.uncertain = {}
        self.set_removed = []
    def sismember(self, key, name):
        return name in self.blocked
    def smembers(self, key):
        return set(self.blocked)
    def sadd(self, key, *values):
        self.blocked.update(values)
    def srem(self, key, *values):
        self.set_removed.extend(values)
        self.blocked.difference_update(values)
    def hset(self, key, entry, raw):
        (self.uncertain if key=='uncertain' else self.dispatching)[entry] = raw
    def hgetall(self, key):
        return dict(self.uncertain if key=='uncertain' else self.dispatching)
    def hdel(self, key, *ids):
        target = self.uncertain if key=='uncertain' else self.dispatching
        for entry in ids:
            target.pop(entry, None)
    def xrange(self, key, min='-', max='+', count=None):
        if min != '-':
            return [(min, self.stream[min])] if min in self.stream else []
        return list(self.stream.items())
    def xack(self, stream, group, entry):
        self.acks.append(entry)
        self.pending.discard(entry)
        return 1
    def xdel(self, stream, entry):
        self.deletes.append(entry)
        return bool(self.stream.pop(entry, None))
    def xpending_range(self, stream, group, start, end, count):
        return [{'message_id': x} for x in self.pending]
    def xinfo_groups(self, stream):
        return [{'name':'group', 'last-delivered-id':'1-0'}]


def make_dispatcher(redis=None):
    fake = redis or RedisFake()
    worker = mod.GuiPromptDispatcher.__new__(mod.GuiPromptDispatcher)
    worker.redis = fake
    worker.stream = 'ingress'
    worker.group = 'group'
    worker.dispatching_key = 'dispatching'
    worker.uncertain_key = 'uncertain'
    worker.suppressed_prompt_ids_key = 'suppressed'
    worker.dispatch_active = threading.Event()
    worker.stop_event = threading.Event()
    worker.pause_dispatch = threading.Event()
    worker._activity_lock = threading.RLock()
    worker._last_activity = 0.0
    worker._last_busy_probe = 0.0
    worker._probed_activity = -1.0
    worker.activity_wakeup = threading.Event()
    worker._now_iso = lambda: '2026-10-08T00:00:00+00:00'
    worker.resource_activity_grace_seconds = 30.0
    return worker, fake


def test_resource_only_grace():
    dispatcher, db = make_dispatcher()
    clock = Clock()
    dispatcher.activity_wakeup = Event(clock)
    dispatcher._busy_probe_due = lambda: True
    dispatcher._busy_payload = lambda: {'busy': True, 'phase': 'resource_activity'}
    with patch.object(mod.time, 'monotonic', clock.monotonic):
        assert dispatcher._wait_until_idle('uuid-1') is True
    assert 30 <= clock.now < 31, clock.now
    print('PASS resource activity allows dispatch after 30s')


def test_tracked_activity_never_times_out():
    dispatcher, db = make_dispatcher()
    clock = Clock()
    dispatcher.activity_wakeup = Event(clock)
    dispatcher._busy_probe_due = lambda: True
    def get_busy():
        return {'busy': True, 'phase':'worker_execution'} if clock.now < 50 else {'busy':False,'phase':'idle'}
    dispatcher._busy_payload = get_busy
    with patch.object(mod.time, 'monotonic', clock.monotonic):
        assert dispatcher._wait_until_idle('uuid-1') is True
    assert 50 <= clock.now < 51
    print('PASS tracked busy always blocks even beyond 30s')


def test_suppression_during_resource_wait():
    dispatcher, db = make_dispatcher()
    clock = Clock()
    class SuppressingEvent(Event):
        def wait(self, seconds):
            super().wait(seconds)
            if clock.now >= 12:
                db.sadd('suppressed', 'uuid-1')
    dispatcher.activity_wakeup = SuppressingEvent(clock)
    dispatcher._busy_probe_due = lambda: True
    dispatcher._busy_payload = lambda: {'busy': True, 'phase': 'resource_activity'}
    with patch.object(mod.time, 'monotonic', clock.monotonic):
        assert dispatcher._wait_until_idle('uuid-1') is False
    assert 12 <= clock.now < 13
    print('PASS suppression during idle wait cancels admission')


def test_suppression_after_wait_before_http():
    dispatcher, db = make_dispatcher()
    # Operator presses suppress immediately after the last status sample.
    dispatcher._wait_until_idle = lambda prompt_id: (db.sadd('suppressed', prompt_id), True)[1]
    def reject(*args):
        raise AssertionError('No thread or HTTP should be created')
    dispatcher._ensure_current_thread = reject
    dispatcher._dispatch_prompt('1-0', db.stream['1-0'])
    assert db.acks == ['1-0'] and db.deletes == ['1-0']
    assert not dispatcher.dispatch_active.is_set()
    print('PASS late runway suppression grounds prompt before HTTP')


def test_flush_preserves_tombstone():
    fake = RedisFake()
    fake.blocked.add('uuid-1')
    syntax = ast.parse((ROOT / 'core/norm_main.py').read_text(encoding='utf-8'))
    f = next(node for node in syntax.body if isinstance(node, ast.FunctionDef) and node.name == '_flush_console_suppressed_state')
    # Narrowly compile only the flush function to avoid importing live runtime.
    ns = {'json': json, 'logging': __import__('logging'), 'redis': SimpleNamespace(Redis=None)}
    exec(compile(ast.Module(body=[f], type_ignores=[]), str(ROOT / 'core/norm_main.py'), 'exec'), ns)
    cfg={'console_queue': {'console_ingress_stream':'ingress', 'console_ingress_group':'group',
                          'console_dispatching_key':'dispatching','console_uncertain_key':'uncertain',
                          'console_suppressed_prompt_ids_key':'suppressed'}}
    result = ns['_flush_console_suppressed_state'](cfg,'console_queue',default_prefix='norm:gui',client_factory=lambda **kwargs:fake)
    assert result['stream_deleted']==1, result
    assert '1-0' not in fake.stream
    assert 'uuid-1' in fake.blocked, 'flush erased cancellation authorization'
    assert not fake.set_removed

    # A claimed preflight/runway entry should also disappear from the visible
    # stream immediately, without losing the suppression marker that its live
    # dispatcher still needs to read.
    waiting = RedisFake()
    waiting.blocked.add('uuid-1')
    waiting.dispatching['1-0'] = json.dumps({'prompt_id':'uuid-1', 'phase':'awaiting_idle'})
    result = ns['_flush_console_suppressed_state'](cfg, 'console_queue', default_prefix='norm:gui', client_factory=lambda **kwargs: waiting)
    assert result['stream_deleted']==1 and not waiting.stream, result
    assert 'uuid-1' in waiting.blocked, 'runway cancellation was undone'
    assert '1-0' in waiting.dispatching, 'live preflight ownership erased before dispatcher sees cancellation'

    # In contrast, an HTTP request that already started retains its delivery
    # record until its owner finishes so a late result cannot get lost.
    flying = RedisFake()
    flying.blocked.add('uuid-1')
    flying.dispatching['1-0'] = json.dumps({'prompt_id':'uuid-1', 'phase':'http_in_flight'})
    result = ns['_flush_console_suppressed_state'](cfg, 'console_queue', default_prefix='norm:gui', client_factory=lambda **kwargs: flying)
    assert result['stream_deleted']==0 and '1-0' in flying.stream and 'uuid-1' in flying.blocked
    print('PASS flush deletes queued/runway delivery but retains HTTP records and all tombstones')

if __name__ == '__main__':
    test_resource_only_grace()
    test_tracked_activity_never_times_out()
    test_suppression_during_resource_wait()
    test_suppression_after_wait_before_http()
    test_flush_preserves_tombstone()
    print('ALL RUNWAY SUPPRESSION TESTS PASSED')
