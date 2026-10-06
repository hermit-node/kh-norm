from __future__ import annotations

import configparser
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "core"))

from norm_runtime.file_access_policy import authorize_path, load_file_access_policy
from norm_runtime.settings import load_path_settings
from norm_runtime.history_maintenance import DeepHistoryMaintainer


class FileAccessPolicyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="norm-file-policy-"))
        shutil.copytree(ROOT / "config", self.tmp / "config")
        (self.tmp / "docs").mkdir()
        (self.tmp / "plugins").mkdir()
        # settings loader requires the schema-2 verbatim helper and external dirs.
        helper = self.tmp / "plugins" / "verbatim_lines" / "src"
        helper.mkdir(parents=True)
        (helper / "_cli.py").write_text("# test\n", encoding="utf-8")
        docs = self.tmp.parent / (self.tmp.name + "-documents")
        docs.mkdir()
        self.documents_root = docs
        parser = configparser.ConfigParser(interpolation=None)
        parser.read(self.tmp / "config" / "settings.ini", encoding="utf-8-sig")
        parser.set("paths", "runtime_root", ".")
        parser.set("paths", "documents_root", str(docs))
        parser.set("paths", "workspace_root", "workspace")
        parser.set("paths", "temp_root", "temp")
        parser.set("paths", "state_root", "state")
        with (self.tmp / "config" / "settings.ini").open("w", encoding="utf-8", newline="\n") as h:
            parser.write(h)
        (docs / "workspace").mkdir()
        (docs / "temp").mkdir()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)
        shutil.rmtree(getattr(self, "documents_root", Path("/__missing__")), ignore_errors=True)

    def test_state_is_internal_not_tool_visible_by_default(self):
        paths = load_path_settings(self.tmp)
        policy = load_file_access_policy(self.tmp, capability="core")
        self.assertIn(paths["state_root"], policy.internal_roots)
        self.assertNotIn(paths["state_root"], policy.read_roots)
        self.assertNotIn(paths["state_root"], policy.write_roots)
        with self.assertRaises(PermissionError):
            authorize_path(paths["state_root"] / "checkpoint.json", policy.read_roots, access="read")

    def test_capability_override_can_add_state_without_widening_core(self):
        parser = configparser.ConfigParser(interpolation=None)
        settings = self.tmp / "config" / "settings.ini"
        parser.read(settings, encoding="utf-8-sig")
        parser.set("file_access_overrides", "file_read.read_add", "@state")
        with settings.open("w", encoding="utf-8", newline="\n") as h:
            parser.write(h)
        state_root = load_path_settings(self.tmp)["state_root"]
        core = load_file_access_policy(self.tmp, capability="core")
        reader = load_file_access_policy(self.tmp, capability="file_read")
        self.assertNotIn(state_root, core.read_roots)
        self.assertIn(state_root, reader.read_roots)


    def test_maintenance_uses_canonical_state_root(self):
        paths = load_path_settings(self.tmp)
        maintainer = DeepHistoryMaintainer(None, None, runtime_root=self.tmp, config={})
        self.assertEqual(maintainer.state_root, paths["state_root"])

    def test_non_hardlock_still_rejects_outside_root(self):
        policy = load_file_access_policy(self.tmp, capability="soft_delete")
        outside = self.tmp.parent / "outside.txt"
        with self.assertRaisesRegex(PermissionError, "outside allowed write roots"):
            authorize_path(outside, policy.write_roots, access="write", hardlock=False)


if __name__ == "__main__":
    unittest.main()
