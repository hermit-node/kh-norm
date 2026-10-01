from __future__ import annotations

import json
import sys
from pathlib import Path

from .settings import load_plugin_settings, load_project_metadata


def about_info(runtime_root: str | Path) -> dict:
    root = Path(runtime_root).resolve()
    project = load_project_metadata(root)
    plugin_root = load_plugin_settings(root)["plugin_root"]
    manifest_path = root / "package-manifest.json"
    manifest = {}
    if manifest_path.is_file():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            manifest = {}
    plugin_count = 0
    if plugin_root.is_dir():
        plugin_count = sum(1 for child in plugin_root.iterdir() if child.is_dir() and not child.name.startswith("."))
    return {
        "name": project["name"],
        "version": project["version"],
        "author": project["author"],
        "repository": project["repository"],
        "runtime_root": str(root),
        "executable": str(root / "core" / "norm.exe"),
        "runtime_mode": "frozen" if bool(getattr(sys, "frozen", False)) else "source/python",
        "python": sys.version.split()[0],
        "package_type": str(manifest.get("package_type") or "unknown"),
        "package_schema": manifest.get("package_schema"),
        "plugin_root": str(plugin_root),
        "plugin_count": plugin_count,
    }


def format_about(runtime_root: str | Path) -> str:
    info = about_info(runtime_root)
    schema = info["package_schema"] if info["package_schema"] is not None else "unknown"
    return "\n".join([
        f"{info['name']} {info['version']}",
        f"Author: {info['author']}",
        f"Repository: {info['repository']}",
        f"Runtime: {info['runtime_root']}",
        f"Executable: {info['executable']}",
        f"Mode: {info['runtime_mode']} | Python {info['python']}",
        f"Package: {info['package_type']} (schema {schema})",
        f"Plugins: {info['plugin_count']} under {info['plugin_root']}",
    ])
