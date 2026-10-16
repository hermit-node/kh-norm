"""Single Windows Celery worker process for Norm terminal checks."""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--instance",type=int,required=True)
    ap.add_argument("--source-root",default="")
    args=ap.parse_args()
    if not 1<=args.instance<=5:
        raise SystemExit("instance must be between 1 and 5")
    runtime_root=Path(os.environ.get("NORM_ROOT") or Path(__file__).resolve().parents[1])
    source_root=Path(args.source_root or runtime_root)
    sys.path.insert(0,str(source_root/"core"))
    os.environ["NORM_ROOT"]=str(runtime_root)
    from norm_runtime.terminal_celery import celery_app, QUEUE
    celery_app.worker_main([
        "worker","--pool=solo","--concurrency=1",
        f"--hostname=norm-terminal-{args.instance}@%h",
        f"--queues={QUEUE}","--loglevel=INFO",
        "--without-gossip","--without-mingle",
    ])


if __name__=="__main__":
    main()
