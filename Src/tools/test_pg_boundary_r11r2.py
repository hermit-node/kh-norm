"""Offline r11r2 regression for common PostgreSQL safety/SQL composition boundary.

The production psycopg/python-sql packages must be tested again on the user's
Windows venv; this test deliberately requires neither nor a live database.
"""
from __future__ import annotations

import ast
import importlib.util
import json
import logging
import math
import sys
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / 'core' / 'norm_runtime'
sys.path.insert(0, str(ROOT / 'core'))

spec = importlib.util.spec_from_file_location('norm_pg_json_safety_test_r2', SRC / 'pg_json_safety.py')
safety = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(safety)


def isolated_function(path: Path, name: str, namespace: dict):
    module = ast.parse(path.read_text(encoding='utf-8'))
    found = next(node for node in module.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name)
    selection = ast.Module(body=[found], type_ignores=[])
    ast.fix_missing_locations(selection)
    exec(compile(selection, str(path), 'exec'), namespace)
    return namespace[name]


class PGSharedBoundaryTests(unittest.TestCase):
    def test_malformed_unicode_is_cleaned_without_mutating_original(self):
        original = {'evidence': {'stdout': 'hello\x00\ud800'}, 'ok': True}
        serialized = safety.dumps_pg_json(original)
        decoded = json.loads(serialized)
        self.assertEqual(decoded['evidence']['stdout'], r'hello\u0000\ud800')
        self.assertEqual(original['evidence']['stdout'], 'hello\x00\ud800')
        self.assertNotIn('\x00', serialized)
        self.assertNotIn('\ud800', serialized)

    def test_binary_and_invalid_float(self):
        payload = {'output': b'\xff\x00\x80', 'measure': math.inf}
        serialized = json.loads(safety.dumps_pg_json(payload))
        import base64
        self.assertEqual(base64.b64decode(serialized['output']['data']), payload['output'])
        self.assertEqual(serialized['measure']['_norm_encoding'], 'non-finite-float')

    def test_normal_json_shapes(self):
        original = {'success': True, 'arr': [None, 3, 1.25, {'x': 'ok'}]}
        self.assertEqual(json.loads(safety.dumps_pg_json(original)), original)

    def test_json_key_collision_is_fatal_not_silent(self):
        with self.assertRaises(ValueError):
            safety.dumps_pg_json({'\x00': 'x', r'\u0000': 'y'})

    def test_warning_carries_no_raw_binary(self):
        logger = logging.getLogger(safety.__name__)
        with self.assertLogs(logger, level='WARNING') as logs:
            safety.dumps_pg_json({'raw': b'\xff\x00sensitive'})
        self.assertNotIn('sensitive', str(logs.output))
        self.assertIn('database representation differs', str(logs.output))

    def test_connection_callback_registers_only_per_connection(self):
        fn_file = ROOT / 'plugins/postgres_pool/src/_pool.py'
        seen = []
        module_psycopg = types.ModuleType('psycopg')
        module_psycopg.__path__ = []
        module_types = types.ModuleType('psycopg.types')
        module_types.__path__ = []
        module_json = types.ModuleType('psycopg.types.json')
        module_json.set_json_dumps = lambda fn, conn: seen.append((fn, conn))
        # The genuine production import uses this module name.
        import norm_runtime.pg_json_safety as shared
        previous = {name: sys.modules.get(name) for name in ['psycopg', 'psycopg.types', 'psycopg.types.json']}
        try:
            sys.modules.update({'psycopg': module_psycopg, 'psycopg.types': module_types, 'psycopg.types.json': module_json})
            callback = isolated_function(fn_file, '_configure_norm_json', {'psycopg': module_psycopg})
            conn = object()
            callback(conn)
            self.assertEqual(seen, [(shared.dumps_pg_json, conn)])
            tree = ast.parse(fn_file.read_text(encoding='utf-8'))
            self.assertTrue(any(isinstance(n, ast.keyword) and n.arg == 'configure'
                                and isinstance(n.value, ast.Name) and n.value.id == '_configure_norm_json'
                                for n in ast.walk(tree)))
        finally:
            for name, obj in previous.items():
                if obj is None:
                    sys.modules.pop(name, None)
                else:
                    sys.modules[name] = obj

    def test_sql_builder_rejects_unsafe_schema_before_import(self):
        f = SRC / 'sql_query_builder.py'
        spec = importlib.util.spec_from_file_location('norm_sql_query_builder_test_r2', f)
        module = importlib.util.module_from_spec(spec)
        assert spec and spec.loader
        spec.loader.exec_module(module)
        for name in ['bad.schema', 'bad";drop table x', ' normal', '', '💀']:
            with self.assertRaises(ValueError):
                module.runtime_state_get_query(name, 'key')

    def test_query_construction_is_parameterized(self):
        f = SRC / 'sql_query_builder.py'
        source = f.read_text(encoding='utf-8')
        self.assertIn('Table("runtime_state", schema)', source)
        self.assertIn('where=table.state_key == key', source)
        self.assertIn('return tuple(table.select(', source)
        self.assertNotIn('f"SELECT', source)

    def test_consistent_error_classification(self):
        f = SRC / 'pg_error_policy.py'
        spec = importlib.util.spec_from_file_location('norm_pg_error_policy_test_r2', f)
        mod = importlib.util.module_from_spec(spec)
        assert spec and spec.loader
        spec.loader.exec_module(mod)
        self.assertEqual(mod.classify_sqlstate('22P05'), 'deterministic_sql_or_data')
        self.assertEqual(mod.classify_sqlstate('23505'), 'deterministic_sql_or_data')
        self.assertEqual(mod.classify_sqlstate('40P01'), 'transient_transaction')
        self.assertEqual(mod.classify_sqlstate('08006'), 'connection_or_resource')
        self.assertEqual(mod.classify_sqlstate(None), 'unclassified')

    def test_snapshot_does_not_interpolate_schemas(self):
        for name in ('context_snapshot.py', 'shutdown_snapshot.py'):
            s = (SRC / name).read_text(encoding='utf-8')
            self.assertIn('pg_sql.Identifier(durable.schema)', s)
            self.assertNotIn('f"FROM {durable.schema}', s)


if __name__ == '__main__':
    unittest.main()
