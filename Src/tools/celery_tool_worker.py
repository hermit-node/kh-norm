"""Standalone Windows Celery solo worker, one Python process per worker."""
from __future__ import annotations
import argparse,json,logging,os,threading,time
from pathlib import Path
import sys
norm_root=Path(__file__).resolve().parents[1]
expected=norm_root/".venv"/"Scripts"/"python.exe"
# pythonw.exe is the preferred background interpreter from the same venv.
allowed={str(expected.resolve()).casefold(),
         str((norm_root/".venv"/"Scripts"/"pythonw.exe").resolve()).casefold()}
observed=str(Path(sys.executable).resolve()).casefold()
if observed not in allowed:
    raise SystemExit("Refusing Celery outside Norm local venv; expected "
                     +str(norm_root/".venv"/"Scripts"))
sys.path.insert(0,str(norm_root/"core"))
from norm_runtime.celery_tool_executor import CFG,app,redis,supervisor_key,expiry_reason,dispatch_started_at
def main():
    if not CFG["enabled"]:raise SystemExit("Celery disabled in config/settings.ini")
    ap=argparse.ArgumentParser()
    ap.add_argument("--id",type=int,required=True)
    number=ap.parse_args().id
    r=redis()
    heartbeat=f"{CFG['prefix']}:worker:{os.getpid()}"
    stop=threading.Event()
    ready=threading.Event()
    started=time.monotonic()
    last_supervisor=None
    from celery.signals import worker_ready
    @worker_ready.connect(weak=False)
    def _on_ready(sender=None, **kwargs):
        ready.set()
    def ping():
        nonlocal last_supervisor
        active_key = CFG["prefix"] + ":active:" + str(os.getpid())
        while not stop.is_set():
            now=time.monotonic()
            current_supervisor = None
            try:
                # A Redis lease expires if norm.exe crashes OR its main event
                # loop becomes permanently unresponsive. One transient outage
                # does not immediately interrupt a valid tool operation.
                current_supervisor = r.get(supervisor_key())
                if current_supervisor:
                    last_supervisor=now
                if ready.is_set():
                    r.set(heartbeat,str(number),ex=30)
            except Exception:
                logging.exception("Celery worker heartbeat check failed")
            reason=expiry_reason(started,last_supervisor,now,
                startup_grace=CFG["startup_grace_seconds"],
                orphan_grace=CFG["orphan_grace_seconds"])
            active = None
            if reason is None:
                try:
                    active = r.get(active_key)
                    if active:
                        jobid = active.decode("utf-8")
                        raw = r.get(CFG["prefix"] + ":approved:" + jobid)
                        if raw:
                            approved=json.loads(raw)["approved"]
                            if (approved.get("supervisor_token") and current_supervisor and
                                    approved["supervisor_token"] != current_supervisor.decode("utf-8")):
                                reason="supervisor_replaced"
                            elif time.time()-dispatch_started_at(approved) >= CFG["max_job_age_seconds"]:
                                reason="dispatch_deadline"
                        else:
                            # A live execution without its approval record is
                            # not authorized to run without a time boundary.
                            reason="approval_missing"
                except Exception:
                    logging.exception("Celery execution deadline check failed")
            if reason:
                try:
                    if active is None:
                        active=r.get(active_key)
                    if active:
                        jobid=active.decode("utf-8")
                        state="timed_out" if reason=="dispatch_deadline" else "orphaned"
                        r.set(CFG["prefix"] + ":execution:" + jobid,
                              json.dumps({"state":state,"at":time.time(),"pid":os.getpid(),
                                          "reason":reason}),ex=172800)
                    r.delete(active_key,heartbeat)
                except Exception:
                    logging.exception("Celery watchdog could not persist terminal observation")
                # The solo pool may be blocked in C/native code, so a graceful
                # worker shutdown cannot enforce a hard limit. Kill only self.
                try:
                    os.write(2, f"Celery worker {number} PID {os.getpid()} exiting: {reason}\n".encode())
                except OSError:
                    pass
                os._exit(75)
            stop.wait(5)
    thread=threading.Thread(target=ping,daemon=True)
    thread.start()
    try:
        app.worker_main(["worker","--pool=solo","--concurrency=1",
            "--hostname",f"norm-async-{number}@%h","--queues",CFG["queue"],
            "--loglevel=WARNING","--without-gossip","--without-mingle"])
    finally:
        stop.set()
        try:
            thread.join(timeout=2)
        except Exception:
            pass
        # Each Celery process may have opened ITS OWN PostgreSQL pools during
        # tool execution. The main Norm service cannot close another PID's
        # psycopg_pool connections. Close here before this worker exits.
        try:
            from runtime_bootstrap import close_postgres_pool
            close_postgres_pool(norm_root)
        except Exception:
            import logging
            logging.exception("Celery local PostgreSQL pool shutdown failed")
        try:
            r.delete(heartbeat)
        except Exception:
            pass
        finally:
            # Disconnect only this worker's own Redis client pool.
            try:r.close()
            except Exception:pass
if __name__=="__main__":
    main()