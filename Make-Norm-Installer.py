from __future__ import annotations

import argparse
import configparser
import hashlib
import json
import os
import queue
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

INSTALLER_VERSION = "1.4.16"
INSTALLER_TEMPLATE_FILENAME = "Norm-Installer.py"
OUTPUT_EXE_FILENAME = f"Norm-Installer-{INSTALLER_VERSION}.exe"
DEFAULT_PIP_VERSION = "26.2.1"
PYPI_TIMEOUT_SECONDS = 8


class BuilderError(RuntimeError):
    pass


@dataclass(frozen=True)
class RequirementPin:
    name: str
    version: str
    line_index: int


@dataclass(frozen=True)
class SourceInfo:
    zip_path: Path
    zip_root: PurePosixPath
    manifest_member: str
    manifest: dict
    requirements_member: str
    requirements_text: str
    pins: tuple[RequirementPin, ...]
    expected_python: tuple[int, int] | None

    @property
    def version(self) -> str:
        return str(self.manifest.get("version") or "unknown")


@dataclass(frozen=True)
class BuildResult:
    exe_path: Path
    source_zip: Path
    sha_path: Path
    payload_sha256: str


def _app_dir() -> Path:
    return Path(__file__).resolve().parent


def _builder_cache_root() -> Path:
    """Stable per-user cache so installer-builder venvs survive kit upgrades and temp cleanup."""
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local"))
    else:
        base = Path(os.environ.get("XDG_CACHE_HOME") or (Path.home() / ".cache"))
    root = base / "Norm" / "InstallerBuilder"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _venv_python(venv: Path) -> Path:
    return venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def _installed_distribution_version(python_exe: Path, distribution: str) -> str | None:
    cp = subprocess.run(
        [
            str(python_exe), "-c",
            "import importlib.metadata as m, sys; "
            "name=sys.argv[1]; "
            "print(m.version(name))",
            distribution,
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=15,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0,
    )
    if cp.returncode != 0:
        return None
    lines = (cp.stdout or "").strip().splitlines()
    return lines[-1].strip() if lines else None


def _pip_check_ok(python_exe: Path, *, cwd: Path, log) -> bool:
    command = [str(python_exe), "-m", "pip", "check"]
    log("$ " + subprocess.list2cmdline(command))
    cp = subprocess.run(
        command,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0,
    )
    combined = "\n".join(part for part in (cp.stdout, cp.stderr) if part).strip()
    if combined:
        for line in combined.splitlines():
            log(line)
    return cp.returncode == 0


def _ensure_reusable_venv(
    *,
    base_python: Path,
    role: str,
    pip_version: str,
    cwd: Path,
    log,
    required: dict[str, str] | None = None,
) -> Path:
    """Reuse a stable venv and invoke pip install only when its required state is actually stale."""
    required = dict(required or {})
    base_version = _python_version(base_python)
    role_key = re.sub(r"[^A-Za-z0-9_.-]+", "-", role).strip("-") or "default"
    venv = _builder_cache_root() / f"{role_key}-py{base_version[0]}.{base_version[1]}"
    python_exe = _venv_python(venv)

    recreate = False
    if python_exe.is_file():
        try:
            cached_version = _python_version(python_exe)
            if cached_version[:2] != base_version[:2]:
                recreate = True
        except Exception:
            recreate = True
    elif venv.exists():
        recreate = True

    if recreate:
        log(f"Cached builder venv is incompatible or incomplete; recreating: {venv}")
        shutil.rmtree(venv, ignore_errors=True)

    if not python_exe.is_file():
        log(f"Creating reusable builder venv: {venv}")
        _run([str(base_python), "-m", "venv", str(venv)], cwd=cwd, log=log)
        if not python_exe.is_file():
            raise BuilderError(f"Reusable builder venv Python is missing: {python_exe}")
    else:
        log(f"Reusing builder venv: {venv}")

    desired = {"pip": pip_version, **required}
    actual = {name: _installed_distribution_version(python_exe, name) for name in desired}
    stale = {name: version for name, version in desired.items() if actual.get(name) != version}

    if not stale and _pip_check_ok(python_exe, cwd=cwd, log=log):
        log("Reusable builder venv already satisfies all required versions; skipping pip install.")
        return python_exe

    if actual.get("pip") != pip_version:
        _run(
            [str(python_exe), "-m", "pip", "install", "--disable-pip-version-check", f"pip=={pip_version}"],
            cwd=cwd,
            log=log,
        )

    packages = [f"{name}=={version}" for name, version in required.items()
                if _installed_distribution_version(python_exe, name) != version]
    if packages:
        _run(
            [str(python_exe), "-m", "pip", "install", "--disable-pip-version-check", *packages],
            cwd=cwd,
            log=log,
        )

    remaining = {
        name: version for name, version in desired.items()
        if _installed_distribution_version(python_exe, name) != version
    }
    if remaining:
        detail = ", ".join(
            f"{name} expected {version}, got {_installed_distribution_version(python_exe, name)!r}"
            for name, version in remaining.items()
        )
        raise BuilderError(f"Reusable builder venv did not converge to required versions: {detail}")
    if not _pip_check_ok(python_exe, cwd=cwd, log=log):
        raise BuilderError(f"Reusable builder venv failed pip check: {venv}")
    return python_exe


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _parse_requirements(text: str) -> tuple[RequirementPin, ...]:
    pins: list[RequirementPin] = []
    for index, raw in enumerate(text.splitlines()):
        stripped = raw.strip()
        if not stripped or stripped.startswith("#") or stripped.startswith("-"):
            continue
        match = re.fullmatch(r"([A-Za-z0-9_.-]+)==([^\s;]+)", stripped)
        if not match:
            raise BuilderError(
                f"Unsupported requirements line {index + 1}: {raw!r}. "
                "The version-choice builder currently requires exact name==version pins."
            )
        pins.append(RequirementPin(match.group(1), match.group(2), index))
    if not pins:
        raise BuilderError("The source requirements lock contains no exact pins.")
    return tuple(pins)


def inspect_source(path: Path) -> SourceInfo:
    path = Path(path).expanduser().resolve()
    if not path.is_file():
        raise BuilderError(f"Base source ZIP is missing: {path}")
    try:
        with zipfile.ZipFile(path, "r") as zf:
            names = zf.namelist()
            manifests = [name for name in names if PurePosixPath(name).name == "package-manifest.json" and not name.endswith("/")]
            if len(manifests) != 1:
                raise BuilderError(f"Expected one package-manifest.json, found {len(manifests)}")
            manifest_member = manifests[0]
            manifest = json.loads(zf.read(manifest_member).decode("utf-8-sig"))
            root = PurePosixPath(manifest_member).parent
            req_rel = PurePosixPath(str(manifest["requirements_lock"]).replace("\\", "/"))
            req_member = str(root / req_rel)
            requirements_text = zf.read(req_member).decode("utf-8-sig")
            settings_rel = PurePosixPath(str(manifest["settings"]).replace("\\", "/"))
            settings_text = zf.read(str(root / settings_rel)).decode("utf-8-sig")
    except (zipfile.BadZipFile, KeyError, json.JSONDecodeError) as exc:
        raise BuilderError(f"Invalid base source ZIP: {exc}") from exc

    parser = configparser.ConfigParser(interpolation=None)
    parser.read_string(settings_text)
    expected_python: tuple[int, int] | None = None
    raw_py = parser.get("environment", "python_version", fallback="").strip()
    match = re.match(r"^(\d+)\.(\d+)", raw_py)
    if match:
        expected_python = (int(match.group(1)), int(match.group(2)))

    return SourceInfo(
        zip_path=path,
        zip_root=root,
        manifest_member=manifest_member,
        manifest=manifest,
        requirements_member=req_member,
        requirements_text=requirements_text,
        pins=_parse_requirements(requirements_text),
        expected_python=expected_python,
    )


def _source_version_key(raw: str) -> tuple:
    Version = _version_class()
    if Version is not None:
        try:
            return (1, Version(raw))
        except Exception:
            pass
    return (0, _fallback_version_key(raw))


def find_latest_base_source(directory: Path | None = None) -> Path:
    """Find the newest Norm portable-source ZIP in the builder folder.

    Manifest version wins first; modification time breaks same-version ties so a
    newly customized/rebuilt ZIP is picked without changing builder source code.
    """
    root = Path(directory).resolve() if directory else _app_dir()
    candidates: list[tuple[tuple, int, str, Path]] = []
    for path in root.glob("*.zip"):
        if not path.is_file():
            continue
        try:
            info = inspect_source(path)
        except Exception:
            continue
        if str(info.manifest.get("name") or "").strip().lower() != "norm":
            continue
        if str(info.manifest.get("package_type") or "").strip() != "portable-source":
            continue
        try:
            mtime = path.stat().st_mtime_ns
        except OSError:
            mtime = 0
        candidates.append((_source_version_key(info.version), mtime, path.name.lower(), path.resolve()))
    if not candidates:
        raise BuilderError(f"No Norm portable-source ZIP was found in {root}")
    return max(candidates, key=lambda item: (item[0], item[1], item[2]))[3]


def _version_class():
    try:
        from packaging.version import Version  # type: ignore
        return Version
    except Exception:
        try:
            from pip._vendor.packaging.version import Version  # type: ignore
            return Version
        except Exception:
            return None


def _fallback_version_key(raw: str) -> tuple:
    text = raw.strip().lower()
    base_match = re.match(r"^(\d+(?:\.\d+)*)(?:(rc|a|b)(\d+))?(?:\.post(\d+))?", text)
    if not base_match:
        return ((-1,), -1, -1, text)
    nums = tuple(int(x) for x in base_match.group(1).split("."))
    nums = nums + (0,) * (8 - len(nums))
    pre = base_match.group(2)
    pre_n = int(base_match.group(3) or 0)
    post = int(base_match.group(4) or 0)
    stage = 3 if pre is None else (2 if pre == "rc" else 1)
    return (nums, stage, pre_n, post)


def _eligible_version(raw: str) -> bool:
    Version = _version_class()
    if Version is not None:
        try:
            value = Version(raw)
        except Exception:
            return False
        if value.is_devrelease:
            return False
        if value.is_prerelease:
            return bool(value.pre and value.pre[0] == "rc")
        return True
    low = raw.lower()
    if "dev" in low:
        return False
    if re.search(r"(?:^|[.\-])(?:a|alpha|b|beta)\d*", low) or re.search(r"\d(?:a|b)\d", low):
        return False
    return True


def _requires_python_allows(specifier: str | None, target_python: str | None) -> bool:
    if not specifier or not target_python:
        return True
    try:
        from packaging.specifiers import SpecifierSet  # type: ignore
    except Exception:
        try:
            from pip._vendor.packaging.specifiers import SpecifierSet  # type: ignore
        except Exception:
            return True
    try:
        return bool(SpecifierSet(str(specifier)).contains(target_python, prereleases=True))
    except Exception:
        return True


def _max_version(versions: list[str]) -> str | None:
    eligible = [v for v in versions if _eligible_version(v)]
    if not eligible:
        return None
    Version = _version_class()
    if Version is not None:
        valid: list[tuple[object, str]] = []
        for raw in eligible:
            try:
                valid.append((Version(raw), raw))
            except Exception:
                continue
        return max(valid, key=lambda x: x[0])[1] if valid else None
    return max(eligible, key=_fallback_version_key)


def fetch_latest_eligible(package: str, target_python: str | None = None) -> str:
    canonical = package.replace("_", "-")
    url = f"https://pypi.org/pypi/{urllib.parse.quote(canonical, safe='')}/json"
    request = urllib.request.Request(url, headers={"User-Agent": f"Norm-Installer-Builder/{INSTALLER_VERSION}"})
    try:
        with urllib.request.urlopen(request, timeout=PYPI_TIMEOUT_SECONDS) as response:
            payload = json.load(response)
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise BuilderError(f"PyPI lookup failed: {exc}") from exc
    releases = payload.get("releases") or {}
    versions: list[str] = []
    for version, files in releases.items():
        if not files:
            continue
        usable = [
            item for item in files
            if not bool(item.get("yanked"))
            and _requires_python_allows(item.get("requires_python"), target_python)
        ]
        if not usable:
            continue
        versions.append(str(version))
    latest = _max_version(versions)
    if not latest:
        raise BuilderError("No eligible stable/release-candidate release found")
    return latest




def _canonical_name(name: str) -> str:
    try:
        from packaging.utils import canonicalize_name  # type: ignore
        return str(canonicalize_name(name))
    except Exception:
        try:
            from pip._vendor.packaging.utils import canonicalize_name  # type: ignore
            return str(canonicalize_name(name))
        except Exception:
            return re.sub(r"[-_.]+", "-", name).lower()


def _is_release_candidate(raw: str) -> bool:
    Version = _version_class()
    if Version is not None:
        try:
            value = Version(raw)
        except Exception:
            return False
        return bool(value.is_prerelease and value.pre and value.pre[0] == "rc")
    return bool(re.search(r"rc\d+$", raw.lower()))


def _version_gt(left: str, right: str) -> bool:
    Version = _version_class()
    if Version is not None:
        try:
            return Version(left) > Version(right)
        except Exception:
            pass
    return _fallback_version_key(left) > _fallback_version_key(right)


def _relax_requirements(text: str, exact_overrides: dict[str, str] | None = None) -> str:
    exact_overrides = exact_overrides or {}
    lines = text.splitlines()
    for pin in _parse_requirements(text):
        override = exact_overrides.get(pin.name)
        lines[pin.line_index] = f"{pin.name}=={override}" if override else pin.name
    ending = "\n" if text.endswith("\n") else ""
    return "\n".join(lines) + ending


def _resolver_python(base_python: Path, pip_version: str, temp_root: Path, log) -> Path:
    return _ensure_reusable_venv(
        base_python=base_python,
        role="resolver",
        pip_version=pip_version,
        cwd=temp_root,
        log=log,
    )


def _parse_pip_report(report_path: Path, direct_names: list[str]) -> dict[str, str]:
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    wanted = {_canonical_name(name): name for name in direct_names}
    resolved: dict[str, str] = {}
    disallowed: list[str] = []
    for item in payload.get("install") or []:
        metadata = item.get("metadata") or {}
        name = str(metadata.get("name") or "")
        version = str(metadata.get("version") or "")
        if name and version and not _eligible_version(version):
            disallowed.append(f"{name}=={version}")
        canonical = _canonical_name(name)
        original = wanted.get(canonical)
        if original and version:
            resolved[original] = version
    if disallowed:
        raise BuilderError(
            "pip selected a prerelease type Norm does not permit (alpha/beta/dev): " + ", ".join(disallowed)
        )
    missing = [name for name in direct_names if name not in resolved]
    if missing:
        raise BuilderError(
            "pip resolved the dependency set but its report did not contain direct package(s): "
            + ", ".join(missing)
        )
    return resolved


def _resolve_top_level_versions(
    resolver_python: Path,
    source_info: SourceInfo,
    *,
    temp_root: Path,
    exact_overrides: dict[str, str] | None = None,
    log,
) -> dict[str, str]:
    req = temp_root / "resolve-requirements.txt"
    report = temp_root / "resolve-report.json"
    req.write_text(_relax_requirements(source_info.requirements_text, exact_overrides), encoding="utf-8", newline="\n")
    if report.exists():
        report.unlink()
    command = [
        str(resolver_python), "-m", "pip", "install",
        "--dry-run", "--ignore-installed", "--disable-pip-version-check",
        "--report", str(report), "-r", str(req),
    ]
    log("$ " + subprocess.list2cmdline(command))
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
    cp = subprocess.run(
        command, cwd=str(temp_root), capture_output=True, text=True, encoding="utf-8", errors="replace",
        creationflags=creationflags,
    )
    combined = "\n".join(part for part in (cp.stdout, cp.stderr) if part).strip()
    if combined:
        for line in combined.splitlines():
            log(line)
    if cp.returncode != 0:
        lines = [line.rstrip() for line in combined.splitlines() if line.strip()]
        raise BuilderError("pip could not resolve a compatible dependency set.\n\n" + "\n".join(lines[-35:]))
    if not report.is_file():
        raise BuilderError("pip completed resolution but did not create its JSON report")
    return _parse_pip_report(report, [pin.name for pin in source_info.pins])


def resolve_newest_compatible(
    *,
    source_info: SourceInfo,
    base_python: Path,
    pip_version: str,
    online_latest: dict[str, str],
    log,
) -> dict[str, str]:
    actual_python = _python_version(base_python)
    if source_info.expected_python and actual_python[:2] != source_info.expected_python:
        raise BuilderError(
            f"Norm {source_info.version} expects Python {source_info.expected_python[0]}.{source_info.expected_python[1]}.x, "
            f"but the selected base interpreter is {actual_python[0]}.{actual_python[1]}.{actual_python[2]}."
        )
    temp_root = Path(tempfile.mkdtemp(prefix=f"norm-installer-{INSTALLER_VERSION}-resolve-"))
    try:
        log(f"Resolver workspace: {temp_root}")
        resolver_python = _resolver_python(base_python, pip_version, temp_root, log)
        log("Resolving newest mutually compatible stable package set…")
        resolved = _resolve_top_level_versions(
            resolver_python, source_info, temp_root=temp_root, exact_overrides=None, log=log
        )

        # pip intentionally ignores prereleases unless explicitly requested. Try only the newest
        # allowed release-candidate for each direct package, one at a time, while leaving every
        # other direct dependency free for pip to re-resolve around it. Alpha/beta/dev are never
        # offered by online_latest and are rejected again when parsing the report.
        rc_candidates: list[tuple[str, str]] = []
        for pin in source_info.pins:
            candidate = online_latest.get(pin.name)
            if candidate and _is_release_candidate(candidate) and _version_gt(candidate, resolved[pin.name]):
                rc_candidates.append((pin.name, candidate))

        accepted_rc: dict[str, str] = {}
        for package, candidate in rc_candidates:
            trial = dict(accepted_rc)
            trial[package] = candidate
            log(f"Trying release candidate {package}=={candidate} with global re-resolution…")
            try:
                trial_resolved = _resolve_top_level_versions(
                    resolver_python, source_info, temp_root=temp_root, exact_overrides=trial, log=log
                )
            except BuilderError as exc:
                log(f"Skipping incompatible RC {package}=={candidate}: {exc}")
                continue
            accepted_rc = trial
            resolved = trial_resolved
            log(f"Accepted compatible RC {package}=={candidate}")

        return resolved
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)

def _rewrite_requirements(text: str, selected: dict[str, str]) -> str:
    lines = text.splitlines()
    pins = _parse_requirements(text)
    for pin in pins:
        version = selected.get(pin.name, pin.version)
        lines[pin.line_index] = f"{pin.name}=={version}"
    ending = "\n" if text.endswith("\n") else ""
    return "\n".join(lines) + ending


def _rewrite_direct_requirements(text: str, selected: dict[str, str]) -> str:
    """Rewrite exact direct pins while preserving extras such as psycopg[binary]."""
    selected_by_canonical = {_canonical_name(name): version for name, version in selected.items()}
    lines = text.splitlines()
    pattern = re.compile(r"^(\s*)([A-Za-z0-9_.-]+)(\[[^\]]+\])?==([^\s;]+)(.*)$")
    for index, raw in enumerate(lines):
        match = pattern.match(raw)
        if not match:
            continue
        canonical = _canonical_name(match.group(2))
        version = selected_by_canonical.get(canonical)
        if version:
            lines[index] = f"{match.group(1)}{match.group(2)}{match.group(3) or ''}=={version}{match.group(5)}"
    ending = "\n" if text.endswith("\n") else ""
    return "\n".join(lines) + ending


def persist_selected_requirements_to_source(
    source_info: SourceInfo,
    selected: dict[str, str],
    *,
    log,
) -> SourceInfo:
    """Atomically persist a resolved dependency set into the base portable source ZIP.

    The full environment lock is authoritative for installation/build reproducibility.
    Matching direct pins in core/requirements.txt are updated as well so source-mode
    installs and the human-facing direct dependency list do not drift from the lock.
    """
    source_zip = source_info.zip_path
    temp_root = Path(tempfile.mkdtemp(prefix=f"norm-source-lock-{INSTALLER_VERSION}-"))
    try:
        extracted_root = _extract_source(source_info, temp_root / "source")
        lock_path = extracted_root / str(source_info.manifest["requirements_lock"])
        new_lock = _rewrite_requirements(source_info.requirements_text, selected)
        lock_path.write_text(new_lock, encoding="utf-8", newline="\n")

        source_dir = Path(str(source_info.manifest.get("source_dir") or "core"))
        direct_path = extracted_root / source_dir / "requirements.txt"
        if direct_path.is_file():
            direct_text = direct_path.read_text(encoding="utf-8-sig")
            direct_path.write_text(
                _rewrite_direct_requirements(direct_text, selected),
                encoding="utf-8",
                newline="\n",
            )
            log(f"Updated direct requirements: {direct_path.relative_to(extracted_root)}")

        replacement = source_zip.with_name(source_zip.name + ".new")
        _zip_tree(extracted_root, replacement)
        # Validate the replacement before it can supersede the user's base source.
        replacement_info = inspect_source(replacement)
        expected = {_canonical_name(name): version for name, version in selected.items()}
        actual = {_canonical_name(pin.name): pin.version for pin in replacement_info.pins}
        mismatch = {name: version for name, version in expected.items() if actual.get(name) != version}
        if mismatch:
            raise BuilderError("Persisted source lock verification failed: " + ", ".join(
                f"{name} expected {version}, got {actual.get(name)!r}" for name, version in sorted(mismatch.items())
            ))

        os.replace(replacement, source_zip)
        digest = _sha256_file(source_zip)
        sha_path = source_zip.with_name(source_zip.name + ".sha256")
        sha_path.write_text(f"{digest}  {source_zip.name}\n", encoding="utf-8")
        log(f"Persisted resolved versions into base source: {source_zip}")
        log(f"Updated companion SHA-256: {sha_path}")
        return inspect_source(source_zip)
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)


def _extract_source(info: SourceInfo, destination: Path) -> Path:
    with zipfile.ZipFile(info.zip_path, "r") as zf:
        zf.extractall(destination)
    root = destination.joinpath(*info.zip_root.parts)
    if not root.is_dir():
        raise BuilderError(f"Extracted source root is missing: {root}")
    return root


def _zip_tree(root: Path, output: Path) -> None:
    if output.exists():
        output.unlink()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for path in sorted(root.rglob("*")):
            if path.is_dir():
                continue
            rel = path.relative_to(root)
            if "__pycache__" in rel.parts or path.suffix.lower() in {".pyc", ".pyo"}:
                continue
            if path.name.lower() == "norm.exe" or ".venv" in rel.parts:
                continue
            zf.write(path, (Path(root.name) / rel).as_posix())


def _patch_installer_template(template_text: str, *, pip_version: str) -> str:
    replacements = {
        r'^INSTALLER_VERSION = ".*?"$': f'INSTALLER_VERSION = "{INSTALLER_VERSION}"',
        r'^PIP_VERSION = ".*?"$': f'PIP_VERSION = "{pip_version}"',
    }
    result = template_text
    for pattern, replacement in replacements.items():
        result, count = re.subn(pattern, replacement, result, count=1, flags=re.M)
        if count != 1:
            raise BuilderError(f"Installer template constant was not found: {pattern}")
    return result


def _run(command: list[str], *, cwd: Path | None, log) -> subprocess.CompletedProcess[str]:
    log("$ " + subprocess.list2cmdline(command))
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
    proc = subprocess.Popen(
        [str(x) for x in command],
        cwd=str(cwd) if cwd else None,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=creationflags,
    )
    assert proc.stdout is not None
    collected: list[str] = []
    for line in proc.stdout:
        line = line.rstrip("\r\n")
        collected.append(line)
        log(line)
    code = proc.wait()
    result = subprocess.CompletedProcess(command, code, "\n".join(collected), "")
    if code != 0:
        raise BuilderError(f"Command failed with exit code {code}: {subprocess.list2cmdline(command)}")
    return result




def _validate_selected_requirements(venv_python: Path, requirements_path: Path, *, cwd: Path, log) -> None:
    """Ask pip's real resolver to verify the exact selected lock before we build/publish anything."""
    command = [
        str(venv_python), "-m", "pip", "install",
        "--dry-run", "--ignore-installed", "--disable-pip-version-check",
        "-r", str(requirements_path),
    ]
    log("$ " + subprocess.list2cmdline(command))
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
    cp = subprocess.run(
        command,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=creationflags,
    )
    combined = "\n".join(part for part in (cp.stdout, cp.stderr) if part).strip()
    if combined:
        for line in combined.splitlines():
            log(line)
    if cp.returncode != 0:
        lines = [line.rstrip() for line in combined.splitlines() if line.strip()]
        tail = "\n".join(lines[-40:])
        raise BuilderError(
            "Selected dependency versions are not mutually compatible.\n\n"
            "Nothing was published and Norm was not modified. Change the conflicting row(s) back to the requirements version, "
            "or choose matching newer dependencies, then build again.\n\n" + tail
        )

def _python_version(python_exe: Path) -> tuple[int, int, int]:
    cp = subprocess.run(
        [str(python_exe), "-c", "import sys; print('.'.join(map(str, sys.version_info[:3])))"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=8,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0,
    )
    if cp.returncode != 0:
        raise BuilderError(f"Selected Python could not run: {cp.stdout or cp.stderr}")
    try:
        major, minor, patch = (int(x) for x in cp.stdout.strip().split(".")[:3])
    except Exception as exc:
        raise BuilderError(f"Could not parse selected Python version: {cp.stdout!r}") from exc
    return major, minor, patch


def build_installer(
    *,
    source_info: SourceInfo,
    output_dir: Path,
    base_python: Path,
    selected: dict[str, str],
    pip_version: str,
    log,
    progress,
) -> BuildResult:
    output_dir = Path(output_dir).expanduser().resolve()
    base_python = Path(base_python).expanduser().resolve()
    if not base_python.is_file():
        raise BuilderError(f"Base Python does not exist: {base_python}")
    actual_python = _python_version(base_python)
    if source_info.expected_python and actual_python[:2] != source_info.expected_python:
        raise BuilderError(
            f"Norm {source_info.version} expects Python {source_info.expected_python[0]}.{source_info.expected_python[1]}.x, "
            f"but the selected base interpreter is {actual_python[0]}.{actual_python[1]}.{actual_python[2]}."
        )
    output_dir.mkdir(parents=True, exist_ok=True)

    temp_root = Path(tempfile.mkdtemp(prefix=f"norm-installer-{INSTALLER_VERSION}-build-"))
    log(f"Temporary build root: {temp_root}")
    try:
        progress(5, "Preparing selected source payload")
        extracted_parent = temp_root / "payload"
        extracted_root = _extract_source(source_info, extracted_parent)
        req_path = extracted_root / str(source_info.manifest["requirements_lock"])
        req_path.write_text(
            _rewrite_requirements(source_info.requirements_text, selected),
            encoding="utf-8",
            newline="\n",
        )
        payload_filename = source_info.zip_path.name
        payload_zip = temp_root / payload_filename
        _zip_tree(extracted_root, payload_zip)
        payload_sha = _sha256_file(payload_zip)
        payload_sha_path = temp_root / f"{payload_filename}.sha256"
        payload_sha_path.write_text(f"{payload_sha}  {payload_filename}\n", encoding="utf-8")
        log(f"Bound payload SHA-256: {payload_sha}")

        progress(15, "Generating bound installer source")
        template_path = _app_dir() / INSTALLER_TEMPLATE_FILENAME
        template = template_path.read_text(encoding="utf-8")
        generated_installer = temp_root / "Norm-Installer.generated.py"
        generated_installer.write_text(
            _patch_installer_template(template, pip_version=pip_version),
            encoding="utf-8",
            newline="\n",
        )

        pyinstaller_version = selected.get("pyinstaller")
        if not pyinstaller_version:
            for name, version in selected.items():
                if name.lower().replace("_", "-") == "pyinstaller":
                    pyinstaller_version = version
                    break
        if not pyinstaller_version:
            raise BuilderError("requirements-lock.txt does not contain a PyInstaller pin")

        progress(22, "Reusing installer build venv")
        venv_python = _ensure_reusable_venv(
            base_python=base_python,
            role="build",
            pip_version=pip_version,
            required={"pyinstaller": pyinstaller_version},
            cwd=temp_root,
            log=log,
        )

        progress(36, "Validating selected dependency stack")
        _validate_selected_requirements(venv_python, req_path, cwd=temp_root, log=log)

        progress(54, f"Building {OUTPUT_EXE_FILENAME}")
        dist = temp_root / "dist"
        work = temp_root / "work"
        spec = temp_root / "spec"
        _run(
            [
                str(venv_python), "-m", "PyInstaller",
                "--noconfirm", "--clean", "--onefile", "--windowed",
                "--name", f"Norm-Installer-{INSTALLER_VERSION}",
                "--distpath", str(dist),
                "--workpath", str(work),
                "--specpath", str(spec),
                str(generated_installer),
            ],
            cwd=temp_root,
            log=log,
        )
        built_exe = dist / OUTPUT_EXE_FILENAME
        if not built_exe.is_file():
            raise BuilderError(f"PyInstaller did not create {built_exe}")

        progress(78, "Smoke-testing generated installer")
        smoke_dir = temp_root / "smoke-bundle"
        smoke_dir.mkdir(parents=True, exist_ok=True)
        smoke_exe = smoke_dir / OUTPUT_EXE_FILENAME
        smoke_zip = smoke_dir / payload_filename
        smoke_sha = smoke_dir / f"{payload_filename}.sha256"
        shutil.copy2(built_exe, smoke_exe)
        shutil.copy2(payload_zip, smoke_zip)
        shutil.copy2(payload_sha_path, smoke_sha)

        sentinel = smoke_dir / "installer-self-test.txt"
        cp = subprocess.run(
            [str(smoke_exe), "--self-test-file", str(sentinel)],
            cwd=str(smoke_dir),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0,
        )
        if cp.returncode != 0 or not sentinel.is_file():
            raise BuilderError(
                f"Generated installer failed startup self-test (exit {cp.returncode}).\n{cp.stdout}\n{cp.stderr}"
            )
        marker = sentinel.read_text(encoding="utf-8", errors="replace").strip()
        expected_marker = f"ok {INSTALLER_VERSION} auto-source"
        if marker != expected_marker:
            raise BuilderError(f"Generated installer returned the wrong self-test marker: {marker!r}")

        # Exercise the exact path that previously looked like a launch crash: the EXE
        # must discover the newest package beside itself and validate it successfully.
        cp = subprocess.run(
            [str(smoke_exe), "--validate-only"],
            cwd=str(smoke_dir),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0,
        )
        if cp.returncode != 0:
            raise BuilderError(
                f"Generated installer failed local-source discovery smoke test (exit {cp.returncode}).\n{cp.stdout}\n{cp.stderr}"
            )

        progress(90, "Publishing installer bundle")
        out_exe = output_dir / OUTPUT_EXE_FILENAME
        out_zip = output_dir / payload_filename
        out_sha = output_dir / f"{payload_filename}.sha256"
        shutil.copy2(built_exe, out_exe)
        shutil.copy2(payload_zip, out_zip)
        shutil.copy2(payload_sha_path, out_sha)

        progress(100, "Build complete")
        result = BuildResult(out_exe, out_zip, out_sha, payload_sha)
        shutil.rmtree(temp_root, ignore_errors=True)
        return result
    except Exception:
        log(f"Build failed; temporary files were preserved at: {temp_root}")
        raise


def detect_python_candidates() -> list[Path]:
    found: list[Path] = []
    current = Path(sys.executable)
    if current.is_file() and current.name.lower().startswith("python"):
        found.append(current.resolve())
    if os.name == "nt":
        try:
            cp = subprocess.run(
                ["py", "-0p"], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=5,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            for line in cp.stdout.splitlines():
                match = re.search(r"([A-Za-z]:\\[^\r\n]*?python(?:w)?\.exe)\s*$", line, re.I)
                if match and Path(match.group(1)).is_file():
                    found.append(Path(match.group(1)).resolve())
        except Exception:
            pass
        for path_item in os.environ.get("PATH", "").split(os.pathsep):
            for name in ("python.exe", "python3.exe"):
                candidate = Path(path_item) / name
                if candidate.is_file():
                    found.append(candidate.resolve())
    unique: list[Path] = []
    seen: set[str] = set()
    for item in found:
        key = os.path.normcase(str(item))
        if key not in seen:
            seen.add(key)
            unique.append(item)
    return unique


def launch_gui() -> int:
    try:
        import tkinter as tk
        from tkinter import filedialog, messagebox, ttk
        from tkinter.scrolledtext import ScrolledText
    except Exception as exc:
        raise BuilderError(f"Tkinter is required for the installer builder: {exc}") from exc

    source_info = inspect_source(find_latest_base_source())
    template_path = _app_dir() / INSTALLER_TEMPLATE_FILENAME
    if not template_path.is_file():
        raise BuilderError(f"Installer template is missing: {template_path}")

    current_versions = {pin.name: pin.version for pin in source_info.pins}
    selected_versions = dict(current_versions)
    selected_pip = {"value": DEFAULT_PIP_VERSION}
    online_versions: dict[str, str] = {}
    online_errors: dict[str, str] = {}

    root = tk.Tk()
    root.title(f"Make Norm Installer {INSTALLER_VERSION}")
    root.geometry("980x720")
    root.minsize(840, 620)

    def center_child_over_root(window, width: int, height: int) -> None:
        """Place installer child dialogs over the builder window, not monitor center."""
        root.update_idletasks()
        x = root.winfo_rootx() + max(0, (root.winfo_width() - width) // 2)
        y = root.winfo_rooty() + max(0, (root.winfo_height() - height) // 2)
        window.geometry(f"{width}x{height}+{x}+{y}")

    def ask_newest_compatible_scope(resolved: dict[str, str]) -> str | None:
        """Return 'build', 'persist', or None after newest-compatible resolution."""
        dialog = tk.Toplevel(root)
        dialog.title("Newest compatible versions selected")
        dialog.transient(root)
        dialog.resizable(False, False)
        answer = {"value": None}

        frame = ttk.Frame(dialog, padding=(18, 16))
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="Newest compatible dependency set is ready", font=("Segoe UI", 12, "bold")).pack(anchor="w")
        changed = sum(1 for name, version in resolved.items() if current_versions.get(name) != version)
        ttk.Label(
            frame,
            text=(
                f"{changed} package pin(s) differ from the current base source.\n\n"
                "Choose whether these versions apply only to the installer you build now, "
                "or become the new requirements baseline for future builds."
            ),
            justify="left",
            wraplength=540,
        ).pack(anchor="w", pady=(10, 14))

        persist_box = ttk.LabelFrame(frame, text="Update requirements + this build", padding=(10, 8))
        persist_box.pack(fill="x", pady=(0, 8))
        ttk.Label(
            persist_box,
            text=(
                "Writes the resolved versions back into the base portable-source ZIP's "
                "tools\\requirements-lock.txt and matching core\\requirements.txt pins, "
                "then refreshes its .sha256. Future builder runs start from these versions."
            ),
            justify="left",
            wraplength=520,
        ).pack(anchor="w")

        build_box = ttk.LabelFrame(frame, text="Update this build only", padding=(10, 8))
        build_box.pack(fill="x", pady=(0, 14))
        ttk.Label(
            build_box,
            text="Uses the resolved versions for this installer payload but leaves the base source ZIP unchanged.",
            justify="left",
            wraplength=520,
        ).pack(anchor="w")

        buttons = ttk.Frame(frame)
        buttons.pack(fill="x")

        def choose(value: str | None) -> None:
            answer["value"] = value
            dialog.destroy()

        ttk.Button(buttons, text="Update requirements + this build", command=lambda: choose("persist")).pack(side="left")
        ttk.Button(buttons, text="This build only", command=lambda: choose("build")).pack(side="left", padx=(8, 0))
        ttk.Button(buttons, text="Cancel", command=lambda: choose(None)).pack(side="right")
        dialog.protocol("WM_DELETE_WINDOW", lambda: choose(None))
        center_child_over_root(dialog, 610, 355)
        dialog.grab_set()
        dialog.focus_force()
        root.wait_window(dialog)
        return answer["value"]

    python_candidates = detect_python_candidates()
    python_default = python_candidates[0] if python_candidates else Path(sys.executable)
    python_var = tk.StringVar(value=str(python_default))
    output_var = tk.StringVar(value=str(_app_dir()))
    status_var = tk.StringVar(value="Ready — All newest globally resolves a mutually compatible stable/RC set; manual overrides are still checked before build.")
    progress_var = tk.DoubleVar(value=0)

    outer = ttk.Frame(root, padding=(14, 12))
    outer.pack(fill="both", expand=True)
    outer.columnconfigure(1, weight=1)
    outer.rowconfigure(6, weight=1)

    ttk.Label(outer, text="Make Norm Installer", font=("Segoe UI", 16, "bold")).grid(row=0, column=0, columnspan=3, sticky="w")
    ttk.Label(
        outer,
        text=f"Norm {source_info.version}  •  newest local base {source_info.zip_path.name}  •  output {OUTPUT_EXE_FILENAME}",
    ).grid(row=1, column=0, columnspan=3, sticky="w", pady=(2, 10))

    ttk.Label(outer, text="Base Python (reusable build venv)").grid(row=2, column=0, sticky="w", padx=(0, 8), pady=4)
    ttk.Entry(outer, textvariable=python_var).grid(row=2, column=1, sticky="ew", pady=4)

    def browse_python() -> None:
        path = filedialog.askopenfilename(title="Select base Python for the reusable installer build venv", filetypes=[("Python", "python.exe" if os.name == "nt" else "python*"), ("All files", "*.*")])
        if path:
            python_var.set(path)

    ttk.Button(outer, text="Browse…", command=browse_python).grid(row=2, column=2, padx=(8, 0), pady=4)

    ttk.Label(outer, text="Build output").grid(row=3, column=0, sticky="w", padx=(0, 8), pady=4)
    ttk.Entry(outer, textvariable=output_var).grid(row=3, column=1, sticky="ew", pady=4)

    def browse_output() -> None:
        path = filedialog.askdirectory(title="Select output folder for installer EXE + bound payload")
        if path:
            output_var.set(path)

    ttk.Button(outer, text="Browse…", command=browse_output).grid(row=3, column=2, padx=(8, 0), pady=4)

    table_frame = ttk.LabelFrame(outer, text="Dependency versions", padding=(8, 6))
    table_frame.grid(row=4, column=0, columnspan=3, sticky="nsew", pady=(8, 6))
    table_frame.columnconfigure(0, weight=1)
    table_frame.rowconfigure(0, weight=1)

    columns = ("locked", "online", "selected", "status")
    tree = ttk.Treeview(table_frame, columns=columns, show="tree headings", selectmode="extended", height=15)
    tree.heading("#0", text="Package")
    tree.heading("locked", text="Requirements / current")
    tree.heading("online", text="Newest stable/RC online")
    tree.heading("selected", text="Build with")
    tree.heading("status", text="Lookup")
    tree.column("#0", width=190, anchor="w")
    tree.column("locked", width=145, anchor="w")
    tree.column("online", width=155, anchor="w")
    tree.column("selected", width=145, anchor="w")
    tree.column("status", width=190, anchor="w")
    scroll = ttk.Scrollbar(table_frame, orient="vertical", command=tree.yview)
    tree.configure(yscrollcommand=scroll.set)
    tree.grid(row=0, column=0, sticky="nsew")
    scroll.grid(row=0, column=1, sticky="ns")

    row_to_package: dict[str, str] = {}
    package_to_row: dict[str, str] = {}

    def add_row(package: str, locked: str, *, label: str | None = None) -> None:
        row_id = f"pkg{len(row_to_package)}"
        row_to_package[row_id] = package
        package_to_row[package] = row_id
        tree.insert("", "end", iid=row_id, text=label or package, values=(locked, "…", locked, "not checked"))

    add_row("__pip__", DEFAULT_PIP_VERSION, label="pip (installer bootstrap)")
    for pin in source_info.pins:
        add_row(pin.name, pin.version)

    controls = ttk.Frame(outer)
    controls.grid(row=5, column=0, columnspan=3, sticky="ew", pady=(0, 6))

    events: queue.Queue[tuple[str, object]] = queue.Queue()
    busy = {"build": False, "lookup": False, "resolve": False}

    def refresh_row(package: str) -> None:
        row_id = package_to_row[package]
        locked = DEFAULT_PIP_VERSION if package == "__pip__" else current_versions[package]
        online = online_versions.get(package, "—")
        selected = selected_pip["value"] if package == "__pip__" else selected_versions[package]
        if package in online_errors:
            lookup = online_errors[package]
        elif package in online_versions:
            lookup = "eligible"
        else:
            lookup = "not checked"
        tree.item(row_id, values=(locked, online, selected, lookup))

    def selected_packages() -> list[str]:
        return [row_to_package[row] for row in tree.selection() if row in row_to_package]

    def choose_locked(packages: list[str]) -> None:
        for package in packages:
            if package == "__pip__":
                selected_pip["value"] = DEFAULT_PIP_VERSION
            else:
                selected_versions[package] = current_versions[package]
            refresh_row(package)

    def choose_online(packages: list[str]) -> None:
        unavailable: list[str] = []
        for package in packages:
            value = online_versions.get(package)
            if not value:
                unavailable.append("pip" if package == "__pip__" else package)
                continue
            if package == "__pip__":
                selected_pip["value"] = value
            else:
                selected_versions[package] = value
            refresh_row(package)
        if unavailable:
            messagebox.showinfo("Online version unavailable", "No eligible online version is loaded for: " + ", ".join(unavailable), parent=root)

    ttk.Button(controls, text="Use requirements", command=lambda: choose_locked(selected_packages())).pack(side="left")
    ttk.Button(controls, text="Use online (manual)", command=lambda: choose_online(selected_packages())).pack(side="left", padx=(6, 0))
    ttk.Button(controls, text="All requirements", command=lambda: choose_locked(list(package_to_row))).pack(side="left", padx=(14, 0))
    all_newest_btn = ttk.Button(controls, text="All newest (resolve)")
    all_newest_btn.pack(side="left", padx=(6, 0))

    def newest_worker(base_python: str, pip_version: str, online_snapshot: dict[str, str]) -> None:
        try:
            target_python = None
            if source_info.expected_python:
                target_python = f"{source_info.expected_python[0]}.{source_info.expected_python[1]}.0"

            complete_online = dict(online_snapshot)
            missing = [pin.name for pin in source_info.pins if pin.name not in complete_online]
            lookup_names = list(missing)
            if "__pip__" not in complete_online:
                lookup_names.append("__pip__")
            if lookup_names:
                with ThreadPoolExecutor(max_workers=6) as pool:
                    future_map = {
                        pool.submit(fetch_latest_eligible, "pip" if package == "__pip__" else package, target_python): package
                        for package in lookup_names
                    }
                    for future in as_completed(future_map):
                        package = future_map[future]
                        try:
                            complete_online[package] = future.result()
                        except Exception as exc:
                            events.put(("log", f"Online lookup skipped for {'pip' if package == '__pip__' else package}: {exc}"))

            resolver_pip = complete_online.get("__pip__", pip_version)
            resolved = resolve_newest_compatible(
                source_info=source_info,
                base_python=Path(base_python),
                pip_version=resolver_pip,
                online_latest=complete_online,
                log=lambda text: events.put(("log", text)),
            )
            events.put(("newest_done", (resolved, complete_online)))
        except Exception as exc:
            events.put(("newest_error", exc))

    def resolve_all_newest() -> None:
        if busy["build"] or busy.get("resolve"):
            return
        base_python = python_var.get().strip()
        if not base_python:
            messagebox.showerror("Missing Python", "Choose the base Python before resolving newest dependencies.", parent=root)
            return
        pip_candidate = online_versions.get("__pip__", selected_pip["value"])
        busy["resolve"] = True
        all_newest_btn.configure(state="disabled")
        build_btn.configure(state="disabled")
        status_var.set("Resolving newest mutually compatible stable/RC dependency set…")
        append_log("=== Resolve all newest compatible dependencies ===")
        threading.Thread(
            target=newest_worker,
            args=(base_python, pip_candidate, dict(online_versions)),
            daemon=True,
        ).start()

    all_newest_btn.configure(command=resolve_all_newest)

    def toggle_selected(_event=None) -> None:
        packages = selected_packages()
        for package in packages:
            locked = DEFAULT_PIP_VERSION if package == "__pip__" else current_versions[package]
            current = selected_pip["value"] if package == "__pip__" else selected_versions[package]
            if current == locked and package in online_versions:
                choose_online([package])
            else:
                choose_locked([package])

    tree.bind("<Double-1>", toggle_selected)

    log_box = ScrolledText(outer, height=9, wrap="word", font=("Consolas", 9))
    log_box.grid(row=6, column=0, columnspan=3, sticky="nsew")
    log_box.configure(state="disabled")

    def append_log(text: str) -> None:
        log_box.configure(state="normal")
        log_box.insert("end", text + "\n")
        log_box.see("end")
        log_box.configure(state="disabled")

    bottom = ttk.Frame(outer)
    bottom.grid(row=7, column=0, columnspan=3, sticky="ew", pady=(8, 0))
    bottom.columnconfigure(0, weight=1)
    ttk.Label(bottom, textvariable=status_var).grid(row=0, column=0, sticky="w")
    ttk.Progressbar(bottom, maximum=100, variable=progress_var, length=220).grid(row=0, column=1, padx=(8, 8))
    build_btn = ttk.Button(bottom, text=f"Build {OUTPUT_EXE_FILENAME}")
    build_btn.grid(row=0, column=2, sticky="e")

    def lookup_worker() -> None:
        packages = ["pip"] + [pin.name for pin in source_info.pins]
        target_python = None
        if source_info.expected_python:
            target_python = f"{source_info.expected_python[0]}.{source_info.expected_python[1]}.0"
        with ThreadPoolExecutor(max_workers=6) as pool:
            future_map = {
                pool.submit(fetch_latest_eligible, package, target_python): ("__pip__" if package == "pip" else package)
                for package in packages
            }
            for future in as_completed(future_map):
                key = future_map[future]
                try:
                    value = future.result()
                    events.put(("online", (key, value, None)))
                except Exception as exc:
                    events.put(("online", (key, None, str(exc))))
        events.put(("lookup_done", None))

    def refresh_online() -> None:
        if busy["lookup"] or busy["build"] or busy.get("resolve"):
            return
        busy["lookup"] = True
        online_errors.clear()
        status_var.set("Checking PyPI for newest stable/RC releases…")
        for package in package_to_row:
            row = package_to_row[package]
            values = list(tree.item(row, "values"))
            values[1] = "…"
            values[3] = "checking"
            tree.item(row, values=values)
        threading.Thread(target=lookup_worker, daemon=True).start()

    ttk.Button(controls, text="Refresh online", command=refresh_online).pack(side="right")

    def build_worker(snapshot: dict[str, str], pip_version: str, output: str, base_python: str) -> None:
        try:
            result = build_installer(
                source_info=source_info,
                output_dir=Path(output),
                base_python=Path(base_python),
                selected=snapshot,
                pip_version=pip_version,
                log=lambda text: events.put(("log", text)),
                progress=lambda value, text: events.put(("progress", (value, text))),
            )
            events.put(("build_done", result))
        except Exception as exc:
            events.put(("build_error", exc))

    def build_clicked() -> None:
        if busy["build"] or busy.get("resolve"):
            return
        output = output_var.get().strip()
        base_python = python_var.get().strip()
        if not output or not base_python:
            messagebox.showerror("Missing information", "Choose the build output folder and base Python.", parent=root)
            return
        busy["build"] = True
        build_btn.configure(state="disabled")
        log_box.configure(state="normal")
        log_box.delete("1.0", "end")
        log_box.configure(state="disabled")
        progress_var.set(0)
        status_var.set("Starting build — selected versions will be dependency-resolved before packaging…")
        snapshot = dict(selected_versions)
        threading.Thread(target=build_worker, args=(snapshot, selected_pip["value"], output, base_python), daemon=True).start()

    build_btn.configure(command=build_clicked)

    def pump_events() -> None:
        nonlocal source_info
        try:
            while True:
                kind, payload = events.get_nowait()
                if kind == "online":
                    package, value, error = payload  # type: ignore[misc]
                    if value:
                        online_versions[str(package)] = str(value)
                        online_errors.pop(str(package), None)
                    else:
                        online_errors[str(package)] = str(error or "lookup failed")[:80]
                    refresh_row(str(package))
                elif kind == "lookup_done":
                    busy["lookup"] = False
                    status_var.set("Online lookup complete — use All newest (resolve) for a compatible global update, or manually override individual rows.")
                elif kind == "log":
                    append_log(str(payload))
                elif kind == "progress":
                    value, text = payload  # type: ignore[misc]
                    progress_var.set(float(value))
                    status_var.set(str(text))
                elif kind == "newest_error":
                    busy["resolve"] = False
                    all_newest_btn.configure(state="normal")
                    build_btn.configure(state="normal")
                    status_var.set("Newest-compatible resolution failed")
                    append_log(f"RESOLVER ERROR: {payload}")
                    messagebox.showerror("Newest-compatible resolution failed", str(payload), parent=root)
                elif kind == "newest_done":
                    resolved, complete_online = payload  # type: ignore[misc]
                    online_versions.update({str(k): str(v) for k, v in complete_online.items()})
                    for package, version in resolved.items():
                        selected_versions[str(package)] = str(version)
                        refresh_row(str(package))
                    if "__pip__" in online_versions:
                        selected_pip["value"] = online_versions["__pip__"]
                        refresh_row("__pip__")
                    busy["resolve"] = False
                    all_newest_btn.configure(state="normal")
                    build_btn.configure(state="normal")
                    status_var.set("Newest mutually compatible dependency set selected")
                    append_log("Resolved newest compatible set and wrote it back to the dependency table.")

                    scope = ask_newest_compatible_scope({str(k): str(v) for k, v in resolved.items()})
                    if scope == "persist":
                        try:
                            source_info = persist_selected_requirements_to_source(
                                source_info,
                                {str(k): str(v) for k, v in resolved.items()},
                                log=append_log,
                            )
                            current_versions.clear()
                            current_versions.update({pin.name: pin.version for pin in source_info.pins})
                            for package in current_versions:
                                if package in package_to_row:
                                    refresh_row(package)
                            status_var.set("Newest compatible set persisted as the new requirements baseline")
                            append_log("Future builds will start from the newly persisted requirements baseline.")
                        except Exception as exc:
                            status_var.set("Resolved set selected, but source requirements update failed")
                            append_log(f"SOURCE REQUIREMENTS UPDATE ERROR: {exc}")
                            messagebox.showerror(
                                "Could not update source requirements",
                                str(exc),
                                parent=root,
                            )
                    elif scope == "build":
                        status_var.set("Newest compatible set selected for this build only")
                        append_log("Base source requirements left unchanged; resolved versions apply to this build only.")
                    else:
                        status_var.set("Newest compatible set selected; no persistence choice made")
                        append_log("Newest-compatible scope dialog cancelled; dependency table remains selected for review.")
                elif kind == "build_error":
                    busy["build"] = False
                    build_btn.configure(state="normal")
                    status_var.set("Build failed")
                    append_log(f"ERROR: {payload}")
                    messagebox.showerror("Installer build failed", str(payload), parent=root)
                elif kind == "build_done":
                    busy["build"] = False
                    build_btn.configure(state="normal")
                    result: BuildResult = payload  # type: ignore[assignment]
                    progress_var.set(100)
                    status_var.set("Build complete")
                    messagebox.showinfo(
                        "Installer build complete",
                        f"EXE: {result.exe_path}\n\nPayload: {result.source_zip}\nSHA-256: {result.payload_sha256}\n\n"
                        "At launch, the installer auto-selects the newest valid Norm portable-source ZIP beside it.",
                        parent=root,
                    )
        except queue.Empty:
            pass
        root.after(100, pump_events)

    root.after(250, refresh_online)
    root.after(100, pump_events)
    root.mainloop()
    return 0


def self_test() -> int:
    info = inspect_source(find_latest_base_source())
    assert _eligible_version("1.2.3")
    assert _eligible_version("1.2.4rc1")
    assert not _eligible_version("1.2.4b1")
    assert not _eligible_version("1.2.4a1")
    assert not _eligible_version("1.2.4.dev1")
    pyinstaller = [pin for pin in info.pins if pin.name.lower().replace("_", "-") == "pyinstaller"]
    if not pyinstaller:
        raise BuilderError("Base requirements do not contain PyInstaller")
    template = (_app_dir() / INSTALLER_TEMPLATE_FILENAME).read_text(encoding="utf-8")
    patched = _patch_installer_template(template, pip_version=DEFAULT_PIP_VERSION)
    if 'INSTALLER_VERSION = "1.4.16"' not in patched:
        raise BuilderError("Installer version injection self-test failed")
    if 'find_latest_source()' not in patched:
        raise BuilderError("Installer auto-source discovery self-test failed")
    if '_verify_bound_source' in patched:
        raise BuilderError("Installer template still contains removed hard-bound source validation")
    print(json.dumps({
        "builder_version": INSTALLER_VERSION,
        "norm_version": info.version,
        "requirements": len(info.pins),
        "pyinstaller_lock": pyinstaller[0].version,
        "base_source_sha256": _sha256_file(info.zip_path),
        "eligible_policy": "Python-compatible stable+rc; All newest globally resolves compatible direct pins; alpha/beta/dev excluded; manual selections are revalidated before build",
    }, indent=2))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Build a Norm installer that auto-selects the newest local source package")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        return self_test()
    return launch_gui()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BuilderError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(2)
