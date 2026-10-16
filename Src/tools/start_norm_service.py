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


def _worker_identity_valid(root: Path) -> bool:
    """Any old duplicate pool is a reason not to launch more work."""
    sys.path.insert(0, str(root / "core"))
    from norm_runtime.celery_shutdown import _owned_workers
    from norm_runtime.celery_tool_executor import CFG
    workers = _owned_workers(root)
    ids = [int(row["number"]) for row in workers]
    wanted = int(CFG["count"])
    if len(ids) != len(set(ids)) or len(ids) > wanted or any(x < 1 or x > wanted for x in ids):
        print(f"Refusing Norm startup: found {len(ids)} Celery processes for {wanted} configured slots.",
              file=sys.stderr)
        print("Stop orphaned workers first with C:\\Norm\\Safe-Shutdown-Norm.bat; "
              "the PostgreSQL server is not stopped.", file=sys.stderr)
        return False
    return True


def main() -> int:
    with startup_lock():
        root = Path(__file__).resolve().parents[1]
        if not _worker_identity_valid(root):
            return 3
        if norm_process_running():
            print("Existing norm.exe detected; attaching to the existing service.")
            return 2

        try:
            from start_async_workers import main as start_celery
            start_celery()
        except Exception as exc:
            print(f"Celery unavailable; Norm retains synchronous tool execution: {exc}")

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
