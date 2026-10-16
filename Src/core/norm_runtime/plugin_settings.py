from __future__ import annotations

import configparser
import hashlib
import io
import json
import os
import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

from .backup_retention import DEFAULT_BACKUP_LIMIT, prune_backup_files

_BACKUP_LIMIT = DEFAULT_BACKUP_LIMIT
_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]*$")


def _ini_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return ""
    if isinstance(value, (str, int, float)):
        return str(value)
    raise ValueError(f"plugin setting defaults must be scalar, got {type(value).__name__}")


def _value_valid(raw: str, default: Any) -> bool:
    if isinstance(default, bool):
        return str(raw).strip().lower() in {"1", "0", "yes", "no", "true", "false", "on", "off"}
    if isinstance(default, int) and not isinstance(default, bool):
        try:
            int(str(raw).strip())
            return True
        except ValueError:
            return False
    if isinstance(default, float):
        try:
            float(str(raw).strip())
            return True
        except ValueError:
            return False
    return True


def _load_manifest(folder: Path) -> dict[str, Any] | None:
    path = folder / "plugin.json"
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception as exc:
        raise ValueError(f"{folder.name}: invalid plugin.json: {exc}") from exc
    if not isinstance(data, dict) or data.get("schema_version") != 2:
        return None
    return data


def _declarations(plugin_root: Path) -> tuple[dict[str, dict[str, Any]], dict[tuple[str, str], list[str]]]:
    desired: dict[str, dict[str, Any]] = {}
    owners: dict[tuple[str, str], list[str]] = {}
    for folder in sorted(plugin_root.iterdir(), key=lambda p: p.name.lower()):
        if not folder.is_dir() or folder.name.startswith((".", "_")):
            continue
        meta = _load_manifest(folder)
        if meta is None:
            continue
        raw = meta.get("settings", {})
        if raw is None:
            raw = {}
        if not isinstance(raw, dict):
            raise ValueError(f"{folder.name}: plugin.json settings must be an object")
        for section, values in raw.items():
            section = str(section).strip().lower()
            if not _NAME_RE.fullmatch(section):
                raise ValueError(f"{folder.name}: unsafe plugin settings section {section!r}")
            if not isinstance(values, dict):
                raise ValueError(f"{folder.name}: settings.{section} must be an object")
            bucket = desired.setdefault(section, {})
            for key, default in values.items():
                key = str(key).strip().lower()
                if not _NAME_RE.fullmatch(key):
                    raise ValueError(f"{folder.name}: unsafe plugin setting key {section}.{key}")
                _ini_value(default)  # validate scalar now
                marker = (section, key)
                owners.setdefault(marker, []).append(folder.name)
                if key in bucket and _ini_value(bucket[key]) != _ini_value(default):
                    prior = ", ".join(owners[marker][:-1])
                    raise ValueError(
                        f"conflicting plugin default for [{section}] {key}: {prior} vs {folder.name}"
                    )
                bucket[key] = default
    return desired, owners


def _read(path: Path) -> configparser.ConfigParser:
    parser = configparser.ConfigParser(interpolation=None)
    if path.is_file():
        with path.open("r", encoding="utf-8-sig") as handle:
            parser.read_file(handle)
    return parser


def _render(desired: dict[str, dict[str, Any]], current: configparser.ConfigParser) -> tuple[str, dict[str, dict[str, str]], list[str]]:
    out = configparser.ConfigParser(interpolation=None)
    resolved: dict[str, dict[str, str]] = {}
    repairs: list[str] = []
    for section in sorted(desired):
        out.add_section(section)
        resolved[section] = {}
        for key in sorted(desired[section]):
            default = desired[section][key]
            if current.has_section(section) and current.has_option(section, key):
                raw = current.get(section, key, raw=True)
                if _value_valid(raw, default):
                    value = raw
                else:
                    value = _ini_value(default)
                    repairs.append(f"reset invalid {section}.{key}")
            else:
                value = _ini_value(default)
                repairs.append(f"added {section}.{key}")
            out.set(section, key, value)
            resolved[section][key] = value
    for section in current.sections():
        if section not in desired:
            repairs.append(f"removed dangling section [{section}]")
            continue
        for key, _ in current.items(section, raw=True):
            if key not in desired[section]:
                repairs.append(f"removed dangling key {section}.{key}")
    buf = io.StringIO()
    buf.write("# Norm plugin settings. Schema/defaults come from installed plugin.json manifests.\n")
    buf.write("# Existing declared values survive refresh; missing/invalid values fall back to plugin defaults.\n")
    buf.write("# Secrets belong in the configured Norm secrets file, never here.\n\n")
    out.write(buf, space_around_delimiters=True)
    return buf.getvalue(), resolved, repairs


def _backup(path: Path, *, limit: int = _BACKUP_LIMIT) -> Path:
    backup_dir = path.parent / "backups" / "plugins-ini"
    backup_dir.mkdir(parents=True, exist_ok=True)
    raw = path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()[:12]
    stamp = datetime.now().astimezone().strftime("%Y%m%d-%H%M%S-%f%z")
    target = backup_dir / f"plugins-{stamp}-{digest}.ini"
    shutil.copy2(path, target)
    prune_backup_files(backup_dir, pattern="plugins-*.ini", limit=limit)
    return target


def reconcile_plugin_settings(
    plugin_root: str | Path,
    settings_path: str | Path | None = None,
    *,
    backup_limit: int = _BACKUP_LIMIT,
) -> dict[str, Any]:
    root = Path(plugin_root).expanduser().resolve()
    path = Path(settings_path).expanduser().resolve() if settings_path else (root.parent / "config" / "plugins.ini").resolve()
    desired, owners = _declarations(root)
    current = _read(path)
    rendered, resolved, repairs = _render(desired, current)
    existing = path.read_text(encoding="utf-8-sig") if path.is_file() else None
    changed = existing != rendered
    backup = None
    if changed and path.is_file():
        backup = _backup(path, limit=backup_limit)
    if desired:
        if changed:
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_name(path.name + f".tmp-{os.getpid()}")
            temporary.write_text(rendered, encoding="utf-8", newline="\n")
            os.replace(temporary, path)
    elif path.is_file():
        path.unlink(missing_ok=True)
    return {
        "ok": True,
        "changed": changed,
        "path": str(path),
        "backup": str(backup) if backup else "",
        "sections": sorted(desired),
        "repairs": repairs,
        "resolved": resolved,
        "owners": {f"{section}.{key}": names for (section, key), names in sorted(owners.items())},
        "backup_limit": max(1, int(backup_limit)),
    }
