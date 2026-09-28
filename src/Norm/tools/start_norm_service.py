from __future__ import annotations

import sys

from norm_gui_common import norm_process_running, start_norm_detached


def main() -> int:
    if norm_process_running():
        print("Existing norm.exe detected; refusing to launch a duplicate service.")
        return 2
    start_norm_detached()
    print("Norm service launch requested in detached --service mode.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
