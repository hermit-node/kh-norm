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
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Iterable

import installer_environment as envtools

INSTALLER_VERSION = "1.6.5-unified"
PIP_VERSION = "26.2.1"
PIP_MIN_VERSION = PIP_VERSION  # backward-compatible internal print helper
PIP_SPEC = f"pip=={PIP_VERSION}"
SUPPORTED_PACKAGE_SCHEMA = {1}


class InstallerError(RuntimeError):
    pass


@dataclass(frozen=True)
class PackageInfo:
    source_zip: Path
    zip_root: PurePosixPath
    manifest_member: str
    manifest: dict

    @property
    def name(self) -> str:
        return str(self.manifest.get("name", "Norm"))

    @property
    def version(self) -> str:
        return str(self.manifest.get("version", "unknown"))

    @property
    def label(self) -> str:
        return f"{self.name} {self.version}"


@dataclass
class InstallOptions:
    source_zip: Path
    target_dir: Path
    python_exe: Path
    compile_exe: bool = True
    install_torch: bool = True
    replace_existing: bool = False
    recreate_venv: bool = False
    dependency_mode: str = "package"
    imprint: dict[str, Any] | None = None
    secret_values: dict[str, str] | None = None
    package_imprint_baseline: dict[str, Any] | None = None


@dataclass
class InstallResult:
    package: PackageInfo
    target_dir: Path
    venv_python: Path
    compiled_exe: Path | None
    warnings: list[str]


LogFn = Callable[[str], None]
ProgressFn = Callable[[int, str], None]


def _noop_log(_: str) -> None:
    pass


def _noop_progress(_: int, __: str) -> None:
    pass


def _version_key(value: str) -> tuple:
    parts = re.findall(r"\d+|[A-Za-z]+", value or "")
    out: list[tuple[int, object]] = []
    for part in parts:
        if part.isdigit():
            out.append((0, int(part)))
        else:
            out.append((1, part.lower()))
    return tuple(out)


def _safe_zip_member(member: str) -> PurePosixPath:
    p = PurePosixPath(member)
    if p.is_absolute() or ".." in p.parts:
        raise InstallerError(f"Unsafe path in source package: {member!r}")
    if p.parts and re.fullmatch(r"[A-Za-z]:", p.parts[0]):
        raise InstallerError(f"Unsafe drive-qualified path in source package: {member!r}")
    return p


def _verify_companion_sha256(source_zip: Path) -> None:
    """Verify <archive>.sha256 when it is present beside the selected package."""
    companion = source_zip.with_name(source_zip.name + ".sha256")
    if not companion.is_file():
        return
    try:
        first_line = companion.read_text(encoding="utf-8-sig").splitlines()[0].strip()
        expected = first_line.split()[0].lower()
    except Exception as exc:
        raise InstallerError(f"Could not read companion SHA-256 file {companion.name}: {exc}") from exc
    if not re.fullmatch(r"[0-9a-f]{64}", expected):
        raise InstallerError(
            f"Invalid SHA-256 value in {companion.name}. "
            "Rebuild the source ZIP and regenerate its companion checksum."
        )
    digest = hashlib.sha256()
    with source_zip.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    actual = digest.hexdigest()
    if actual != expected:
        raise InstallerError(
            f"SHA-256 mismatch for {source_zip.name}. Expected {expected}, got {actual}. "
            "If you intentionally rebuilt this source ZIP, regenerate its .sha256 companion first."
        )


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()



def inspect_package(source_zip: Path) -> PackageInfo:
    source_zip = Path(source_zip).expanduser().resolve()
    if not source_zip.is_file():
        raise InstallerError(f"Source ZIP does not exist: {source_zip}")
    if source_zip.suffix.lower() != ".zip":
        raise InstallerError("Source package must be a .zip file")
    _verify_companion_sha256(source_zip)

    try:
        with zipfile.ZipFile(source_zip, "r") as zf:
            members = zf.namelist()
            for member in members:
                _safe_zip_member(member)
            manifests = [
                m for m in members
                if PurePosixPath(m).name == "package-manifest.json"
                and not m.endswith("/")
            ]
            if len(manifests) != 1:
                raise InstallerError(
                    f"Expected exactly one package-manifest.json, found {len(manifests)}"
                )
            manifest_member = manifests[0]
            try:
                manifest = json.loads(zf.read(manifest_member).decode("utf-8-sig"))
            except Exception as exc:
                raise InstallerError(f"Invalid package-manifest.json: {exc}") from exc
    except zipfile.BadZipFile as exc:
        raise InstallerError(f"Invalid ZIP file: {source_zip}") from exc

    schema = manifest.get("package_schema")
    if schema not in SUPPORTED_PACKAGE_SCHEMA:
        raise InstallerError(
            f"Unsupported package schema {schema!r}; installer supports {sorted(SUPPORTED_PACKAGE_SCHEMA)}"
        )
    package_type = str(manifest.get("package_type") or "")
    if package_type not in {"portable-source", "full-backup"}:
        raise InstallerError(
            f"Unsupported package_type {package_type!r}; expected 'portable-source' or 'full-backup'"
        )
    if package_type == "full-backup" and not str(manifest.get("backup_payload") or "").strip():
        raise InstallerError("full-backup package is missing backup_payload")

    required_keys = (
        "name",
        "version",
        "entrypoint",
        "settings",
        "requirements_lock",
        "build_script",
    )
    missing = [key for key in required_keys if not str(manifest.get(key, "")).strip()]
    if missing:
        raise InstallerError(f"Manifest missing required field(s): {', '.join(missing)}")

    root = PurePosixPath(manifest_member).parent
    with zipfile.ZipFile(source_zip, "r") as zf:
        names = set(zf.namelist())
        for key in ("entrypoint", "settings", "requirements_lock", "build_script"):
            rel = PurePosixPath(str(manifest[key]).replace("\\", "/"))
            member = str(root / rel)
            if member not in names:
                raise InstallerError(f"Package is missing {key}: {manifest[key]}")

    return PackageInfo(
        source_zip=source_zip,
        zip_root=root,
        manifest_member=manifest_member,
        manifest=manifest,
    )


def _extract_package(info: PackageInfo, extraction_dir: Path) -> Path:
    extraction_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(info.source_zip, "r") as zf:
        for entry in zf.infolist():
            p = _safe_zip_member(entry.filename)
            out = extraction_dir.joinpath(*p.parts)
            try:
                out.resolve().relative_to(extraction_dir.resolve())
            except ValueError as exc:
                raise InstallerError(f"Unsafe extraction path: {entry.filename}") from exc
            if entry.is_dir():
                out.mkdir(parents=True, exist_ok=True)
                continue
            out.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(entry, "r") as src, out.open("wb") as dst:
                shutil.copyfileobj(src, dst)
    root = extraction_dir.joinpath(*info.zip_root.parts)
    if not root.is_dir():
        raise InstallerError(f"Package root was not extracted: {root}")
    return root


def _run(
    command: Iterable[str],
    *,
    cwd: Path | None,
    log: LogFn,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    cmd = [str(x) for x in command]
    log("$ " + subprocess.list2cmdline(cmd))
    startupinfo = None
    creationflags = 0
    if os.name == "nt":
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    proc = subprocess.Popen(
        cmd,
        cwd=str(cwd) if cwd else None,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        startupinfo=startupinfo,
        creationflags=creationflags,
    )
    assert proc.stdout is not None
    lines: list[str] = []
    for line in proc.stdout:
        line = line.rstrip("\r\n")
        lines.append(line)
        log(line)
    returncode = proc.wait()
    result = subprocess.CompletedProcess(cmd, returncode, "\n".join(lines), "")
    if returncode != 0:
        raise InstallerError(f"Command failed with exit code {returncode}: {subprocess.list2cmdline(cmd)}")
    return result


def _python_version(python_exe: Path, log: LogFn) -> tuple[int, int, int]:
    cp = _run(
        [str(python_exe), "-c", "import sys; print('.'.join(map(str, sys.version_info[:3])))"],
        cwd=None,
        log=log,
    )
    line = (cp.stdout or "").strip().splitlines()[-1]
    match = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)", line)
    if not match:
        raise InstallerError(f"Could not determine Python version from {python_exe}")
    return tuple(int(match.group(i)) for i in (1, 2, 3))


def _read_settings(package_root: Path, manifest: dict) -> configparser.ConfigParser:
    settings_path = package_root / str(manifest["settings"])
    parser = configparser.ConfigParser(interpolation=None)
    with settings_path.open("r", encoding="utf-8-sig") as handle:
        parser.read_file(handle)
    return parser


def _set_ini_value(path: Path, section: str, key: str, value: str) -> None:
    """Set one INI value without reformatting the rest of the human-edited file."""
    text = path.read_text(encoding="utf-8-sig")
    lines = text.splitlines(keepends=True)
    section_l = section.strip().lower()
    key_l = key.strip().lower()
    in_section = False
    section_found = False
    key_done = False
    insert_at = len(lines)
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            name = stripped[1:-1].strip().lower()
            if in_section and not key_done:
                insert_at = i
                break
            in_section = name == section_l
            if in_section:
                section_found = True
            continue
        if in_section and "=" in line and not stripped.startswith((";", "#")):
            lhs = line.split("=", 1)[0].strip().lower()
            if lhs == key_l:
                newline = "\r\n" if line.endswith("\r\n") else "\n"
                lines[i] = f"{key} = {value}{newline}"
                key_done = True
                break
    if not section_found:
        if lines and not lines[-1].endswith(("\n", "\r")):
            lines[-1] += "\n"
        lines.extend([f"\n[{section}]\n", f"{key} = {value}\n"])
    elif not key_done:
        lines.insert(insert_at, f"{key} = {value}\n")
    path.write_text("".join(lines), encoding="utf-8", newline="")


def _bind_installed_runtime_root(target: Path, manifest: dict, log: LogFn) -> None:
    settings_path = target / str(manifest["settings"])
    if not settings_path.is_file():
        raise InstallerError(f"Installed settings file is missing: {settings_path}")
    _set_ini_value(settings_path, "paths", "runtime_root", str(target))
    log(f"Bound settings paths.runtime_root to installed location: {target}")


def _expected_python(settings: configparser.ConfigParser) -> tuple[int, int] | None:
    raw = settings.get("environment", "python_version", fallback="").strip()
    match = re.match(r"^(\d+)\.(\d+)", raw)
    if not match:
        return None
    return int(match.group(1)), int(match.group(2))


def _configured_torch(settings: configparser.ConfigParser) -> tuple[str, str] | None:
    version = settings.get("environment", "torch_version", fallback="").strip()
    index = settings.get("environment", "torch_index_url", fallback="").strip()
    if not version:
        return None
    return version, index


def _expand_windows_vars(value: str) -> str:
    # os.path.expandvars expands %NAME% on Windows. This second pass also makes
    # validation/testing behave sensibly when the installer script is inspected elsewhere.
    expanded = os.path.expandvars(value)
    if os.name != "nt":
        def repl(match: re.Match[str]) -> str:
            return os.environ.get(match.group(1), match.group(0))
        expanded = re.sub(r"%([^%]+)%", repl, expanded)
    return expanded


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _norm_rel(path: Path) -> str:
    return path.as_posix().strip("/")


def _is_protected(rel: str, protected: set[str]) -> bool:
    rel_l = rel.lower() if os.name == "nt" else rel
    for item in protected:
        item_l = item.lower() if os.name == "nt" else item
        if rel_l == item_l or rel_l.startswith(item_l + "/"):
            return True
    return False


def _has_protected_descendant(rel: str, protected: set[str]) -> bool:
    rel_l = rel.lower() if os.name == "nt" else rel
    prefix = rel_l + "/" if rel_l else ""
    for item in protected:
        item_l = item.lower() if os.name == "nt" else item
        if item_l.startswith(prefix):
            return True
    return False



def _snapshot_existing_config(target: Path, log: LogFn) -> Path | None:
    """Persist a rollback copy of operator config before an in-place package sync."""
    config_dir = target / "config"
    if not config_dir.is_dir():
        return None
    stamp = time.strftime("%Y%m%d-%H%M%S")
    backup_root = target / "backups" / f"installer-config-{stamp}"
    candidate = backup_root
    suffix = 1
    while candidate.exists():
        candidate = target / "backups" / f"installer-config-{stamp}-{suffix}"
        suffix += 1
    shutil.copytree(config_dir, candidate)
    log(f"Snapshotted existing config for rollback: {candidate}")
    return candidate


def _migrate_existing_settings(package_settings: Path, old_settings: Path, *, package_version: str, log: LogFn) -> dict[str, list[str]]:
    """Rebuild settings.ini from the new schema, then migrate matching old values."""
    result = {"migrated": [], "dropped": []}
    if not old_settings.is_file() or not package_settings.is_file():
        return result
    new_parser = configparser.ConfigParser(interpolation=None)
    old_parser = configparser.ConfigParser(interpolation=None)
    with package_settings.open("r", encoding="utf-8-sig") as handle:
        new_parser.read_file(handle)
    with old_settings.open("r", encoding="utf-8-sig") as handle:
        old_parser.read_file(handle)

    package_owned = {("project", "version"), ("paths", "runtime_root")}
    for section in old_parser.sections():
        for key, old_value in old_parser.items(section, raw=True):
            marker = (section, key)
            label = f"{section}.{key}"
            if marker in package_owned:
                continue
            if new_parser.has_option(section, key):
                new_parser.set(section, key, old_value)
                result["migrated"].append(label)
            else:
                result["dropped"].append(label)

    if not new_parser.has_section("project"):
        new_parser.add_section("project")
    new_parser.set("project", "version", package_version)
    with package_settings.open("w", encoding="utf-8", newline="\n") as handle:
        new_parser.write(handle)
    log(
        f"Rebuilt settings.ini from new schema: migrated {len(result['migrated'])} existing values; "
        f"dropped {len(result['dropped'])} retired/unknown keys into rollback-only history."
    )
    return result


def _migrate_json_value(new_value: Any, old_value: Any, *, prefix: str = "") -> tuple[Any, list[str], list[str]]:
    migrated: list[str] = []
    dropped: list[str] = []
    if isinstance(new_value, dict) and isinstance(old_value, dict):
        result = _deep_copy(new_value)
        for key, old_child in old_value.items():
            label = f"{prefix}.{key}" if prefix else str(key)
            if key not in new_value:
                dropped.append(label)
                continue
            new_child = new_value[key]
            if isinstance(new_child, dict) and isinstance(old_child, dict):
                merged_child, child_migrated, child_dropped = _migrate_json_value(
                    new_child, old_child, prefix=label
                )
                result[key] = merged_child
                migrated.extend(child_migrated)
                dropped.extend(child_dropped)
            else:
                result[key] = _deep_copy(old_child)
                migrated.append(label)
        return result, migrated, dropped
    return _deep_copy(old_value), [prefix or "<root>"], []


def _migrate_existing_json(package_path: Path, old_path: Path, log: LogFn) -> dict[str, list[str]]:
    result = {"migrated": [], "dropped": []}
    if not package_path.is_file() or not old_path.is_file():
        return result
    try:
        new_value = json.loads(package_path.read_text(encoding="utf-8-sig"))
        old_value = json.loads(old_path.read_text(encoding="utf-8-sig"))
    except Exception as exc:
        raise InstallerError(f"Could not migrate JSON config {package_path}: {exc}") from exc
    merged, migrated, dropped = _migrate_json_value(new_value, old_value)
    package_path.write_text(
        json.dumps(merged, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    result["migrated"] = migrated
    result["dropped"] = dropped
    log(
        f"Migrated matching existing values into {package_path.name}: "
        f"{len(migrated)} migrated, {len(dropped)} retired/unknown keys left in rollback snapshot."
    )
    return result


def _restore_operator_config(target: Path, snapshot: Path | None, *, package_version: str, log: LogFn) -> None:
    """Migrate old config values into the freshly installed package schema."""
    if snapshot is None or not snapshot.is_dir():
        return
    config_dir = target / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    audit: dict[str, Any] = {
        "schema": 1,
        "package_version": package_version,
        "migrated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "settings": {},
        "json": {},
        "dropped_files": [],
    }

    audit["settings"] = _migrate_existing_settings(
        config_dir / "settings.ini",
        snapshot / "settings.ini",
        package_version=package_version,
        log=log,
    )

    package_json_paths = {
        item.relative_to(config_dir).as_posix(): item
        for item in config_dir.rglob("*.json")
        if item.is_file()
    }
    for rel, package_json in sorted(package_json_paths.items()):
        old_json = snapshot.joinpath(*PurePosixPath(rel).parts)
        if old_json.is_file():
            audit["json"][rel] = _migrate_existing_json(package_json, old_json, log)

    for old_json in sorted(snapshot.rglob("*.json")):
        rel = old_json.relative_to(snapshot).as_posix()
        if rel not in package_json_paths and rel != "migration.json":
            audit["dropped_files"].append(rel)

    audit_path = snapshot / "migration.json"
    audit_path.write_text(
        json.dumps(audit, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    log(f"Config migration audit written to rollback snapshot: {audit_path}")


def _sync_tree_contents(
    source_root: Path,
    target: Path,
    *,
    protected: set[str],
    log: LogFn,
    ignore_source_roots: set[str] | None = None,
) -> dict[str, int]:
    """Mirror package-owned content while preserving generated/persistent paths."""
    target.mkdir(parents=True, exist_ok=True)

    ignored = {_norm_rel(Path(x)) for x in (ignore_source_roots or set())}
    def ignored_rel(rel: str) -> bool:
        return any(rel == item or rel.startswith(item + "/") for item in ignored)

    source_files: set[str] = set()
    source_dirs: set[str] = set()
    for item in source_root.rglob("*"):
        rel = _norm_rel(item.relative_to(source_root))
        if ignored_rel(rel):
            continue
        if item.is_dir():
            source_dirs.add(rel)
        else:
            source_files.add(rel)

    stats = {"added": 0, "updated": 0, "unchanged": 0, "removed": 0, "preserved": 0}

    # Remove target content that is no longer present in the source package.
    # Walk bottom-up so now-empty directories can be removed safely.
    if target.exists():
        all_target = sorted(target.rglob("*"), key=lambda x: len(x.parts), reverse=True)
        for item in all_target:
            rel = _norm_rel(item.relative_to(target))
            if _is_protected(rel, protected):
                stats["preserved"] += 1
                continue
            if item.is_dir() and not item.is_symlink():
                if rel in source_dirs or _has_protected_descendant(rel, protected):
                    continue
                try:
                    item.rmdir()
                    stats["removed"] += 1
                    log(f"REMOVE dir  {rel}")
                except OSError:
                    # Non-empty means it contains a source/protected descendant or
                    # an item that will be handled separately.
                    pass
            else:
                if rel not in source_files:
                    item.unlink(missing_ok=True)
                    stats["removed"] += 1
                    log(f"REMOVE file {rel}")

    # Create directories first, resolving file/dir shape changes.
    for rel in sorted(source_dirs, key=lambda x: len(PurePosixPath(x).parts)):
        dest = target.joinpath(*PurePosixPath(rel).parts)
        if dest.exists() and not dest.is_dir():
            if _is_protected(rel, protected):
                raise InstallerError(f"Protected path conflicts with package directory: {rel}")
            dest.unlink()
            stats["removed"] += 1
        dest.mkdir(parents=True, exist_ok=True)

    # Copy only new or changed files.
    for rel in sorted(source_files):
        src = source_root.joinpath(*PurePosixPath(rel).parts)
        dest = target.joinpath(*PurePosixPath(rel).parts)
        if dest.exists() and dest.is_dir():
            if _is_protected(rel, protected):
                raise InstallerError(f"Protected path conflicts with package file: {rel}")
            shutil.rmtree(dest)
            stats["removed"] += 1
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.is_file():
            same = src.stat().st_size == dest.stat().st_size and _sha256(src) == _sha256(dest)
            if same:
                stats["unchanged"] += 1
                continue
            shutil.copy2(src, dest)
            stats["updated"] += 1
            log(f"UPDATE      {rel}")
        else:
            shutil.copy2(src, dest)
            stats["added"] += 1
            log(f"ADD         {rel}")

    return stats


def _installed_version(python_exe: Path, distribution: str, log: LogFn) -> str | None:
    code = (
        "import importlib.metadata as m; "
        f"print(m.version({distribution!r}))"
    )
    try:
        cp = subprocess.run(
            [str(python_exe), "-c", code],
            cwd=None,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0,
        )
    except Exception:
        return None
    if cp.returncode != 0:
        return None
    value = (cp.stdout or "").strip().splitlines()
    return value[-1].strip() if value else None


def _exact_requirement_pins(requirements: Path) -> dict[str, str]:
    pins: dict[str, str] = {}
    for raw in requirements.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        match = re.fullmatch(r"([A-Za-z0-9_.-]+)==([^;\s]+)(?:\s*;.*)?", line)
        if not match:
            return {}
        pins[match.group(1)] = match.group(2)
    return pins


def _requirements_exactly_satisfied(python_exe: Path, requirements: Path, log: LogFn) -> bool:
    pins = _exact_requirement_pins(requirements)
    if not pins:
        return False
    mismatched: list[str] = []
    for name, expected in pins.items():
        installed = _installed_version(python_exe, name, log)
        if installed != expected:
            mismatched.append(f"{name}: installed={installed or 'missing'} expected={expected}")
    if mismatched:
        log("Pinned dependency reconciliation required: " + "; ".join(mismatched[:12]))
        if len(mismatched) > 12:
            log(f"... and {len(mismatched) - 12} more mismatch(es)")
        return False
    return True


def _external_path(settings: configparser.ConfigParser, key: str, default: str) -> Path:
    raw_docs = settings.get("paths", "documents_root", fallback="").strip()
    docs = Path(_expand_windows_vars(raw_docs)).expanduser() if raw_docs else (Path.home() / "Documents" / "Norm")
    raw = settings.get("paths", key, fallback=default).strip() or default
    value = Path(_expand_windows_vars(raw)).expanduser()
    return value if value.is_absolute() else docs / value


def _post_install_workspace(settings: configparser.ConfigParser, target: Path, warnings: list[str], log: LogFn) -> None:
    raw_docs = settings.get("paths", "documents_root", fallback="").strip()
    docs = Path(_expand_windows_vars(raw_docs)).expanduser() if raw_docs else (Path.home() / "Documents" / "Norm")
    try:
        docs.mkdir(parents=True, exist_ok=True)
        workspace = _external_path(settings, "workspace_root", "workspace")
        temp_root = _external_path(settings, "temp_root", "temp")
        workspace.mkdir(parents=True, exist_ok=True)
        for child in (temp_root, temp_root / "tasks", temp_root / "scratch", temp_root / "recovery"):
            child.mkdir(parents=True, exist_ok=True)
        (target / "plugins").mkdir(parents=True, exist_ok=True)
        (target / ".ssh").mkdir(parents=True, exist_ok=True)
        log(f"Documents root: {docs}")
        log(f"Durable workspace: {workspace}")
        log(f"Disposable temp: {temp_root}")
    except Exception as exc:
        warnings.append(f"Could not create Norm workspace/temp directories: {exc}")

    raw_secret = settings.get("environment", "secrets_file", fallback="").strip()
    if raw_secret:
        secret = Path(_expand_windows_vars(raw_secret)).expanduser()
        if not secret.exists():
            warnings.append(f"Secrets file is not present yet: {secret}")

    (target / "logs").mkdir(exist_ok=True)
    (target / "state").mkdir(exist_ok=True)


def _find_pg_restore(settings: configparser.ConfigParser, preferred: str) -> Path | None:
    candidates: list[Path] = []
    if preferred:
        candidates.append(Path(_expand_windows_vars(preferred)).expanduser())
    raw = settings.get("backup", "postgres_restore_executable", fallback="").strip()
    if raw:
        candidates.append(Path(_expand_windows_vars(raw)).expanduser())
    if os.name == "nt":
        base = Path(r"C:\Program Files\PostgreSQL")
        if base.is_dir():
            candidates.extend(sorted(base.glob(r"*\bin\pg_restore.exe"), reverse=True))
    for path in candidates:
        if path.is_file():
            return path.resolve()
    return None


def _ensure_norm_not_running() -> None:
    if os.name != "nt":
        return
    try:
        cp = subprocess.run(["tasklist", "/FI", "IMAGENAME eq norm.exe"], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=5, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if "norm.exe" in (cp.stdout or "").lower():
            raise InstallerError("norm.exe is running. Stop Norm before restoring a full backup.")
    except InstallerError:
        raise
    except Exception:
        pass


def _restore_full_backup_state(package_root: Path, target: Path, settings: configparser.ConfigParser, manifest: dict, *, log: LogFn) -> None:
    payload_rel = str(manifest.get("backup_payload") or "").strip()
    payload_path = package_root / payload_rel
    if not payload_path.is_file():
        raise InstallerError(f"Full-backup payload manifest is missing: {payload_rel}")
    backup = json.loads(payload_path.read_text(encoding="utf-8-sig"))
    if not backup.get("sensitive"):
        log("WARNING: full-backup manifest did not mark itself sensitive")
    restore = dict(backup.get("restore") or {})
    state_root = package_root / "backup-state"
    _ensure_norm_not_running()

    # Restore persistent runtime trees exactly as captured.
    for key, dest in (
        ("plugins_member", target / "plugins"),
        ("ssh_member", target / ".ssh"),
        ("logs_member", target / "logs"),
        ("state_member", target / "state"),
    ):
        rel = str(restore.get(key) or "").strip()
        if not rel:
            continue
        src = package_root / rel
        if src.is_dir():
            log(f"Restoring {dest.name} from backup payload")
            _sync_tree_contents(src, dest, protected=set(), log=log)

    workspace_rel = str(restore.get("workspace_member") or "").strip()
    if workspace_rel:
        src = package_root / workspace_rel
        if src.is_dir():
            workspace = _external_path(settings, "workspace_root", "workspace")
            log(f"Restoring workspace: {workspace}")
            _sync_tree_contents(src, workspace, protected=set(), log=log)

    recovery_rel = str(restore.get("temp_recovery_member") or "").strip()
    if recovery_rel:
        src = package_root / recovery_rel
        if src.is_dir():
            recovery = _external_path(settings, "temp_root", "temp") / "recovery"
            log(f"Restoring recovery material: {recovery}")
            _sync_tree_contents(src, recovery, protected=set(), log=log)

    secret_rel = str(restore.get("secrets_member") or "").strip()
    if secret_rel:
        src = package_root / secret_rel
        raw_secret = settings.get("environment", "secrets_file", fallback="").strip()
        if not raw_secret:
            raise InstallerError("Backup contains secrets but installed settings do not define environment.secrets_file")
        dest = Path(_expand_windows_vars(raw_secret)).expanduser()
        if not src.is_file():
            raise InstallerError(f"Backup secrets payload is missing: {secret_rel}")
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)
        log(f"Restored configured secrets file: {dest}")

    dump_rel = str(restore.get("postgres_member") or "").strip()
    pg_conn = dict(restore.get("postgres_connection") or {})
    if dump_rel and pg_conn:
        dump = package_root / dump_rel
        if not dump.is_file():
            raise InstallerError(f"PostgreSQL backup payload is missing: {dump_rel}")
        pg_restore = _find_pg_restore(settings, str(restore.get("pg_restore") or ""))
        if pg_restore is None:
            raise InstallerError("pg_restore.exe could not be found for full-backup restore")
        host = str(pg_conn.get("host") or "").strip()
        port = str(pg_conn.get("port") or "").strip()
        dbname = str(pg_conn.get("dbname") or "").strip()
        user = str(pg_conn.get("user") or "").strip()
        password = str(pg_conn.get("password") or "")
        cmd = [str(pg_restore), "--clean", "--if-exists", "--no-owner"]
        if host: cmd.extend(["--host", host])
        if port: cmd.extend(["--port", port])
        if user: cmd.extend(["--username", user])
        if dbname: cmd.extend(["--dbname", dbname])
        cmd.append(str(dump))
        env = os.environ.copy()
        if password:
            env["PGPASSWORD"] = password
        log(f"Restoring PostgreSQL schema {restore.get('postgres_schema') or 'norm_runtime'}")
        _run(cmd, cwd=target, log=log, env=env)


def install_norm(
    options: InstallOptions,
    *,
    log: LogFn = _noop_log,
    progress: ProgressFn = _noop_progress,
) -> InstallResult:
    source = Path(options.source_zip).expanduser().resolve()
    target = Path(options.target_dir).expanduser().resolve()
    python_exe = Path(options.python_exe).expanduser().resolve()
    warnings: list[str] = []

    progress(2, "Validating package")
    info = inspect_package(source)
    log(f"Package: {info.label}")
    log(f"Source:  {source}")
    log(f"Target:  {target}")

    if not python_exe.is_file():
        raise InstallerError(f"Python executable does not exist: {python_exe}")

    with tempfile.TemporaryDirectory(prefix="norm-installer-") as temp_name:
        temp = Path(temp_name)
        progress(8, "Staging source package")
        staged_zip = temp / source.name
        shutil.copy2(source, staged_zip)
        staged_info = inspect_package(staged_zip)
        extracted_root = _extract_package(staged_info, temp / "source")
        settings = _read_settings(extracted_root, staged_info.manifest)

        progress(14, "Checking Python")
        actual_py = _python_version(python_exe, log)
        expected_py = _expected_python(settings)
        log(f"Selected Python: {actual_py[0]}.{actual_py[1]}.{actual_py[2]} ({python_exe})")
        if expected_py and actual_py[:2] != expected_py:
            raise InstallerError(
                f"This package expects Python {expected_py[0]}.{expected_py[1]}.x, "
                f"but the selected interpreter is {actual_py[0]}.{actual_py[1]}.{actual_py[2]}"
            )

        target_nonempty = target.exists() and any(target.iterdir())
        if target_nonempty and not options.replace_existing:
            raise InstallerError(
                f"Target directory is not empty: {target}. Enable in-place update to continue."
            )

        # The new package defines the environment location and compiled output.
        # These generated/persistent paths survive the source mirror.
        venv_rel = settings.get("environment", "venv_path", fallback=".venv").strip() or ".venv"
        compiled_rel = str(staged_info.manifest.get("compiled_executable", "core/norm.exe"))
        protected = {
            _norm_rel(Path(venv_rel)),
            _norm_rel(Path(compiled_rel)),
            "logs",
            "state",
            "plugins",
            ".ssh",
            "backup",
            "backups",
            ".env",
            ".norm-install-state.json",
        }

        config_snapshot = _snapshot_existing_config(target, log) if target_nonempty else None

        progress(20, "Synchronizing Norm files")
        ignore_source = {"backup-state", "SENSITIVE_BACKUP.txt"} if staged_info.manifest.get("package_type") == "full-backup" else set()
        stats = _sync_tree_contents(extracted_root, target, protected=protected, log=log, ignore_source_roots=ignore_source)
        _restore_operator_config(target, config_snapshot, package_version=staged_info.version, log=log)
        for subtree in staged_info.manifest.get("managed_persistent_subtrees", []) or []:
            rel = _norm_rel(Path(str(subtree)))
            src_subtree = extracted_root.joinpath(*PurePosixPath(rel).parts)
            if src_subtree.is_dir():
                dst_subtree = target.joinpath(*PurePosixPath(rel).parts)
                substats = _sync_tree_contents(src_subtree, dst_subtree, protected=set(), log=log)
                log(f"Managed persistent subtree {rel}: {substats}")
        log(
            "Sync summary: "
            f"{stats['added']} added, {stats['updated']} updated, "
            f"{stats['unchanged']} unchanged, {stats['removed']} removed"
        )
        _bind_installed_runtime_root(target, staged_info.manifest, log)
        if staged_info.manifest.get("package_type") == "full-backup":
            progress(23, "Restoring full-backup private state")
            installed_settings = _read_settings(target, staged_info.manifest)
            _restore_full_backup_state(extracted_root, target, installed_settings, staged_info.manifest, log=log)
            normalized_manifest = dict(staged_info.manifest)
            normalized_manifest["package_type"] = "portable-source"
            normalized_manifest.pop("backup_payload", None)
            (target / "package-manifest.json").write_text(json.dumps(normalized_manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
            log("Normalized installed package manifest back to portable-source after full-backup restore.")

        # Apply the operator/environment imprint only after package synchronization and
        # any backup-state restore. This keeps the public package generic while allowing
        # a local sidecar to deterministically bind this installation to its environment.
        installed_manifest_path = target / "package-manifest.json"
        installed_manifest = json.loads(installed_manifest_path.read_text(encoding="utf-8-sig"))
        _apply_imprint(
            target,
            installed_manifest,
            options.imprint,
            options.secret_values,
            log=log,
        )

    # From here onward the install operates only on the target copy.
    manifest_path = target / "package-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    settings = _read_settings(target, manifest)

    venv_rel = settings.get("environment", "venv_path", fallback=".venv").strip() or ".venv"
    venv_dir = target / venv_rel
    venv_python = venv_dir / ("Scripts/python.exe" if os.name == "nt" else "bin/python")

    if options.recreate_venv and venv_dir.exists():
        progress(25, "Recreating virtual environment")
        log(f"Removing existing virtual environment: {venv_dir}")
        shutil.rmtree(venv_dir)

    if venv_python.is_file():
        progress(28, "Reusing virtual environment")
        venv_ver = _python_version(venv_python, log)
        expected_py = _expected_python(settings)
        if expected_py and venv_ver[:2] != expected_py:
            raise InstallerError(
                f"Existing venv uses Python {venv_ver[0]}.{venv_ver[1]}.{venv_ver[2]}, "
                f"but this package expects Python {expected_py[0]}.{expected_py[1]}.x. "
                "Enable 'Recreate .venv' for this update."
            )
        log(f"Reusing existing virtual environment: {venv_dir}")
    else:
        progress(28, "Creating virtual environment")
        _run([str(python_exe), "-m", "venv", str(venv_dir)], cwd=target, log=log)

    if not venv_python.is_file():
        raise InstallerError(f"Virtual environment does not contain Python at {venv_python}")

    # pip is installer infrastructure, not a Norm runtime dependency. Keep it
    # out of requirements-lock.txt so changing pip never mutates the portable
    # source package or invalidates the source ZIP's companion SHA-256.
    installed_pip = _installed_version(venv_python, "pip", log)
    if installed_pip == PIP_VERSION:
        progress(32, f"pip {PIP_VERSION} already installed")
        log(f"pip {PIP_VERSION} matches the installer infrastructure pin; skipping reinstall.")
    else:
        progress(32, f"Normalizing pip to {PIP_VERSION}")
        if installed_pip:
            log(f"Current pip version: {installed_pip}; installing exact reproducible version {PIP_VERSION}.")
        _run(
            [str(venv_python), "-m", "pip", "install", PIP_SPEC],
            cwd=target,
            log=log,
        )

    if options.install_torch:
        torch_cfg = _configured_torch(settings)
        if torch_cfg:
            version, index_url = torch_cfg
            installed_torch = _installed_version(venv_python, "torch", log)
            if installed_torch == version:
                progress(38, f"PyTorch {version} already installed")
                log(f"PyTorch {version} already satisfies the package; skipping reinstall.")
            else:
                progress(38, f"Installing PyTorch {version}")
                cmd = [str(venv_python), "-m", "pip", "install", f"torch=={version}"]
                if index_url:
                    cmd.extend(["--index-url", index_url])
                _run(cmd, cwd=target, log=log)

    requirements = target / str(manifest["requirements_lock"])
    dependency_mode = (options.dependency_mode or "package").strip().lower()
    if dependency_mode == "package":
        if _requirements_exactly_satisfied(venv_python, requirements, log):
            progress(52, "Package requirements already satisfied")
            log("Every package-locked distribution already matches exactly; skipping pip dependency reconciliation.")
        else:
            progress(52, "Installing package requirements")
            _run(
                [str(venv_python), "-m", "pip", "install", "--disable-pip-version-check", "-r", str(requirements)],
                cwd=target,
                log=log,
            )
    elif dependency_mode == "newest":
        progress(52, "Resolving newest available requirements")
        pins = _exact_requirement_pins(requirements)
        if not pins:
            raise InstallerError(f"No exact package requirements were found in {requirements}")
        requested = sorted(pins)
        log(
            "Newest-available mode: resolving the newest non-prerelease releases pip accepts "
            "for the package's locked distribution set: " + ", ".join(requested)
        )
        _run(
            [str(venv_python), "-m", "pip", "install", "--disable-pip-version-check", "--upgrade", *requested],
            cwd=target,
            log=log,
        )
        evidence_dir = target / "state" / "installer"
        evidence_dir.mkdir(parents=True, exist_ok=True)
        freeze = _run(
            [str(venv_python), "-m", "pip", "freeze", "--all"],
            cwd=target,
            log=log,
        )
        (evidence_dir / "resolved-requirements.txt").write_text(
            freeze.stdout if freeze.stdout.endswith("\n") else freeze.stdout + "\n",
            encoding="utf-8",
        )
        log(f"Recorded resolved environment: {evidence_dir / 'resolved-requirements.txt'}")
    else:
        raise InstallerError(f"Unknown dependency mode: {options.dependency_mode!r}")

    progress(68, "Checking installed environment")
    _run([str(venv_python), "-m", "pip", "check"], cwd=target, log=log)
    source_dir = target / str(manifest.get("source_dir", "core"))
    tools_dir = target / "tools"
    _run(
        [str(venv_python), "-m", "compileall", "-q", str(source_dir), str(tools_dir)],
        cwd=target,
        log=log,
    )
    # compileall is a validation step; keep generated source caches out of the
    # installed application tree (the venv keeps its own normal caches).
    for scan_root in (source_dir, tools_dir):
        if scan_root.is_dir():
            for cache in sorted(scan_root.rglob("__pycache__"), reverse=True):
                shutil.rmtree(cache, ignore_errors=True)

    compiled: Path | None = None
    if options.compile_exe:
        compiled_rel = str(manifest.get("compiled_executable", "core/norm.exe"))
        compiled = target / compiled_rel
        compiled.parent.mkdir(parents=True, exist_ok=True)
        progress(76, "Compiling norm.exe")
        build_script = target / str(manifest["build_script"])
        _run(
            [str(venv_python), str(build_script), "--candidate", str(compiled)],
            cwd=target,
            log=log,
        )
        if not compiled.is_file() or compiled.stat().st_size == 0:
            raise InstallerError(f"Build completed without producing {compiled}")
        if os.name == "nt":
            with compiled.open("rb") as handle:
                if handle.read(2) != b"MZ":
                    raise InstallerError(f"Compiled file does not look like a Windows executable: {compiled}")
        # The package build script uses staging as disposable PyInstaller work
        # space. A successful install keeps only the requested executable.
        shutil.rmtree(target / "staging", ignore_errors=True)

    progress(92, "Creating runtime directories")
    _post_install_workspace(settings, target, warnings, log)

    entrypoint = target / str(manifest["entrypoint"])
    if not entrypoint.is_file():
        raise InstallerError(f"Installed entrypoint is missing: {entrypoint}")
    if not manifest_path.is_file():
        raise InstallerError("Installed package-manifest.json is missing")

    state_path = _write_install_state(target, str(manifest.get("version") or info.version), options.package_imprint_baseline)
    log(f"Saved non-secret package-imprint baseline for future migrations: {state_path}")
    saved_imprint = save_local_imprint(options.imprint or {})
    log(f"Saved accepted non-secret Environment values to private local imprint: {saved_imprint}")
    progress(100, "Installation complete")
    return InstallResult(
        package=PackageInfo(
            source_zip=source,
            zip_root=PurePosixPath("."),
            manifest_member="package-manifest.json",
            manifest=manifest,
        ),
        target_dir=target,
        venv_python=venv_python,
        compiled_exe=compiled,
        warnings=warnings,
    )


def _candidate_roots() -> list[Path]:
    roots: list[Path] = []
    if getattr(sys, "frozen", False):
        roots.append(Path(sys.executable).resolve().parent)
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            roots.append(Path(meipass))
    else:
        roots.append(Path(__file__).resolve().parent)
    # Preserve order while deduplicating.
    unique: list[Path] = []
    seen: set[Path] = set()
    for root in roots:
        try:
            key = root.resolve()
        except Exception:
            key = root
        if key not in seen:
            seen.add(key)
            unique.append(root)
    return unique


def _source_directory() -> Path:
    """Directory beside the running installer, never PyInstaller's temporary _MEIPASS."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent



IMPRINT_SCHEMA_VERSION = 1
LOCAL_IMPRINT_NAME = "norm-imprint.local.json"
LOCAL_PRIVATE_DIR = ".norm-local"
EXAMPLE_IMPRINT_NAME = "norm-imprint.example.json"
PUBLIC_PACKAGE_IMPRINT_NAME = "norm-imprint.json"
_SECRET_KEY_RE = re.compile(r"(?:password|passwd|pwd|secret|token|authkey|api[_-]?key|private[_-]?key)", re.I)

DEFAULT_IMPRINT: dict[str, Any] = {
    "schema": IMPRINT_SCHEMA_VERSION,
    "install": {
        "target_dir": r"C:\Norm",
        "dependency_mode": "newest",
        "python_exe": "",
    },
    "paths": {
        "documents_root": r"%USERPROFILE%\Documents\Norm",
        "workspace_root": "workspace",
        "temp_root": "temp",
    },
    "network": {
        "current_machine": "norm-host",
        "current_domain": "example.invalid",
        "require_tailscale": False,
        "ollama_host": "loopback",
        "ollama_port": 11434,
        "norm_host": "loopback",
        "norm_port": 12543,
        "activity_host": "loopback",
        "activity_port": 8766,
        "postgres_host": "loopback",
        "postgres_port": 5432,
        "redis_host": "loopback",
        "redis_port": 6379,
    },
    "postgres": {
        "user": "norm",
        "database": "norm",
        "schema": "norm_runtime",
        "stocks_database": "stocks_api",
    },
    "ssh": {
        "enabled": False,
        "user": "",
        "remote_host": "",
        "docker_host": "",
        "port": 22,
        "identity_file": "norm_remote_ed25519",
    },
    "runtime": {
        "allowed_roots": [r"%USERPROFILE%\Documents\Norm"],
        "storage_context": {
            "primary_name": "documents",
            "primary_root": r"%USERPROFILE%\Documents\Norm",
            "backup_name": "workspace",
        },
        "network_map": {
            "never_probe_name_patterns": [
                "*decoy*",
                "*honeypot*",
                "*mesh-canary*",
                "*mesh-cache*",
                "*decoy-guard*",
            ],
            "never_probe_cidrs": [],
            "targets": [],
        },
    },
}


def _deep_copy(value: Any) -> Any:
    return json.loads(json.dumps(value))


def _deep_merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    merged = _deep_copy(base)
    for key, value in overlay.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = _deep_copy(value)
    return merged


def _find_forbidden_imprint_key(value: Any, prefix: str = "") -> str | None:
    if isinstance(value, dict):
        for key, child in value.items():
            name = str(key)
            path = f"{prefix}.{name}" if prefix else name
            if _SECRET_KEY_RE.search(name):
                return path
            found = _find_forbidden_imprint_key(child, path)
            if found:
                return found
    elif isinstance(value, list):
        for index, child in enumerate(value):
            found = _find_forbidden_imprint_key(child, f"{prefix}[{index}]")
            if found:
                return found
    return None


def validate_imprint(data: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise InstallerError("Imprint must be a JSON object.")
    schema = data.get("schema", IMPRINT_SCHEMA_VERSION)
    if schema != IMPRINT_SCHEMA_VERSION:
        raise InstallerError(
            f"Unsupported imprint schema {schema!r}; expected {IMPRINT_SCHEMA_VERSION}."
        )
    forbidden = _find_forbidden_imprint_key(data)
    if forbidden:
        raise InstallerError(
            f"Imprint contains a secret-like key ({forbidden}). "
            "Imprint files are intentionally non-secret; enter secrets in masked installer fields instead."
        )
    merged = _deep_merge(DEFAULT_IMPRINT, data)
    merged["schema"] = IMPRINT_SCHEMA_VERSION
    mode = str(merged.get("install", {}).get("dependency_mode", "newest")).strip().lower()
    if mode not in {"newest", "package"}:
        raise InstallerError("install.dependency_mode must be 'newest' or 'package'.")
    merged["install"]["dependency_mode"] = mode
    return merged


def load_imprint(path: Path | None = None) -> tuple[dict[str, Any], Path | None]:
    if path is not None:
        candidates = [Path(path).expanduser().resolve()]
    else:
        candidates = [
            (_source_directory() / LOCAL_IMPRINT_NAME).resolve(),
            envtools.persistent_imprint_path(LOCAL_IMPRINT_NAME),
        ]
    candidate = next((item for item in candidates if item.is_file()), None)
    if candidate is None:
        return _deep_copy(DEFAULT_IMPRINT), None
    try:
        raw = json.loads(candidate.read_text(encoding="utf-8-sig"))
    except Exception as exc:
        raise InstallerError(f"Could not read imprint {candidate}: {exc}") from exc
    return validate_imprint(raw), candidate


def read_package_public_imprint(info: PackageInfo | None) -> dict[str, Any]:
    """Read the raw non-secret public/default imprint from the selected source ZIP."""
    if info is None:
        return {}
    rel = str(info.manifest.get("imprint") or PUBLIC_PACKAGE_IMPRINT_NAME).strip()
    if not rel:
        return {}
    member = str(info.zip_root / PurePosixPath(rel))
    try:
        with zipfile.ZipFile(info.source_zip, "r") as zf:
            raw = json.loads(zf.read(member).decode("utf-8-sig"))
    except KeyError:
        return {}
    except Exception as exc:
        raise InstallerError(f"Could not read package public imprint {rel}: {exc}") from exc
    if not isinstance(raw, dict):
        raise InstallerError(f"Package public imprint must be a JSON object: {rel}")
    forbidden = _find_forbidden_imprint_key(raw)
    if forbidden:
        raise InstallerError(f"Package public imprint contains secret-like key: {forbidden}")
    return _deep_copy(raw)



def _write_install_state(target: Path, package_version: str, baseline: dict[str, Any] | None) -> Path:
    clean = _deep_copy(baseline or {})
    forbidden = _find_forbidden_imprint_key(clean)
    if forbidden:
        raise InstallerError(f"Package imprint baseline contains secret-like key: {forbidden}")
    path = target / ".norm-install-state.json"
    payload = {
        "schema": 1,
        "package_version": str(package_version),
        "package_imprint_baseline": clean,
    }
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    return path


def _nonsecret_imprint_for_save(data: dict[str, Any]) -> dict[str, Any]:
    clean = validate_imprint(data)
    # validate_imprint already rejects secret-like keys; deep-copy so caller mutation cannot alter UI state.
    return _deep_copy(clean)


def save_local_imprint(data: dict[str, Any], path: Path | None = None) -> Path:
    destination = Path(path).expanduser().resolve() if path else envtools.persistent_imprint_path(LOCAL_IMPRINT_NAME)
    clean = _nonsecret_imprint_for_save(data)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(clean, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return destination


def _coerce_port(value: Any, label: str) -> int:
    try:
        port = int(str(value).strip())
    except (TypeError, ValueError) as exc:
        raise InstallerError(f"{label} must be an integer port.") from exc
    if not 1 <= port <= 65535:
        raise InstallerError(f"{label} must be between 1 and 65535.")
    return port


def _bool_text(value: Any) -> str:
    return "true" if bool(value) else "false"


def _resolve_secrets_path(settings_path: Path) -> Path:
    parser = configparser.ConfigParser(interpolation=None)
    parser.read(settings_path, encoding="utf-8-sig")
    raw = parser.get("environment", "secrets_file", fallback="").strip()
    if not raw:
        raise InstallerError("Installed settings do not define environment.secrets_file.")
    expanded = _expand_windows_vars(raw)
    return Path(expanded).expanduser().resolve()


def _update_env_file(path: Path, updates: dict[str, str], *, require_password: bool) -> None:
    existing: dict[str, str] = {}
    comments: list[str] = []
    if path.is_file():
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                comments.append(line)
                continue
            key, value = stripped.split("=", 1)
            existing[key.strip()] = value.strip()

    for key, value in updates.items():
        if value is not None:
            existing[key] = str(value)

    if require_password and not existing.get("NORM_POSTGRES_PASSWORD", "").strip():
        raise InstallerError(
            "PostgreSQL password is required for a new Norm environment. "
            "Enter it in the masked Database password field. It will be written only to the configured secrets file."
        )

    path.parent.mkdir(parents=True, exist_ok=True)
    ordered_keys = [
        "NORM_POSTGRES_USER",
        "NORM_POSTGRES_PASSWORD",
        "NORM_POSTGRES_DB",
        "NORM_POSTGRES_SCHEMA",
        "NORM_STOCKS_DB",
        "NORM_ROTOR5_SECRET",
        "NORM_ROTOR5_PREVIOUS_SECRETS",
    ]
    lines: list[str] = [
        "# Norm local secrets. This file is not part of the source package or imprint.",
        "# Keep permissions restricted and never commit it.",
    ]
    seen: set[str] = set()
    for key in ordered_keys:
        if key in existing:
            lines.append(f"{key}={existing[key]}")
            seen.add(key)
    for key in sorted(existing):
        if key not in seen:
            lines.append(f"{key}={existing[key]}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def _apply_imprint(
    target: Path,
    manifest: dict[str, Any],
    imprint: dict[str, Any] | None,
    secret_values: dict[str, str] | None,
    *,
    log: LogFn,
    preserve_existing_secrets: bool = False,
) -> None:
    data = validate_imprint(imprint or {})
    settings_path = target / str(manifest["settings"])
    if not settings_path.is_file():
        raise InstallerError(f"Installed settings file is missing: {settings_path}")

    paths = data["paths"]
    for key in ("documents_root", "workspace_root", "temp_root"):
        _set_ini_value(settings_path, "paths", key, str(paths[key]))

    network = data["network"]
    network_keys = (
        "current_machine", "current_domain", "ollama_host", "norm_host",
        "activity_host", "postgres_host", "redis_host",
    )
    for key in network_keys:
        _set_ini_value(settings_path, "network", key, str(network[key]))
    _set_ini_value(settings_path, "network", "require_tailscale", _bool_text(network["require_tailscale"]))
    for key in ("ollama_port", "norm_port", "activity_port", "postgres_port", "redis_port"):
        _set_ini_value(settings_path, "network", key, str(_coerce_port(network[key], f"network.{key}")))

    ssh = data["ssh"]
    _set_ini_value(settings_path, "ssh", "enabled", _bool_text(ssh["enabled"]))
    _set_ini_value(settings_path, "ssh", "ca8d_host", str(ssh["remote_host"]))
    _set_ini_value(settings_path, "ssh", "ca8d_docker_host", str(ssh["docker_host"]))
    _set_ini_value(settings_path, "ssh", "ca8d_port", str(_coerce_port(ssh["port"], "ssh.port")))
    _set_ini_value(settings_path, "ssh", "ca8d_user", str(ssh["user"]))
    _set_ini_value(settings_path, "ssh", "ca8d_identity_file", str(ssh["identity_file"]))

    runtime_path = target / str(manifest.get("runtime_config", "config/runtime.json"))
    if runtime_path.is_file():
        runtime = json.loads(runtime_path.read_text(encoding="utf-8-sig"))
        tools = runtime.setdefault("tools", {})
        allowed = data.get("runtime", {}).get("allowed_roots", [])
        if not isinstance(allowed, list) or not [str(item).strip() for item in allowed if str(item).strip()]:
            allowed = [str(paths["documents_root"])]
        tools["allowed_roots"] = [str(item) for item in allowed if str(item).strip()]
        storage = data.get("runtime", {}).get("storage_context", {})
        if isinstance(storage, dict):
            tools["storage_context"] = {
                "primary_name": str(storage.get("primary_name") or "documents"),
                "primary_root": str(storage.get("primary_root") or paths["documents_root"]),
                "backup_name": str(storage.get("backup_name") or "workspace"),
                "probe_timeout_seconds": float(storage.get("probe_timeout_seconds", 2.0)),
                "status_cache_seconds": float(storage.get("status_cache_seconds", 30.0)),
            }
        runtime_path.write_text(json.dumps(runtime, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")

    map_path = target / "config" / "network-map.json"
    map_cfg = data.get("runtime", {}).get("network_map", {})
    if isinstance(map_cfg, dict):
        safe_map = {
            "schema": 1,
            "never_probe_name_patterns": list(map_cfg.get("never_probe_name_patterns") or []),
            "never_probe_cidrs": list(map_cfg.get("never_probe_cidrs") or []),
            "targets": list(map_cfg.get("targets") or []),
        }
        map_path.parent.mkdir(parents=True, exist_ok=True)
        map_path.write_text(json.dumps(safe_map, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")

    pg = data["postgres"]
    for key in ("user", "database", "schema", "stocks_database"):
        _set_ini_value(settings_path, "postgres", key, str(pg[key]))
    updates = {
        "NORM_POSTGRES_USER": str(pg.get("user") or "norm"),
        "NORM_POSTGRES_DB": str(pg.get("database") or "norm"),
        "NORM_POSTGRES_SCHEMA": str(pg.get("schema") or "norm_runtime"),
        "NORM_STOCKS_DB": str(pg.get("stocks_database") or "stocks_api"),
    }
    for key, value in (secret_values or {}).items():
        if key in {"NORM_POSTGRES_PASSWORD", "NORM_ROTOR5_SECRET", "NORM_ROTOR5_PREVIOUS_SECRETS"}:
            updates[key] = str(value)

    secrets_path = _resolve_secrets_path(settings_path)
    if preserve_existing_secrets:
        log(f"Applied non-secret installer imprint to config baseline; existing secrets file left untouched: {secrets_path}.")
    else:
        _update_env_file(
            secrets_path,
            updates,
            require_password=True,
        )
        log(f"Applied non-secret installer imprint from memory to {settings_path.name}.")
        log(f"Secrets were written/preserved only in configured secrets file: {secrets_path} (values not logged).")

def _peek_norm_source(path: Path) -> tuple[str, str] | None:
    """Return (version, package_type) for a Norm package without enforcing its companion SHA yet."""
    try:
        with zipfile.ZipFile(path, "r") as zf:
            manifests = [
                name for name in zf.namelist()
                if PurePosixPath(name).name == "package-manifest.json" and not name.endswith("/")
            ]
            if len(manifests) != 1:
                return None
            manifest = json.loads(zf.read(manifests[0]).decode("utf-8-sig"))
    except Exception:
        return None
    if str(manifest.get("name") or "").strip().lower() != "norm":
        return None
    package_type = str(manifest.get("package_type") or "").strip()
    if package_type != "portable-source":
        return None
    return str(manifest.get("version") or "0"), package_type


def list_local_sources(directory: Path | None = None) -> list[Path]:
    """Return valid local Norm source ZIPs, highest package version first.

    Local operator packages under .norm-local are considered first and win
    same-version ties. The .norm-local directory is Git-ignored and is never
    part of the public release bundle.
    """
    root = Path(directory).resolve() if directory else _source_directory()
    candidates: list[tuple[tuple, int, int, str, Path]] = []

    search_roots: list[tuple[Path, int]] = []
    private_root = root / LOCAL_PRIVATE_DIR
    if private_root.is_dir():
        search_roots.append((private_root, 1))
    search_roots.append((root, 0))

    for search_root, private_priority in search_roots:
        for path in search_root.glob("*.zip"):
            if not path.is_file():
                continue
            meta = _peek_norm_source(path)
            if meta is None:
                continue
            version, _ = meta
            try:
                mtime = path.stat().st_mtime_ns
            except OSError:
                mtime = 0
            candidates.append(
                (
                    _version_key(version),
                    private_priority,
                    mtime,
                    path.name.lower(),
                    path.resolve(),
                )
            )

    candidates.sort(
        key=lambda item: (item[0], item[1], item[2], item[3]),
        reverse=True,
    )
    return [item[4] for item in candidates]


def find_latest_source(directory: Path | None = None) -> Path | None:
    sources = list_local_sources(directory)
    return sources[0] if sources else None


def _normalized_requirements_text(text: str) -> str:
    lines: list[str] = []
    for raw in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        lines.append(line)
    return "\n".join(lines) + ("\n" if lines else "")


def _source_requirements_text(source_zip: Path) -> str:
    with zipfile.ZipFile(source_zip, "r") as zf:
        manifests = [
            name for name in zf.namelist()
            if PurePosixPath(name).name == "package-manifest.json" and not name.endswith("/")
        ]
        if len(manifests) != 1:
            raise InstallerError(f"Expected exactly one package-manifest.json in {source_zip.name}")
        manifest_member = manifests[0]
        manifest = json.loads(zf.read(manifest_member).decode("utf-8-sig"))
        root = PurePosixPath(manifest_member).parent
        rel = PurePosixPath(str(manifest.get("requirements_lock") or "").replace("\\", "/"))
        if not rel.parts:
            raise InstallerError("Selected source package has no requirements_lock entry")
        member = str(root / rel)
        try:
            return zf.read(member).decode("utf-8-sig")
        except KeyError as exc:
            raise InstallerError(f"Selected source package is missing {rel}") from exc


def compare_companion_requirements(source_zip: Path) -> tuple[bool, str]:
    companion = _source_directory() / "requirements.txt"
    if not companion.is_file():
        return False, f"Missing companion requirements.txt beside the installer: {companion}"
    local_text = _normalized_requirements_text(companion.read_text(encoding="utf-8-sig"))
    source_text = _normalized_requirements_text(_source_requirements_text(source_zip))
    if local_text == source_text:
        count = len([line for line in local_text.splitlines() if line.strip()])
        return True, f"requirements.txt matches selected source ({count} pinned distributions)"
    local_lines = set(local_text.splitlines())
    source_lines = set(source_text.splitlines())
    only_local = sorted(local_lines - source_lines)
    only_source = sorted(source_lines - local_lines)
    detail: list[str] = ["requirements.txt does not match the selected source lock"]
    if only_local:
        detail.append("Only beside installer: " + ", ".join(only_local[:5]))
    if only_source:
        detail.append("Only in source ZIP: " + ", ".join(only_source[:5]))
    if len(only_local) > 5 or len(only_source) > 5:
        detail.append("Additional differences omitted")
    return False, "; ".join(detail)


def _which_file(name: str) -> list[Path]:
    found: list[Path] = []
    for item in os.environ.get("PATH", "").split(os.pathsep):
        if not item:
            continue
        p = Path(item) / name
        if p.is_file():
            found.append(p.resolve())
    return found


def detect_python_candidates() -> list[Path]:
    found: list[Path] = []

    if not getattr(sys, "frozen", False):
        current = Path(sys.executable)
        if current.is_file() and current.name.lower().startswith("python"):
            found.append(current.resolve())

    if os.name == "nt":
        try:
            cp = subprocess.run(
                ["py", "-0p"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=5,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            for line in (cp.stdout or "").splitlines():
                # Launcher output normally ends each row with an absolute python.exe path.
                match = re.search(r"([A-Za-z]:\\[^\r\n]*?python(?:w)?\.exe)\s*$", line, re.I)
                if match:
                    p = Path(match.group(1))
                    if p.is_file():
                        found.append(p.resolve())
        except Exception:
            pass
        for name in ("python.exe", "python3.exe"):
            found.extend(_which_file(name))
        for env_name in ("LOCALAPPDATA", "ProgramFiles", "ProgramFiles(x86)"):
            base = os.environ.get(env_name)
            if not base:
                continue
            b = Path(base)
            for pattern in (
                "Programs/Python/Python314/python.exe",
                "Python314/python.exe",
                "Python313/python.exe",
            ):
                p = b / pattern
                if p.is_file():
                    found.append(p.resolve())

    unique: list[Path] = []
    seen: set[str] = set()
    for p in found:
        key = os.path.normcase(str(p))
        if key not in seen:
            seen.add(key)
            unique.append(p)
    return unique


def choose_default_python(source: Path | None) -> Path | None:
    candidates = detect_python_candidates()
    if not candidates:
        return None
    expected: tuple[int, int] | None = None
    if source:
        try:
            info = inspect_package(source)
            with tempfile.TemporaryDirectory(prefix="norm-python-probe-") as td:
                root = _extract_package(info, Path(td))
                expected = _expected_python(_read_settings(root, info.manifest))
        except Exception:
            pass

    if expected:
        for candidate in candidates:
            try:
                cp = subprocess.run(
                    [str(candidate), "-c", "import sys; print(f'{sys.version_info[0]}.{sys.version_info[1]}')"],
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=4,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0,
                )
                if cp.returncode == 0 and cp.stdout.strip() == f"{expected[0]}.{expected[1]}":
                    return candidate
            except Exception:
                pass
    return candidates[0]


def launch_gui(initial_source: Path | None = None, imprint_path: Path | None = None) -> int:
    try:
        import tkinter as tk
        from tkinter import filedialog, messagebox, ttk
        from tkinter.scrolledtext import ScrolledText
    except Exception as exc:
        raise InstallerError(f"Tkinter is required for the GUI installer: {exc}") from exc

    imprint, loaded_imprint_path = load_imprint(imprint_path)
    raw_imprint: dict[str, Any] = {}
    if loaded_imprint_path:
        try:
            loaded_raw = json.loads(loaded_imprint_path.read_text(encoding="utf-8-sig"))
            if isinstance(loaded_raw, dict):
                raw_imprint = loaded_raw
        except Exception:
            raw_imprint = {}

    def nested_get(data: dict[str, Any], dotted: str, default: Any = "") -> Any:
        return envtools.nested_get(data, dotted, default)

    def provided_in_raw(dotted: str) -> bool:
        return envtools.nested_has(raw_imprint, dotted)

    root = tk.Tk()
    root.title(f"Norm Installer {INSTALLER_VERSION}")
    root.geometry("980x760")
    root.minsize(820, 640)

    target_var = tk.StringVar(value=str(nested_get(imprint, "install.target_dir", r"C:\Norm" if os.name == "nt" else str(Path.home() / "Norm"))))
    python_var = tk.StringVar(value=str(nested_get(imprint, "install.python_exe", "")))
    dependency_var = tk.StringVar(value=str(nested_get(imprint, "install.dependency_mode", "newest")))
    package_var = tk.StringVar(value="Searching for the newest Norm package…")
    package_detail_var = tk.StringVar(value="")
    status_var = tk.StringVar(value="Ready")
    progress_var = tk.DoubleVar(value=0)
    imprint_status_var = tk.StringVar(
        value=f"Auto-fill: {loaded_imprint_path.name}" if loaded_imprint_path else f"Auto-fill: no {LOCAL_IMPRINT_NAME}; using public defaults"
    )

    # Environment variables. Secret values intentionally never come from an imprint.
    machine_var = tk.StringVar(value=str(nested_get(imprint, "network.current_machine", "norm-host")))
    domain_var = tk.StringVar(value=str(nested_get(imprint, "network.current_domain", "example.invalid")))
    require_tailscale_var = tk.BooleanVar(value=bool(nested_get(imprint, "network.require_tailscale", False)))
    ollama_host_var = tk.StringVar(value=str(nested_get(imprint, "network.ollama_host", "loopback")))
    ollama_port_var = tk.StringVar(value=str(nested_get(imprint, "network.ollama_port", 11434)))
    norm_host_var = tk.StringVar(value=str(nested_get(imprint, "network.norm_host", "loopback")))
    norm_port_var = tk.StringVar(value=str(nested_get(imprint, "network.norm_port", 12543)))
    activity_host_var = tk.StringVar(value=str(nested_get(imprint, "network.activity_host", "loopback")))
    activity_port_var = tk.StringVar(value=str(nested_get(imprint, "network.activity_port", 8766)))
    postgres_host_var = tk.StringVar(value=str(nested_get(imprint, "network.postgres_host", "loopback")))
    postgres_port_var = tk.StringVar(value=str(nested_get(imprint, "network.postgres_port", 5432)))
    redis_host_var = tk.StringVar(value=str(nested_get(imprint, "network.redis_host", "loopback")))
    redis_port_var = tk.StringVar(value=str(nested_get(imprint, "network.redis_port", 6379)))

    pg_user_var = tk.StringVar(value=str(nested_get(imprint, "postgres.user", "norm")))
    pg_db_var = tk.StringVar(value=str(nested_get(imprint, "postgres.database", "norm")))
    pg_schema_var = tk.StringVar(value=str(nested_get(imprint, "postgres.schema", "norm_runtime")))
    stocks_db_var = tk.StringVar(value=str(nested_get(imprint, "postgres.stocks_database", "stocks_api")))
    pg_password_var = tk.StringVar(value="")
    rotor_secret_var = tk.StringVar(value="")
    rotor_previous_var = tk.StringVar(value="")

    documents_root_var = tk.StringVar(value=str(nested_get(imprint, "paths.documents_root", r"%USERPROFILE%\Documents\Norm")))
    workspace_root_var = tk.StringVar(value=str(nested_get(imprint, "paths.workspace_root", "workspace")))
    temp_root_var = tk.StringVar(value=str(nested_get(imprint, "paths.temp_root", "temp")))
    allowed_roots_var = tk.StringVar(
        value=";".join(str(item) for item in nested_get(imprint, "runtime.allowed_roots", [r"%USERPROFILE%\Documents\Norm"]))
    )
    storage_primary_name_var = tk.StringVar(value=str(nested_get(imprint, "runtime.storage_context.primary_name", "documents")))
    storage_primary_root_var = tk.StringVar(value=str(nested_get(imprint, "runtime.storage_context.primary_root", r"%USERPROFILE%\Documents\Norm")))
    storage_backup_name_var = tk.StringVar(value=str(nested_get(imprint, "runtime.storage_context.backup_name", "workspace")))
    never_probe_patterns_var = tk.StringVar(
        value=";".join(str(item) for item in nested_get(imprint, "runtime.network_map.never_probe_name_patterns", []))
    )
    never_probe_cidrs_var = tk.StringVar(
        value=";".join(str(item) for item in nested_get(imprint, "runtime.network_map.never_probe_cidrs", []))
    )

    ssh_enabled_var = tk.BooleanVar(value=bool(nested_get(imprint, "ssh.enabled", False)))
    ssh_user_var = tk.StringVar(value=str(nested_get(imprint, "ssh.user", "")))
    ssh_remote_var = tk.StringVar(value=str(nested_get(imprint, "ssh.remote_host", "")))
    ssh_docker_var = tk.StringVar(value=str(nested_get(imprint, "ssh.docker_host", "")))
    ssh_port_var = tk.StringVar(value=str(nested_get(imprint, "ssh.port", 22)))
    ssh_identity_var = tk.StringVar(value=str(nested_get(imprint, "ssh.identity_file", "norm_remote_ed25519")))

    selected_source: Path | None = initial_source.resolve() if initial_source else None
    selected_info: PackageInfo | None = None
    busy = False
    environment_loaded_for: str | None = None
    environment_origins: dict[str, str] = {}
    events: queue.Queue[tuple[str, object]] = queue.Queue()
    origin_labels: dict[str, Any] = {}

    shell = ttk.Frame(root, padding=(18, 14))
    shell.pack(fill="both", expand=True)
    shell.columnconfigure(0, weight=1)
    shell.rowconfigure(2, weight=1)

    ttk.Label(shell, text="Norm Installer", font=("Segoe UI", 18, "bold")).grid(row=0, column=0, sticky="w")
    step_var = tk.StringVar(value="● Package     ○ Environment     ○ Install     ○ Complete")
    ttk.Label(shell, textvariable=step_var, font=("Segoe UI", 10)).grid(row=1, column=0, sticky="w", pady=(4, 14))

    pages = ttk.Frame(shell)
    pages.grid(row=2, column=0, sticky="nsew")
    pages.rowconfigure(0, weight=1)
    pages.columnconfigure(0, weight=1)

    page_setup = ttk.Frame(pages)
    page_environment = ttk.Frame(pages)
    page_install = ttk.Frame(pages)
    page_complete = ttk.Frame(pages)
    for page in (page_setup, page_environment, page_install, page_complete):
        page.grid(row=0, column=0, sticky="nsew")

    actions = ttk.Frame(shell)
    actions.grid(row=3, column=0, sticky="ew", pady=(14, 0))
    actions.columnconfigure(0, weight=1)

    back_btn = ttk.Button(actions, text="< Back")
    cancel_btn = ttk.Button(actions, text="Cancel", command=root.destroy)
    next_btn = ttk.Button(actions, text="Next >", state="disabled")
    finish_btn = ttk.Button(actions, text="Finish", command=root.destroy)
    cancel_btn.grid(row=0, column=2, padx=(8, 0))
    next_btn.grid(row=0, column=3, padx=(8, 0))

    # Page 1: package / requirements.
    page_setup.columnconfigure(0, weight=1)
    package_box = ttk.LabelFrame(page_setup, text="Package", padding=12)
    package_box.grid(row=0, column=0, sticky="ew")
    package_box.columnconfigure(0, weight=1)
    ttk.Label(package_box, textvariable=package_var, font=("Segoe UI", 11, "bold")).grid(row=0, column=0, sticky="w")
    ttk.Label(package_box, textvariable=package_detail_var, wraplength=860, justify="left").grid(
        row=1, column=0, sticky="w", pady=(5, 0)
    )

    def browse_source() -> None:
        nonlocal selected_source
        path = filedialog.askopenfilename(
            title="Select Norm portable source ZIP",
            initialdir=str(_source_directory()),
            filetypes=[("Norm source ZIP", "*.zip"), ("All files", "*.*")],
        )
        if path:
            selected_source = Path(path).resolve()
            analyze_source()

    ttk.Button(package_box, text="Advanced: choose another package…", command=browse_source).grid(
        row=2, column=0, sticky="w", pady=(10, 0)
    )

    req_box = ttk.LabelFrame(page_setup, text="Requirements", padding=12)
    req_box.grid(row=1, column=0, sticky="ew", pady=(12, 0))
    ttk.Radiobutton(
        req_box, text="Newest available packages", variable=dependency_var, value="newest"
    ).grid(row=0, column=0, sticky="w")
    ttk.Label(
        req_box,
        text="Upgrade the distributions named by the selected package lock and let pip resolve newest normal releases.",
        wraplength=850,
    ).grid(row=1, column=0, sticky="w", padx=(24, 0), pady=(0, 8))
    ttk.Radiobutton(
        req_box, text="Use package requirements", variable=dependency_var, value="package"
    ).grid(row=2, column=0, sticky="w")
    ttk.Label(
        req_box,
        text="Install the exact tools/requirements-lock.txt shipped inside the selected Norm package.",
        wraplength=850,
    ).grid(row=3, column=0, sticky="w", padx=(24, 0))

    target_box = ttk.LabelFrame(page_setup, text="Install location", padding=12)
    target_box.grid(row=2, column=0, sticky="ew", pady=(12, 0))
    target_box.columnconfigure(0, weight=1)
    ttk.Entry(target_box, textvariable=target_var).grid(row=0, column=0, sticky="ew")

    def browse_target() -> None:
        path = filedialog.askdirectory(title="Select Norm installation directory")
        if path:
            target_var.set(path)

    ttk.Button(target_box, text="Browse…", command=browse_target).grid(row=0, column=1, padx=(8, 0))

    python_box = ttk.LabelFrame(page_setup, text="Base Python", padding=12)
    python_box.grid(row=3, column=0, sticky="ew", pady=(12, 0))
    python_box.columnconfigure(0, weight=1)
    ttk.Entry(python_box, textvariable=python_var).grid(row=0, column=0, sticky="ew")

    def browse_python() -> None:
        path = filedialog.askopenfilename(
            title="Select Python executable",
            filetypes=[("Python", "python.exe" if os.name == "nt" else "python*"), ("All files", "*.*")],
        )
        if path:
            python_var.set(path)

    ttk.Button(python_box, text="Browse…", command=browse_python).grid(row=0, column=1, padx=(8, 0))

    # Page 2: environment / imprint.
    page_environment.columnconfigure(0, weight=1)
    page_environment.rowconfigure(2, weight=1)
    imprint_banner = ttk.Frame(page_environment)
    imprint_banner.grid(row=0, column=0, sticky="ew", pady=(0, 8))
    imprint_banner.columnconfigure(0, weight=1)
    ttk.Label(imprint_banner, textvariable=imprint_status_var, font=("Segoe UI", 10, "bold")).grid(row=0, column=0, sticky="w")
    ttk.Label(
        imprint_banner,
        text="Imprints contain only non-secret topology/settings. Passwords and secrets below are masked and are never saved to the imprint.",
        wraplength=850,
    ).grid(row=1, column=0, sticky="w", pady=(2, 0))

    notebook = ttk.Notebook(page_environment)
    notebook.grid(row=2, column=0, sticky="nsew")
    network_tab = ttk.Frame(notebook, padding=12)
    db_tab = ttk.Frame(notebook, padding=12)
    runtime_tab = ttk.Frame(notebook, padding=12)
    notebook.add(network_tab, text="Network & services")
    notebook.add(db_tab, text="Database & SSH")
    notebook.add(runtime_tab, text="Paths & safety")

    def mark_entered(key: str) -> None:
        label = origin_labels.get(key)
        if label is not None:
            label.configure(text="[entered]")

    def origin_text(key: str) -> str:
        origin = environment_origins.get(key)
        if origin:
            return f"[{origin}]"
        return "[imprint]" if provided_in_raw(key) else "[default]"

    def add_entry(parent: Any, row: int, label_text: str, var: Any, key: str, *, show: str | None = None, width: int = 34) -> Any:
        ttk.Label(parent, text=label_text).grid(row=row, column=0, sticky="w", padx=(0, 8), pady=4)
        entry = ttk.Entry(parent, textvariable=var, width=width, show=show or "")
        entry.grid(row=row, column=1, sticky="ew", pady=4)
        badge = ttk.Label(parent, text=origin_text(key), width=11)
        badge.grid(row=row, column=2, sticky="w", padx=(8, 0))
        origin_labels[key] = badge
        entry.bind("<KeyRelease>", lambda _event, k=key: mark_entered(k))
        return entry

    for tab in (network_tab, db_tab, runtime_tab):
        tab.columnconfigure(1, weight=1)

    add_entry(network_tab, 0, "Machine name", machine_var, "network.current_machine")
    add_entry(network_tab, 1, "DNS / tailnet domain", domain_var, "network.current_domain")
    tailscale_cb = ttk.Checkbutton(
        network_tab,
        text="Require Tailscale for current-host bindings",
        variable=require_tailscale_var,
        command=lambda: mark_entered("network.require_tailscale"),
    )
    tailscale_cb.grid(row=2, column=1, sticky="w", pady=4)
    badge = ttk.Label(network_tab, text=origin_text("network.require_tailscale"), width=11)
    badge.grid(row=2, column=2, sticky="w", padx=(8, 0))
    origin_labels["network.require_tailscale"] = badge

    service_rows = [
        ("Ollama", ollama_host_var, "network.ollama_host", ollama_port_var, "network.ollama_port"),
        ("Norm HTTP", norm_host_var, "network.norm_host", norm_port_var, "network.norm_port"),
        ("Activity", activity_host_var, "network.activity_host", activity_port_var, "network.activity_port"),
        ("PostgreSQL", postgres_host_var, "network.postgres_host", postgres_port_var, "network.postgres_port"),
        ("Redis", redis_host_var, "network.redis_host", redis_port_var, "network.redis_port"),
    ]
    ttk.Label(network_tab, text="Service").grid(row=3, column=0, sticky="w", pady=(12, 2))
    ttk.Label(network_tab, text="Host").grid(row=3, column=1, sticky="w", pady=(12, 2))
    for offset, (label_text, host_var, host_key, port_var, port_key) in enumerate(service_rows, start=4):
        row_frame = ttk.Frame(network_tab)
        row_frame.grid(row=offset, column=1, columnspan=2, sticky="ew", pady=3)
        row_frame.columnconfigure(0, weight=1)
        ttk.Label(network_tab, text=label_text).grid(row=offset, column=0, sticky="w", pady=3)
        host_entry = ttk.Entry(row_frame, textvariable=host_var)
        host_entry.grid(row=0, column=0, sticky="ew")
        port_entry = ttk.Entry(row_frame, textvariable=port_var, width=8)
        port_entry.grid(row=0, column=1, padx=(8, 0))
        host_badge = ttk.Label(row_frame, text=origin_text(host_key), width=11)
        host_badge.grid(row=0, column=2, padx=(8, 0))
        port_badge = ttk.Label(row_frame, text=origin_text(port_key), width=11)
        port_badge.grid(row=0, column=3, padx=(4, 0))
        origin_labels[host_key] = host_badge
        origin_labels[port_key] = port_badge
        host_entry.bind("<KeyRelease>", lambda _e, k=host_key: mark_entered(k))
        port_entry.bind("<KeyRelease>", lambda _e, k=port_key: mark_entered(k))

    add_entry(db_tab, 0, "PostgreSQL user", pg_user_var, "postgres.user")
    add_entry(db_tab, 1, "Database", pg_db_var, "postgres.database")
    add_entry(db_tab, 2, "Schema", pg_schema_var, "postgres.schema")
    add_entry(db_tab, 3, "Stocks database", stocks_db_var, "postgres.stocks_database")
    ttk.Label(db_tab, text="PostgreSQL password").grid(row=4, column=0, sticky="w", padx=(0, 8), pady=4)
    ttk.Entry(db_tab, textvariable=pg_password_var, show="•").grid(row=4, column=1, sticky="ew", pady=4)
    pg_password_badge = ttk.Label(db_tab, text="[secret]", width=16)
    pg_password_badge.grid(row=4, column=2, sticky="w", padx=(8, 0))
    ttk.Label(db_tab, text="Rotor5 secret (optional)").grid(row=5, column=0, sticky="w", padx=(0, 8), pady=4)
    ttk.Entry(db_tab, textvariable=rotor_secret_var, show="•").grid(row=5, column=1, sticky="ew", pady=4)
    rotor_secret_badge = ttk.Label(db_tab, text="[secret]", width=16)
    rotor_secret_badge.grid(row=5, column=2, sticky="w", padx=(8, 0))
    ttk.Label(db_tab, text="Previous Rotor5 secrets").grid(row=6, column=0, sticky="w", padx=(0, 8), pady=4)
    ttk.Entry(db_tab, textvariable=rotor_previous_var, show="*").grid(row=6, column=1, sticky="ew", pady=4)
    rotor_previous_badge = ttk.Label(db_tab, text="[secret]", width=16)
    rotor_previous_badge.grid(row=6, column=2, sticky="w", padx=(8, 0))

    ssh_cb = ttk.Checkbutton(
        db_tab,
        text="Enable SSH convenience settings",
        variable=ssh_enabled_var,
        command=lambda: mark_entered("ssh.enabled"),
    )
    ssh_cb.grid(row=7, column=1, sticky="w", pady=(12, 4))
    badge = ttk.Label(db_tab, text=origin_text("ssh.enabled"), width=11)
    badge.grid(row=7, column=2, sticky="w", padx=(8, 0))
    origin_labels["ssh.enabled"] = badge
    add_entry(db_tab, 8, "SSH user", ssh_user_var, "ssh.user")
    add_entry(db_tab, 9, "Remote host", ssh_remote_var, "ssh.remote_host")
    add_entry(db_tab, 10, "Docker host", ssh_docker_var, "ssh.docker_host")
    add_entry(db_tab, 11, "SSH port", ssh_port_var, "ssh.port")
    add_entry(db_tab, 12, "Identity filename", ssh_identity_var, "ssh.identity_file")

    add_entry(runtime_tab, 0, "Documents root", documents_root_var, "paths.documents_root")
    add_entry(runtime_tab, 1, "Workspace root", workspace_root_var, "paths.workspace_root")
    add_entry(runtime_tab, 2, "Temp root", temp_root_var, "paths.temp_root")
    add_entry(runtime_tab, 3, "Allowed roots (; separated)", allowed_roots_var, "runtime.allowed_roots")
    add_entry(runtime_tab, 4, "Storage primary name", storage_primary_name_var, "runtime.storage_context.primary_name")
    add_entry(runtime_tab, 5, "Storage primary root", storage_primary_root_var, "runtime.storage_context.primary_root")
    add_entry(runtime_tab, 6, "Storage backup name", storage_backup_name_var, "runtime.storage_context.backup_name")
    add_entry(runtime_tab, 7, "Never-probe names (; separated)", never_probe_patterns_var, "runtime.network_map.never_probe_name_patterns")
    add_entry(runtime_tab, 8, "Never-probe CIDRs (; separated)", never_probe_cidrs_var, "runtime.network_map.never_probe_cidrs")
    ttk.Label(
        runtime_tab,
        text="Network-map active probes are never inferred from discovered peers. Only explicit targets already present in the imprint are probed.",
        wraplength=760,
    ).grid(row=9, column=0, columnspan=3, sticky="w", pady=(10, 0))

    def split_list(value: str) -> list[str]:
        return [item.strip() for item in value.split(";") if item.strip()]

    def apply_environment_prefill() -> None:
        nonlocal imprint, environment_loaded_for, environment_origins
        target = Path(target_var.get().strip()).expanduser()
        target_key = str(target.resolve())
        if environment_loaded_for == target_key:
            return
        package_raw = read_package_public_imprint(selected_info)
        package_defaults = validate_imprint(package_raw) if package_raw else _deep_copy(DEFAULT_IMPRINT)
        effective = validate_imprint(_deep_merge(package_raw, raw_imprint))
        resolved, secrets, origins = envtools.resolve_environment_prefill(
            target, effective, raw_imprint, package_defaults
        )
        imprint = resolved
        environment_origins = origins
        scalar_vars = {
            "paths.documents_root": documents_root_var, "paths.workspace_root": workspace_root_var,
            "paths.temp_root": temp_root_var, "network.current_machine": machine_var,
            "network.current_domain": domain_var, "network.ollama_host": ollama_host_var,
            "network.ollama_port": ollama_port_var, "network.norm_host": norm_host_var,
            "network.norm_port": norm_port_var, "network.activity_host": activity_host_var,
            "network.activity_port": activity_port_var, "network.postgres_host": postgres_host_var,
            "network.postgres_port": postgres_port_var, "network.redis_host": redis_host_var,
            "network.redis_port": redis_port_var, "postgres.user": pg_user_var,
            "postgres.database": pg_db_var, "postgres.schema": pg_schema_var,
            "postgres.stocks_database": stocks_db_var, "ssh.user": ssh_user_var,
            "ssh.remote_host": ssh_remote_var, "ssh.docker_host": ssh_docker_var,
            "ssh.port": ssh_port_var, "ssh.identity_file": ssh_identity_var,
            "runtime.storage_context.primary_name": storage_primary_name_var,
            "runtime.storage_context.primary_root": storage_primary_root_var,
            "runtime.storage_context.backup_name": storage_backup_name_var,
        }
        for dotted, var in scalar_vars.items():
            var.set(str(envtools.nested_get(resolved, dotted, "")))
        require_tailscale_var.set(bool(envtools.nested_get(resolved, "network.require_tailscale", False)))
        ssh_enabled_var.set(bool(envtools.nested_get(resolved, "ssh.enabled", False)))
        allowed_roots_var.set(";".join(str(x) for x in (envtools.nested_get(resolved, "runtime.allowed_roots", []) or [])))
        never_probe_patterns_var.set(";".join(str(x) for x in (envtools.nested_get(resolved, "runtime.network_map.never_probe_name_patterns", []) or [])))
        never_probe_cidrs_var.set(";".join(str(x) for x in (envtools.nested_get(resolved, "runtime.network_map.never_probe_cidrs", []) or [])))
        pg_password_var.set(secrets.get("NORM_POSTGRES_PASSWORD", ""))
        rotor_secret_var.set(secrets.get("NORM_ROTOR5_SECRET", ""))
        rotor_previous_var.set(secrets.get("NORM_ROTOR5_PREVIOUS_SECRETS", ""))
        pg_password_badge.configure(text="[current secret]" if pg_password_var.get() else "[blank]")
        rotor_secret_badge.configure(text="[current secret]" if rotor_secret_var.get() else "[blank]")
        rotor_previous_badge.configure(text="[current secret]" if rotor_previous_var.get() else "[blank]")
        for key, label in origin_labels.items():
            label.configure(text=f"[{environment_origins.get(key, 'imprint' if provided_in_raw(key) else 'default')}]")
        environment_loaded_for = target_key

    def collect_imprint() -> dict[str, Any]:
        # Preserve complex explicit target definitions from the loaded imprint; the GUI
        # edits only safe scalar/list environment fields.
        existing_map = nested_get(imprint, "runtime.network_map", {})
        existing_targets = existing_map.get("targets", []) if isinstance(existing_map, dict) else []
        data: dict[str, Any] = {
            "schema": IMPRINT_SCHEMA_VERSION,
            "install": {
                "target_dir": target_var.get().strip(),
                "dependency_mode": dependency_var.get().strip().lower(),
                "python_exe": python_var.get().strip(),
            },
            "paths": {
                "documents_root": documents_root_var.get().strip(),
                "workspace_root": workspace_root_var.get().strip(),
                "temp_root": temp_root_var.get().strip(),
            },
            "network": {
                "current_machine": machine_var.get().strip(),
                "current_domain": domain_var.get().strip(),
                "require_tailscale": bool(require_tailscale_var.get()),
                "ollama_host": ollama_host_var.get().strip(),
                "ollama_port": _coerce_port(ollama_port_var.get(), "Ollama port"),
                "norm_host": norm_host_var.get().strip(),
                "norm_port": _coerce_port(norm_port_var.get(), "Norm HTTP port"),
                "activity_host": activity_host_var.get().strip(),
                "activity_port": _coerce_port(activity_port_var.get(), "Activity port"),
                "postgres_host": postgres_host_var.get().strip(),
                "postgres_port": _coerce_port(postgres_port_var.get(), "PostgreSQL port"),
                "redis_host": redis_host_var.get().strip(),
                "redis_port": _coerce_port(redis_port_var.get(), "Redis port"),
            },
            "postgres": {
                "user": pg_user_var.get().strip(),
                "database": pg_db_var.get().strip(),
                "schema": pg_schema_var.get().strip(),
                "stocks_database": stocks_db_var.get().strip(),
            },
            "ssh": {
                "enabled": bool(ssh_enabled_var.get()),
                "user": ssh_user_var.get().strip(),
                "remote_host": ssh_remote_var.get().strip(),
                "docker_host": ssh_docker_var.get().strip(),
                "port": _coerce_port(ssh_port_var.get(), "SSH port"),
                "identity_file": ssh_identity_var.get().strip(),
            },
            "runtime": {
                "allowed_roots": split_list(allowed_roots_var.get()) or [documents_root_var.get().strip()],
                "storage_context": {
                    "primary_name": storage_primary_name_var.get().strip() or "documents",
                    "primary_root": storage_primary_root_var.get().strip() or documents_root_var.get().strip(),
                    "backup_name": storage_backup_name_var.get().strip() or "workspace",
                },
                "network_map": {
                    "never_probe_name_patterns": split_list(never_probe_patterns_var.get()),
                    "never_probe_cidrs": split_list(never_probe_cidrs_var.get()),
                    "targets": list(existing_targets) if isinstance(existing_targets, list) else [],
                },
            },
        }
        if not data["network"]["current_machine"] or not data["network"]["current_domain"]:
            raise InstallerError("Machine name and DNS/tailnet domain are required.")
        for key in ("user", "database", "schema", "stocks_database"):
            if not data["postgres"][key]:
                raise InstallerError(f"PostgreSQL {key.replace('_', ' ')} is required.")
        return validate_imprint(data)

    def save_imprint_clicked() -> None:
        try:
            saved = save_local_imprint(collect_imprint())
            imprint_status_var.set(f"Auto-fill saved: {saved.name} (non-secret only)")
            messagebox.showinfo(
                "Norm Installer",
                f"Saved non-secret auto-fill settings to:\n{saved}\n\n"
                "Passwords and secret fields were not written.",
                parent=root,
            )
        except Exception as exc:
            messagebox.showerror("Norm Installer", str(exc), parent=root)

    def test_connections_clicked() -> None:
        try:
            data = collect_imprint()
            secrets = {
                "NORM_POSTGRES_PASSWORD": pg_password_var.get(),
                "NORM_ROTOR5_SECRET": rotor_secret_var.get(),
                "NORM_ROTOR5_PREVIOUS_SECRETS": rotor_previous_var.get(),
            }
            target = Path(target_var.get().strip()).expanduser()
            candidates: list[Path] = []
            settings_path = target / "config" / "settings.ini"
            if settings_path.is_file():
                settings = configparser.ConfigParser(interpolation=None)
                settings.read(settings_path, encoding="utf-8-sig")
                venv_rel = settings.get("environment", "venv_path", fallback=".venv").strip() or ".venv"
                candidates.append(target / venv_rel / ("Scripts/python.exe" if os.name == "nt" else "bin/python"))
            if python_var.get().strip():
                candidates.append(Path(python_var.get().strip()).expanduser())
            results = envtools.test_environment_connections(data, secrets, candidates)
            symbols = {"ok": "?", "partial": "?", "error": "?"}
            message = "\n".join(f"{symbols.get(status, '?')} {label}: {detail}" for label, status, detail in results)
            if any(status == "error" for _, status, _ in results):
                messagebox.showwarning("Connection test", message, parent=root)
            else:
                messagebox.showinfo("Connection test", message, parent=root)
        except Exception as exc:
            messagebox.showerror("Connection test", str(exc), parent=root)

    save_bar = ttk.Frame(page_environment)
    save_bar.grid(row=3, column=0, sticky="ew", pady=(10, 0))
    ttk.Button(save_bar, text=f"Save non-secret {LOCAL_IMPRINT_NAME}", command=save_imprint_clicked).pack(side="left")
    ttk.Button(save_bar, text="Test connections", command=test_connections_clicked).pack(side="left", padx=(8, 0))

    # Page 3: install.
    page_install.columnconfigure(1, weight=1)
    page_install.rowconfigure(0, weight=1)
    timeline_box = ttk.LabelFrame(page_install, text="Timeline", padding=10)
    timeline_box.grid(row=0, column=0, sticky="nsw", padx=(0, 10))
    timeline_steps = [
        ("validate", "Validate package", 0, 14),
        ("sync", "Synchronize / configure", 15, 24),
        ("venv", "Python environment", 25, 37),
        ("deps", "Requirements", 38, 67),
        ("check", "Validate environment", 68, 75),
        ("build", "Build norm.exe", 76, 91),
        ("final", "Finalize", 92, 100),
    ]
    timeline_labels: dict[str, Any] = {}
    timeline_state = {key: "pending" for key, _, _, _ in timeline_steps}
    for row, (key, label, _, _) in enumerate(timeline_steps):
        widget = tk.Label(timeline_box, text=f"○  {label}", anchor="w", font=("Segoe UI", 9))
        widget.grid(row=row, column=0, sticky="w", pady=5)
        timeline_labels[key] = widget

    output_box = ttk.LabelFrame(page_install, text="Installer output", padding=6)
    output_box.grid(row=0, column=1, sticky="nsew")
    output_box.rowconfigure(0, weight=1)
    output_box.columnconfigure(0, weight=1)
    log_box = ScrolledText(output_box, wrap="word", font=("Consolas", 9))
    log_box.grid(row=0, column=0, sticky="nsew")
    log_box.configure(state="disabled")
    ttk.Progressbar(page_install, maximum=100, variable=progress_var).grid(
        row=1, column=0, columnspan=2, sticky="ew", pady=(10, 3)
    )
    tk.Label(page_install, textvariable=status_var, anchor="w").grid(row=2, column=0, columnspan=2, sticky="ew")

    # Page 4: complete.
    page_complete.columnconfigure(0, weight=1)
    ttk.Label(page_complete, text="✓  Norm was successfully installed / updated", font=("Segoe UI", 16, "bold")).grid(
        row=0, column=0, sticky="w", pady=(8, 16)
    )
    completion_var = tk.StringVar(value="")
    ttk.Label(page_complete, textvariable=completion_var, justify="left", wraplength=860).grid(row=1, column=0, sticky="nw")

    def append_log(text: str) -> None:
        log_box.configure(state="normal")
        log_box.insert("end", text + "\n")
        log_box.see("end")
        log_box.configure(state="disabled")

    def render_timeline() -> None:
        symbols = {"pending": "○", "active": "▶", "done": "✓", "error": "×"}
        colors = {"pending": "#666666", "active": "#1557a0", "done": "#087f23", "error": "#a00000"}
        for key, label, _, _ in timeline_steps:
            timeline_labels[key].configure(text=f"{symbols[timeline_state[key]]}  {label}", foreground=colors[timeline_state[key]])

    def update_timeline(value: int) -> None:
        for key, _, lo, hi in timeline_steps:
            if value > hi:
                timeline_state[key] = "done"
            elif lo <= value <= hi:
                timeline_state[key] = "active"
            elif timeline_state[key] != "done":
                timeline_state[key] = "pending"
        if value >= 100:
            for key in timeline_state:
                timeline_state[key] = "done"
        render_timeline()

    def analyze_source() -> None:
        nonlocal selected_source, selected_info
        if selected_source is None:
            selected_source = find_latest_source()
        if selected_source is None:
            selected_info = None
            package_var.set("No Norm portable-source package found")
            package_detail_var.set("Place Norm-*-portable-source.zip beside this installer.")
            next_btn.configure(state="disabled")
            return
        try:
            _verify_companion_sha256(selected_source)
            selected_info = inspect_package(selected_source)
            package_var.set(f"{selected_info.label}  •  {selected_source.name}")
            package_detail_var.set(
                "✓ Local private package selected from .norm-local  •  ✓ SHA-256 verified"
                if LOCAL_PRIVATE_DIR in selected_source.parts
                else "✓ Highest valid public package version found beside installer  •  ✓ SHA-256 verified"
            )
            if not python_var.get().strip():
                default_py = choose_default_python(selected_source)
                if default_py:
                    python_var.set(str(default_py))
            next_btn.configure(state="normal")
        except Exception as exc:
            selected_info = None
            package_var.set(selected_source.name)
            package_detail_var.set(f"Package validation failed: {exc}")
            next_btn.configure(state="disabled")

    def validate_setup() -> None:
        if selected_source is None or selected_info is None:
            raise InstallerError("No valid Norm package is selected.")
        if not target_var.get().strip():
            raise InstallerError("Choose an install location.")
        python_text = python_var.get().strip()
        if not python_text or not Path(python_text).expanduser().is_file():
            raise InstallerError("Choose a valid Base Python executable.")

    def build_options() -> InstallOptions:
        validate_setup()
        assert selected_source is not None and selected_info is not None
        current_imprint = collect_imprint()
        target = Path(target_var.get().strip()).expanduser()
        replace = False
        recreate = False
        if target.exists() and any(target.iterdir()):
            looks_norm = (target / "package-manifest.json").is_file() or (target / "config" / "settings.ini").is_file()
            if not looks_norm:
                raise InstallerError(f"Target is non-empty but does not look like a Norm installation: {target}")
            replace = True
            try:
                manifest_path = target / "package-manifest.json"
                if manifest_path.is_file():
                    installed_manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
                    settings = _read_settings(target, installed_manifest)
                    venv_rel = settings.get("environment", "venv_path", fallback=".venv").strip() or ".venv"
                    existing_py = target / venv_rel / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
                    expected = _expected_python(settings)
                    if existing_py.is_file() and expected:
                        existing_ver = _python_version(existing_py, _noop_log)
                        recreate = existing_ver[:2] != expected
            except Exception as exc:
                append_log(f"Existing environment probe was inconclusive; repair path will continue: {exc}")

        if not pg_password_var.get().strip():
            raise InstallerError("PostgreSQL password is required.")
        secrets: dict[str, str] = {
            "NORM_POSTGRES_PASSWORD": pg_password_var.get(),
            "NORM_ROTOR5_SECRET": rotor_secret_var.get(),
            "NORM_ROTOR5_PREVIOUS_SECRETS": rotor_previous_var.get(),
        }

        return InstallOptions(
            source_zip=selected_source,
            target_dir=target,
            python_exe=Path(python_var.get().strip()).expanduser(),
            compile_exe=True,
            install_torch=True,
            replace_existing=replace,
            recreate_venv=recreate,
            dependency_mode=dependency_var.get(),
            imprint=current_imprint,
            secret_values=secrets,
            package_imprint_baseline=read_package_public_imprint(selected_info),
        )

    def worker(options: InstallOptions) -> None:
        try:
            result = install_norm(
                options,
                log=lambda text: events.put(("log", text)),
                progress=lambda value, text: events.put(("progress", (value, text))),
            )
            events.put(("done", result))
        except Exception as exc:
            events.put(("error", exc))

    def show_setup() -> None:
        step_var.set("● Package     ○ Environment     ○ Install     ○ Complete")
        page_setup.tkraise()
        back_btn.grid_remove()
        cancel_btn.configure(text="Cancel", state="normal")
        if selected_info is not None:
            next_btn.configure(state="normal", text="Next >", command=show_environment)

    def show_environment() -> None:
        try:
            validate_setup()
            apply_environment_prefill()
        except Exception as exc:
            messagebox.showerror("Norm Installer", str(exc), parent=root)
            return
        step_var.set("✓ Package     ● Environment     ○ Install     ○ Complete")
        page_environment.tkraise()
        back_btn.configure(command=show_setup)
        back_btn.grid(row=0, column=1, padx=(8, 0))
        next_btn.configure(text="Install >", command=start_install, state="normal")

    def start_install() -> None:
        nonlocal busy
        if busy:
            return
        try:
            options = build_options()
        except Exception as exc:
            messagebox.showerror("Norm Installer", str(exc), parent=root)
            return
        busy = True
        step_var.set("✓ Package     ✓ Environment     ● Install     ○ Complete")
        page_install.tkraise()
        back_btn.grid_remove()
        next_btn.grid_remove()
        cancel_btn.configure(text="Close", state="disabled")
        log_box.configure(state="normal")
        log_box.delete("1.0", "end")
        log_box.configure(state="disabled")
        append_log(f"Package: {selected_info.label if selected_info else selected_source.name}")
        append_log(f"Source: {selected_source}")
        append_log(f"Requirements mode: {dependency_var.get()}")
        append_log(f"Target: {options.target_dir}")
        append_log(f"Imprint: {loaded_imprint_path.name if loaded_imprint_path else 'public defaults / current UI values'}")
        append_log("Secret values are masked and are never written to the imprint or installer log.")
        status_var.set("Installing…")
        progress_var.set(0)
        update_timeline(0)
        threading.Thread(target=worker, args=(options,), daemon=True).start()

    def poll_events() -> None:
        nonlocal busy
        try:
            while True:
                kind, payload = events.get_nowait()
                if kind == "log":
                    append_log(str(payload))
                elif kind == "progress":
                    value, text = payload
                    progress_var.set(value)
                    status_var.set(str(text))
                    update_timeline(int(value))
                elif kind == "done":
                    result = payload
                    busy = False
                    progress_var.set(100)
                    update_timeline(100)
                    step_var.set("✓ Package     ✓ Environment     ✓ Install     ● Complete")
                    completion_var.set(
                        f"Package: {result.package.label}\n"
                        f"Installed to: {result.target_dir}\n"
                        f"Requirements: {'Newest available' if dependency_var.get() == 'newest' else 'Package lock'}\n"
                        f"Python environment: {result.venv_python}\n"
                        f"norm.exe: {result.compiled_exe or 'not built'}\n\n"
                        "Environment imprint applied. Secret values were stored only in Norm's configured local secrets file."
                    )
                    page_complete.tkraise()
                    cancel_btn.grid_remove()
                    finish_btn.grid(row=0, column=3, padx=(8, 0))
                elif kind == "error":
                    busy = False
                    active = next((key for key, state in timeline_state.items() if state == "active"), "validate")
                    timeline_state[active] = "error"
                    render_timeline()
                    status_var.set(f"Installation failed: {payload}")
                    append_log(f"ERROR: {payload}")
                    cancel_btn.configure(text="Close", state="normal")
                    next_btn.configure(text="Retry", command=start_install, state="normal")
                    next_btn.grid(row=0, column=3, padx=(8, 0))
        except queue.Empty:
            pass
        root.after(100, poll_events)

    next_btn.configure(command=show_environment)
    analyze_source()
    show_setup()
    root.after(100, poll_events)
    root.mainloop()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Reusable local Norm installer; source package is selected at runtime")
    parser.add_argument("--source", help=argparse.SUPPRESS)
    parser.add_argument("--target", help="Installation directory")
    parser.add_argument("--python", dest="python_exe", help="Python executable used to create the Norm venv")
    parser.add_argument("--imprint", help=f"Non-secret imprint JSON; defaults to {LOCAL_IMPRINT_NAME} beside installer")
    parser.add_argument("--postgres-password-file", help="Headless-only file containing the PostgreSQL password")
    parser.add_argument("--rotor-secret-file", help="Headless-only file containing the optional Rotor5 secret")
    parser.add_argument("--validate-only", action="store_true", help="Validate the source package and exit")
    parser.add_argument("--install", action="store_true", help="Run headless installation instead of the GUI")
    parser.add_argument("--replace", action="store_true", help="Allow in-place synchronization of a non-empty target")
    parser.add_argument("--recreate-venv", action="store_true", help="Discard and recreate the existing virtual environment")
    parser.add_argument("--no-build", action="store_true", help="Do not compile norm.exe")
    parser.add_argument("--no-torch", action="store_true", help="Do not install configured PyTorch/CUDA package")
    parser.add_argument("--print-pip-spec", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--print-pip-version", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--self-test-file", help=argparse.SUPPRESS)
    args = parser.parse_args()

    if args.self_test_file:
        target = Path(args.self_test_file).expanduser()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(f"ok {INSTALLER_VERSION} auto-source\n", encoding="utf-8")
        return 0

    if args.print_pip_spec:
        print(PIP_SPEC)
        return 0
    if args.print_pip_version:
        # Backward-compatible internal helper for older build BATs.
        print(PIP_VERSION)
        return 0

    source = Path(args.source).expanduser() if args.source else find_latest_source()
    imprint_path = Path(args.imprint).expanduser() if args.imprint else None
    private_imprint, _loaded_imprint_path = load_imprint(imprint_path)
    raw_private_imprint: dict[str, Any] = {}
    if _loaded_imprint_path and _loaded_imprint_path.is_file():
        raw_value = json.loads(_loaded_imprint_path.read_text(encoding="utf-8-sig"))
        if isinstance(raw_value, dict):
            raw_private_imprint = raw_value
    source_info = inspect_package(source) if source else None
    raw_package_imprint = read_package_public_imprint(source_info)
    imprint = validate_imprint(_deep_merge(raw_package_imprint, raw_private_imprint))

    if args.validate_only:
        if not source:
            raise InstallerError(f"No Norm portable-source ZIP was found beside the installer in {_source_directory()}")
        info = inspect_package(source)
        print(json.dumps({
            "installer_version": INSTALLER_VERSION,
            "source_selection": "explicit --source" if args.source else "most recently modified local package",
            "source": str(info.source_zip),
            "name": info.name,
            "version": info.version,
            "package_schema": info.manifest.get("package_schema"),
            "entrypoint": info.manifest.get("entrypoint"),
            "requirements_lock": info.manifest.get("requirements_lock"),
            "build_script": info.manifest.get("build_script"),
        }, indent=2))
        return 0

    if args.install:
        install_cfg = imprint.get("install", {})
        target_arg = args.target or str(install_cfg.get("target_dir") or "")
        python_arg = args.python_exe or str(install_cfg.get("python_exe") or "")
        if not source or not target_arg or not python_arg:
            parser.error(
                "--install requires a local Norm source package plus target/python values "
                "(from CLI or the non-secret imprint)"
            )
        # Headless updates use the exact same migration precedence as the GUI:
        # existing value != old public package imprint => customized/preserve;
        # otherwise private local overlay, then new public package imprint.
        package_defaults = validate_imprint(raw_package_imprint) if raw_package_imprint else _deep_copy(DEFAULT_IMPRINT)
        imprint, _existing_secrets, _origins = envtools.resolve_environment_prefill(
            Path(target_arg).expanduser(), imprint, raw_private_imprint, package_defaults
        )
        secret_values: dict[str, str] = {}
        for file_arg, key in (
            (args.postgres_password_file, "NORM_POSTGRES_PASSWORD"),
            (args.rotor_secret_file, "NORM_ROTOR5_SECRET"),
        ):
            if file_arg:
                secret_path = Path(file_arg).expanduser()
                if not secret_path.is_file():
                    parser.error(f"Secret file does not exist: {secret_path}")
                secret_values[key] = secret_path.read_text(encoding="utf-8-sig").strip()
        result = install_norm(
            InstallOptions(
                source_zip=source,
                target_dir=Path(target_arg),
                python_exe=Path(python_arg),
                compile_exe=not args.no_build,
                install_torch=not args.no_torch,
                replace_existing=args.replace,
                recreate_venv=args.recreate_venv,
                dependency_mode=str(install_cfg.get("dependency_mode") or "newest"),
                imprint=imprint,
                secret_values=secret_values,
                package_imprint_baseline=_deep_copy(raw_package_imprint),
            ),
            log=print,
            progress=lambda value, text: print(f"[{value:3d}%] {text}"),
        )
        print(f"Installed {result.package.label} to {result.target_dir}")
        for warning in result.warnings:
            print(f"WARNING: {warning}")
        return 0

    return launch_gui(source, imprint_path=imprint_path)


def _show_fatal_error(message: str) -> None:
    # The local installer keeps normal validation/errors inside its main window.
    # This hook remains only for CLI compatibility; it intentionally creates no popup.
    return None


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except InstallerError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        if getattr(sys, "frozen", False):
            _show_fatal_error(str(exc))
        raise SystemExit(2)
