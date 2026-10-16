"""Ensure configured Celery workers are running before starting the Norm service."""
from __future__ import annotations
import os,subprocess,sys,time
from pathlib import Path
root=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(root/"core"))
from norm_runtime.celery_tool_executor import CFG,redis
def main():
    if not CFG["enabled"]:
        print("Celery is disabled; existing synchronous Norm tools remain available.")
        return 0
    r=redis()
    r.ping()
    prefix=CFG["prefix"]+":worker:*"
    online=list(r.scan_iter(match=prefix,count=50))
    needed=max(0,CFG["count"]-len(online))
    if needed==0:
        print(f"Celery already has {len(online)} running worker(s).")
        return 0
    pythonw = root / ".venv" / "Scripts" / "pythonw.exe"
    python = root / ".venv" / "Scripts" / "python.exe"
    py = pythonw if pythonw.is_file() else python
    worker=root/"tools"/"celery_tool_worker.py"
    logs=root/"logs"
    logs.mkdir(parents=True,exist_ok=True)
    for index in range(1,needed+1):
        logfile=logs/f"celery-tool-worker-{index}.log"
        with logfile.open("ab") as output:
            subprocess.Popen([str(py),"-B",str(worker),"--id",str(index)],
                cwd=str(root),stdout=output,stderr=output,stdin=subprocess.DEVNULL,
                # pythonw.exe has no Windows console subsystem. Do not
                # combine DETACHED_PROCESS and CREATE_NO_WINDOW flags.
                creationflags=(0 if py == pythonw else
                               getattr(subprocess,"CREATE_NO_WINDOW",0)),
                env={**os.environ,"NORM_ROOT":str(root)})
    deadline=time.monotonic()+15
    while time.monotonic()<deadline:
        count=sum(1 for _ in r.scan_iter(match=prefix,count=50))
        if count>=CFG["count"]:
            print(f"Celery ready: {count}/{CFG['count']} workers (Redis DB{CFG['db']}).")
            return 0
        time.sleep(.5)
    count=sum(1 for _ in r.scan_iter(match=prefix,count=50))
    print(f"WARNING: Celery ready workers {count}/{CFG['count']}; Norm can use sync fallback.")
    return 0
if __name__=="__main__":
    raise SystemExit(main())