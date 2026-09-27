from __future__ import annotations

import argparse
import ast
import contextlib
import hashlib
import importlib
import importlib.util
import inspect
import io
import json
import os
import re
import shutil
import sys
import uuid
from configparser import ConfigParser
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SETTINGS = ROOT / "config" / "settings.ini"
PLUGIN_NAMESPACE = uuid.uuid5(uuid.NAMESPACE_URL, "https://github.com/hermit-node/norm/plugins/v1")
MANIFEST_KEYS = {"NAME", "VERSION", "ENTRYPOINT", "CAPABILITIES", "DESCRIPTION"}

def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _load_config() -> tuple[Path, Path]:
    cfg = ConfigParser(interpolation=None)
    with SETTINGS.open("r", encoding="utf-8-sig") as handle:
        cfg.read_file(handle)
    documents = Path(cfg.get("paths", "documents_root")).expanduser().resolve()
    raw_root = cfg.get("plugins", "root", fallback="plugins").strip() or "plugins"
    candidate = Path(os.path.expandvars(os.path.expanduser(raw_root)))
    plugin_root = candidate.resolve() if candidate.is_absolute() else (documents / candidate).resolve()
    raw_registry = cfg.get("plugins", "registry_file", fallback=".registry.json").strip() or ".registry.json"
    registry_candidate = Path(os.path.expandvars(os.path.expanduser(raw_registry)))
    registry = registry_candidate.resolve() if registry_candidate.is_absolute() else (plugin_root / registry_candidate).resolve()
    return plugin_root, registry

def _literal_manifest(path: Path) -> dict[str, Any]:
    tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
    values: dict[str, Any] = {}
    for node in tree.body:
        if not isinstance(node, ast.Assign) or len(node.targets) != 1 or not isinstance(node.targets[0], ast.Name):
            continue
        key = node.targets[0].id
        if key not in MANIFEST_KEYS:
            continue
        try:
            values[key] = ast.literal_eval(node.value)
        except Exception as exc:
            raise ValueError(f"{path.name}: {key} must be a literal value") from exc
    return values


def _script_records(folder: Path, name: str, version: str) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for path in sorted(folder.rglob("*.py")):
        if "__pycache__" in path.parts or any(part.startswith(".") for part in path.relative_to(folder).parts):
            continue
        rel = path.relative_to(folder).as_posix()
        digest = _sha256(path)
        script_uuid = uuid.uuid5(PLUGIN_NAMESPACE, f"script|{name}|{version}|{rel}|{digest}")
        content_uuid = uuid.uuid5(PLUGIN_NAMESPACE, f"sha256|{digest}")
        records.append({"path": rel, "version": version, "sha256": digest, "uuid": str(script_uuid), "content_uuid": str(content_uuid), "bytes": path.stat().st_size})
    return records

def _plugin_record(folder: Path) -> dict[str, Any]:
    manifest_path = folder / "init.py"
    if not manifest_path.is_file():
        alt = folder / "__init__.py"
        manifest_path = alt if alt.is_file() else manifest_path
    readme_path = folder / "README.md"
    if not manifest_path.is_file():
        raise FileNotFoundError("init.py is required")
    if not readme_path.is_file():
        raise FileNotFoundError("README.md is required")
    meta = _literal_manifest(manifest_path)
    name = str(meta.get("NAME") or folder.name).strip()
    version = str(meta.get("VERSION") or "").strip()
    entrypoint = str(meta.get("ENTRYPOINT") or "").strip()
    description = str(meta.get("DESCRIPTION") or "").strip()
    capabilities_raw = meta.get("CAPABILITIES") or []
    if not version or not entrypoint:
        raise ValueError("VERSION and ENTRYPOINT are required in init.py")
    if not isinstance(capabilities_raw, (list, tuple)):
        raise ValueError("CAPABILITIES must be a literal list or tuple")
    capabilities = [str(item).strip() for item in capabilities_raw if str(item).strip()]
    entry_file_raw, sep, function = entrypoint.partition(":")
    function = function.strip() if sep else "run"
    entry_file = (folder / entry_file_raw.strip()).resolve()
    if entry_file.suffix.lower() != ".py" or not entry_file.is_file() or not entry_file.is_relative_to(folder.resolve()):
        raise ValueError(f"ENTRYPOINT must reference an existing .py inside {folder.name}")
    readme = readme_path.read_text(encoding="utf-8-sig", errors="replace")
    scripts = _script_records(folder, name, version)
    readme_sha = _sha256(readme_path)
    aggregate_material = json.dumps({"name": name, "version": version, "entrypoint": entrypoint, "readme_sha256": readme_sha, "scripts": scripts}, sort_keys=True, ensure_ascii=False).encode("utf-8")
    aggregate_sha = hashlib.sha256(aggregate_material).hexdigest()
    version_uuid = uuid.uuid5(PLUGIN_NAMESPACE, f"version|{name}|{version}")
    plugin_uuid = uuid.uuid5(PLUGIN_NAMESPACE, f"plugin|{name}|{version}|{aggregate_sha}")
    return {
        "name": name,
        "folder": folder.name,
        "version": version,
        "version_uuid": str(version_uuid),
        "entrypoint": entrypoint,
        "entry_file": entry_file.relative_to(folder.resolve()).as_posix(),
        "entry_function": function or "run",
        "description": description,
        "capabilities": capabilities,
        "readme_sha256": readme_sha,
        "readme_excerpt": readme.strip()[:1200],
        "aggregate_sha256": aggregate_sha,
        "uuid": str(plugin_uuid),
        "scripts": scripts,
    }


def _scan(write_registry: bool = True) -> dict[str, Any]:
    plugin_root, registry_path = _load_config()
    plugin_root.mkdir(parents=True, exist_ok=True)
    plugins: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    for folder in sorted(plugin_root.iterdir(), key=lambda p: p.name.lower()):
        if not folder.is_dir() or folder.name.startswith((".", "_")):
            continue
        try:
            plugins.append(_plugin_record(folder))
        except Exception as exc:
            errors.append({"folder": folder.name, "error": f"{type(exc).__name__}: {exc}"})
    registry = {"schema_version": 1, "generated_at": datetime.now().astimezone().isoformat(), "plugin_root": str(plugin_root), "plugins": plugins, "errors": errors}
    if write_registry:
        registry_path.parent.mkdir(parents=True, exist_ok=True)
        temp = registry_path.with_name(registry_path.name + ".writing")
        temp.write_text(json.dumps(registry, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
        os.replace(temp, registry_path)
    return registry


def _plugin_lookup(registry: dict[str, Any], token: str) -> dict[str, Any]:
    needle = str(token or "").strip().lower()
    matches = []
    for plugin in registry.get("plugins", []):
        values = {str(plugin.get("name", "")).lower(), str(plugin.get("folder", "")).lower(), str(plugin.get("uuid", "")).lower()}
        if needle in values or str(plugin.get("uuid", "")).lower().startswith(needle):
            matches.append(plugin)
    if len(matches) != 1:
        raise LookupError(f"plugin selector {token!r} matched {len(matches)} plugins")
    return matches[0]


def _match(registry: dict[str, Any], query: str, limit: int = 8) -> list[dict[str, Any]]:
    tokens = {t for t in re.findall(r"[a-z0-9_+-]+", query.lower()) if len(t) > 1}
    ranked: list[tuple[int, dict[str, Any]]] = []
    for plugin in registry.get("plugins", []):
        caps = " ".join(plugin.get("capabilities", [])).lower()
        fields = " ".join([str(plugin.get("name", "")), str(plugin.get("description", "")), caps, str(plugin.get("readme_excerpt", ""))]).lower()
        score = sum(4 if token in caps else 1 for token in tokens if token in fields)
        if query.lower() in fields:
            score += 6
        if score:
            ranked.append((score, plugin))
    ranked.sort(key=lambda item: (-item[0], str(item[1].get("name", "")).lower()))
    return [{"score": score, **plugin} for score, plugin in ranked[: max(1, limit)]]


def _load_entry(plugin_root: Path, plugin: dict[str, Any]):
    folder = (plugin_root / str(plugin["folder"])).resolve()
    entry = (folder / str(plugin["entry_file"])).resolve()
    for cache in folder.rglob("__pycache__"):
        if cache.is_dir():
            shutil.rmtree(cache, ignore_errors=True)
    importlib.invalidate_caches()
    for path in folder.glob("*.py"):
        alias = path.stem
        existing = sys.modules.get(alias)
        existing_file = getattr(existing, "__file__", None) if existing is not None else None
        try:
            if existing is not None and (not existing_file or not Path(existing_file).resolve().is_relative_to(folder)):
                sys.modules.pop(alias, None)
        except Exception:
            sys.modules.pop(alias, None)
    current_sha = _sha256(entry)
    record = next((item for item in plugin.get("scripts", []) if item.get("path") == plugin.get("entry_file")), None)
    if record is None or current_sha != record.get("sha256"):
        raise RuntimeError("plugin changed after scan; rescan and retry")
    module_name = f"norm_plugin_{plugin['uuid'].replace('-', '_')}_{current_sha[:12]}"
    spec = importlib.util.spec_from_file_location(module_name, entry)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load plugin entry file: {entry}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    sys.path.insert(0, str(folder))
    try:
        spec.loader.exec_module(module)
    finally:
        try:
            sys.path.remove(str(folder))
        except ValueError:
            pass
        sys.modules.pop(module_name, None)
    fn = getattr(module, str(plugin.get("entry_function") or "run"), None)
    if not callable(fn):
        raise AttributeError(f"entry function {plugin.get('entry_function')!r} is not callable")
    return fn


def _run_plugin(registry: dict[str, Any], selector: str, payload: dict[str, Any]) -> dict[str, Any]:
    plugin_root = Path(registry["plugin_root"]).resolve()
    plugin = _plugin_lookup(registry, selector)
    fn = _load_entry(plugin_root, plugin)
    stdout = io.StringIO()
    stderr = io.StringIO()
    folder = (plugin_root / str(plugin["folder"])).resolve()
    sys.path.insert(0, str(folder))
    try:
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            result = fn() if len(inspect.signature(fn).parameters) == 0 else fn(payload)
    finally:
        try:
            sys.path.remove(str(folder))
        except ValueError:
            pass
    return {
        "ok": True,
        "plugin": {k: plugin[k] for k in ("name", "version", "uuid", "aggregate_sha256", "entrypoint")},
        "result": result,
        "stdout": stdout.getvalue(),
        "stderr": stderr.getvalue(),
    }


def _json_payload(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("plugin payload must be a JSON object")
    return value


def _print_json(value: Any) -> None:
    print(json.dumps(value, indent=2, ensure_ascii=False, default=str))


def _scaffold(name: str) -> dict[str, str]:
    plugin_root, _ = _load_config()
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "-", name.strip()).strip(".-")
    if not safe:
        raise ValueError("plugin name is empty after normalization")
    folder = (plugin_root / safe).resolve()
    if folder.exists():
        raise FileExistsError(folder)
    folder.mkdir(parents=True)
    (folder / "init.py").write_text(
        f'NAME = {safe!r}\nVERSION = "0.1.0"\nENTRYPOINT = "plugin.py:run"\n'
        'CAPABILITIES = ["describe what this plugin can do"]\n'
        'DESCRIPTION = "Short plugin description."\n', encoding="utf-8", newline="\n")
    (folder / "plugin.py").write_text(
        'def run(payload: dict):\n    """Return JSON-serializable output."""\n    return {"ok": True, "payload": payload}\n',
        encoding="utf-8", newline="\n")
    (folder / "README.md").write_text(
        f"# {safe}\n\nDescribe when Norm should use this plugin, accepted input, output, limits, and examples.\n",
        encoding="utf-8", newline="\n")
    return {"folder": str(folder), "name": safe}


def main() -> int:
    parser = argparse.ArgumentParser(description="Discover and hot-load Norm Python plugins without rebuilding norm.exe.")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("scan")
    sub.add_parser("list")
    p_match = sub.add_parser("match")
    p_match.add_argument("query")
    p_match.add_argument("--limit", type=int, default=8)
    p_desc = sub.add_parser("describe")
    p_desc.add_argument("selector")
    p_run = sub.add_parser("run")
    p_run.add_argument("selector")
    p_run.add_argument("--json", dest="payload", default=None)
    p_run.add_argument("--json-file", dest="payload_file", default=None)
    p_scaffold = sub.add_parser("scaffold")
    p_scaffold.add_argument("name")
    args = parser.parse_args()

    if args.command == "scaffold":
        _print_json(_scaffold(args.name))
        return 0
    registry = _scan(write_registry=True)
    if args.command == "scan":
        _print_json(registry)
    elif args.command == "list":
        _print_json({"generated_at": registry["generated_at"], "plugins": registry["plugins"], "errors": registry["errors"]})
    elif args.command == "match":
        _print_json({"query": args.query, "matches": _match(registry, args.query, args.limit), "errors": registry["errors"]})
    elif args.command == "describe":
        _print_json(_plugin_lookup(registry, args.selector))
    elif args.command == "run":
        raw_payload = Path(args.payload_file).read_text(encoding="utf-8-sig") if args.payload_file else args.payload
        _print_json(_run_plugin(registry, args.selector, _json_payload(raw_payload)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
