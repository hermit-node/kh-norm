from __future__ import annotations

import json
import re
import sys
from pathlib import Path

HERE=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(HERE/"tools"))
from public_release_guard import scan_tree

count=scan_tree(HERE)
assert count>100,count
manifest=json.loads((HERE/"src"/"Norm"/"package-manifest.json").read_text(encoding="utf-8"))
assert manifest["version"]=="0.53.17"
assert not (HERE/"src"/"Norm"/"tools"/"weasyprint"/"runtime").exists()
assert "src/Norm/tools/weasyprint/runtime/" in (HERE/".gitignore").read_text(encoding="utf-8")

docs=[
 HERE/"src"/"Norm"/"docs"/"README.md",
 HERE/"src"/"Norm"/"docs"/"CURRENT_STATUS.md",
 HERE/"src"/"Norm"/"docs"/"DEVELOPMENT_NOTES.md",
 HERE/"src"/"Norm"/"docs"/"FUTURE_IMPLEMENTATION_NOTES.md",
 HERE/"src"/"Norm"/"docs"/"MAINTENANCE_VERIFICATION.md",
]
old=re.compile(r"0\.53\.(?:[0-9]|1[0-6])|0\.52\.|0\.51\.")
for p in docs:
    assert not old.search(p.read_text(encoding="utf-8")),f"historical artifact in current-state doc: {p}"

release=(HERE/"src"/"Norm"/"docs"/"RELEASE_NOTES.md").read_text(encoding="utf-8")
assert "## 0.53.17" in release
assert not (HERE/"Publish-To-GitHub.ps1").exists()
assert (HERE/"src"/"Norm"/"docs"/"help_menu.txt").is_file()
assert (HERE/"src"/"Norm"/"core"/"norm_runtime"/"model_switch.py").is_file()
assert (HERE/"src"/"Norm"/"tools"/"test_model_switch.py").is_file()
assert not any(HERE.glob("Norm-*.zip"))
assert not (HERE/"Norm-Installer.exe").exists()
example=json.loads((HERE/"norm-imprint.example.json").read_text(encoding="utf-8"))
serialized=json.dumps(example).lower()
assert '"password"' not in serialized and '"token"' not in serialized and '"secret"' not in serialized
print("PASS public tree files",count)
