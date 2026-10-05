from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path
from public_release_guard import scan_tree, scan_zip

ROOT=Path(__file__).resolve().parents[1]
SOURCE=ROOT/"src"/"Norm"
manifest=json.loads((SOURCE/"package-manifest.json").read_text(encoding="utf-8"))
version=str(manifest["version"])
runtime=SOURCE/"tools"/"weasyprint"/"runtime"/"weasyprint.exe"
if not runtime.is_file():
    raise SystemExit("WeasyPrint runtime is not tracked in Git. Run python src/Norm/tools/fetch_weasyprint_runtime.py first.")

archive=ROOT/f"Norm-{version}-portable-source.zip"
companion=ROOT/f"{archive.name}.sha256"
root_name=f"Norm-{version}"
scan_tree(SOURCE)
tmp=archive.with_suffix(archive.suffix+".tmp")
tmp.unlink(missing_ok=True)
try:
    with zipfile.ZipFile(tmp,"w",zipfile.ZIP_DEFLATED,compresslevel=9) as zf:
        for path in sorted(SOURCE.rglob("*")):
            if not path.is_file(): continue
            rel=path.relative_to(SOURCE)
            if "__pycache__" in rel.parts or path.suffix.lower()==".pyc": continue
            zf.write(path,f"{root_name}/{rel.as_posix()}")
    scan_zip(tmp)
    tmp.replace(archive)
except Exception:
    tmp.unlink(missing_ok=True)
    raise
h=hashlib.sha256(archive.read_bytes()).hexdigest()
companion.write_text(f"{h}  {archive.name}\n",encoding="utf-8")
print(archive.name,h)
