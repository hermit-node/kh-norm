from __future__ import annotations
import json,zipfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
SRC=ROOT/'Src'
ZIP=ROOT/'Norm-0.53.20-portable-source.zip'
manifest=json.loads((ROOT/'PUBLIC_SOURCE_MANIFEST.json').read_text(encoding='utf-8'))
assert manifest['norm_version']=='0.53.20'
assert manifest['installer_version']=='1.6.10-unified'
for rel in ['Norm-Installer.py','installer_environment.py','Norm-Installer.spec','Build-Installer.py','Build-Installer.bat','Run-Installer.bat','Norm-0.53.20-portable-source.zip']:
    assert (ROOT/rel).is_file(), rel
assert 'INSTALLER_VERSION = "1.6.10-unified"' in (ROOT/'Norm-Installer.py').read_text(encoding='utf-8')
assert not (ROOT/'norm-imprint.local.json').exists()
assert not (SRC/'core'/'norm.exe').exists()
assert not (SRC/'tools'/'weasyprint'/'runtime').exists()
with zipfile.ZipFile(ZIP) as zf:
    members={}
    for info in zf.infolist():
        if info.is_dir(): continue
        parts=Path(info.filename).parts
        rel=Path(*parts[1:])
        members[rel.as_posix()]=zf.read(info)
files={p.relative_to(SRC).as_posix():p.read_bytes() for p in SRC.rglob('*') if p.is_file()}
assert set(members)==set(files), (set(members)-set(files),set(files)-set(members))
for rel,data in members.items(): assert files[rel]==data, rel
print('PASS self-contained Norm 0.53.20 / Installer 1.6.10 kit')
print(f'PASS ZIP and Src match exactly: {len(files)} files')
