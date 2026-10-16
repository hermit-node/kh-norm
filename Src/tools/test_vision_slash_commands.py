"""Direct vision command routing tests; no Redis, Ollama, PostgreSQL required."""
from __future__ import annotations

import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from norm_vision_commands import _action, _page_range, _parts, _parse_flags, run_vision_command
from norm_plugin_help import get_help


class ReaderStub:
    def __init__(self):
        self.calls = []

    def vision_parse(self, path, **kwargs):
        self.calls.append((path, kwargs))
        return {
            'path': path, 'mode': kwargs['mode'], 'page_count': 3,
            'vision_calls': 0, 'cache_hits': 0,
            'next_page': 2,
            'pages': [{'page': kwargs['start_page'], 'reading': 'Hello, readable text!',
                       'vision_calls': 0, 'hybrid_decision': 'native_text',
                       'grayscale_coverage': {'explained_page_fraction': .998, 'explained_ink_fraction': .99}}]
        }


class VisionSlashTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.file = self.root / 'my document.pdf'
        self.file.write_text('placeholder')
        self.reader = ReaderStub()
        self.messages = []

    def _action(self, command):
        with patch('norm_vision_commands._plugin', return_value=self.reader), \
             patch('norm_vision_commands._approved_path', return_value=self.file):
            _action(command, self.root, self.messages.append)

    def test_quoted_windows_path(self):
        self.assertEqual(_parts(r'/vision-parse "C:\Norm\My PDFs\Test.pdf" --page 2'),
                         ['/vision-parse', r'C:\Norm\My PDFs\Test.pdf', '--page', '2'])

    def test_default_hybrid_and_page_progress(self):
        self._action('/vision-parse "example.pdf" --page 2')
        self.assertEqual(self.reader.calls[0][1]['mode'], 'hybrid')
        self.assertEqual((self.reader.calls[0][1]['start_page'], self.reader.calls[0][1]['end_page']), (2, 2))
        self.assertTrue(any('Hello, readable text!' in x for x in self.messages))
        self.assertTrue(any('99.80%' in x for x in self.messages))
        self.assertTrue(any('--start 2' in x for x in self.messages))

    def test_modes_and_recheck(self):
        self._action('/vision-text "doc.pdf"')
        self._action('/vision-force "doc.pdf" --focus "inspect diagram"')
        self._action('/vision-recheck "doc.pdf" "odd text line" --page 1')
        self.assertEqual([c[1]['mode'] for c in self.reader.calls], ['text', 'vision', 'hybrid'])
        self.assertEqual(self.reader.calls[1][1]['prompt'], 'inspect diagram')
        self.assertEqual(self.reader.calls[2][1]['suspect_excerpt'], 'odd text line')

    def test_bad_flags_fail_closed(self):
        for command in [
            '/vision-recheck "a.pdf"',
            '/vision-force "a.pdf" --mode text',
            '/vision-text "a.pdf" --suspect "bad"',
            '/vision-read "a.pdf" --start 1 --end 11',
            '/vision-parse "a.pdf" --page 0',
            '/vision-parse "a.pdf" --start 3 --end 1',
            '/vision-read "a.pdf" --unexpected foo',
            '/vision-parse "a.pdf" --page 1 --start 2',
        ]:
            with self.subTest(command=command), self.assertRaises(ValueError):
                self._action(command)
        self.assertEqual(self.reader.calls, [])

    def test_help_is_plugin_owned(self):
        root=Path(__file__).resolve().parents[1]
        self.assertIn('/help vision-parse', get_help(root, 'plugins'))
        self.assertIn('/vision-recheck', get_help(root, 'vision'))
        self.assertIn('/vision-parse', get_help(root, 'pdf-reader'))
        self.assertIn('/help vision-parse', get_help(root))

    def test_unknown_command_does_not_enqueue(self):
        self.assertFalse(run_vision_command('/not-a-reader', self.root))
        out = []
        self.assertTrue(run_vision_command('/vision', self.root, out.append))
        self.assertIn('/help vision-parse', out[-1])

    def test_hardlock_respected(self):
        with patch('norm_vision_commands._plugin', return_value=self.reader), \
             patch('norm_vision_commands._approved_path', side_effect=PermissionError('HARDLOCKED')):
            with self.assertRaises(PermissionError):
                _action('/vision-parse "C:\\secret.pdf"', self.root, self.messages.append)
        self.assertEqual(self.reader.calls, [])

    def test_plugin_real_source_identity(self):
        from norm_vision_commands import _plugin
        root=Path(__file__).resolve().parents[1]
        plugin = _plugin(root)
        self.assertTrue(callable(plugin.vision_parse))


if __name__ == '__main__':
    unittest.main()
