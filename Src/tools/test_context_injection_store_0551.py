"""Exercise durable context injection API against a transaction/SQL test double.

This verifies lifecycle logic and generated statement shape; it is NOT a real
PostgreSQL migration test.
"""
import importlib.util
import json
from pathlib import Path
import sys
import types
import uuid
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[1]


class SQLString:
    def __init__(self, s):
        self.s = str(s)

    def format(self, *args):
        return SQLString(self.s.format(*(str(x) for x in args)))

    def __add__(self, other):
        return SQLString(self.s + str(other))

    def __str__(self):
        return self.s


class DB:
    def __init__(self):
        self.rows = []
        self.saved_requests = {}
        self.executed = []
        self.last = None
        self.id = 1
        self.status = 'running'
        self.schema = 'norm_runtime'

    def _connect(self):
        return self

    def root_task_id(self, task):
        return 'root-A' if task in ('root-A', 'step-A') else ('root-B' if task == 'root-B' else None)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        pass

    def cursor(self):
        return self

    def execute(self, stmt, params=()):
        stmt = str(stmt)
        self.executed.append((stmt, params))
        self.last = None
        if 'SELECT status FROM' in stmt and 'task_runs' in stmt:
            self.last = [(self.status,)]
        elif 'INSERT INTO' in stmt and 'task_context_injections' in stmt:
            uid, root, content, retire = params
            if uid not in self.saved_requests:
                row = {'id':self.id,'request_id':uid,'root':root,'content':content,'retire':retire,
                       'status':'active','step':None,'reason':None}
                self.id += 1
                self.rows.append(row)
                self.saved_requests[uid] = row
                self.last = [(row['id'],)]
            else:
                self.last = []
        elif 'SELECT injection_id,root_task_id,content,retire_when_satisfied' in stmt:
            r = self.saved_requests[params[0]]
            self.last = [(r['id'],r['root'],r['content'],r['retire'])]
        elif 'SELECT injection_id,content,retire_when_satisfied,status' in stmt:
            (root,) = params
            self.last = [(r['id'],r['content'],r['retire'],r['status'],None,r['step'],r['reason'])
                         for r in self.rows if r['root']==root]
        elif 'SELECT injection_id,content,retire_when_satisfied' in stmt:
            root, after = params
            self.last = [(r['id'],r['content'],r['retire']) for r in self.rows
                         if r['root']==root and r['status']=='active' and r['id']>after]
        elif 'UPDATE' in stmt and "SET status='active'" in stmt:
            iid, root = params
            rows = [r for r in self.rows if r['id']==iid and r['root']==root and r['status'] in ('satisfied','superseded')]
            for r in rows: r.update(status='active',reason=None,step=None)
            self.last = [(r['id'],) for r in rows]
        elif 'UPDATE' in stmt and 'SET status=%s' in stmt:
            status, step, reason, iid, root, verified = params
            rows = [r for r in self.rows if r['id']==iid and r['root']==root and r['status']=='active'
                    and (not verified or r['retire'])]
            for r in rows: r.update(status=status,step=step,reason=reason)
            self.last = [(r['id'],) for r in rows]

    def fetchone(self):
        return self.last[0] if self.last else None

    def fetchall(self):
        return self.last or []


def _load_store():
    ps = types.ModuleType('psycopg')
    ps.sql = types.SimpleNamespace(SQL=SQLString, Identifier=lambda x: SQLString('"'+str(x).replace('"','""')+'"'))
    old = sys.modules.get('psycopg')
    sys.modules['psycopg'] = ps
    try:
        path = ROOT/'core/norm_runtime/context_injections.py'
        spec = importlib.util.spec_from_file_location('_norm_injections_0551', path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module.ContextInjections
    finally:
        if old is None: sys.modules.pop('psycopg', None)
        else: sys.modules['psycopg'] = old


def test_durable_state_and_scope():
    cls = _load_store()
    db = DB()
    store = cls(db)
    store.ensure_schema()
    ddl = '\n'.join(sql for sql, _ in db.executed)
    assert 'ADD COLUMN IF NOT EXISTS retire_when_satisfied' in ddl
    assert 'ADD COLUMN IF NOT EXISTS status' in ddl
    req = str(uuid.uuid4())
    created = store.save('step-A', 'Use only native PDF extraction', req, True)
    assert created['retire_when_satisfied']
    assert created['injection_id'] == 1
    assert store.save('step-A', 'Use only native PDF extraction', req, True)['injection_id'] == 1
    assert len(store.read('step-A')) == 1
    assert not store.retire('root-B', 1, verified=True)['retired']
    assert store.retire('root-A', 1, step_id='step-02',verified=True)['retired']
    assert store.read('root-A') == []
    summary = store.list('root-A')['injections']
    assert summary[0]['status'] == 'satisfied' and summary[0]['retired_by_step'] == 'step-02'
    assert store.restore('root-A',1)['restored']
    assert len(store.read('root-A')) == 1
    import pytest
    with pytest.raises(ValueError):
        store.save('root-A', 'changed', req, True)
