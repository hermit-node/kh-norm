from __future__ import annotations

# Compatibility shim for pre-schema-2 frozen Norm builds. The maintained writer
# lives under src/_cli.py; older executables may still resolve this root path.
import runpy
from pathlib import Path

_TARGET = Path(__file__).resolve().parent / "src" / "_cli.py"

if __name__ == "__main__":
    runpy.run_path(str(_TARGET), run_name="__main__")
