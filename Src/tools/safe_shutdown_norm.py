"""Safe shutdown of THIS Norm installation, with post-exit verification.

Never force-kill a live norm.exe service with an uncheckpointed task.
The Norm service must handle its own checkpoint and psycopg pool close.
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import subprocess
import sys
import time
from urllib import request

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"core"))
from norm_runtime.settings import load_ports


def _ps_processes() -> list[dict]:
    cmd=(
        "Get-CimInstance Win32_Process -Filter \"Name='norm.exe'\" | "
        "Select-Object ProcessId,ExecutablePath | ConvertTo-Json -Compress"
    )
    pr=subprocess.run(["powershell.exe","-NoProfile","-NonInteractive",
                       "-Command",cmd],capture_output=True,text=True,timeout=12,
                       creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0))
    if pr.returncode!=0:
        raise RuntimeError("Cannot read Windows process inventory safely")
    data=json.loads(pr.stdout.strip() or "[]")
    return data if isinstance(data,list) else [data]


def running_norm_pids() -> list[int]:
    target=str((ROOT/"core/norm.exe").resolve()).casefold()
    result=[]
    for item in _ps_processes():
        if not isinstance(item,dict):continue
        value=item.get("ExecutablePath")
        if not value:
            raise RuntimeError("A norm.exe process lacks a readable executable path; refusing cleanup")
        if str(value).casefold()==target:
            result.append(int(item["ProcessId"]))
    return result


def _tailscale_ip() -> str:
    command=subprocess.run(["tailscale","ip","-4"],capture_output=True,text=True,timeout=5)
    if command.returncode!=0:
        raise RuntimeError("Tailscale IPv4 unavailable; no unsafe network fallback")
    addresses=[x.strip() for x in command.stdout.splitlines() if x.strip()]
    if not addresses:
        raise RuntimeError("No Tailscale IPv4 available")
    return addresses[0]


def graceful_shutdown(*, timeout:float=300, dry_run:bool=False) -> int:
    timeout=max(10,min(3600,float(timeout)))
    pids=running_norm_pids()
    print(f"Norm runtime processes for this installation: {pids}",flush=True)
    if dry_run:
        print("Dry-run only: would request graceful API shutdown, wait for service, "
              "and verify Celery workers, 3 consoles, GUI/browser helpers and PostgreSQL pool close.")
        return 0
    if pids:
        try:
            ip=_tailscale_ip()
            url=f"http://{ip}:{load_ports(ROOT)['activity']}/control/shutdown-norm"
            req=request.Request(url,data=b"{}",method="POST",
                    headers={"Content-Type":"application/json"})
            with request.urlopen(req,timeout=12) as reply:
                accepted=json.load(reply)
            if not isinstance(accepted,dict) or accepted.get("status")!="ok":
                raise RuntimeError(f"Norm did not acknowledge shutdown: {accepted}")
            print("Shutdown acknowledged; waiting for checkpoint, work drain and pool release...",flush=True)
        except Exception as exc:
            print(f"Shutdown NOT requested: {type(exc).__name__}: {exc}",file=sys.stderr)
            print("Norm is still alive; refusing to force-kill it or the worker processes.",file=sys.stderr)
            return 2
        deadline=time.monotonic()+timeout
        while time.monotonic()<deadline:
            remaining=running_norm_pids()
            if not remaining:
                break
            time.sleep(1.5)
        else:
            print("Norm is still running after the configured wait period.",file=sys.stderr)
            print("Its existing task drain may still be active; no destructive force-kill was attempted.",file=sys.stderr)
            return 3
    # Norm's own finally block should have shut down all helpers already.
    # A crashed or old-version service may leave orphaned helpers: clear
    # only verified workers and send normal operator-console shutdown signal.
    from norm_runtime.celery_shutdown import stop_owned_celery_workers
    from norm_runtime.shutdown_auxiliary import stop_owned_auxiliaries
    result=stop_owned_celery_workers(ROOT,timeout_seconds=8)
    print("Verified Norm Celery workers:",json.dumps(result),flush=True)
    aux=stop_owned_auxiliaries(ROOT)
    print("Norm console/GUI/browser cleanup:",json.dumps(aux),flush=True)
    for failed in ("task-stop-failed","task-inspection-failed"):
        if aux.get("desktop_gui")==failed:
            print(f"Desktop GUI cleanup unverified: {failed}",file=sys.stderr)
            return 8
    if "console_error" in aux:
        print("Operator console shutdown signalling failed.",file=sys.stderr)
        return 8
    if result["remaining"] or result["errors"]:
        print("Warning: some Norm-owned workers could not be confirmed stopped.",file=sys.stderr)
        return 4
    # Ownership verification after graceful service shutdown: no Norm-local
    # Celery or operator-console process may remain. Do NOT stop PostgreSQL
    # or Redis servers, do NOT disconnect clients owned by other applications.
    from norm_runtime.celery_shutdown import _owned_workers
    sys.path.insert(0,str(ROOT/"tools"))
    from start_operator_consoles import _owned_host_rows
    from start_shared_browser import ready as browser_ready
    remaining={}
    deadline=time.monotonic()+20
    while True:
        try:
            workers=_owned_workers(ROOT)
            hosts=_owned_host_rows()
            browser=browser_ready()
            service_pids=running_norm_pids()
        except Exception as exc:
            print(f"Could not verify all Norm-owned processes: {exc}",file=sys.stderr)
            return 6
        remaining={"norm.exe":service_pids,
                   "celery":[p["pid"] for p in workers],
                   "consoles":{role:pids for role,pids in hosts.items() if pids},
                   "shared_browser":browser}
        if not (service_pids or workers or any(hosts.values()) or browser):
            break
        if time.monotonic()>=deadline:
            print("Incomplete cleanup; owned processes remain:",json.dumps(remaining),file=sys.stderr)
            return 7
        time.sleep(.5)
    print("Verified all Norm service/worker/console/browser processes exited.")
    print("Norm client PostgreSQL pools were closed in their owning processes; PostgreSQL SERVER left running.")
    print("Norm stopped. PostgreSQL pool cleanup executed in Norm/Celery process exit paths.")
    print("PostgreSQL server backend processes owned by OTHER applications are not terminated.")
    return 0


def main()->int:
    parser=argparse.ArgumentParser()
    parser.add_argument("--dry-run",action="store_true")
    parser.add_argument("--wait-seconds",type=int,default=300)
    args=parser.parse_args()
    try:return graceful_shutdown(timeout=args.wait_seconds,dry_run=args.dry_run)
    except Exception as exc:
        print(f"Shutdown validation error: {type(exc).__name__}: {exc}",file=sys.stderr)
        return 5

if __name__=="__main__":
    raise SystemExit(main())
