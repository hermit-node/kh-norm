"""Bootstrap current Norm from the ORIGINAL native Win32 workspace.

- Reads native user-scoped JSON font preferences.
- Updates only [workspace] font settings in Norm's INI.
- Calls the CURRENT roles startup, which calls Run-Norm.bat --service-only.
- Never launches duplicate N1/N2/Prompt/Celery sessions.
"""
from __future__ import annotations
import argparse
import json
import subprocess
import sys
from pathlib import Path

def native_font_updates(config: Path) -> dict[str, str]:
    from workspace_settings import validate_font
    data=json.loads(config.read_text(encoding="utf-8-sig"))
    if not isinstance(data,dict):
        raise ValueError("Native workspace preferences are not an object")
    updates={}
    for role in ("runtime","replies","prompt","ui"):
        options=(
            data.get(f"{role}_font_face"),
            data.get(f"{role}_font_size"),
            data.get(f"{role}_font_weight"),
        )
        if any(value is None for value in options):
            continue  # Don't overwrite unspecified preferences
        face,size,weight=options
        validated=validate_font(role,face,int(size),weight)
        for key,value in validated.items():
            updates[f"{role}_font_{'face' if key=='face' else 'size' if key=='size' else 'weight'}"]=str(value)
    if "profiled_launch" in data:
        updates["profiled_launch"]="true" if bool(data["profiled_launch"]) else "false"
    return updates

def main(argv=None)->int:
    parser=argparse.ArgumentParser()
    parser.add_argument("--root",type=Path,required=True)
    parser.add_argument("--config",type=Path,required=True)
    args=parser.parse_args(argv)
    root=args.root.resolve()
    launcher=root/"tools/workspace/start_roles.py"
    python=root/".venv/Scripts/python.exe"
    if not launcher.is_file() or not python.is_file():
        raise FileNotFoundError("Current Norm roles launcher or interpreter missing")
    sys.path.insert(0,str(root/"tools/workspace"))
    from workspace_settings import persist_ini_workspace,patch_workspace_section
    settings=root/"config/settings.ini"
    native=args.config.resolve()
    if not native.is_file():
        raise FileNotFoundError("Native workspace font preferences missing: "+str(native))
    changes=native_font_updates(native)
    if changes:
        current=settings.read_text(encoding="utf-8-sig")
        desired=patch_workspace_section(current,changes)
        if current!=desired:
            persist_ini_workspace(settings,changes)
            print("Native fonts synchronized for NEW operator terminals; existing roles untouched.",flush=True)
    print("Booting or attaching to current Norm service and operator roles...",flush=True)
    done=subprocess.run([str(python),"-B",str(launcher)],
                        cwd=str(root),check=False,timeout=290)
    return int(done.returncode)

if __name__=="__main__":
    raise SystemExit(main())
