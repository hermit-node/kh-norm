from __future__ import annotations

from configparser import ConfigParser
from pathlib import Path

PORT_KEYS = ("ollama", "norm_http", "activity")
DOCUMENT_KEYS = ("current_status", "development_notes", "future_implementation_notes")
PROJECT_KEYS = ("name", "version", "author", "repository")


def load_settings(root: Path) -> ConfigParser:
    path = root / "config" / "settings.ini"
    if not path.exists():
        raise FileNotFoundError(f"Norm settings file is missing: {path}")
    parser = ConfigParser(interpolation=None)
    with path.open("r", encoding="utf-8-sig") as handle:
        parser.read_file(handle)
    return parser


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
    result: dict[str, Path] = {}
    for key in DOCUMENT_KEYS:
        raw = parser.get("documentation", key, fallback="").strip()
        if not raw:
            raise ValueError(f"settings.ini requires documentation.{key}")
        path = Path(raw)
        result[key] = path if path.is_absolute() else root / path
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
