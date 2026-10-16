"""Start Norm service if needed, then attach/create only missing operator roles."""
from __future__ import annotations
import subprocess,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
def main():
    service=ROOT/"Run-Norm.bat"
    if not service.is_file():raise FileNotFoundError(service)
    proc=subprocess.run(["cmd.exe","/D","/C",str(service),"--service-only"],
                        cwd=str(ROOT),timeout=200,check=False,
                        creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0))
    if proc.returncode:
        raise RuntimeError(f"Norm service startup failed: {proc.returncode}")
    py=ROOT/".venv/Scripts/python.exe"
    launcher=ROOT/"tools/start_operator_consoles.py"
    proc=subprocess.run([str(py),"-B",str(launcher)],
                        cwd=str(ROOT),timeout=90,check=False,
                        creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0))
    if proc.returncode:
        raise RuntimeError(f"Operator session startup failed: {proc.returncode}")
    return 0
if __name__=="__main__":
    try:raise SystemExit(main())
    except Exception as exc:
        print("Norm Workspace startup failed:",exc,file=sys.stderr,flush=True)
        raise SystemExit(1)
