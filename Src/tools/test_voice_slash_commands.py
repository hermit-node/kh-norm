"""Voice slash routing and idempotence tests; no Ollama, Redis, or database needed."""
from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from norm_plugin_help import get_help, plugin_help_files
from norm_voice_commands import _activate, _parts, run_voice_command


class StubVoice:
    def __init__(self):
        self.prompt = "VOICE PROFILE\nEdgy British diction\n"
        self.pointer: dict = {"active": False, "profile_name": None}
        self.calls = 0

    def list_profiles(self):
        return {"profiles": [{"profile_name": "Cockney", "sources": 3, "anchors": 6, "active": self.pointer.get('active')}],
                "active": self.pointer.get("profile_name")}

    def active_profile(self):
        return self.pointer.copy()

    def render_voice_prompt(self, name, anchor_count=3):
        assert name == "Cockney"
        return {"prompt": self.prompt + f"anchors={anchor_count}\n"}

    def activate_profile(self, name, anchor_count=3):
        self.calls += 1
        text = self.render_voice_prompt(name, anchor_count)["prompt"]
        self.pointer = {"active": True, "profile_name": name, "anchor_count": anchor_count,
                        "prompt_sha256": hashlib.sha256(text.encode()).hexdigest()}
        return self.pointer.copy()


class VoiceTests(unittest.TestCase):
    def test_windows_quote(self):
        self.assertEqual(_parts(r'/voice-build Cockney "C:\Norm\My PDF Files" --mode text'),
                         ['/voice-build', 'Cockney', r'C:\Norm\My PDF Files', '--mode', 'text'])

    def test_activation_idempotent_and_changed_json_refreshes(self):
        stub = StubVoice()
        output: list[str] = []
        root = Path(tempfile.gettempdir())
        with patch('norm_voice_commands._plugin', return_value=stub), \
             patch('norm_runtime.voice_profile_context.load_active_voice_context', return_value='VALID'):
            self.assertTrue(run_voice_command('/voice-activate cockney --anchors 6', root, output.append))
            self.assertEqual(stub.calls, 1)
            self.assertTrue(run_voice_command('/voice-activate Cockney', root, output.append))
            self.assertEqual(stub.calls, 1)
            self.assertIn('No rewrite needed', output[-1])
            stub.prompt = 'REVISED\n'
            self.assertTrue(run_voice_command('/voice-activate Cockney', root, output.append))
            self.assertEqual(stub.calls, 2)
            self.assertEqual(stub.pointer['anchor_count'], 6)
            run_voice_command('/voice-activate Cockney --force', root, output.append)
            self.assertEqual(stub.calls, 3)

    def test_absent_or_invalid_live_prompt_needs_reactivation(self):
        stub = StubVoice()
        output = []
        with patch('norm_voice_commands._plugin', return_value=stub), \
             patch('norm_runtime.voice_profile_context.load_active_voice_context', return_value=''):
            run_voice_command('/voice-activate Cockney', Path('/unused'), output.append)
            run_voice_command('/voice-activate Cockney', Path('/unused'), output.append)
            self.assertEqual(stub.calls, 2)  # cannot trust a damaged/missing active prompt

    def test_help_only_trusted_plugin_manuals(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root/'docs').mkdir()
            (root/'docs'/'help_menu.txt').write_text('Slim')
            (root/'plugins'/'voice_profile').mkdir(parents=True)
            (root/'plugins'/'voice_profile'/'help.txt').write_text('VOICE COMMANDS')
            (root/'plugins'/'unlisted').mkdir()
            self.assertEqual(get_help(root), 'Slim')
            self.assertIn('/help voice-profiler', get_help(root, 'plugins'))
            self.assertEqual(get_help(root, 'voice-profiler'), 'VOICE COMMANDS')
            self.assertEqual(len(plugin_help_files(root)), 1)


    def test_installed_plugin_real_activation_with_json_revision(self):
        """Integration: activate -> unchanged no-op -> edited JSON refresh -> loader passes SHA."""
        from norm_voice_commands import _plugin
        from norm_runtime.voice_profile_context import load_active_voice_context
        root = Path(__file__).resolve().parents[1]
        # The installed plugin's code identity is checked, but its test state stays temporary.
        voice = _plugin(root)
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp)
            pdir = state/'voice_profiles'/'Cockney'
            pdir.mkdir(parents=True)
            profile = {'schema': 2, 'profile_name': 'Cockney',
                       'synthesis': {'voice_summary': 'Rough London voice', 'tone': ['blunt']},
                       'anchors': []}
            f = pdir/'voice_profile.json'
            import json
            f.write_text(json.dumps(profile), encoding='utf-8')
            output = []
            with patch('norm_runtime.settings.load_path_settings', return_value={'state_root': state}), \
                 patch('norm_runtime.voice_profile_context.load_path_settings', return_value={'state_root': state}):
                run_voice_command('/voice-activate Cockney', root, output.append)
                first = json.loads((state/'voice_profiles'/'active.json').read_text())
                self.assertIn('Rough London voice', load_active_voice_context(root))
                run_voice_command('/voice-activate Cockney', root, output.append)
                second = json.loads((state/'voice_profiles'/'active.json').read_text())
                self.assertEqual(first, second)
                profile['synthesis']['voice_summary'] = 'Much rougher London voice'
                f.write_text(json.dumps(profile), encoding='utf-8')
                run_voice_command('/voice-activate Cockney', root, output.append)
                third = json.loads((state/'voice_profiles'/'active.json').read_text())
                self.assertNotEqual(first['prompt_sha256'], third['prompt_sha256'])
                self.assertIn('Much rougher London voice', load_active_voice_context(root))

    def test_voice_command_not_queued_or_unknown(self):
        self.assertFalse(run_voice_command('/not-a-command', Path('/unused')))
        stub = StubVoice()
        output = []
        with patch('norm_voice_commands._plugin', return_value=stub):
            self.assertTrue(run_voice_command('/voice-list', Path('/unused'), output.append))
        self.assertTrue(any('Cockney' in row for row in output))


if __name__ == '__main__':
    unittest.main()
