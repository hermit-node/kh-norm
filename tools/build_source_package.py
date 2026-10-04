from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path

from public_release_guard import scan_tree, scan_zip

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "src" / "Norm"

manifest = json.loads((SOURCE / "package-manifest.json").read_text(encoding="utf-8"))
version = str(manifest["version"])
archive = ROOT / f"Norm-{version}-portable-source.zip"
companion = ROOT / f"{archive.name}.sha256"
root_name = f"Norm-{version}"

# Fail closed before any public artifact is created.
checked_tree = scan_tree(SOURCE)
print(f"Public-release preflight passed: {checked_tree} source files checked.")

temp_archive = archive.with_suffix(archive.suffix + ".tmp")
if temp_archive.exists():
    temp_archive.unlink()

try:
    with zipfile.ZipFile(temp_archive, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(SOURCE.rglob("*")):
            if not path.is_file():
                continue
            relative = path.relative_to(SOURCE)
            if "__pycache__" in relative.parts or path.suffix == ".pyc":
                continue
            zf.write(path, f"{root_name}/{relative.as_posix()}")

    # Scan the actual bytes that would be promoted, not only the source tree.
    checked_zip = scan_zip(temp_archive)
    print(f"Public-release ZIP scan passed: {checked_zip} archive files checked.")
    temp_archive.replace(archive)
except Exception:
    temp_archive.unlink(missing_ok=True)
    raise

digest = hashlib.sha256(archive.read_bytes()).hexdigest()
companion.write_text(f"{digest}  {archive.name}\n", encoding="utf-8", newline="\n")
print(f"{archive.name}: {digest}")
