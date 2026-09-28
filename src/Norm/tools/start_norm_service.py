from __future__ import annotations

import sys

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
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
