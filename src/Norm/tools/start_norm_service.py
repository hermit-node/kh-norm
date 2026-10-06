from __future__ import annotations

import sys
import time
from pathlib import Path

from norm_gui_common import norm_process_running, start_norm_detached


def main() -> int:
    if norm_process_running():
        print("Existing norm.exe detected; attaching to the existing service.")
        return 2

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
