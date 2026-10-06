from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

from fetch_weasyprint_runtime import ensure_runtime

ROOT = Path(__file__).resolve().parents[1]
EXE = ROOT / "tools" / "weasyprint" / "runtime" / "weasyprint.exe"


def main() -> int:
    ensure_runtime(quiet=True)
    info = subprocess.run(
        [str(EXE), "--info"], capture_output=True, text=True, encoding="utf-8",
        errors="replace", timeout=30, check=False,
    )
    combined = (info.stdout or "") + (info.stderr or "")
    if info.returncode != 0 or "WeasyPrint version: 70.0" not in combined or "Pango version:" not in combined:
        raise RuntimeError(f"WeasyPrint info check failed rc={info.returncode}: {combined[-2000:]}")
    with tempfile.TemporaryDirectory(prefix="norm-weasyprint-smoke-") as name:
        root = Path(name)
        html = root / "smoke.html"
        pdf = root / "smoke.pdf"
        html.write_text("<html><body><h1>Norm WeasyPrint smoke</h1><p>Pango render check.</p></body></html>", encoding="utf-8")
        proc = subprocess.run(
            [str(EXE), str(html), str(pdf)], capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=60, check=False,
        )
        if proc.returncode != 0:
            raise RuntimeError(f"WeasyPrint render failed rc={proc.returncode}: {(proc.stdout or '') + (proc.stderr or '')}")
        if not pdf.is_file() or pdf.stat().st_size < 5 or pdf.read_bytes()[:5] != b"%PDF-":
            raise RuntimeError("Bundled WeasyPrint did not create a valid PDF")
        print(f"WEASYPRINT_SMOKE_PASS version=70.0 pdf_bytes={pdf.stat().st_size}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
