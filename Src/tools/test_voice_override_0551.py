"""Standalone voice-override tests; no runtime DB or Ollama dependency."""
import importlib.util
import json
from pathlib import Path
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def _load_engine():
    path = ROOT / 'plugins/voice_profile/src/_engine.py'
    spec = importlib.util.spec_from_file_location('voice_0551_engine', path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_tuning_is_durable_and_affects_activation_but_not_corpus():
    m = _load_engine()
    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder)
        m._profiles_root = lambda: root
        d = m._profile_dir('Cockney')
        profile = {'profile_name': 'Cockney', 'synthesis': {'voice_summary': 'measured engineer',
                   'tone': ['moderate'], 'avoid': ['overly casual slang']}, 'anchors': []}
        (d / 'voice_profile.json').write_text(json.dumps(profile))
        (d / 'corpus.jsonl').write_text('{"text":"original"}\n')
        untuned = m._render_prompt(profile, 0)
        assert 'OPERATOR-SELECTED STYLE EMPHASIS' not in untuned
        tuned = m.tune_voice('Cockney', preset='cockney')
        assert 'working-class' in tuned['override']
        assert 'style_override.txt' in [x.name for x in d.iterdir()]
        result = m._render_prompt(profile, 0)
        assert 'OPERATOR-SELECTED STYLE EMPHASIS' in result and 'taking the piss' in result
        assert 'overly casual slang' in result  # corpus synthesis preserved, not silently rewritten
        assert m.tune_voice('Cockney')['override'] == tuned['override']
        m.tune_voice('Cockney', clear=True)
        assert 'OPERATOR-SELECTED STYLE EMPHASIS' not in m._render_prompt(profile, 0)
        assert (d / 'corpus.jsonl').read_text() == '{"text":"original"}\n'


def test_operator_tune_and_ab_not_model_tool_exports():
    main = (ROOT / 'plugins/voice_profile/src/main.py').read_text(encoding='utf-8')
    assert 'def _operator_tune_voice(' in main
    assert 'def _operator_test_voice(' in main
    assert 'def tune_voice(' not in main and 'def test_voice(' not in main
