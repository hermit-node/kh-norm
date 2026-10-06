from __future__ import annotations
import hashlib, json, zipfile
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
SRC=ROOT/"src"/"Norm"
ZIP=ROOT/"Norm-0.53.19-portable-source.zip"

manifest=json.loads((SRC/"package-manifest.json").read_text(encoding="utf-8"))
assert manifest["version"]=="0.53.19", manifest["version"]
assert not (ROOT/"Norm-Installer.exe").exists()  # Public source release
assert (ROOT/"Norm-Installer.py").is_file()
assert (ROOT/"installer_environment.py").is_file()
assert ZIP.is_file()
assert Path(str(ZIP)+".sha256").is_file()
assert not (SRC/"tools"/"weasyprint"/"runtime").exists()
assert (SRC/"tools"/"fetch_weasyprint_runtime.py").is_file()
assert (SRC/"docs"/"help_menu.txt").is_file()
assert (SRC/"core"/"norm_runtime"/"model_switch.py").is_file()
assert (SRC/"core"/"norm_runtime"/"public_web.py").is_file()
assert (SRC/"tools"/"test_maintenance_control.py").is_file()
assert (SRC/"tools"/"test_public_web.py").is_file()
assert not (SRC/"core"/"norm.exe").exists()

expected=(Path(str(ZIP)+".sha256").read_text(encoding="ascii").split()[0]).lower()
actual=hashlib.sha256(ZIP.read_bytes()).hexdigest()
assert actual==expected,(actual,expected)

tree={p.relative_to(SRC).as_posix():p.read_bytes() for p in SRC.rglob("*") if p.is_file()}
with zipfile.ZipFile(ZIP) as zf:
    prefix="Norm-0.53.19/"
    zipped={n[len(prefix):]:zf.read(n) for n in zf.namelist() if n.startswith(prefix) and not n.endswith("/")}
assert set(tree)==set(zipped),(sorted(set(tree)-set(zipped))[:10],sorted(set(zipped)-set(tree))[:10])
for rel,data in tree.items():
    assert zipped[rel]==data,rel

print(f"PASS public version 0.53.19")
print(f"PASS src/ZIP exact byte parity files={len(tree)}")
print(f"PASS source SHA256 {actual}")
