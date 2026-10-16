"""Regression: installer's trusted optional-plugin catalog matches packaged plugin.json and src."""
from __future__ import annotations
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'core'))
from norm_runtime.plugin_identity import source_tree_sha256  # noqa: E402

class CatalogContractTests(unittest.TestCase):
    def test_installer_catalog_identity(self):
        manifest = json.loads((ROOT / 'package-manifest.json').read_text(encoding='utf-8'))
        entries = manifest['public_optional_plugins']['plugins']
        subtrees = manifest['public_plugin_install']['subtrees']
        self.assertEqual(set(subtrees), set(entries))
        for rel in subtrees:
            with self.subTest(plugin=rel):
                folder = ROOT / rel
                actual = json.loads((folder / 'plugin.json').read_text(encoding='utf-8'))
                expected = entries[rel]
                for field in ('name', 'version', 'entrypoint', 'sha256'):
                    self.assertEqual(actual[field], expected[field], f'{rel}: {field}')
                self.assertEqual(source_tree_sha256(folder), actual['sha256'], rel)
                self.assertTrue((folder / actual['entrypoint']).is_file(), rel)

if __name__ == '__main__':
    unittest.main()
