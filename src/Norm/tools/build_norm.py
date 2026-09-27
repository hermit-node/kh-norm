from __future__ import annotations
import argparse, hashlib, re, shutil, subprocess, sys
from configparser import ConfigParser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SETTINGS = ROOT / "config" / "settings.ini"

def metadata() -> dict[str,str]:
    parser=ConfigParser(interpolation=None)
    with SETTINGS.open("r",encoding="utf-8-sig") as h: parser.read_file(h)
    if not parser.has_section("project"): raise RuntimeError("settings.ini missing [project]")
    result={k:parser.get("project",k,fallback="").strip() for k in ("name","version","author","repository")}
    if not all(result.values()): raise RuntimeError(f"incomplete project metadata: {result}")
    return result

def version_tuple(value: str) -> tuple[int,int,int,int]:
    match=re.fullmatch(r"(\d+)\.(\d+)\.(\d+)(?:([a-zA-Z]))?", value.strip())
    if not match:
        raise ValueError(f"version must look like 0.51.2 or 0.51.2b: {value!r}")
    major,minor,patch=[int(match.group(i)) for i in (1,2,3)]
    suffix=match.group(4)
    revision=(ord(suffix.lower())-ord('a')+1) if suffix else 0
    return (major,minor,patch,revision)

def version_resource(meta: dict[str,str], target: Path) -> None:
    nums=version_tuple(meta["version"])
    target.write_text(f'''VSVersionInfo(
  ffi=FixedFileInfo(filevers={nums}, prodvers={nums}, mask=0x3f, flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[
    StringFileInfo([StringTable('040904B0', [
      StringStruct('CompanyName', {meta['author']!r}),
      StringStruct('FileDescription', {meta['name']!r}),
      StringStruct('FileVersion', {meta['version']!r}),
      StringStruct('InternalName', 'norm'),
      StringStruct('OriginalFilename', 'norm.exe'),
      StringStruct('ProductName', {meta['name']!r}),
      StringStruct('ProductVersion', {meta['version']!r}),
      StringStruct('Comments', {meta['repository']!r}),
    ])]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
''',encoding="utf-8")

def main() -> int:
    ap=argparse.ArgumentParser()
    ap.add_argument("--candidate",default="")
    args=ap.parse_args()
    meta=metadata()
    tag=meta["version"].replace(".","-")
    out=ROOT/"staging"/f"release-{tag}"
    if out.exists(): shutil.rmtree(out)
    (out/"dist").mkdir(parents=True)
    version_file=out/"version_info.txt"
    version_resource(meta,version_file)
    python=ROOT/".venv"/"Scripts"/"python.exe"
    cmd=[str(python),"-m","PyInstaller","--noconfirm","--clean","--onefile","--name","norm","--paths",str(ROOT/"core"),"--version-file",str(version_file),"--distpath",str(out/"dist"),"--workpath",str(out/"work"),"--specpath",str(out/"spec"),str(ROOT/"core"/"norm_main.py")]
    cp=subprocess.run(cmd,cwd=str(ROOT))
    if cp.returncode: return cp.returncode
    candidate=Path(args.candidate) if args.candidate else ROOT/"core"/f"norm-{meta['version']}-candidate.exe"
    shutil.copy2(out/"dist"/"norm.exe",candidate)
    digest=hashlib.sha256(candidate.read_bytes()).hexdigest()
    print(f"candidate={candidate}")
    print(f"version={meta['version']}")
    print(f"author={meta['author']}")
    print(f"repository={meta['repository']}")
    print(f"sha256={digest}")
    print(f"size={candidate.stat().st_size}")
    return 0

if __name__=="__main__": raise SystemExit(main())
