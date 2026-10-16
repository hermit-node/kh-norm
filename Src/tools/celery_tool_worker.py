"""Standalone Windows Celery solo worker, one Python process per worker."""
from __future__ import annotations
import argparse,os,threading
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
from norm_runtime.celery_tool_executor import CFG,app,redis
def main():
    if not CFG["enabled"]:raise SystemExit("Celery disabled in config/settings.ini")
    ap=argparse.ArgumentParser()
    ap.add_argument("--id",type=int,required=True)
    number=ap.parse_args().id
    r=redis()
    heartbeat=f"{CFG['prefix']}:worker:{os.getpid()}"
    stop=threading.Event()
    def ping():
        while not stop.is_set():
            try:r.set(heartbeat,str(number),ex=30)
            except Exception:pass
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
if __name__=="__main__":
    main()