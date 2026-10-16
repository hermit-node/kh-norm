"""Idempotently launch five separate Windows Celery workers on Redis DB5.

Default: five solo child processes; each handles one Celery check at a time.
This is independent of core/norm.exe and survives that executable restarting.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--count",type=int,default=5)
    parser.add_argument("--source-root",default="")
    parser.add_argument("--runtime-root",default="")
    parser.add_argument("--status",action="store_true")
    args=parser.parse_args()
    if not 1<=args.count<=5:
        raise SystemExit("count must be 1..5")
    root=Path(args.runtime_root or Path(__file__).resolve().parents[1]).resolve()
    source=Path(args.source_root or root).resolve()
    entry=source/"tools"/"terminal_worker_entry.py"
    python=root/".venv"/"Scripts"/"python.exe"
    if not python.is_file() or not entry.is_file():
        raise RuntimeError("Missing Python or terminal_worker_entry.py")
    state=root/"state"/"terminal-celery-worker-pids.json"
    logs=root/"logs"/"terminal_celery"
    logs.mkdir(parents=True,exist_ok=True)
    state.parent.mkdir(parents=True,exist_ok=True)
    prior=json.loads(state.read_text(encoding="utf-8")) if state.is_file() else {}

    # Resolve live PIDs by their exact Windows command line, not just a PID
    # file that could reference an unrelated process after Windows restarts.
    discovery=subprocess.run(
        ["powershell","-NoProfile","-Command",
        "Get-CimInstance Win32_Process | "
        "Where-Object { $_.CommandLine -like '*terminal_worker_entry.py*' } | "
        "Select-Object ProcessId,CommandLine | ConvertTo-Json -Compress"],
        capture_output=True,text=True,timeout=15)
    live=[]
    if discovery.returncode==0 and discovery.stdout.strip():
        parsed=json.loads(discovery.stdout)
        live=parsed if isinstance(parsed,list) else [parsed]
    elif discovery.returncode!=0:
        raise RuntimeError("Cannot safely inspect existing worker processes")
    running={}
    for item in live:
        cmd=str(item.get("CommandLine") or "")
        for i in range(1,6):
            if f"--instance {i}" in cmd:
                running[i]=int(item["ProcessId"])
    if args.status:
        print(json.dumps({"runtime":str(root),"workers":running,"expected":args.count},indent=2))
        return
    launched={}
    for i in range(1,args.count+1):
        if i in running:
            print(f"worker {i}: existing PID {running[i]}")
            continue
        target=logs/f"worker-{i}.log"
        env=os.environ.copy()
        env["NORM_ROOT"]=str(root)
        args_list=[str(python),"-u",str(entry),"--instance",str(i),
                   "--source-root",str(source)]
        # Runtime, plugins and secrets remain separate; child needs the same
        # configured network endpoints but has no project mutation tools.
        with target.open("ab") as stream:
            flags=(getattr(subprocess,"CREATE_NO_WINDOW",0) |
                   getattr(subprocess,"DETACHED_PROCESS",0))
            p=subprocess.Popen(args_list,cwd=str(root),env=env,
                stdin=subprocess.DEVNULL,stdout=stream,stderr=subprocess.STDOUT,
                creationflags=flags,close_fds=True)
        launched[i]=p.pid
        print(f"worker {i}: started PID {p.pid}")
    state.write_text(json.dumps({"source":str(source),"runtime":str(root),
       "workers":{**{str(k):v for k,v in running.items()},
                  **{str(k):v for k,v in launched.items()}},
       "started_at":time.time()},indent=2),encoding="utf-8")
    print("Celery DB5 workers are independent of the running norm.exe.")


if __name__=="__main__":
    main()
