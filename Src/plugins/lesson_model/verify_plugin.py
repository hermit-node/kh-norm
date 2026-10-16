from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SRC = ROOT / "src"
MANIFEST = ROOT / "plugin.json"


def source_tree_sha256() -> str:
    h = hashlib.sha256()
    for path in sorted(SRC.rglob("*")):
        rel_path = path.relative_to(SRC)
        if any(part.startswith(".") or part == "__pycache__" for part in rel_path.parts):
            continue
        if path.is_symlink():
            raise RuntimeError(f"source cannot contain symlink: {path}")
        if not path.is_file() or path.suffix == ".pyc":
            continue
        rel = rel_path.as_posix().encode("utf-8")
        data = path.read_bytes()
        h.update(len(rel).to_bytes(4, "big"))
        h.update(rel)
        h.update(len(data).to_bytes(8, "big"))
        h.update(data)
    return h.hexdigest()


manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
actual = source_tree_sha256()
expected = manifest["sha256"]
print(f"expected={expected}")
print(f"actual={actual}")
raise SystemExit(0 if expected == actual else 1)
