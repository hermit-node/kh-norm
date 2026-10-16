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
spec = importlib.util.spec_from_file_location("lesson_model_graph", MAIN)
module = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(module)

with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp)
    os.environ["NORM_LESSON_MODEL_STATE"] = str(root / "state")
    archive = root / "demo.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("service.py", '''
def normalize(value: int) -> int:
    return value + 1

def compute(value: int, scale: int = 1) -> dict:
    normalized = normalize(value)
    return {"score": normalized * scale}
'''.strip())
        z.writestr("app.py", '''
from service import compute

def handle(raw: int) -> dict:
    result = compute(raw, scale=2)
    return result

if __name__ == "__main__":
    print(handle(4))
'''.strip())
        z.writestr("web/main.js", '''
function render(value) {
  return String(value);
}
render(42);
'''.strip())

    scan = module.code_memory_scan_zip(str(archive), "DemoGraph", "1.0")
    result = module.code_map_build(scan["scan_id"], max_nodes=1000)
    assert result["ok"] is True
    assert Path(result["graph_path"]).is_file()
    assert Path(result["html_path"]).is_file()
    graph = json.loads(Path(result["graph_path"]).read_text(encoding="utf-8"))
    labels = {n["label"] for n in graph["nodes"]}
    assert {"handle", "compute", "normalize"}.issubset(labels)
    calls = [e for e in graph["edges"] if e["kind"] in {"calls", "awaits"}]
    assert any((next(n for n in graph["nodes"] if n["id"] == e["source"])["label"] == "handle" and next(n for n in graph["nodes"] if n["id"] == e["target"])["label"] == "compute") for e in calls)
    edge = next(e for e in calls if next(n for n in graph["nodes"] if n["id"] == e["source"])["label"] == "handle" and next(n for n in graph["nodes"] if n["id"] == e["target"])["label"] == "compute")
    assert any(b["parameter"] == "value" and b["expression"] == "raw" for b in edge["bindings"])
    assert "dict" in edge["expected_response"]
    html = Path(result["html_path"]).read_text(encoding="utf-8")
    assert "Parameter binding" in html
    assert "Full chain" in html
    assert "DemoGraph" in html

print("interactive code topology smoke test: PASS")
