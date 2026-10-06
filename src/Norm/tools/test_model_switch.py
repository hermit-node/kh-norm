from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
CORE = ROOT / "core"
if str(CORE) not in sys.path:
    sys.path.insert(0, str(CORE))

from norm_runtime.model_switch import ordered_models, resolve_model_selector, same_model
from norm_runtime.ollama_client import OllamaClient


class _FakeResponse:
    def __init__(self, payload: bytes):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self) -> bytes:
        return self.payload


class ModelSwitchTests(unittest.TestCase):
    def test_norm_is_first_and_names_are_deduplicated(self):
        models = ordered_models([
            "qwen3:8b",
            "mistral-small:14b",
            "norm:latest",
            "QWEN3:8B",
        ])
        self.assertEqual(models[0], "norm:latest")
        self.assertEqual(len(models), 3)
        self.assertEqual(models[1:], ["mistral-small:14b", "qwen3:8b"])

    def test_selector_supports_number_exact_and_unique_base(self):
        models = ["norm:latest", "mistral-small:14b", "qwen3:8b"]
        self.assertEqual(resolve_model_selector(models, "1"), "norm:latest")
        self.assertEqual(resolve_model_selector(models, "3"), "qwen3:8b")
        self.assertEqual(resolve_model_selector(models, "QWEN3:8B"), "qwen3:8b")
        self.assertEqual(resolve_model_selector(models, "mistral-small"), "mistral-small:14b")

    def test_selector_rejects_ambiguous_or_missing_models(self):
        models = ["norm:latest", "qwen3:8b", "qwen3:14b"]
        with self.assertRaisesRegex(ValueError, "ambiguous"):
            resolve_model_selector(models, "qwen3")
        with self.assertRaisesRegex(ValueError, "out of range"):
            resolve_model_selector(models, "9")
        with self.assertRaisesRegex(ValueError, "not installed"):
            resolve_model_selector(models, "phi4")

    def test_norm_and_norm_latest_are_same_runtime_model(self):
        self.assertTrue(same_model("norm", "norm:latest"))
        self.assertFalse(same_model("norm", "qwen3:8b"))

    @patch("norm_runtime.ollama_client.request.urlopen")
    def test_ollama_model_listing_uses_api_tags(self, urlopen):
        urlopen.return_value = _FakeResponse(
            b'{"models":[{"name":"norm:latest"},{"name":"qwen3:8b"}]}'
        )
        client = OllamaClient("http://127.0.0.1:11434", model="norm")
        self.assertEqual(client.list_models(), ["norm:latest", "qwen3:8b"])
        req = urlopen.call_args.args[0]
        self.assertEqual(req.full_url, "http://127.0.0.1:11434/api/tags")

    def test_help_menu_is_single_text_authority(self):
        help_file = ROOT / "docs" / "help_menu.txt"
        self.assertTrue(help_file.is_file())
        help_text = help_file.read_text(encoding="utf-8")
        self.assertIn("/switch-model", help_text)
        gui = (ROOT / "tools" / "norm_gui_prompt.py").read_text(encoding="utf-8")
        rich = (ROOT / "core" / "norm_runtime" / "rich_console.py").read_text(encoding="utf-8")
        self.assertIn('ROOT / "docs" / "help_menu.txt"', gui)
        self.assertIn('"docs" / "help_menu.txt"', rich)
        self.assertNotIn('print("  /about', gui)
        self.assertNotIn('"  /about', rich)

    def test_runtime_boot_model_is_hardwired_to_norm(self):
        source = (ROOT / "core" / "norm_main.py").read_text(encoding="utf-8")
        self.assertIn('MODEL_NAME = "norm"', source)
        self.assertIn("model_name = MODEL_NAME", source)
        self.assertNotIn("model_name = str(ollama_cfg.get('model', MODEL_NAME))", source)


if __name__ == "__main__":
    unittest.main(verbosity=2)
