from __future__ import annotations

import os
from configparser import ConfigParser
from pathlib import Path

PORT_KEYS = ("ollama", "norm_http", "activity")
DOCUMENT_KEYS = ("readme", "current_status", "development_notes", "future_implementation_notes")
PROJECT_KEYS = ("name", "version", "author", "repository")
PATH_KEYS = ("runtime_root", "workspace_root", "verbatim_writer")


def load_settings(root: Path) -> ConfigParser:
    path = Path(root) / "config" / "settings.ini"
    if not path.exists():
        raise FileNotFoundError(f"Norm settings file is missing: {path}")
    parser = ConfigParser(interpolation=None)
    with path.open("r", encoding="utf-8-sig") as handle:
        parser.read_file(handle)
    return parser


def _setting_path(raw: str) -> Path:
    return Path(os.path.expandvars(os.path.expanduser(raw.strip()))).resolve()


def load_path_settings(root: Path) -> dict[str, Path]:
    root = Path(root).resolve()
    parser = load_settings(root)
    if not parser.has_section("paths"):
        raise ValueError("settings.ini requires a [paths] section")
    result: dict[str, Path] = {}
    for key in PATH_KEYS:
        raw = parser.get("paths", key, fallback="").strip()
        if not raw:
            raise ValueError(f"settings.ini requires paths.{key}")
        result[key] = _setting_path(raw)
    if result["runtime_root"] != root:
        raise ValueError(
            f"Configured runtime_root {result['runtime_root']} does not match running root {root}"
        )
    workspace = result["workspace_root"]
    if workspace == root or workspace in root.parents or root in workspace.parents:
        raise ValueError("runtime_root and workspace_root must not overlap")
    if not workspace.is_dir():
        raise FileNotFoundError(f"Norm workspace root is missing: {workspace}")
    if not result["verbatim_writer"].is_file():
        raise FileNotFoundError(f"Norm verbatim writer is missing: {result['verbatim_writer']}")
    return result


def load_ports(root: Path) -> dict[str, int]:
    parser = load_settings(root)
    if not parser.has_section("ports"):
        raise ValueError("settings.ini requires a [ports] section")
    result: dict[str, int] = {}
    for key in PORT_KEYS:
        raw = parser.get("ports", key, fallback="").strip()
        try:
            port = int(raw)
        except ValueError as exc:
            raise ValueError(f"invalid port for {key}: {raw!r}") from exc
        if not 1 <= port <= 65535:
            raise ValueError(f"port out of range for {key}: {port}")
        result[key] = port
    if len(set(result.values())) != len(result):
        raise ValueError("ollama, norm_http, and activity ports must be distinct")
    return result

def load_document_paths(root: Path) -> dict[str, Path]:
    parser = load_settings(root)
    if not parser.has_section("documentation"):
        raise ValueError("settings.ini requires a [documentation] section")
    workspace = load_path_settings(root)["workspace_root"]
    result: dict[str, Path] = {}
    for key in DOCUMENT_KEYS:
        raw = parser.get("documentation", key, fallback="").strip()
        if not raw:
            raise ValueError(f"settings.ini requires documentation.{key}")
        path = Path(raw)
        result[key] = path.resolve() if path.is_absolute() else (workspace / path).resolve()
    return result


def load_project_metadata(root: Path) -> dict[str, str]:
    parser = load_settings(root)
    if not parser.has_section("project"):
        raise ValueError("settings.ini requires a [project] section")
    result: dict[str, str] = {}
    for key in PROJECT_KEYS:
        value = parser.get("project", key, fallback="").strip()
        if not value:
            raise ValueError(f"settings.ini requires project.{key}")
        result[key] = value
    return result


def ollama_base_url(root: Path, host: str = "127.0.0.1") -> str:
    return f"http://{host}:{load_ports(root)['ollama']}"
