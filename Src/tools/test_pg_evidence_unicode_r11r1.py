"""Regression: an executed filesystem command must not fail on JSONB evidence Unicode."""
from __future__ import annotations

import ast
import base64
import hashlib
import importlib.util
import json
import math
import unittest
from pathlib import Path

SAFETY = Path(__file__).resolve().parents[1] / 'core' / 'norm_runtime' / 'pg_json_safety.py'
spec = importlib.util.spec_from_file_location('norm_pg_json_safety_test', SAFETY)
safety = importlib.util.module_from_spec(spec)
spec.loader.exec_module(safety)


class PostgresEvidenceUnicodeTests(unittest.TestCase):
    def test_nul_in_tool_output_is_literal_escape_after_json_round_trip(self):
        original = {'ok': True, 'stdout': 'renamed 2 files\x00\ufffd\x18', 'path': 'C:/tmp/valid.txt'}
        normalized, changes = safety.normalize_pg_jsonb(original)
        self.assertEqual(len(changes), 1)
        self.assertEqual(changes[0]['path'], '$.stdout')
        self.assertEqual(changes[0]['original_sha256'], hashlib.sha256(original['stdout'].encode()).hexdigest())
        self.assertNotIn('\x00', normalized['stdout'])
        self.assertIn(r'\u0000', normalized['stdout'])
        self.assertEqual(normalized['path'], original['path'])
        self.assertEqual(original['stdout'][15], '\x00')  # untouched execution result
        self.assertEqual(json.loads(json.dumps(normalized)), normalized)
        self.assertIn(r'\\u0000', json.dumps(normalized))

    def test_unpaired_surrogates_are_escaped_and_reported(self):
        normalized, changes = safety.normalize_pg_jsonb({'status': '\ud800/\udfff'})
        self.assertEqual(normalized['status'], r'\ud800/\udfff')
        self.assertEqual(len(changes), 1)
        self.assertNotIn('\ud800', normalized['status'])
        self.assertEqual(json.loads(json.dumps(normalized)), normalized)

    def test_binary_evidence_is_losslessly_encoded(self):
        raw = b'\xff\x00abc\x80'
        normalized, changes = safety.normalize_pg_jsonb({'stdout': raw})
        self.assertEqual(base64.b64decode(normalized['stdout']['data']), raw)
        self.assertEqual(changes[0]['original_bytes'], len(raw))
        self.assertEqual(normalized['stdout']['_norm_encoding'], 'base64')

    def test_clean_evidence_is_unchanged(self):
        original = {'ok': True, 'arguments': {'path': 'C:/stuff/new.txt'}, 'result': [1, 'fine', None]}
        normalized, changes = safety.normalize_pg_jsonb(original)
        self.assertEqual(normalized, original)
        self.assertFalse(changes)

    def test_nested_values_and_nonfinite_floats(self):
        normalized, changes = safety.normalize_pg_jsonb({'out': ['good', '\x00', math.nan]})
        self.assertEqual(normalized['out'][1], r'\u0000')
        self.assertEqual(normalized['out'][2]['_norm_encoding'], 'non-finite-float')
        self.assertEqual(len(changes), 2)

    def test_no_key_collision_hidden(self):
        with self.assertRaises(ValueError):
            safety.normalize_pg_jsonb({'\x00': 1, r'\u0000': 2})

    def test_actual_record_evidence_sql_arguments_and_result_are_sanitized(self):
        """Exercise the real method without needing a live PostgreSQL server."""
        file = SAFETY.with_name('durable_log.py')
        tree = ast.parse(file.read_text(encoding='utf-8'))
        cls = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == 'PostgresTaskLog')
        method = next(node for node in cls.body if isinstance(node, ast.FunctionDef) and node.name == 'record_evidence')
        module = ast.Module(body=[method], type_ignores=[])
        ast.fix_missing_locations(module)

        class SQL:
            def __init__(self, sql): self.sql = sql
            def format(self, *args): return self

        class StubSql:
            Identifier = staticmethod(lambda name: name)
        StubSql.SQL = SQL

        namespace = {'sql': StubSql, 'Jsonb': lambda value: value,
                     'normalize_pg_jsonb': safety.normalize_pg_jsonb}
        exec(compile(module, str(file), 'exec'), namespace)

        class Connection:
            def __enter__(self): return self
            def __exit__(self, *args): return False
            def cursor(self): return self
            def executemany(self, statement, rows):
                self.statement, self.rows = statement, rows

        connection = Connection()
        class Log:
            schema = 'norm_runtime'
            record_evidence = namespace['record_evidence']
            def _connect(self): return connection
            def _node_uuid_cur(self, *args): return 'fake-node-uuid'

        item = {'tool': 'run_command',
                'arguments': {'command': 'Rename-Item old new'},
                'result': {'ok': True, 'stdout': 'done\x00'}}
        Log().record_evidence('task', 'step-01', [item])
        self.assertEqual(len(connection.rows), 1)
        row = connection.rows[0]
        self.assertEqual(row[4]['command'], 'Rename-Item old new')
        self.assertEqual(row[5]['stdout'], r'done\u0000')
        self.assertTrue(row[5]['_norm_persistence_notice']['not_verbatim'])
        self.assertEqual(item['result']['stdout'], 'done\x00')


if __name__ == '__main__':
    unittest.main()
