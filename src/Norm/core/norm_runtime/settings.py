from __future__ import annotations

import os
from .secret_redaction import register_secrets
from configparser import ConfigParser
from pathlib import Path

NETWORK_PORT_KEYS = {"ollama": "ollama_port", "norm_http": "norm_port", "activity": "activity_port", "postgres": "postgres_port", "redis": "redis_port"}
DOCUMENT_KEYS = ("readme", "current_status", "development_notes", "release_notes", "future_implementation_notes")
PROJECT_KEYS = ("name", "version", "author", "repository")
POSTGRES_KEYS = ("user", "database", "schema", "stocks_database")
PATH_KEYS = ("documents_root",)


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
    runtime_raw = parser.get("paths", "runtime_root", fallback=".").strip() or "."
    runtime_candidate = Path(os.path.expandvars(os.path.expanduser(runtime_raw)))
    configured_runtime = runtime_candidate.resolve() if runtime_candidate.is_absolute() else (root / runtime_candidate).resolve()
    if configured_runtime != root:
        raise ValueError(f"settings.ini paths.runtime_root resolves to {configured_runtime}, but this runtime was opened from {root}")
    app_root = Path(os.environ.get("NORM_APP_ROOT") or (configured_runtime / "core")).resolve()
    result: dict[str, Path] = {"runtime_root": configured_runtime, "app_root": app_root}
    for key in PATH_KEYS:
        raw = parser.get("paths", key, fallback="").strip()
        if not raw:
            raise ValueError(f"settings.ini requires paths.{key}")
        result[key] = _setting_path(raw)
    documents = result["documents_root"]
    state_raw = parser.get("paths", "state_root", fallback="state").strip() or "state"
    state_path = Path(os.path.expandvars(os.path.expanduser(state_raw)))
    state_root = state_path.resolve() if state_path.is_absolute() else (root / state_path).resolve()
    result["state_root"] = state_root
    workspace_raw = parser.get("paths", "workspace_root", fallback="").strip()
    if workspace_raw:
        workspace_path = Path(os.path.expandvars(os.path.expanduser(workspace_raw)))
        workspace = workspace_path.resolve() if workspace_path.is_absolute() else (documents / workspace_path).resolve()
    else:
        workspace = documents
    verbatim_raw = parser.get("paths", "verbatim_writer", fallback="").strip()
    configured_verbatim = None
    if verbatim_raw:
        verbatim_path = Path(os.path.expandvars(os.path.expanduser(verbatim_raw)))
        configured_verbatim = verbatim_path.resolve() if verbatim_path.is_absolute() else (root / verbatim_path).resolve()
    verbatim_candidates = [
        configured_verbatim,
        (root / "plugins" / "verbatim_lines" / "src" / "_cli.py").resolve(),
        (root / "plugins" / "verbatim_lines" / "_cli.py").resolve(),
    ]
    verbatim = next((candidate for candidate in verbatim_candidates if candidate is not None and candidate.is_file()), None)
    if verbatim is None:
        # Preserve the configured path in the error when one was supplied; otherwise
        # report the canonical schema-2 location.
        verbatim = configured_verbatim or verbatim_candidates[1]
    temp_raw = parser.get("paths", "temp_root", fallback="temp").strip() or "temp"
    temp_path = Path(os.path.expandvars(os.path.expanduser(temp_raw)))
    temp_root = temp_path.resolve() if temp_path.is_absolute() else (documents / temp_path).resolve()
    result["workspace_root"] = workspace
    result["temp_root"] = temp_root
    result["verbatim_writer"] = verbatim
    for label, path in (("documents_root", documents), ("workspace_root", workspace), ("temp_root", temp_root)):
        if path == root or path in root.parents or root in path.parents:
            raise ValueError(f"runtime_root and {label} must not overlap")
        if not path.is_dir():
            raise FileNotFoundError(f"Norm {label} is missing: {path}")
    if not verbatim.is_file():
        raise FileNotFoundError(f"Norm verbatim writer is missing: {verbatim}")
    return result


def load_network_settings(root: Path) -> dict[str, object]:
    parser = load_settings(root)
    if not parser.has_section("network"):
        raise ValueError("settings.ini requires a [network] section")
    machine = parser.get("network", "current_machine", fallback="").strip().lower()
    domain = parser.get("network", "current_domain", fallback="").strip().lower().strip(".")
    if not machine or not domain:
        raise ValueError("settings.ini requires network.current_machine and network.current_domain")
    result: dict[str, object] = {
        "current_machine": machine, "current_domain": domain, "current_fqdn": f"{machine}.{domain}",
        "ollama_host": parser.get("network", "ollama_host", fallback="loopback").strip(),
        "norm_host": parser.get("network", "norm_host", fallback="current").strip(),
        "activity_host": parser.get("network", "activity_host", fallback="current").strip(),
        "postgres_host": parser.get("network", "postgres_host", fallback="current").strip(),
        "redis_host": parser.get("network", "redis_host", fallback="current").strip(),
        "require_tailscale": parser.getboolean("network", "require_tailscale", fallback=True),
    }
    for name, ini_key in NETWORK_PORT_KEYS.items():
        raw = parser.get("network", ini_key, fallback="").strip()
        try: port = int(raw)
        except ValueError as exc: raise ValueError(f"invalid network port for {name}: {raw!r}") from exc
        if not 1 <= port <= 65535: raise ValueError(f"port out of range for {name}: {port}")
        result[name + "_port"] = port
    return result


def resolve_network_host(network: dict[str, object], key: str, *, bind: bool = False) -> str:
    raw = str(network.get(key) or "").strip()
    if raw.lower() == "current": return "tailscale" if bind else str(network["current_fqdn"])
    if raw.lower() in {"loopback", "localhost", "127.0.0.1"}: return "127.0.0.1"
    return raw


def load_ports(root: Path) -> dict[str, int]:
    network = load_network_settings(root)
    return {name: int(network[name + "_port"]) for name in NETWORK_PORT_KEYS}


def load_postgres_settings(root: Path) -> dict[str, str]:
    """Load non-secret PostgreSQL identity/configuration from settings.ini.

    Older installations stored these non-secret values beside the password in
    the external env file. Keep a read-only compatibility fallback so an old
    install can still boot during migration, but new packages persist them in
    [postgres] and reserve the env file for actual secrets.
    """
    parser = load_settings(root)
    if parser.has_section("postgres"):
        defaults = {
            "user": "norm",
            "database": "norm",
            "schema": "norm_runtime",
            "stocks_database": "stocks_api",
        }
        result = {
            key: parser.get("postgres", key, fallback=defaults[key]).strip() or defaults[key]
            for key in POSTGRES_KEYS
        }
        return result

    legacy = load_secrets(root)
    return {
        "user": legacy.get("NORM_POSTGRES_USER", "norm").strip() or "norm",
        "database": legacy.get("NORM_POSTGRES_DB", "norm").strip() or "norm",
        "schema": legacy.get("NORM_POSTGRES_SCHEMA", "norm_runtime").strip() or "norm_runtime",
        "stocks_database": legacy.get("NORM_STOCKS_DB", "stocks_api").strip() or "stocks_api",
    }


def load_secrets(root: Path) -> dict[str, str]:
    parser = load_settings(root)
    raw = parser.get("environment", "secrets_file", fallback="").strip()
    if not raw: raise ValueError("settings.ini requires environment.secrets_file")
    path = _setting_path(raw)
    if not path.is_file(): raise FileNotFoundError(f"Norm secrets file is missing: {path}")
    result: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped: continue
        key, value = stripped.split("=", 1)
        result[key.strip()] = value.strip()
    register_secrets(result, path)
    return result

def load_ssh_settings(root: Path) -> dict[str, object]:
    parser = load_settings(root)
    if not parser.has_section("ssh"):
        return {"enabled": False}
    enabled = parser.getboolean("ssh", "enabled", fallback=True)
    raw_root = parser.get("ssh", "root", fallback=".ssh").strip() or ".ssh"
    ssh_root = _setting_path(raw_root) if Path(raw_root).is_absolute() else (Path(root).resolve() / raw_root).resolve()

    def child(key: str, default: str) -> Path:
        raw = parser.get("ssh", key, fallback=default).strip() or default
        path = Path(os.path.expandvars(os.path.expanduser(raw)))
        return path.resolve() if path.is_absolute() else (ssh_root / path).resolve()

    return {
        "enabled": enabled,
        "root": ssh_root,
        "executable": child("executable", r"C:\Windows\System32\OpenSSH\ssh.exe"),
        "known_hosts_file": child("known_hosts_file", "known_hosts"),
        "strict_host_key_checking": parser.get("ssh", "strict_host_key_checking", fallback="accept-new").strip() or "accept-new",
        "connect_timeout_seconds": parser.getint("ssh", "connect_timeout_seconds", fallback=8),
        "ca8d_host": parser.get("ssh", "ca8d_host", fallback="").strip(),
        "ca8d_docker_host": parser.get("ssh", "ca8d_docker_host", fallback="").strip(),
        "ca8d_port": parser.getint("ssh", "ca8d_port", fallback=22),
        "ca8d_user": parser.get("ssh", "ca8d_user", fallback="").strip(),
        "ca8d_identity_file": child("ca8d_identity_file", "ca8d_norm_ed25519"),
    }


def load_plugin_settings(root: Path) -> dict[str, Path]:
    root = Path(root).resolve()
    parser = load_settings(root)
    raw_root = parser.get("plugins", "root", fallback="plugins").strip() or "plugins"
    plugin_candidate = Path(os.path.expandvars(os.path.expanduser(raw_root)))
    plugin_root = plugin_candidate.resolve() if plugin_candidate.is_absolute() else (root / plugin_candidate).resolve()
    raw_registry = parser.get("plugins", "registry_file", fallback=".registry.json").strip() or ".registry.json"
    registry_candidate = Path(os.path.expandvars(os.path.expanduser(raw_registry)))
    registry_file = registry_candidate.resolve() if registry_candidate.is_absolute() else (plugin_root / registry_candidate).resolve()
    return {"plugin_root": plugin_root, "registry_file": registry_file}


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
        result[key] = path.resolve() if path.is_absolute() else (root / path).resolve()
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
