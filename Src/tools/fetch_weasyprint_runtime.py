from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import tempfile
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "package-manifest.json"
DEFAULT_RUNTIME = ROOT / "tools" / "weasyprint" / "runtime"


class RuntimeFetchError(RuntimeError):
    pass


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def config() -> dict:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8-sig"))
    cfg = dict((manifest.get("bundled_tools") or {}).get("weasyprint") or {})
    if not cfg:
        raise RuntimeFetchError("package-manifest.json has no bundled_tools.weasyprint configuration")
    return cfg


def validate_runtime(runtime: Path, *, expected_version: str, render: bool = True) -> dict:
    exe = runtime / "weasyprint.exe"
    if not exe.is_file():
        raise RuntimeFetchError(f"WeasyPrint executable is missing: {exe}")
    info = subprocess.run(
        [str(exe), "--info"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        check=False,
    )
    combined = (info.stdout or "") + (info.stderr or "")
    if info.returncode != 0:
        raise RuntimeFetchError(f"WeasyPrint --info failed rc={info.returncode}: {combined[-2000:]}")
    if expected_version and f"WeasyPrint version: {expected_version}" not in combined:
        raise RuntimeFetchError(
            f"WeasyPrint version mismatch; expected {expected_version}: {combined[-2000:]}"
        )
    if "Pango version:" not in combined:
        raise RuntimeFetchError("WeasyPrint did not report a Pango runtime")

    pdf_bytes = 0
    if render:
        with tempfile.TemporaryDirectory(prefix="norm-weasyprint-validate-") as td:
            temp = Path(td)
            html = temp / "smoke.html"
            pdf = temp / "smoke.pdf"
            html.write_text(
                "<html><body><h1>Norm WeasyPrint check</h1><p>Pango render.</p></body></html>",
                encoding="utf-8",
            )
            rendered = subprocess.run(
                [str(exe), str(html), str(pdf)],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=60,
                check=False,
            )
            if rendered.returncode != 0:
                raise RuntimeFetchError(
                    "WeasyPrint render failed rc="
                    f"{rendered.returncode}: {((rendered.stdout or '') + (rendered.stderr or ''))[-2000:]}"
                )
            if not pdf.is_file() or pdf.stat().st_size < 5 or pdf.read_bytes()[:5] != b"%PDF-":
                raise RuntimeFetchError("WeasyPrint did not create a valid PDF")
            pdf_bytes = pdf.stat().st_size

    return {"version": expected_version, "pdf_bytes": pdf_bytes, "info": combined.strip()}


def _safe_extract(zf: zipfile.ZipFile, destination: Path) -> None:
    base = destination.resolve()
    for info in zf.infolist():
        candidate = (destination / info.filename).resolve()
        try:
            candidate.relative_to(base)
        except ValueError as exc:
            raise RuntimeFetchError(f"Unsafe path in WeasyPrint archive: {info.filename}") from exc
    zf.extractall(destination)


def ensure_runtime(
    *,
    archive: Path | None = None,
    destination: Path = DEFAULT_RUNTIME,
    force: bool = False,
    render_check: bool = True,
    quiet: bool = False,
) -> dict:
    cfg = config()
    expected_version = str(cfg.get("version") or "").strip()
    url = str(cfg.get("upstream_url") or "").strip()
    expected_hash = str(cfg.get("upstream_archive_sha256") or "").strip().lower()
    if not url or len(expected_hash) != 64:
        raise RuntimeFetchError("WeasyPrint upstream_url/archive SHA-256 is missing from package manifest")

    destination = destination.resolve()
    if not force:
        try:
            result = validate_runtime(destination, expected_version=expected_version, render=render_check)
            if not quiet:
                print(f"WeasyPrint runtime already valid: {destination}")
            return {"status": "existing", "runtime": str(destination), **result}
        except Exception:
            pass

    with tempfile.TemporaryDirectory(prefix="norm-weasyprint-fetch-") as td:
        work = Path(td)
        source_archive = Path(archive).resolve() if archive else work / "weasyprint-windows-onedir.zip"
        if archive is None:
            if not quiet:
                print(f"Downloading official WeasyPrint {expected_version} Windows runtime")
                print(url)
            request = urllib.request.Request(url, headers={"User-Agent": "Norm-Installer/1.6.8"})
            with urllib.request.urlopen(request, timeout=120) as response, source_archive.open("wb") as out:
                shutil.copyfileobj(response, out)
        if not source_archive.is_file():
            raise RuntimeFetchError(f"WeasyPrint archive does not exist: {source_archive}")

        actual = sha256(source_archive)
        if actual.lower() != expected_hash:
            raise RuntimeFetchError(
                f"WeasyPrint archive SHA-256 mismatch: expected {expected_hash}, got {actual}"
            )

        extract = work / "extract"
        extract.mkdir()
        with zipfile.ZipFile(source_archive, "r") as zf:
            _safe_extract(zf, extract)

        source = extract / "onedir" / "weasyprint"
        if not (source / "weasyprint.exe").is_file():
            matches = list(extract.rglob("weasyprint.exe"))
            if len(matches) != 1:
                raise RuntimeFetchError("Official WeasyPrint archive layout was not recognized")
            source = matches[0].parent

        staged = destination.parent / (destination.name + ".new")
        shutil.rmtree(staged, ignore_errors=True)
        staged.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(source, staged)
        validate_runtime(staged, expected_version=expected_version, render=render_check)

        old = destination.parent / (destination.name + ".old")
        shutil.rmtree(old, ignore_errors=True)
        if destination.exists():
            destination.replace(old)
        try:
            staged.replace(destination)
        except Exception:
            if old.exists() and not destination.exists():
                old.replace(destination)
            raise
        shutil.rmtree(old, ignore_errors=True)

    result = validate_runtime(destination, expected_version=expected_version, render=render_check)
    if not quiet:
        print(f"Installed verified WeasyPrint runtime: {destination}")
        print(f"archive_sha256={expected_hash}")
    return {"status": "downloaded", "runtime": str(destination), "archive_sha256": expected_hash, **result}


def main() -> int:
    parser = argparse.ArgumentParser(description="Ensure Norm's verified WeasyPrint/Pango Windows runtime.")
    parser.add_argument("--archive", type=Path, help="Use a local copy of the pinned upstream archive.")
    parser.add_argument("--destination", type=Path, default=DEFAULT_RUNTIME)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--no-render-check", action="store_true")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()
    try:
        ensure_runtime(
            archive=args.archive,
            destination=args.destination,
            force=args.force,
            render_check=not args.no_render_check,
            quiet=args.quiet,
        )
        return 0
    except Exception as exc:
        if not args.quiet:
            print(f"WEASYPRINT_RUNTIME_ERROR: {type(exc).__name__}: {exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
