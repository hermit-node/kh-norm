from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
import uuid
from datetime import timezone
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "core"))
from norm_runtime.plugin_manager import PluginManager
from norm_runtime.plugin_identity import source_tree_sha256, verify_identity
from norm_runtime.prompt_worker import PromptWorker
from test_oversize_noop_artifact import main as recovery_regressions


def seal(folder):
    meta = {
        "schema_version": 2,
        "name": folder.name,
        "version": "1",
        "date": "2026-10-02",
        "entrypoint": "src/main.py",
        "description": "test plugin",
        "capabilities": ["test"],
        "sha256": source_tree_sha256(folder),
    }
    (folder / "plugin.json").write_text(json.dumps(meta), encoding="utf-8")


class ArtifactTests(unittest.TestCase):
    def test_recovery_contract(self):
        recovery_regressions()

    def test_read_must_follow_last_write(self):
        write = {"tool": "write_file", "result": {"ok": True, "path": "x", "sha256": "a"}}
        read = {"tool": "read_file", "result": {"ok": True, "path": "x", "sha256": "a"}}
        for evidence in ([read, write], [write, read, write]):
            self.assertFalse(PromptWorker._artifact_snapshot(evidence)[0]["verified"])
        self.assertTrue(PromptWorker._artifact_snapshot([write, read])[0]["verified"])

    def test_unhashed_write_cannot_be_noop(self):
        rows = PromptWorker._artifact_snapshot([{"tool": "write_file", "result": {"ok": True, "path": "x"}}])
        self.assertFalse(PromptWorker._final_artifact_state({"artifacts": rows}))

    def test_plugin_mutation_requires_readback(self):
        write = {"tool": "plugin_verbatim_lines__main__append_text", "result": {
            "ok": True, "path": "x", "sha256": "a", "file_mutation": True}}
        self.assertFalse(PromptWorker._artifact_snapshot([write])[0]["verified"])
        read = {"tool": "read_file", "result": {"ok": True, "path": "x", "sha256": "a"}}
        self.assertTrue(PromptWorker._artifact_snapshot([write, read])[0]["verified"])

    def test_hash_mismatch_fails_deterministically(self):
        worker = PromptWorker.__new__(PromptWorker)
        worker._persist_evidence_now = lambda *args: None
        with self.assertRaisesRegex(RuntimeError, "hash mismatch"):
            worker._verify_pending(None, {"x": "expected"}, SimpleNamespace(execute=lambda *args: {"ok": True,"sha256":"wrong"}))


class ContextTests(unittest.TestCase):
    def worker(self, cfg=None):
        w=PromptWorker.__new__(PromptWorker)
        w._runtime_config=lambda: {"validation_pool": cfg or {}}
        return w

    def test_budget_and_emergency_cap(self):
        w=self.worker({"context_char_budget": 2200, "context_token_budget": 1000, "max_context_items": 1})
        requested=[]
        def recent(**kw):
            requested.append(kw["limit"])
            return [{"subject": f"fact-{i}", "value": "v", "recent_checks": 9, "generation_checks": 20,
                     "storage": "redis_live"} for i in range(100)]
        w.live=SimpleNamespace(global_validation=lambda *a,**k: None,recent_global_validations=recent)
        w.durable=None
        text=w._validation_context(SimpleNamespace(task_id="test"))
        self.assertEqual(requested,[512])
        self.assertLessEqual(len(text),2200)
        self.assertIn("omitted",text)
        self.assertIn("never a permission gate",text)
        self.assertIn("fact-1",text)

    def test_empty_context_respects_tiny_budget(self):
        w=self.worker({"context_char_budget": 20})
        w._recent_validations=lambda *args: []
        self.assertLessEqual(len(w._validation_context(None)),20)

    def test_durable_fallback_does_not_invent_live_counts(self):
        w=self.worker()
        w.live=SimpleNamespace(global_validation=lambda *a,**k: None)
        w.durable=SimpleNamespace(global_validations=lambda *a,**k:[{"subject":"x","current_value":"v","generation_check_count":42}])
        row=w._recent_validations(SimpleNamespace(task_id="t"),["x"])[0]
        self.assertIsNone(row["recent_checks"])
        self.assertEqual(row["generation_checks"],42)
        reply=w._execute_validation_tool(SimpleNamespace(task_id="t"),"review_validations",{"subjects":["x"]})
        self.assertIn("unknown",reply["reviewed"][0]["reason"])

    def test_validation_timestamp_normalization_is_utc_aware(self):
        naive=PromptWorker._parse_iso_timestamp("2026-10-02T12:00:00")
        eastern=PromptWorker._parse_iso_timestamp("2026-10-02T08:00:00-04:00")
        self.assertIsNotNone(naive)
        self.assertIsNotNone(eastern)
        self.assertEqual(naive.tzinfo,timezone.utc)
        self.assertEqual(eastern.tzinfo,timezone.utc)
        self.assertEqual(naive,eastern)

    def test_shared_pool_refreshes_before_each_tool_decision_round(self):
        w=self.worker()
        w._slice_limits=lambda:(4,8)
        rounds={"n":0}
        w.queue=SimpleNamespace(task_rounds=lambda _tid:0, add_task_rounds=lambda _tid,n: n + rounds["n"])
        seen=[]
        def validation(_job):
            seen.append(len(seen)+1)
            return f"shared-pool-count={45+len(seen)}"
        w._validation_context=validation
        w._consume_context_injections=lambda _job:[]
        w._suppression_requested=lambda _tid:False
        w._drain=SimpleNamespace(is_set=lambda:False)
        w.durable=None
        w._persist_thinking_now=lambda *a:0
        w._persist_evidence_now=lambda *a:None
        w._verify_pending=lambda *a:[]
        class Client:
            def __init__(self): self.prompts=[]
            def chat_with_tools(self,messages,*args,**kwargs):
                self.prompts.append(messages[0]["content"])
                if len(self.prompts)==1:
                    return {"tool_calls":[{"function":{"name":"lookup","arguments":{}}}],"content":"","thinking":""}
                return {"tool_calls":[],"content":"done","thinking":"","done_reason":"stop"}
        w.client=Client()
        tools=SimpleNamespace(instructions=lambda:"TOOLS", schemas=lambda:[], execute=lambda *_a,**_k:{"ok":True})
        job=SimpleNamespace(task_id="t",step_id="work",metadata={},request_type="step",prompt="test")
        result=w._run_tool_slice(job,"test",tools)
        self.assertEqual(result["answer"],"done")
        self.assertEqual(seen,[1,2])
        self.assertIn("shared-pool-count=46",w.client.prompts[0])
        self.assertIn("shared-pool-count=47",w.client.prompts[1])


class PluginTests(unittest.TestCase):
    def setUp(self):
        self.root=ROOT / ".test-temp" / uuid.uuid4().hex
        self.root.mkdir(parents=True)
        self.folder=self.root/"example"
        self.folder.mkdir()
        self.src=self.folder/"src"
        self.src.mkdir()
        self.main=self.src/"main.py"
        (self.folder/"README.md").write_text("Example plugin\n")
        self.main.write_text("def value():\n    return 1\n")
        seal(self.folder)
        self.manager=PluginManager(self.root)

    def tearDown(self):
        import shutil
        assert self.root.resolve().is_relative_to((ROOT / ".test-temp").resolve())
        shutil.rmtree(self.root)

    def test_registry_is_generated_from_runtime_plugin_tree(self):
        registry=self.root/".registry.json"
        self.assertFalse(registry.exists())
        snapshot=self.manager.refresh()
        self.assertTrue(registry.is_file())
        saved=json.loads(registry.read_text(encoding="utf-8"))
        self.assertEqual(saved["generation"],snapshot["generation"])
        self.assertEqual([item["folder"] for item in saved["plugins"]],["example"])

    def test_all_first_party_identities(self):
        folders=[p for p in (ROOT/"plugins").iterdir() if p.is_dir() and not p.name.startswith((".","_"))]
        self.assertEqual(len(folders),9)
        for folder in folders:
            identity=verify_identity(folder)
            self.assertIsNotNone(identity)
            self.assertEqual(identity["schema_version"],2)
            self.assertEqual(identity["entrypoint"],"src/main.py")
            self.assertFalse((folder/"SHA256SUMS").exists())

    def test_readme_is_not_part_of_source_sha(self):
        before=verify_identity(self.folder)["sha256"]
        (self.folder/"README.md").write_text("Changed docs only\n")
        after=verify_identity(self.folder)["sha256"]
        self.assertEqual(before,after)

    def test_metadata_change_with_same_source_sha_does_not_reload_code(self):
        name=self.manager.schemas()[0]["function"]["name"]
        before_fn=self.manager._loaded["example"].functions[name]
        meta=json.loads((self.folder/"plugin.json").read_text())
        meta["date"]="2026-10-03"
        meta["description"]="metadata only"
        (self.folder/"plugin.json").write_text(json.dumps(meta))
        snap=self.manager.refresh()
        after_fn=self.manager._loaded["example"].functions[name]
        self.assertIs(before_fn,after_fn)
        self.assertEqual(self.manager._loaded["example"].description,"metadata only")
        self.assertEqual(snap["plugins"][0]["fingerprint"],meta["sha256"])

    def test_corruption_keeps_last_good_then_valid_update_reloads(self):
        name=self.manager.schemas()[0]["function"]["name"]
        self.main.write_text("def value():\n    return 2\n")
        self.assertEqual(self.manager.execute(name,{})["result"],1)
        self.assertTrue(self.manager.snapshot()["errors"])
        seal(self.folder)
        self.assertEqual(self.manager.execute(name,{})["result"],2)
        self.assertFalse(self.manager.snapshot()["errors"])

    def test_bad_import_keeps_last_good(self):
        name=self.manager.schemas()[0]["function"]["name"]
        self.main.write_text("raise RuntimeError('broken candidate')\n")
        seal(self.folder)
        self.assertEqual(self.manager.execute(name,{})["result"],1)
        self.assertTrue(self.manager.snapshot()["errors"])

    def test_failed_reload_restores_lazy_relative_import(self):
        (self.src/"_helper.py").write_text("VALUE=7\n")
        self.main.write_text("from . import _helper\ndef value():\n    from . import _helper\n    return _helper.VALUE\n")
        seal(self.folder)
        name=self.manager.schemas()[0]["function"]["name"]
        self.assertEqual(self.manager.execute(name,{})["result"],7)
        (self.src/"_helper.py").write_text("VALUE=99\n")
        self.main.write_text("from . import _helper\nraise RuntimeError('candidate failure')\n")
        seal(self.folder)
        self.assertEqual(self.manager.execute(name,{})["result"],7)

    def test_missing_identity_rejected_after_identified_load(self):
        name=self.manager.schemas()[0]["function"]["name"]
        (self.folder/"plugin.json").unlink()
        self.assertEqual(self.manager.execute(name,{})["result"],1)
        self.assertTrue(self.manager.snapshot()["errors"])

    def test_new_invalid_identity_has_no_tools(self):
        self.main.write_text("def value():\n    return 2\n")
        self.assertEqual(self.manager.schemas(),[])

    def test_source_sha_and_entrypoint_validation(self):
        (self.src/"_helper.py").write_text("value=3\n")
        with self.assertRaisesRegex(ValueError,"source sha256 mismatch"):
            verify_identity(self.folder)
        seal(self.folder)
        meta=json.loads((self.folder/"plugin.json").read_text())
        meta["entrypoint"]="../outside.py"
        (self.folder/"plugin.json").write_text(json.dumps(meta))
        with self.assertRaisesRegex(ValueError,"entrypoint"):
            verify_identity(self.folder)

    def test_legacy_plugin_stays_supported(self):
        (self.folder/"plugin.json").unlink()
        self.assertEqual(len(self.manager.schemas()),1)

    def test_runtime_has_no_direct_psycopg_connect(self):
        hits=[]
        for base in (ROOT/"core", ROOT/"tools", ROOT/"plugins"):
            for path in base.rglob("*.py"):
                if "__pycache__" in path.parts:
                    continue
                needle = "psycopg." + "connect("
                if needle in path.read_text(encoding="utf-8-sig", errors="replace"):
                    hits.append(str(path.relative_to(ROOT)))
        self.assertEqual(hits,[])


if __name__ == "__main__":
    unittest.main(verbosity=2)
