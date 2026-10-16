from __future__ import annotations

import sys
import time
import contextlib
import os
from pathlib import Path

from norm_gui_common import norm_process_running, start_norm_detached


@contextlib.contextmanager
def startup_lock():
    """Serialize competing Run-Norm launch attempts across console sessions."""
    if os.name != "nt":
        yield
        return
    import msvcrt
    root = Path(__file__).resolve().parents[1]
    lock_file = root / "state" / "norm-service-startup.lock"
    lock_file.parent.mkdir(parents=True, exist_ok=True)
    with lock_file.open("a+b") as stream:
        stream.seek(0, os.SEEK_END)
        if stream.tell() == 0:
            stream.write(b"0")
            stream.flush()
        end = time.monotonic() + 35
        while True:
            try:
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                break
            except OSError:
                if time.monotonic() >= end:
                    raise TimeoutError("Norm startup already in progress; refusing another service")
                time.sleep(.15)
        try:
            yield
        finally:
            stream.seek(0)
            msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)


def main() -> int:
    with startup_lock():
        root = Path(__file__).resolve().parents[1]
        if norm_process_running():
            print("Existing norm.exe detected; attaching to the existing service.")
            return 2

        # Migration only: stop exact-path Norm legacy Celery workers once,
        # before launching the direct-execution service. No new workers start.
        try:
            sys.path.insert(0,str(root/"core"))
            from norm_runtime.celery_shutdown import stop_owned_celery_workers
            residual=stop_owned_celery_workers(root,timeout_seconds=1)
            if residual["remaining"] or residual["errors"]:
                print("Legacy Celery cleanup unverified; refusing to start Norm:",residual,file=sys.stderr)
                return 3
        except Exception as exc:
            print(f"Legacy worker inventory failed: {exc}",file=sys.stderr)
            return 3

        try:
            from start_desktop_gui import main as start_gui
            start_gui()
        except Exception as exc:
            print(f"Desktop GUI helper unavailable (no effect on N1/N2): {exc}")

        try:
            process = start_norm_detached()
        except Exception as exc:
            print(f"Could not launch Norm service: {exc}", file=sys.stderr)
            return 1

        print(f"Norm service launch requested in detached --service mode. pid={process.pid}")

        # Catch the common black-hole failure: the frozen executable can die during
        # imports before norm_main.setup_logging() creates/updates norm-runtime.log.
        # Give it a short settling window and surface the dedicated bootstrap stderr.
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline and process.poll() is None:
            time.sleep(0.1)
        rc = process.poll()
        if rc is not None:
            print(f"Norm service exited immediately with code {rc}.", file=sys.stderr)
            root = Path(__file__).resolve().parents[1]
            for name in ("norm-bootstrap.stderr.log", "norm-bootstrap.stdout.log", "norm-runtime.log"):
                path = root / "logs" / name
                if not path.is_file():
                    continue
                try:
                    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()[-40:]
                except Exception:
                    continue
                if lines:
                    print(f"--- tail {path} ---", file=sys.stderr)
                    print("\n".join(lines), file=sys.stderr)
            return 1
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
