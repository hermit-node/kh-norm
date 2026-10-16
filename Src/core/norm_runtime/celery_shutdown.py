"""Stop verified Norm-local Celery worker processes after Norm checkpoints N2.

Do not depend on Redis heartbeats: a stale/inoperative Celery process
may still be alive even if its heartbeat has expired. Enumerate only
Windows processes whose command line contains the exact installed worker
script path. Never kill all python.exe/pythonw.exe processes.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
import subprocess
import time


def _owned_workers(root: Path) -> list[dict]:
    if os.name != "nt":
        return []
    script = str(root.resolve() / "tools" / "celery_tool_worker.py").casefold().replace("/", "\\\\")
    py_dir = str(root.resolve() / ".venv" / "Scripts").casefold().replace("/", "\\\\")
    query = (
        "Get-CimInstance Win32_Process -Filter \"Name='python.exe' OR Name='pythonw.exe'\" "
        "| Select-Object ProcessId,ParentProcessId,CommandLine "
        "| ConvertTo-Json -Compress"
    )
    proc = subprocess.run(["powershell.exe", "-NoLogo", "-NoProfile", "-NonInteractive",
                           "-Command", query], capture_output=True, text=True,
                           timeout=15, check=False,
                           creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0))
    if proc.returncode != 0:
        raise RuntimeError("Could not inventory Celery processes using Windows CIM")
    raw = json.loads(proc.stdout.strip() or "[]")
    rows = raw if isinstance(raw,list) else [raw]
    owned = []
    for item in rows:
        if not isinstance(item, dict):
            continue
        cmd = str(item.get("CommandLine") or "").casefold().replace("/", "\\")
        if script not in cmd or py_dir not in cmd:
            continue
        try:
            pid = int(item["ProcessId"])
            parent = int(item.get("ParentProcessId") or 0)
            import re
            match = re.search(r"--id\s+(\d+)", cmd)
            number = int(match.group(1)) if match else 0
            if pid > 0:
                owned.append({"pid": pid, "parent_pid": parent, "number": number})
        except (ValueError, TypeError, KeyError):
            pass
    by_pid = {row["pid"]: row for row in owned}
    roots = []
    for row in owned:
        ancestor = row["parent_pid"]
        seen = {row["pid"]}
        duplicate_child = False
        while ancestor in by_pid and ancestor not in seen:
            seen.add(ancestor)
            parent = by_pid[ancestor]
            if parent["number"] == row["number"]:
                duplicate_child = True
                break
            ancestor = parent["parent_pid"]
        if not duplicate_child:
            roots.append({"pid": row["pid"], "number": row["number"]})
    # Independently started workers are NOT collapsed: duplicate IDs in
    # unrelated process trees must still trigger the launcher safety guard.
    return roots


def stop_owned_celery_workers(root: str | Path, *, timeout_seconds: float = 15) -> dict:
    root=Path(root).resolve()
    result={"found":0,"gracefully_stopped":0,"force_stopped":0,
            "remaining":0,"errors":[]}
    try:
        workers=_owned_workers(root)
    except Exception as exc:
        logging.exception("Celery process inventory unavailable")
        result["errors"].append(f"inventory: {type(exc).__name__}")
        return result
    result["found"]=len(workers)
    if not workers:
        return result
    try:
        from norm_runtime.celery_tool_executor import app
        import socket
        host=socket.gethostname()
        destinations=sorted({
            f"norm-async-{x['number']}@{host}"
            for x in workers if x["number"] > 0
        })
        if destinations:
            app.control.broadcast("shutdown",destination=destinations,timeout=3)
    except Exception:
        logging.exception("Could not broadcast graceful Celery shutdown; PID-scoped fallback follows")
    deadline=time.monotonic()+max(0,min(30,float(timeout_seconds)))
    seen={x["pid"] for x in workers}
    while time.monotonic()<deadline:
        try:now={x["pid"] for x in _owned_workers(root)}
        except Exception as exc:
            result["errors"].append(f"poll: {type(exc).__name__}")
            return result
        if not now.intersection(seen):
            break
        time.sleep(0.75)
    try:still={x["pid"] for x in _owned_workers(root)}
    except Exception as exc:
        result["errors"].append(f"final inventory: {type(exc).__name__}")
        return result
    result["gracefully_stopped"]=len(seen-still)
    for pid in sorted(still.intersection(seen)):
        # Reverify identity immediately before targeting a PID (reuse guard).
        try:
            if pid not in {x["pid"] for x in _owned_workers(root)}:
                result["gracefully_stopped"]+=1
                continue
            stop=subprocess.run(["taskkill.exe","/PID",str(pid),"/T","/F"],
                                capture_output=True,text=True,timeout=10,check=False,
                                creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0))
            if stop.returncode==0:
                result["force_stopped"]+=1
            else:
                result["errors"].append(f"PID {pid}: taskkill returned {stop.returncode}")
        except Exception as exc:
            result["errors"].append(f"PID {pid}: {type(exc).__name__}")
    try:
        result["remaining"]=len(_owned_workers(root))
    except Exception as exc:
        result["errors"].append(f"postcheck: {type(exc).__name__}")
    logging.info("Norm Celery process exit: %s",result)
    return result


def stop_task_celery_worker(root: str | Path, pid: int, jobid: str, redis_client, prefix: str) -> bool:
    """Hard-stop one verified Norm solo worker ONLY while it owns this job.

    Never terminate other Celery workers or unverified process IDs. The Redis
    active assignment closes most stale-marker cases; a final Windows process
    identity check also guards recycled PIDs and unrelated Python processes.
    """
    if os.name != "nt" or int(pid) <= 0:
        return False
    active_key = prefix + ":active:" + str(pid)
    try:
        if redis_client.get(active_key) != jobid.encode("utf-8"):
            return False
        if int(pid) not in {int(w["pid"]) for w in _owned_workers(Path(root).resolve())}:
            return False
        # Recheck immediately before sending the process-scoped kill.
        if redis_client.get(active_key) != jobid.encode("utf-8"):
            return False
        proc = subprocess.run(["taskkill.exe", "/PID", str(pid), "/T", "/F"],
                              capture_output=True, text=True, timeout=10, check=False,
                              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return proc.returncode == 0
    except Exception:
        logging.exception("Task-scoped Celery worker termination failed dispatch=%s pid=%s", jobid, pid)
        return False
