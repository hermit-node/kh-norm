"""Regression checks for the packaged Nabler native/local API and release wiring."""
from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "plugins" / "nabler" / "src"
sys.path[:0] = [str(ROOT / "core"), str(SRC)]


def test_builder():
    from nabler import table

    calls = {"lambda": 0, "inline": 0}
    files = ["good.py", "next.py"]

    def check():
        calls["lambda"] += 1
        return True

    report = table("Test")
    report.add_row("Lambda", check)
    report.add_row("Multiline", script="""
        calls['inline'] += 1
        collected = []
        for item in files:
            collected.append(item.endswith('.py'))
        result = {'ok': all(collected), 'files': len(collected)}
    """)
    report.add_row("Exception", script="raise RuntimeError('check failed')")
    report.add_row("No result", script="x = 5")
    report.add_row("Context", script="result = number > 10", context={"number": 11})
    report.add_row("Result predicate", lambda: 4, success=lambda x: x > 3)
    report.add_row("Unclassified number", 7)
    data = report.as_dict()
    assert [row["status"] for row in data["rows"]] == [
        "PASS", "PASS", "FAIL", "FAIL", "PASS", "PASS", "INFO",
    ]
    assert "files\": 2" in data["rows"][1]["value"]
    assert "RuntimeError" in data["rows"][2]["value"]
    assert "must assign" in data["rows"][3]["value"]
    assert (calls["lambda"], calls["inline"]) == (1, 1)
    report.render(mode="table", output="markdown")
    report.render(mode="checklist", output="plain")
    report.render(mode="table", output="terminal")
    assert (calls["lambda"], calls["inline"]) == (1, 1), "reran a check during rendering"
    report.run(force=True)
    assert (calls["lambda"], calls["inline"]) == (2, 2)
    print("PASS lambda, multiline scripts, scoping, failures, status and lazy/cached execution")


def test_native():
    from main import render_report

    records = [
        {"name": "Build", "value": True},
        {"name": "Count", "value": 3},
        {"name": "Only data", "script": "raise RuntimeError('DO NOT EXECUTE')", "status": "PASS"},
    ]
    output = render_report("User report", records, mode="table", output="markdown")
    assert output["counts"]["PASS"] == 2
    assert output["counts"]["INFO"] == 1
    assert "Only data" in output["rendered"]
    assert "|" in output["rendered"]
    assert "DO NOT EXECUTE" not in output["rendered"]
    assert render_report("x", [{"label": "x", "success": True}], mode="checklist")["rows"][0]["status"] == "PASS"
    for malformed in ([[1]], [{"value": True}], "hello"):
        try:
            render_report("Bad", malformed)
            raise AssertionError("malformed rows were accepted")
        except ValueError:
            pass
    print("PASS native one-call data-only tool, status rendering and input validation")


def test_manifest_plugin_manager():
    from norm_runtime.plugin_identity import source_tree_sha256, verify_identity
    from norm_runtime.plugin_manager import PluginManager

    plugin_root = ROOT / "plugins" / "nabler"
    manifest = json.loads((plugin_root / "plugin.json").read_text(encoding="utf-8"))
    assert verify_identity(plugin_root) is not None
    assert manifest["sha256"] == source_tree_sha256(plugin_root)
    pm = PluginManager(ROOT / "plugins")
    target = "plugin_nabler__main__render_report"
    schemas = pm.schemas()
    assert target in [s["function"]["name"] for s in schemas]
    result = pm.execute(target, {"title": "Build", "rows": [{"name": "OK", "value": True}]})
    assert result["result"]["rows"][0]["status"] == "PASS", result
    assert not (ROOT / "plugins" / "nabler_inline").exists()
    print("PASS schema-2 SHA identity, native plugin manager invocation and single canonical plugin")


def test_release_wiring():
    manifest = json.loads((ROOT / "package-manifest.json").read_text(encoding="utf-8"))
    assert manifest["version"] == "0.55.7"
    assert manifest["built_in_plugins"]["nabler"] == "plugins/nabler"
    assert "plugins/nabler" in manifest["managed_persistent_subtrees"]
    assert "nabler_inline" not in manifest["built_in_plugins"]
    for relative in ("core/requirements.txt", "tools/requirements-lock.txt"):
        assert "tabulate==0.9.0" in (ROOT / relative).read_text(encoding="utf-8")
    config = json.loads((ROOT / "config/runtime.json").read_text(encoding="utf-8"))
    n2 = config["agents"]["n2"]["persistent_instructions"]
    assert any("plugin_nabler__main__render_report" in x for x in n2)
    worker = (ROOT / "core/norm_runtime/prompt_worker.py").read_text(encoding="utf-8")
    assert "prefer the Nabler render_report native tool" in worker
    print("PASS package inventory, config N2 preference, prompt directive and dependency pins")


if __name__ == "__main__":
    test_builder()
    test_native()
    test_manifest_plugin_manager()
    test_release_wiring()
    print("ALL NABLER TESTS PASSED")
