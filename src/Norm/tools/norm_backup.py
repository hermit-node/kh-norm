from __future__ import annotations

import argparse
import configparser
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SETTINGS = ROOT / "config" / "settings.ini"
RUNTIME_JSON = ROOT / "config" / "runtime.json"


def load_settings() -> configparser.ConfigParser:
    cfg = configparser.ConfigParser(interpolation=None)
    with SETTINGS.open("r", encoding="utf-8-sig") as handle:
        cfg.read_file(handle)
    return cfg


def split_dirs(value: str) -> set[str]:
    return {part.strip().replace("\\", "/").strip("/").lower() for part in value.split(";") if part.strip()}


def is_excluded(rel: Path, exclusions: set[str]) -> bool:
    key = rel.as_posix().strip("/").lower()
    return any(key == item or key.startswith(item + "/") for item in exclusions)


def add_tree(zf: zipfile.ZipFile, source: Path, prefix: str, exclusions: set[str]) -> tuple[int, int]:
    files = 0
    total = 0
    for path in source.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(source)
        if is_excluded(rel, exclusions):
            continue
        zf.write(path, f"{prefix}/{rel.as_posix()}")
        files += 1
        total += path.stat().st_size
        if files % 500 == 0:
            print(f"  {prefix}: {files} files", flush=True)
    return files, total

def measure_tree(source: Path, exclusions: set[str]) -> tuple[int, int]:
    files = 0
    total = 0
    for path in source.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(source)
        if is_excluded(rel, exclusions):
            continue
        files += 1
        total += path.stat().st_size
    return files, total


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--validate-only', action='store_true')
    args = ap.parse_args()
    cfg = load_settings()
    runtime_cfg = json.loads(RUNTIME_JSON.read_text(encoding="utf-8-sig"))
    runtime_root = Path(cfg.get("paths", "runtime_root")).resolve()
    workspace_root = Path(cfg.get("paths", "workspace_root")).resolve()
    backup_root = Path(cfg.get("backup", "backup_root")).resolve()
    pg_dump = Path(cfg.get("backup", "postgres_dump_executable")).resolve()
    pg_restore = Path(cfg.get("backup", "postgres_restore_executable")).resolve()
    schema = cfg.get("backup", "postgres_schema", fallback="norm_runtime").strip()
    runtime_excludes = split_dirs(cfg.get("backup_policy", "runtime_exclude_dirs", fallback=""))
    workspace_excludes = split_dirs(cfg.get("backup_policy", "workspace_exclude_dirs", fallback=""))
    if runtime_root != ROOT:
        raise RuntimeError(f"runtime_root mismatch: settings={runtime_root} tool={ROOT}")
    if not workspace_root.is_dir():
        raise FileNotFoundError(workspace_root)
    if not pg_dump.is_file():
        raise FileNotFoundError(pg_dump)
    backup_root.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().astimezone().strftime("%Y%m%d-%H%M%S%z")
    final_zip = backup_root / f"Norm-backup-{stamp}.zip"
    temp_zip = backup_root / f".{final_zip.name}.partial"
    conninfo = str(runtime_cfg["postgres"]["conninfo"])
    db_schema = str(runtime_cfg["postgres"].get("schema", schema))

    with tempfile.TemporaryDirectory(prefix="norm-backup-") as temp_name:
        temp = Path(temp_name)
        postgres_dir = temp / "postgres"
        postgres_dir.mkdir(parents=True, exist_ok=True)
        dump_path = postgres_dir / f"{db_schema}.dump"
        print(f"Dumping PostgreSQL schema {db_schema}...", flush=True)
        proc = subprocess.run(
            [str(pg_dump), f"--dbname={conninfo}", f"--schema={db_schema}", "--format=custom", f"--file={dump_path}"],
            text=True, capture_output=True, check=False,
        )
        if proc.returncode != 0:
            raise RuntimeError(f"pg_dump failed ({proc.returncode}): {proc.stderr.strip()}")
        if args.validate_only:
            list_proc = subprocess.run([str(pg_restore), '--list', str(dump_path)], text=True, capture_output=True, check=False)
            if list_proc.returncode != 0:
                raise RuntimeError(f"pg_restore --list failed ({list_proc.returncode}): {list_proc.stderr.strip()}")
            runtime_files, runtime_bytes = measure_tree(runtime_root, runtime_excludes)
            workspace_files, workspace_bytes = measure_tree(workspace_root, workspace_excludes)
            print(f'VALIDATION_OK schema={db_schema} runtime_files={runtime_files} runtime_bytes={runtime_bytes} workspace_files={workspace_files} workspace_bytes={workspace_bytes}')
            return 0
        manifest = {
            "backup_version": 1,
            "created_at": datetime.now().astimezone().isoformat(),
            "paths": {
                "runtime_root": str(runtime_root),
                "workspace_root": str(workspace_root),
                "verbatim_writer": cfg.get("paths", "verbatim_writer"),
            },
            "postgres": {
                "conninfo": conninfo,
                "schema": db_schema,
                "dump": f"postgres/{dump_path.name}",
                "pg_restore": str(pg_restore),
            },
            "environment": dict(cfg.items("environment")),
            "runtime_exclude_dirs": sorted(runtime_excludes),
            "workspace_exclude_dirs": sorted(workspace_excludes),
            "project": dict(cfg.items("project")),
        }
        manifest_path = temp / "backup-manifest.json"
        manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")

        if temp_zip.exists():
            temp_zip.unlink()
        print(f"Creating {final_zip.name}...", flush=True)
        with zipfile.ZipFile(temp_zip, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6, allowZip64=True) as zf:
            runtime_files, runtime_bytes = add_tree(zf, runtime_root, "runtime", runtime_excludes)
            workspace_files, workspace_bytes = add_tree(zf, workspace_root, "workspace", workspace_excludes)
            zf.write(dump_path, f"postgres/{dump_path.name}")
            zf.write(manifest_path, "backup-manifest.json")
            for helper in ("restore_norm_backup.bat", "restore_norm_backup.ps1", "ENVIRONMENT_REBUILD.md"):
                helper_path = ROOT / "tools" / helper
                if helper_path.is_file():
                    zf.write(helper_path, helper)
        temp_zip.replace(final_zip)
        digest = sha256_file(final_zip)
        (final_zip.with_suffix(final_zip.suffix + ".sha256")).write_text(
            f"{digest}  {final_zip.name}\n", encoding="utf-8", newline="\n"
        )
        for helper in ("restore_norm_backup.bat", "restore_norm_backup.ps1", "ENVIRONMENT_REBUILD.md"):
            helper_path = ROOT / "tools" / helper
            if helper_path.is_file():
                shutil.copy2(helper_path, backup_root / helper)
        print(f"BACKUP_ZIP={final_zip}")
        print(f"SHA256={digest}")
        print(f"RUNTIME_FILES={runtime_files} RUNTIME_BYTES={runtime_bytes}")
        print(f"WORKSPACE_FILES={workspace_files} WORKSPACE_BYTES={workspace_bytes}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
