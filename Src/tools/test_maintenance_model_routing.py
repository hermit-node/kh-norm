from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "core"))

from norm_runtime.maintenance_model_client import MaintenanceModelClient
from norm_runtime.ollama_client import ModelGenerationCancelled


class FakeBase:
    base_url = "http://127.0.0.1:11434"
    timeout_seconds = 60
    activity_sink = None
    crash_sink = None

    def __init__(self, models=None):
        self.models = list(models) if models is not None else None

    def list_models(self):
        if self.models is None:
            raise RuntimeError("inventory unavailable")
        return list(self.models)


class MaintenanceModelRoutingTests(unittest.TestCase):
    def test_blanks_collapse_to_norm(self):
        client = MaintenanceModelClient(FakeBase(["norm:latest"]), primary_model="", backup_model="")
        self.assertEqual(client.model_chain, ("norm:latest",))

    def test_friendly_names_resolve_to_installed_tags(self):
        client = MaintenanceModelClient(
            FakeBase(["north-mini-code-1.0:latest", "granite3.3:8b", "norm:latest"]),
            primary_model="north-mini-code",
            backup_model="granite",
        )
        self.assertEqual(
            client.model_chain,
            ("north-mini-code-1.0:latest", "granite3.3:8b", "norm:latest"),
        )

    def test_exact_base_prefers_latest(self):
        resolved, kind, _ = MaintenanceModelClient.resolve_model_name(
            "granite3.3", ["granite3.3:2b", "granite3.3:latest", "granite3.3:8b"]
        )
        self.assertEqual(resolved, "granite3.3:latest")
        self.assertEqual(kind, "exact-base-latest")

    def test_ambiguous_friendly_primary_skips_to_backup(self):
        client = MaintenanceModelClient(
            FakeBase(["granite3.3:8b", "granite-code:20b", "laguna-xs:latest", "norm:latest"]),
            primary_model="granite",
            backup_model="laguna-xs",
        )
        self.assertEqual(client.model_chain, ("laguna-xs:latest", "norm:latest"))

    def test_inventory_failure_preserves_configured_chain(self):
        client = MaintenanceModelClient(FakeBase(None), primary_model="granite", backup_model="laguna-xs")
        self.assertEqual(client.model_chain, ("granite", "laguna-xs", "norm"))

    def test_blank_backup_means_norm(self):
        client = MaintenanceModelClient(
            FakeBase(["granite3.3:8b", "norm:latest"]), primary_model="granite", backup_model=""
        )
        self.assertEqual(client.model_chain, ("granite3.3:8b", "norm:latest"))

    def test_failure_promotes_backup_for_rest_of_pass(self):
        client = MaintenanceModelClient(FakeBase(None), primary_model="granite", backup_model="laguna-xs")
        calls = []

        class Stub:
            def __init__(self, model):
                self.model = model

            def generate(self, prompt, **kwargs):
                calls.append(self.model)
                if self.model == "granite":
                    raise RuntimeError("unavailable")
                return self.model

        client._client = lambda model: Stub(model)
        self.assertEqual(client.generate("x"), "laguna-xs")
        self.assertEqual(client.generate("x"), "laguna-xs")
        self.assertEqual(calls, ["granite", "laguna-xs", "laguna-xs"])

    def test_cancel_does_not_fall_through(self):
        client = MaintenanceModelClient(FakeBase(None), primary_model="granite", backup_model="laguna-xs")
        calls = []

        class Stub:
            def __init__(self, model):
                self.model = model

            def generate(self, prompt, **kwargs):
                calls.append(self.model)
                raise ModelGenerationCancelled("stop")

        client._client = lambda model: Stub(model)
        with self.assertRaises(ModelGenerationCancelled):
            client.generate("x")
        self.assertEqual(calls, ["granite"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
