"""Launch the existing native C++ Norm workspace (one-instance Win32 mutex)."""
from __future__ import annotations
import ctypes,os,subprocess,sys,time,configparser
from ctypes import wintypes
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
APP=ROOT/"tools/workspace/app.py"
def interactive():
    if os.name!="nt":return False
    session=wintypes.DWORD()
    if not ctypes.windll.kernel32.ProcessIdToSessionId(os.getpid(),ctypes.byref(session)):
        return False
    active=ctypes.windll.kernel32.WTSGetActiveConsoleSessionId()
    return session.value==active and active!=0xFFFFFFFF and active!=0
def main():
    cfg=configparser.ConfigParser(interpolation=None)
    cfg.read(ROOT/"config/settings.ini",encoding="utf-8-sig")
    if not cfg.getboolean("workspace","enabled",fallback=True):
        console_launcher=ROOT/"tools/start_operator_consoles.py"
        python=ROOT/".venv/Scripts/python.exe"
        if not python.is_file() or not console_launcher.is_file():
            raise FileNotFoundError("Operator launcher or Norm Python is missing")
        result=subprocess.run([str(python),"-B",str(console_launcher)],
                              cwd=str(ROOT),timeout=90,check=False)
        return result.returncode
    native=ROOT/"tools/workspace/native/NormWorkspace.exe"
    if not native.is_file():
        raise FileNotFoundError("Native workspace build is missing: "+str(native))
    if not interactive():
        print("No interactive desktop: workspace startup skipped.")
        return 0
    proc=subprocess.Popen([str(native),"--root",str(ROOT)],
        cwd=str(ROOT),stdin=subprocess.DEVNULL,
        creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0))
    time.sleep(1.0)
    if proc.poll() not in (None,0):
        raise RuntimeError(f"Native workspace failed, exit {proc.returncode}; check native/NormWorkspace.log")
    print("Workspace open or attached; existing instance prevents duplicate UI.")
    return 0
if __name__=="__main__":
    try:raise SystemExit(main())
    except Exception as exc:
        print("Norm Workspace launch error:",exc,file=sys.stderr)
        raise SystemExit(1)
