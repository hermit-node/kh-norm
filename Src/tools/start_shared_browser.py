"""Start Norm's hidden Chromium helper once in the logged-in Windows desktop."""
from __future__ import annotations
import ctypes
import json
import os
from pathlib import Path
import subprocess
import time
from urllib import request

ROOT=Path(__file__).resolve().parents[1]
APP=ROOT/"tools/shared_browser/app.py"
TOKEN=ROOT/"state/shared_browser_token"


def _interactive_console() -> bool:
    if os.name!="nt":return False
    from ctypes import wintypes
    session=wintypes.DWORD(0)
    if not ctypes.windll.kernel32.ProcessIdToSessionId(os.getpid(),ctypes.byref(session)):
        return False
    current=int(session.value)
    active=int(ctypes.windll.kernel32.WTSGetActiveConsoleSessionId())
    return current==active and current!=0 and active!=0xFFFFFFFF


def ready() -> bool:
    if not TOKEN.is_file():return False
    token=TOKEN.read_text("ascii").strip()
    if len(token)!=64:return False
    data=json.dumps({"action":"status","args":{}}).encode("utf8")
    req=request.Request("http://127.0.0.1:18767/rpc",data,method="POST",
        headers={"Authorization":"Bearer "+token,"Content-Type":"application/json"})
    try:
        with request.urlopen(req,timeout=1.0) as response:
            obj=json.load(response)
        return bool(obj.get("ok"))
    except Exception:return False


def main() -> int:
    if ready():
        print("Norm Shared Browser already running.")
        return 0
    if not APP.is_file():
        print("Shared Browser app.py not installed; optional component skipped.")
        return 0
    if not _interactive_console():
        print("No matching logged-in interactive Windows session; shared browser not started.")
        return 0
    pythonw=ROOT/".venv/Scripts/pythonw.exe"
    py=pythonw if pythonw.exists() else ROOT/".venv/Scripts/python.exe"
    if not py.exists():
        print("No Norm-local Python to run Shared Browser.")
        return 0
    logs=ROOT/"logs";logs.mkdir(parents=True,exist_ok=True)
    log=(logs/"shared-browser.log").open("ab")
    try:
        proc=subprocess.Popen([str(py),"-B",str(APP)],cwd=str(ROOT),
            stdin=subprocess.DEVNULL,stdout=log,stderr=log,
            creationflags=(0 if py==pythonw else getattr(subprocess,"CREATE_NO_WINDOW",0)),
            env={**os.environ,"NORM_ROOT":str(ROOT),
                 "VIRTUAL_ENV":str(ROOT/".venv")})
    finally:log.close()
    for _ in range(32):
        if ready():
            print("Norm Shared Browser running hidden; ready for N1-approved tools.")
            return 0
        if proc.poll() is not None:
            print("Optional Shared Browser failed to start; see logs/shared-browser.log.")
            return 0
        time.sleep(.25)
    print("Shared Browser launch timed out; see logs/shared-browser.log.")
    return 0

if __name__=="__main__":
    raise SystemExit(main())
