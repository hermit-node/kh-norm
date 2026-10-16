from __future__ import annotations

import importlib.util
import os
import tempfile
from pathlib import Path

PLUGIN = Path(__file__).resolve().parents[1]
MAIN = PLUGIN / "src" / "main.py"
spec = importlib.util.spec_from_file_location("lesson_model_main", MAIN)
module = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(module)

with tempfile.TemporaryDirectory() as tmp:
    os.environ["NORM_LESSON_MODEL_STATE"] = tmp

    proposed = module.propose_lesson(
        lesson="When a bounded model call hits a size limit, reduce the scope before retrying.",
        category="tool_use",
        tags=["retry_strategy", "chunking"],
        source_refs=["test:smoke"],
    )
    assert proposed["status"] == "candidate"
    lesson_id = proposed["lesson_id"]

    approved = module.approve_lesson(lesson_id, training_eligible=True)
    assert approved["status"] == "approved"
    assert approved["training_eligible"] is True

    exported = module.export_training_corpus("smoke")
    assert exported["lesson_count"] == 1
    assert Path(exported["path"]).is_file()

    build = module.prepare_model_build(
        base_model="example-base:latest",
        model_prefix="norm",
        minimum_lessons=1,
    )
    assert build["candidate_tag"].startswith("norm:")
    assert build["training_enabled"] is False
    assert Path(build["manifest_path"]).is_file()

    status = module.lesson_model_status()
    assert status["training_eligible_lessons"] == 1
    assert status["build_count"] == 1

print("lesson_model smoke test: PASS")
