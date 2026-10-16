"""Standalone Windows Celery solo worker, one Python process per worker."""
from __future__ import annotations
import argparse,os,threading
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"core"))
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
        r.delete(heartbeat)
if __name__=="__main__":
    main()