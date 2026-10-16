from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
import zipfile
from pathlib import Path

PLUGIN = Path(__file__).resolve().parents[1]
SRC = PLUGIN / "src"
sys.path.insert(0, str(SRC))
MAIN = SRC / "main.py"
spec = importlib.util.spec_from_file_location("lesson_model_main_v2", MAIN)
module = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(module)

with tempfile.TemporaryDirectory() as tmp:
    os.environ["NORM_LESSON_MODEL_STATE"] = str(Path(tmp) / "state")
    archive = Path(tmp) / "demo.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("package.json", json.dumps({
            "name": "demo",
            "scripts": {"start": "node src/main.js"},
            "dependencies": {"express": "^5.0.0"}
        }))
        z.writestr("src/main.js", "const express=require('express');\nconst app=express();\napp.listen(3000);\n")
        z.writestr("src/lib.js", "module.exports = {answer: 42};\n")
        z.writestr("node_modules/nope.js", "ignored\n")

    scan = module.code_memory_scan_zip(str(archive), "Demo", "1.2.3")
    assert scan["project_name"] == "Demo"
    assert scan["version_label"] == "1.2.3"
    assert scan["text_candidate_count"] >= 3
    retained = Path(scan["retained_source_path"])
    assert retained.is_file()
    assert retained != archive
    archive.unlink()
    assert not archive.exists()
    assert retained.exists()

    chunk = module.code_memory_next(scan["scan_id"], scan["next_cursor"], 5000)
    assert chunk["content"]
    assert chunk["evidence_ref"]

    saved = module.code_memory_save_analysis(
        scan["scan_id"],
        raw_summary={
            "project_overview": {"purpose": "demo"},
            "entrypoints": [{"path": "src/main.js"}],
            "features_complete": ["HTTP server starts"],
            "next_actions": ["add tests"],
            "evidence": [{"refs": [chunk["evidence_ref"]]}],
        },
        analyzed_by_model="smoke-model",
        completeness="prototype",
        intent="demonstrate scan pipeline",
    )
    assert saved["analysis_saved"] is True

    readme = module.code_memory_write_readme(
        scan["scan_id"],
        "# Demo\n\nA generated human-facing README.\n"
    )
    assert Path(readme["path"]).is_file()

    got = module.code_memory_get(scan["scan_id"])
    assert got["analysis"]["raw_summary"]["project_overview"]["purpose"] == "demo"
    assert got["readme"]["path"]

print("code memory smoke test: PASS")
