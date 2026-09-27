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
from typing import Callable, Iterable

INSTALLER_VERSION = "1.3.6"
PIP_MIN_VERSION = "26.1"
PIP_SPEC = f"pip>={PIP_MIN_VERSION}"
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

        progress(20, "Synchronizing Norm files")
        ignore_source = {"backup-state", "SENSITIVE_BACKUP.txt"} if staged_info.manifest.get("package_type") == "full-backup" else set()
        stats = _sync_tree_contents(extracted_root, target, protected=protected, log=log, ignore_source_roots=ignore_source)
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
    progress(32, f"Updating pip ({PIP_SPEC})")
    if installed_pip:
        log(f"Current pip version: {installed_pip}; allowing any newer release satisfying {PIP_SPEC}.")
    _run(
        [str(venv_python), "-m", "pip", "install", "--upgrade", PIP_SPEC],
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

    progress(52, "Installing pinned dependencies")
    requirements = target / str(manifest["requirements_lock"])
    _run(
        [str(venv_python), "-m", "pip", "install", "--disable-pip-version-check", "-r", str(requirements)],
        cwd=target,
        log=log,
    )

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


def find_default_source() -> Path | None:
    valid: list[tuple[tuple, float, Path]] = []
    for root in _candidate_roots():
        if not root.is_dir():
            continue
        for path in root.glob("*.zip"):
            try:
                info = inspect_package(path)
            except Exception:
                continue
            valid.append((_version_key(info.version), path.stat().st_mtime, path.resolve()))
    if not valid:
        return None
    valid.sort(reverse=True)
    return valid[0][2]


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


def launch_gui(initial_source: Path | None = None) -> int:
    try:
        import tkinter as tk
        from tkinter import filedialog, messagebox, ttk
        from tkinter.scrolledtext import ScrolledText
    except Exception as exc:
        raise InstallerError(f"Tkinter is required for the GUI installer: {exc}") from exc

    root = tk.Tk()
    root.title(f"Norm Installer {INSTALLER_VERSION}")
    root.geometry("800x570")
    root.minsize(720, 520)

    source_default = initial_source or find_default_source()
    python_default = choose_default_python(source_default)

    source_var = tk.StringVar(value=str(source_default or ""))
    target_var = tk.StringVar(value=r"C:\Norm" if os.name == "nt" else str(Path.home() / "Norm"))
    python_var = tk.StringVar(value=str(python_default or ""))
    package_var = tk.StringVar(value="Select a Norm source ZIP")
    compile_var = tk.BooleanVar(value=True)
    torch_var = tk.BooleanVar(value=True)
    recreate_venv_var = tk.BooleanVar(value=False)
    progress_var = tk.DoubleVar(value=0)
    status_var = tk.StringVar(value="Ready")

    outer = ttk.Frame(root, padding=(14, 12))
    outer.pack(fill="both", expand=True)
    outer.columnconfigure(1, weight=1)
    outer.rowconfigure(9, weight=1)

    ttk.Label(outer, text="Norm Installer", font=("Segoe UI", 16, "bold")).grid(
        row=0, column=0, columnspan=3, sticky="w", pady=(0, 4)
    )
    ttk.Label(outer, textvariable=package_var).grid(
        row=1, column=0, columnspan=3, sticky="w", pady=(0, 14)
    )

    ttk.Label(outer, text="Source package").grid(row=2, column=0, sticky="w", padx=(0, 8), pady=4)
    source_entry = ttk.Entry(outer, textvariable=source_var)
    source_entry.grid(row=2, column=1, sticky="ew", pady=4)

    def browse_source() -> None:
        path = filedialog.askopenfilename(
            title="Select Norm source or full-backup package",
            filetypes=[("ZIP packages", "*.zip"), ("All files", "*.*")],
        )
        if path:
            source_var.set(path)
            refresh_package_label()
            if not python_var.get().strip():
                candidate = choose_default_python(Path(path))
                if candidate:
                    python_var.set(str(candidate))

    ttk.Button(outer, text="Browse…", command=browse_source).grid(row=2, column=2, padx=(8, 0), pady=4)

    ttk.Label(outer, text="Install to").grid(row=3, column=0, sticky="w", padx=(0, 8), pady=4)
    ttk.Entry(outer, textvariable=target_var).grid(row=3, column=1, sticky="ew", pady=4)

    def browse_target() -> None:
        path = filedialog.askdirectory(title="Select Norm installation directory")
        if path:
            target_var.set(path)

    ttk.Button(outer, text="Browse…", command=browse_target).grid(row=3, column=2, padx=(8, 0), pady=4)

    ttk.Label(outer, text="Python").grid(row=4, column=0, sticky="w", padx=(0, 8), pady=4)
    ttk.Entry(outer, textvariable=python_var).grid(row=4, column=1, sticky="ew", pady=4)

    def browse_python() -> None:
        path = filedialog.askopenfilename(
            title="Select Python executable",
            filetypes=[("Python", "python.exe" if os.name == "nt" else "python*"), ("All files", "*.*")],
        )
        if path:
            python_var.set(path)

    ttk.Button(outer, text="Browse…", command=browse_python).grid(row=4, column=2, padx=(8, 0), pady=4)

    options_frame = ttk.LabelFrame(outer, text="Install behavior", padding=(10, 6))
    options_frame.grid(row=5, column=0, columnspan=3, sticky="ew", pady=(8, 6))
    ttk.Checkbutton(options_frame, text="Compile norm.exe", variable=compile_var).grid(row=0, column=0, sticky="w", padx=(0, 18))
    ttk.Checkbutton(options_frame, text="Ensure configured PyTorch/CUDA", variable=torch_var).grid(row=0, column=1, sticky="w", padx=(0, 18))
    ttk.Checkbutton(options_frame, text="Recreate .venv", variable=recreate_venv_var).grid(row=0, column=2, sticky="w")
    ttk.Label(options_frame, text="Existing compatible .venv, .ssh, user plugins, logs, and state are preserved during normal updates.").grid(
        row=1, column=0, columnspan=3, sticky="w", pady=(5, 0)
    )

    progress = ttk.Progressbar(outer, maximum=100, variable=progress_var)
    progress.grid(row=7, column=0, columnspan=3, sticky="ew", pady=(6, 2))
    ttk.Label(outer, textvariable=status_var).grid(row=8, column=0, columnspan=3, sticky="w", pady=(0, 5))

    log_box = ScrolledText(outer, height=14, wrap="word", font=("Consolas", 9))
    log_box.grid(row=9, column=0, columnspan=3, sticky="nsew")
    log_box.configure(state="disabled")

    actions = ttk.Frame(outer)
    actions.grid(row=10, column=0, columnspan=3, sticky="e", pady=(10, 0))
    validate_btn = ttk.Button(actions, text="Validate Package")
    validate_btn.pack(side="left", padx=(0, 8))
    install_btn = ttk.Button(actions, text="Install Norm")
    install_btn.pack(side="left")

    events: queue.Queue[tuple[str, object]] = queue.Queue()
    busy = False

    def append_log(text: str) -> None:
        log_box.configure(state="normal")
        log_box.insert("end", text + "\n")
        log_box.see("end")
        log_box.configure(state="disabled")

    def refresh_package_label() -> bool:
        raw = source_var.get().strip()
        if not raw:
            package_var.set("Select a Norm source ZIP")
            return False
        try:
            info = inspect_package(Path(raw))
            package_var.set(f"{info.label}  •  {info.manifest.get('package_type')}  •  package schema {info.manifest.get('package_schema')}")
            return True
        except Exception as exc:
            package_var.set(f"Invalid source package: {exc}")
            return False

    def validate_clicked() -> None:
        try:
            info = inspect_package(Path(source_var.get().strip()))
            package_var.set(f"{info.label}  •  {info.manifest.get('package_type')}  •  package schema {info.manifest.get('package_schema')}")
            messagebox.showinfo(
                "Valid Norm package",
                f"{info.label}\n\nEntrypoint: {info.manifest['entrypoint']}\n"
                f"Requirements: {info.manifest['requirements_lock']}\nBuild: {info.manifest['build_script']}",
            )
        except Exception as exc:
            messagebox.showerror("Invalid package", str(exc))

    validate_btn.configure(command=validate_clicked)

    def set_busy(value: bool) -> None:
        nonlocal busy
        busy = value
        state = "disabled" if value else "normal"
        install_btn.configure(state=state)
        validate_btn.configure(state=state)

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

    def install_clicked() -> None:
        if busy:
            return
        source_text = source_var.get().strip()
        target_text = target_var.get().strip()
        python_text = python_var.get().strip()
        if not source_text or not target_text or not python_text:
            messagebox.showerror("Missing information", "Select a source package, installation target, and Python executable.")
            return
        try:
            info = inspect_package(Path(source_text))
        except Exception as exc:
            messagebox.showerror("Invalid source package", str(exc))
            return

        target = Path(target_text).expanduser()
        replace = False
        if target.exists():
            try:
                nonempty = any(target.iterdir())
            except Exception as exc:
                messagebox.showerror("Target error", str(exc))
                return
            if nonempty:
                replace = messagebox.askyesno(
                    "Update existing Norm installation?",
                    f"{target} is not empty.\n\n"
                    f"The installer will synchronize package-owned files to {info.label}: changed files are replaced and files removed from the new base are deleted.\n\n"
                    "For a normal source update, the existing .venv, .ssh, plugins, logs/state, local secrets, and compiled norm.exe are preserved. If Compile norm.exe is checked, the executable is rebuilt after the sync.\n\n"
                    + ("THIS IS A FULL BACKUP: its saved .ssh, plugins, secrets, workspace, runtime state, and PostgreSQL snapshot will be restored.\n\n" if info.manifest.get("package_type") == "full-backup" else "")
                    + "Continue?",
                    icon="question",
                )
                if not replace:
                    return

        log_box.configure(state="normal")
        log_box.delete("1.0", "end")
        log_box.configure(state="disabled")
        progress_var.set(0)
        status_var.set("Starting installation…")
        set_busy(True)
        opts = InstallOptions(
            source_zip=Path(source_text),
            target_dir=target,
            python_exe=Path(python_text),
            compile_exe=compile_var.get(),
            install_torch=torch_var.get(),
            replace_existing=replace,
            recreate_venv=recreate_venv_var.get(),
        )
        threading.Thread(target=worker, args=(opts,), daemon=True).start()

    install_btn.configure(command=install_clicked)

    def pump_events() -> None:
        try:
            while True:
                kind, payload = events.get_nowait()
                if kind == "log":
                    append_log(str(payload))
                elif kind == "progress":
                    value, text = payload  # type: ignore[misc]
                    progress_var.set(float(value))
                    status_var.set(str(text))
                elif kind == "error":
                    set_busy(False)
                    status_var.set("Installation failed")
                    append_log(f"ERROR: {payload}")
                    messagebox.showerror("Norm installation failed", str(payload))
                elif kind == "done":
                    result: InstallResult = payload  # type: ignore[assignment]
                    set_busy(False)
                    progress_var.set(100)
                    status_var.set("Installation complete")
                    lines = [
                        f"Installed {result.package.name} {result.package.version}",
                        f"Target: {result.target_dir}",
                        f"Virtual environment: {result.venv_python}",
                    ]
                    if result.compiled_exe:
                        lines.append(f"Executable: {result.compiled_exe}")
                    if result.warnings:
                        lines.append("")
                        lines.append("Warnings:")
                        lines.extend(f"• {item}" for item in result.warnings)
                    messagebox.showinfo("Norm installation complete", "\n".join(lines))
        except queue.Empty:
            pass
        root.after(100, pump_events)

    source_entry.bind("<FocusOut>", lambda _e: refresh_package_label())
    refresh_package_label()
    root.after(100, pump_events)
    root.mainloop()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Reusable Norm source/full-backup installer")
    parser.add_argument("--source", help="Norm portable-source or full-backup ZIP")
    parser.add_argument("--target", help="Installation directory")
    parser.add_argument("--python", dest="python_exe", help="Python executable used to create the Norm venv")
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
        target.write_text(f"ok {INSTALLER_VERSION}\n", encoding="utf-8")
        return 0

    if args.print_pip_spec:
        print(PIP_SPEC)
        return 0
    if args.print_pip_version:
        # Backward-compatible internal helper for older build BATs.
        print(PIP_MIN_VERSION)
        return 0

    source = Path(args.source).expanduser() if args.source else find_default_source()

    if args.validate_only:
        if not source:
            raise InstallerError("No source package was specified or found beside the installer")
        info = inspect_package(source)
        print(json.dumps({
            "installer_version": INSTALLER_VERSION,
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
        if not source or not args.target or not args.python_exe:
            parser.error("--install requires --source (or a detected package), --target, and --python")
        result = install_norm(
            InstallOptions(
                source_zip=source,
                target_dir=Path(args.target),
                python_exe=Path(args.python_exe),
                compile_exe=not args.no_build,
                install_torch=not args.no_torch,
                replace_existing=args.replace,
                recreate_venv=args.recreate_venv,
            ),
            log=print,
            progress=lambda value, text: print(f"[{value:3d}%] {text}"),
        )
        print(f"Installed {result.package.label} to {result.target_dir}")
        for warning in result.warnings:
            print(f"WARNING: {warning}")
        return 0

    return launch_gui(source)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except InstallerError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(2)
