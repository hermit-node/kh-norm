from __future__ import annotations

import argparse
import configparser
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
SETTINGS = ROOT / "config" / "settings.ini"
APP = ROOT / "core"
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))

from norm_runtime.settings import load_path_settings, load_plugin_settings, load_ssh_settings


def load_settings() -> configparser.ConfigParser:
    cfg = configparser.ConfigParser(interpolation=None)
    with SETTINGS.open("r", encoding="utf-8-sig") as handle:
        cfg.read_file(handle)
    return cfg


def expand_path(raw: str, *, base: Path = ROOT) -> Path:
    value = Path(os.path.expandvars(os.path.expanduser(str(raw).strip())))
    return value.resolve() if value.is_absolute() else (base / value).resolve()


def split_dirs(value: str) -> set[str]:
    return {part.strip().replace("\\", "/").strip("/").lower() for part in value.split(";") if part.strip()}


def is_excluded(rel: Path, exclusions: set[str]) -> bool:
    key = rel.as_posix().strip("/").lower()
    parts = {part.lower() for part in rel.parts}
    for item in exclusions:
        if "/" in item:
            if key == item or key.startswith(item + "/"):
                return True
        elif item in parts:
            return True
    return False


def add_tree(zf: zipfile.ZipFile, source: Path, prefix: str, exclusions: set[str] | None = None) -> tuple[int, int]:
    exclusions = exclusions or set()
    files = 0
    total = 0
    if not source.exists():
        return files, total
    for path in source.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(source)
        if is_excluded(rel, exclusions) or path.suffix.lower() in {".pyc", ".pyo"} or "__pycache__" in rel.parts:
            continue
        arc = f"{prefix.rstrip('/')}/{rel.as_posix()}" if prefix else rel.as_posix()
        zf.write(path, arc)
        files += 1
        total += path.stat().st_size
    return files, total


def _managed_subtrees(manifest: dict) -> list[str]:
    out: list[str] = []
    for raw in manifest.get("managed_persistent_subtrees", []) or []:
        rel = PurePosixPath(str(raw).replace("\\", "/"))
        if rel.is_absolute() or ".." in rel.parts:
            raise RuntimeError(f"unsafe managed persistent subtree: {raw!r}")
        out.append(rel.as_posix().strip("/"))
    return out


def add_package_source(zf: zipfile.ZipFile, source: Path, manifest: dict) -> tuple[int, int]:
    """Write package-managed source while omitting generated/private persistent state.

    The whole plugins directory is persistent, but built-in subtrees declared by the
    manifest are package-managed and therefore included explicitly.
    """
    exclusions = {
        ".venv", ".ssh", "plugins", "logs", "state", "staging", "build", "dist",
        "__pycache__", "backup", "backups",
    }
    files = 0
    total = 0
    for path in source.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(source)
        if is_excluded(rel, exclusions) or path.suffix.lower() in {".pyc", ".pyo"} or path.name == "package-manifest.json":
            continue
        zf.write(path, rel.as_posix())
        files += 1
        total += path.stat().st_size

    for subtree in _managed_subtrees(manifest):
        src = source.joinpath(*PurePosixPath(subtree).parts)
        if not src.is_dir():
            continue
        f, b = add_tree(zf, src, subtree, set())
        files += f
        total += b

    # Preserve the plugin-root README if present; it is harmless package documentation.
    plugin_readme = source / "plugins" / "README.md"
    if plugin_readme.is_file():
        zf.write(plugin_readme, "plugins/README.md")
        files += 1
        total += plugin_readme.stat().st_size

    zf.writestr("package-manifest.json", json.dumps(manifest, indent=2, ensure_ascii=False) + "\n")
    files += 1
    return files, total


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _finalize_zip(partial: Path, final_zip: Path) -> dict:
    partial.replace(final_zip)
    digest = sha256_file(final_zip)
    final_zip.with_suffix(final_zip.suffix + ".sha256").write_text(
        f"{digest}  {final_zip.name}\n", encoding="utf-8", newline="\n"
    )
    return {"backup_zip": str(final_zip), "sha256": digest}


def _source_backup(cfg: configparser.ConfigParser, *, label: str, validate_only: bool) -> dict:
    paths = load_path_settings(ROOT)
    runtime_root = paths["runtime_root"]
    if runtime_root != ROOT:
        raise RuntimeError(f"runtime_root mismatch: settings={runtime_root} tool={ROOT}")
    manifest_path = ROOT / "package-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    manifest["package_type"] = "portable-source"
    manifest.pop("backup_payload", None)
    for subtree in _managed_subtrees(manifest):
        if not (ROOT / subtree).is_dir():
            raise FileNotFoundError(f"managed built-in plugin subtree is missing: {subtree}")
    if validate_only:
        return {
            "ok": True,
            "mode": "source",
            "runtime_root": str(ROOT),
            "managed_persistent_subtrees": _managed_subtrees(manifest),
        }

    backup_root = expand_path(cfg.get("backup", "backup_root"))
    backup_root.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().astimezone().strftime("%Y%m%d-%H%M%S%z")
    version = str(manifest.get("version") or cfg.get("project", "version", fallback="state"))
    requested = str(label or "").strip()
    suffix = "-" + re.sub(r"[^A-Za-z0-9._-]+", "-", requested).strip("-") if requested else ""
    final_zip = backup_root / f"Norm-source-{version}{suffix}-{stamp}.zip"
    partial = backup_root / f".{final_zip.name}.partial"
    if partial.exists():
        partial.unlink()
    with zipfile.ZipFile(partial, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6, allowZip64=True) as zf:
        files, total = add_package_source(zf, ROOT, manifest)
    result = _finalize_zip(partial, final_zip)
    return {"ok": True, "mode": "source", **result, "source_files": files, "source_bytes": total, "sensitive": False}


def _full_backup(cfg: configparser.ConfigParser, *, label: str, validate_only: bool) -> dict:
    # Full backups need live network/secrets only here; source backups deliberately do not.
    from runtime_bootstrap import build_postgres_pool, load_config

    paths = load_path_settings(ROOT)
    plugins = load_plugin_settings(ROOT)
    ssh = load_ssh_settings(ROOT)
    resolved = load_config(ROOT)
    runtime_root = paths["runtime_root"]
    workspace_root = paths["workspace_root"]
    temp_root = paths["temp_root"]
    backup_root = expand_path(cfg.get("backup", "backup_root"))
    pg_dump = expand_path(cfg.get("backup", "postgres_dump_executable"))
    pg_restore = expand_path(cfg.get("backup", "postgres_restore_executable"))
    schema = str(resolved["postgres"].get("schema") or cfg.get("backup", "postgres_schema", fallback="norm_runtime"))
    conn_parts = build_postgres_pool(ROOT).connection_parameters("norm", include_password=True)
    secrets_file = expand_path(cfg.get("environment", "secrets_file"))
    runtime_excludes = split_dirs(cfg.get("backup_policy", "runtime_exclude_dirs", fallback=""))
    workspace_excludes = split_dirs(cfg.get("backup_policy", "workspace_exclude_dirs", fallback=""))
    runtime_excludes.update({".venv", "staging", "build", "dist", "__pycache__"})

    if runtime_root != ROOT:
        raise RuntimeError(f"runtime_root mismatch: settings={runtime_root} tool={ROOT}")
    if not pg_dump.is_file():
        raise FileNotFoundError(pg_dump)
    if not secrets_file.is_file():
        raise FileNotFoundError(f"configured secrets file is missing: {secrets_file}")
    backup_root.mkdir(parents=True, exist_ok=True)

    base_manifest = json.loads((ROOT / "package-manifest.json").read_text(encoding="utf-8-sig"))
    full_manifest = dict(base_manifest)
    full_manifest["package_type"] = "full-backup"
    full_manifest["backup_payload"] = "backup-state/backup-manifest.json"

    stamp = datetime.now().astimezone().strftime("%Y%m%d-%H%M%S%z")
    requested = str(label or "").strip()
    checkpoint = requested or cfg.get("project", "version", fallback="").strip()
    safe = re.sub(r"[^A-Za-z0-9._-]+", "-", checkpoint).strip("-") or "state"
    final_zip = backup_root / f"Norm-full-backup-{safe}-{stamp}.zip"
    partial = backup_root / f".{final_zip.name}.partial"

    with tempfile.TemporaryDirectory(prefix="norm-full-backup-") as td:
        tmp = Path(td)
        dump_path = tmp / f"{schema}.dump"
        dump_cmd = [str(pg_dump)]
        if conn_parts.get("host"): dump_cmd.extend(["--host", str(conn_parts["host"])])
        if conn_parts.get("port"): dump_cmd.extend(["--port", str(conn_parts["port"])])
        if conn_parts.get("user"): dump_cmd.extend(["--username", str(conn_parts["user"])])
        if conn_parts.get("dbname"): dump_cmd.extend(["--dbname", str(conn_parts["dbname"])])
        dump_cmd.extend([f"--schema={schema}", "--format=custom", f"--file={dump_path}"])
        dump_env = os.environ.copy()
        if conn_parts.get("password"):
            dump_env["PGPASSWORD"] = str(conn_parts["password"])
        proc = subprocess.run(dump_cmd, text=True, capture_output=True, check=False, env=dump_env)
        if proc.returncode != 0:
            raise RuntimeError(f"pg_dump failed ({proc.returncode}): {proc.stderr.strip()}")
        if pg_restore.is_file():
            list_proc = subprocess.run([str(pg_restore), "--list", str(dump_path)], text=True, capture_output=True, check=False)
            if list_proc.returncode != 0:
                raise RuntimeError(f"pg_restore --list failed ({list_proc.returncode}): {list_proc.stderr.strip()}")
        if validate_only:
            return {
                "ok": True, "mode": "full", "schema": schema, "dump_bytes": dump_path.stat().st_size,
                "runtime_root": str(ROOT), "workspace_root": str(workspace_root), "temp_root": str(temp_root),
            }

        env = dict(cfg.items("environment"))
        backup_manifest = {
            "backup_schema": 2,
            "created_at": datetime.now().astimezone().isoformat(),
            "checkpoint_label": checkpoint,
            "sensitive": True,
            "warning": "Contains configured secrets and Norm SSH private material. Protect this archive accordingly.",
            "project": dict(cfg.items("project")),
            "paths": {
                "runtime_root": str(runtime_root),
                "documents_root": str(paths["documents_root"]),
                "workspace_root": str(workspace_root),
                "temp_root": str(temp_root),
                "plugin_root": str(plugins["plugin_root"]),
                "ssh_root": str(ssh.get("root") or (ROOT / ".ssh")),
            },
            "environment": env,
            "restore": {
                "secrets_member": f"backup-state/secrets/{secrets_file.name}",
                "postgres_member": f"backup-state/postgres/{dump_path.name}",
                "postgres_schema": schema,
                "postgres_connection": {
                    "host": str(conn_parts.get("host") or ""),
                    "port": str(conn_parts.get("port") or ""),
                    "dbname": str(conn_parts.get("dbname") or ""),
                    "user": str(conn_parts.get("user") or ""),
                    "password": str(conn_parts.get("password") or ""),
                },
                "pg_restore": str(pg_restore),
                "workspace_member": "backup-state/workspace",
                "temp_recovery_member": "backup-state/temp/recovery",
                "plugins_member": "backup-state/runtime-persistent/plugins",
                "ssh_member": "backup-state/runtime-persistent/.ssh",
                "logs_member": "backup-state/runtime-persistent/logs",
                "state_member": "backup-state/runtime-persistent/state",
            },
            "excludes": {
                "venv": True,
                "runtime": sorted(runtime_excludes),
                "workspace": sorted(workspace_excludes),
                "temp": "only recovery state is included; disposable scratch/task temp is omitted",
            },
        }

        if partial.exists():
            partial.unlink()
        with zipfile.ZipFile(partial, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6, allowZip64=True) as zf:
            source_files, source_bytes = add_package_source(zf, runtime_root, full_manifest)
            p_files = p_bytes = 0
            for folder_name, folder, extra_excludes in (
                ("plugins", plugins["plugin_root"], set()),
                (".ssh", Path(ssh.get("root") or (ROOT / ".ssh")), set()),
                ("logs", ROOT / "logs", set()),
                ("state", ROOT / "state", {"file-backups", "deletion-trash"}),
            ):
                f, b = add_tree(zf, Path(folder), f"backup-state/runtime-persistent/{folder_name}", extra_excludes)
                p_files += f
                p_bytes += b
            w_files, w_bytes = add_tree(zf, workspace_root, "backup-state/workspace", workspace_excludes)
            r_files, r_bytes = add_tree(zf, temp_root / "recovery", "backup-state/temp/recovery", set())
            zf.write(secrets_file, f"backup-state/secrets/{secrets_file.name}")
            zf.write(dump_path, f"backup-state/postgres/{dump_path.name}")
            zf.writestr("backup-state/backup-manifest.json", json.dumps(backup_manifest, indent=2, ensure_ascii=False) + "\n")
            zf.writestr("SENSITIVE_BACKUP.txt", backup_manifest["warning"] + "\n")
        result = _finalize_zip(partial, final_zip)
        return {
            "ok": True, "mode": "full", **result,
            "source_files": source_files, "source_bytes": source_bytes,
            "persistent_files": p_files, "persistent_bytes": p_bytes,
            "workspace_files": w_files, "workspace_bytes": w_bytes,
            "recovery_files": r_files, "recovery_bytes": r_bytes,
            "sensitive": True,
        }


def create_backup(*, mode: str = "full", label: str = "", validate_only: bool = False) -> dict:
    normalized = str(mode or "full").strip().lower()
    cfg = load_settings()
    if normalized in {"source", "portable", "base"}:
        return _source_backup(cfg, label=label, validate_only=validate_only)
    if normalized in {"full", "private"}:
        return _full_backup(cfg, label=label, validate_only=validate_only)
    raise ValueError("backup mode must be source or full")


def main() -> int:
    ap = argparse.ArgumentParser(description="Create installer-compatible Norm source or private full backup ZIPs.")
    ap.add_argument("--mode", choices=("source", "full"), default="full", help="source omits private/runtime state; full includes secrets, .ssh, workspace, and PostgreSQL")
    ap.add_argument("--validate-only", action="store_true")
    ap.add_argument("--label", default="")
    ap.add_argument("--json", action="store_true", help="Print only JSON result")
    args = ap.parse_args()
    result = create_backup(mode=args.mode, label=args.label, validate_only=args.validate_only)
    if args.json:
        print(json.dumps(result, ensure_ascii=False))
    else:
        for key, value in result.items():
            print(f"{key.upper()}={value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
