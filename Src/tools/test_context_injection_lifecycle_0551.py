"""Isolated lifecycle tests (do not require PostgreSQL or Ollama)."""
import ast
import json
import logging
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]


def _worker_methods():
    source = ast.parse((ROOT / 'core/norm_runtime/prompt_worker.py').read_text(encoding='utf-8'))
    cls = next(x for x in source.body if isinstance(x, ast.ClassDef) and x.name == 'PromptWorker')
    names = {'_consume_context_injections', '_context_injection_block',
             '_prompt_with_context_injections', '_retire_verified_injections',
             '_active_voice_instructions'}
    nodes = [x for x in cls.body if isinstance(x, (ast.FunctionDef, ast.AsyncFunctionDef)) and x.name in names]
    cls_stub = ast.ClassDef(name='WorkerStub', bases=[], keywords=[], body=nodes, decorator_list=[])
    module = ast.fix_missing_locations(ast.Module(body=[ast.ImportFrom(module='__future__', names=[ast.alias(name='annotations')], level=0), cls_stub], type_ignores=[]))
    scope = {'json': json, 'logging': logging, 'load_active_voice_context': lambda x: 'This is Cockney.'}
    exec(compile(module, '<isolated-worker-methods>', 'exec'), scope)
    return scope['WorkerStub']


class Injections:
    def __init__(self):
        self.items = [
            {'id': 54, 'content': 'Use text-only extraction', 'retire_when_satisfied': True},
            {'id': 55, 'content': 'Do not modify the original archive', 'retire_when_satisfied': False},
        ]
        self.events = []

    def read(self, task, after=0):
        return [dict(x) for x in self.items if x['id'] > after]

    def retire(self, task, iid, *, step_id='', reason='', verified=False):
        assert task == 'job-1' and verified
        match = next((x for x in self.items if x['id'] == iid and x['retire_when_satisfied']), None)
        if match:
            self.items.remove(match)
            self.events.append((iid, step_id))
        return {'retired': bool(match)}


def test_autoretire_only_verified_active_eligible():
    cls = _worker_methods()
    obj = cls()
    obj.context_injections = Injections()
    obj._runtime_root = lambda: ROOT
    job = SimpleNamespace(task_id='job-1', step_id='step-02', metadata={})
    assert len(obj._consume_context_injections(job)) == 2
    assert 'Injection 54 [retire_when_satisfied]' in obj._prompt_with_context_injections(job, 'prompt')
    assert obj._active_voice_instructions().startswith('ACTIVE VOICE PROFILE FOR N2')
    assert len(obj._consume_context_injections(job)) == 0  # no duplicate delivery
    report = json.dumps({'verdict': 'accept', 'fulfilled_injection_ids': [54, 55, 9999]})
    obj._retire_verified_injections(job, report, evidence=[{'result': {'ok': True}}])
    assert obj.context_injections.events == [(54, 'step-02')]
    active = obj._prompt_with_context_injections(job, 'prompt')
    assert 'text-only' not in active
    assert 'Do not modify' in active
    assert len(job.metadata['applied_context_injections']) == 1


def test_nondeclarative_and_unverified_cannot_retire():
    cls = _worker_methods()
    obj = cls()
    obj.context_injections = Injections()
    obj._runtime_root = lambda: ROOT
    job = SimpleNamespace(task_id='job-1', step_id='step-02', metadata={})
    for verdict, evidence in [('accept', []), ('revise', [{'result': {'ok': True}}]),
                              ('accept', [{'result': {'ok': False}}])]:
        obj._retire_verified_injections(job,
            json.dumps({'verdict': verdict, 'fulfilled_injection_ids': [54]}), evidence=evidence)
    assert len(obj.context_injections.items) == 2


def test_verifier_json_has_retirement_field_and_console_has_checkbox():
    protocol = (ROOT / 'core/norm_runtime/protocol.py').read_text(encoding='utf-8')
    prompt = (ROOT / 'tools/norm_gui_prompt.py').read_text(encoding='utf-8')
    assert '"fulfilled_injection_ids"' in protocol
    assert 'checkboxlist_dialog(' in prompt
    assert 'retire_when_satisfied' in prompt
    assert '/injection-restore' in prompt
    source = (ROOT / 'core/norm_runtime/prompt_worker.py').read_text(encoding='utf-8')
    assert 'A retire_when_satisfied item may be marked fulfilled ONLY by the independent' in source
    assert 'self._run_tool_slice(job, base_prompt, tools)' in source
    assert 'm.get("content")' in source
