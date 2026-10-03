from __future__ import annotations

import configparser
import json
import os
import socket
import subprocess
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

FIELD_PATHS = (
    "paths.documents_root", "paths.workspace_root", "paths.temp_root",
    "network.current_machine", "network.current_domain", "network.require_tailscale",
    "network.ollama_host", "network.ollama_port", "network.norm_host", "network.norm_port",
    "network.activity_host", "network.activity_port", "network.postgres_host", "network.postgres_port",
    "network.redis_host", "network.redis_port",
    "postgres.user", "postgres.database", "postgres.schema", "postgres.stocks_database",
    "ssh.enabled", "ssh.user", "ssh.remote_host", "ssh.docker_host", "ssh.port", "ssh.identity_file",
    "runtime.allowed_roots", "runtime.storage_context.primary_name",
    "runtime.storage_context.primary_root", "runtime.storage_context.backup_name",
    "runtime.network_map.never_probe_name_patterns", "runtime.network_map.never_probe_cidrs",
)


def deep_copy(value: Any) -> Any:
    return json.loads(json.dumps(value))


def nested_get(data: dict[str, Any], dotted: str, default: Any = None) -> Any:
    current: Any = data
    for part in dotted.split("."):
        if not isinstance(current, dict) or part not in current:
            return default
        current = current[part]
    return current


def nested_has(data: dict[str, Any], dotted: str) -> bool:
    sentinel = object()
    return nested_get(data, dotted, sentinel) is not sentinel


def nested_set(data: dict[str, Any], dotted: str, value: Any) -> None:
    parts = dotted.split(".")
    current: dict[str, Any] = data
    for part in parts[:-1]:
        child = current.get(part)
        if not isinstance(child, dict):
            child = {}
            current[part] = child
        current = child
    current[parts[-1]] = deep_copy(value)


def persistent_imprint_path(local_name: str) -> Path:
    appdata = os.environ.get("APPDATA", "").strip()
    if appdata:
        return (Path(appdata).expanduser() / "Norm" / local_name).resolve()
    return (Path.home() / ".norm" / local_name).resolve()

def _read_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def _resolve_secrets_path(settings_path: Path) -> Path:
    parser = configparser.ConfigParser(interpolation=None)
    parser.read(settings_path, encoding="utf-8-sig")
    raw = parser.get("environment", "secrets_file", fallback="").strip()
    if not raw:
        raise ValueError("settings.ini does not define environment.secrets_file")
    return Path(os.path.expandvars(os.path.expanduser(raw))).resolve()


def read_installed_environment(target: Path) -> tuple[dict[str, Any], dict[str, str]]:
    current: dict[str, Any] = {}
    secrets: dict[str, str] = {}
    settings_path = target / "config" / "settings.ini"
    if not settings_path.is_file():
        return current, secrets
    parser = configparser.ConfigParser(interpolation=None)
    parser.read(settings_path, encoding="utf-8-sig")

    def ini(dotted: str, section: str, key: str, kind: str = "str") -> None:
        if not parser.has_option(section, key):
            return
        raw = parser.get(section, key, fallback="")
        if kind == "bool":
            current[dotted] = parser.getboolean(section, key, fallback=False)
        elif kind == "int":
            try:
                current[dotted] = int(raw.strip())
            except ValueError:
                pass
        else:
            value = raw.strip()
            if value:
                current[dotted] = value

    for key in ("documents_root", "workspace_root", "temp_root"):
        ini(f"paths.{key}", "paths", key)
    for key in ("current_machine", "current_domain", "ollama_host", "norm_host", "activity_host", "postgres_host", "redis_host"):
        ini(f"network.{key}", "network", key)
    ini("network.require_tailscale", "network", "require_tailscale", "bool")
    for key in ("ollama_port", "norm_port", "activity_port", "postgres_port", "redis_port"):
        ini(f"network.{key}", "network", key, "int")
    ini("ssh.enabled", "ssh", "enabled", "bool")
    ini("ssh.user", "ssh", "ca8d_user")
    ini("ssh.remote_host", "ssh", "ca8d_host")
    ini("ssh.docker_host", "ssh", "ca8d_docker_host")
    ini("ssh.port", "ssh", "ca8d_port", "int")
    ini("ssh.identity_file", "ssh", "ca8d_identity_file")

    postgres_env = {
        "user": "NORM_POSTGRES_USER",
        "database": "NORM_POSTGRES_DB",
        "schema": "NORM_POSTGRES_SCHEMA",
        "stocks_database": "NORM_STOCKS_DB",
    }
    if parser.has_section("postgres"):
        for key in postgres_env:
            ini(f"postgres.{key}", "postgres", key)

    try:
        env_values = _read_env_file(_resolve_secrets_path(settings_path))
    except Exception:
        env_values = {}
    if not parser.has_section("postgres"):
        for key, env_key in postgres_env.items():
            value = env_values.get(env_key, "").strip()
            if value:
                current[f"postgres.{key}"] = value
    for key in ("NORM_POSTGRES_PASSWORD", "NORM_ROTOR5_SECRET", "NORM_ROTOR5_PREVIOUS_SECRETS"):
        if key in env_values:
            secrets[key] = env_values[key]

    runtime_path = target / "config" / "runtime.json"
    if runtime_path.is_file():
        try:
            runtime = json.loads(runtime_path.read_text(encoding="utf-8-sig"))
        except Exception:
            runtime = {}
        tools = runtime.get("tools", {}) if isinstance(runtime, dict) else {}
        if isinstance(tools, dict):
            allowed = tools.get("allowed_roots")
            if isinstance(allowed, list):
                current["runtime.allowed_roots"] = [str(item) for item in allowed]
            storage = tools.get("storage_context")
            if isinstance(storage, dict):
                for key in ("primary_name", "primary_root", "backup_name"):
                    if key in storage and str(storage[key]).strip():
                        current[f"runtime.storage_context.{key}"] = str(storage[key]).strip()

    map_path = target / "config" / "network-map.json"
    if map_path.is_file():
        try:
            network_map = json.loads(map_path.read_text(encoding="utf-8-sig"))
        except Exception:
            network_map = {}
        if isinstance(network_map, dict):
            for key in ("never_probe_name_patterns", "never_probe_cidrs", "targets"):
                value = network_map.get(key)
                if isinstance(value, list):
                    current[f"runtime.network_map.{key}"] = value
    return current, secrets


def read_installed_imprint_baseline(target: Path) -> dict[str, Any] | None:
    # The installed public imprint is the historical package default. Older
    # installs may not have one; in that case the old default is intentionally absent.
    public_path = target / "norm-imprint.json"
    if public_path.is_file():
        try:
            data = json.loads(public_path.read_text(encoding="utf-8-sig"))
            if isinstance(data, dict):
                return deep_copy(data)
        except Exception:
            pass
    path = target / ".norm-install-state.json"
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:
        return None
    baseline = data.get("package_imprint_baseline") if isinstance(data, dict) else None
    return deep_copy(baseline) if isinstance(baseline, dict) else None


def resolve_environment_prefill(
    target: Path,
    imprint: dict[str, Any],
    raw_imprint: dict[str, Any],
    default_imprint: dict[str, Any],
    previous_imprint: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, str], dict[str, str]]:
    resolved = deep_copy(imprint)
    current, secrets = read_installed_environment(target)
    old_imprint = read_installed_imprint_baseline(target)
    if old_imprint is None:
        old_imprint = deep_copy(previous_imprint or {})
    origins: dict[str, str] = {}
    for dotted in FIELD_PATHS:
        new_has = nested_has(raw_imprint, dotted)
        old_has = nested_has(old_imprint, dotted)
        if dotted in current:
            current_value = current[dotted]
            old_default = nested_get(old_imprint, dotted) if old_has else None
            customized = (not old_has) or current_value != old_default
            if customized:
                value, origin = current_value, "current-custom"
            elif new_has:
                value, origin = nested_get(raw_imprint, dotted), "private-imprint"
            else:
                value, origin = nested_get(default_imprint, dotted), "package-imprint"
        elif new_has:
            value, origin = nested_get(raw_imprint, dotted), "private-imprint"
        else:
            value, origin = nested_get(default_imprint, dotted), "package-imprint"
        nested_set(resolved, dotted, value)
        origins[dotted] = origin
    if "runtime.network_map.targets" in current:
        nested_set(resolved, "runtime.network_map.targets", current["runtime.network_map.targets"])
    return resolved, secrets, origins

def connection_host(raw: str, machine: str, domain: str) -> str:
    value = str(raw or "").strip()
    lowered = value.lower()
    if lowered in {"loopback", "localhost", "127.0.0.1"}:
        return "127.0.0.1"
    if lowered in {"current", "tailscale"}:
        return f"{machine}.{domain}".strip(".")
    return value


def _http_probe(host: str, port: int, path: str, timeout: float = 2.5) -> tuple[str, str]:
    try:
        with urllib.request.urlopen(f"http://{host}:{port}{path}", timeout=timeout) as response:
            return "ok", f"HTTP {response.status}"
    except urllib.error.HTTPError as exc:
        return "partial", f"HTTP endpoint reachable ({exc.code})"
    except Exception as exc:
        return "error", str(exc)


def _redis_probe(host: str, port: int, timeout: float = 2.5) -> tuple[str, str]:
    try:
        with socket.create_connection((host, port), timeout=timeout) as sock:
            sock.settimeout(timeout)
            sock.sendall(b"*1\r\n$4\r\nPING\r\n")
            reply = sock.recv(128)
        if reply.startswith(b"+PONG"):
            return "ok", "PING/PONG"
        if reply:
            return "partial", reply.decode("utf-8", errors="replace").strip()[:80]
        return "partial", "TCP connected; no Redis reply"
    except Exception as exc:
        return "error", str(exc)


def _postgres_probe(host: str, port: int, user: str, password: str, database: str, python_candidates: list[Path], timeout: float = 3.0) -> tuple[str, str]:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            pass
    except Exception as exc:
        return "error", f"TCP failed: {exc}"
    if not password:
        return "partial", "TCP reachable; password blank, auth not tested"
    script = (
        "import json,sys\n"
        "try:\n import psycopg\nexcept Exception:\n raise SystemExit(3)\n"
        "d=json.load(sys.stdin)\n"
        "try:\n c=psycopg.connect(host=d['host'],port=d['port'],dbname=d['database'],user=d['user'],password=d['password'],connect_timeout=3); c.close()\n"
        "except Exception:\n raise SystemExit(4)\n"
    )
    payload = json.dumps({"host": host, "port": port, "database": database, "user": user, "password": password})
    saw_python = saw_psycopg = False
    for candidate in python_candidates:
        candidate = Path(candidate)
        if not candidate.is_file():
            continue
        saw_python = True
        cp = subprocess.run([str(candidate), "-c", script], input=payload, text=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=8, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0)
        if cp.returncode == 0:
            return "ok", "authenticated"
        if cp.returncode != 3:
            saw_psycopg = True
    if saw_psycopg:
        return "error", "TCP reachable; PostgreSQL authentication failed"
    if saw_python:
        return "partial", "TCP reachable; psycopg unavailable, auth not tested"
    return "partial", "TCP reachable; no usable Python found for auth test"


def test_environment_connections(data: dict[str, Any], secrets: dict[str, str], python_candidates: list[Path]) -> list[tuple[str, str, str]]:
    network = data["network"]
    machine, domain = str(network["current_machine"]), str(network["current_domain"])
    host = lambda key: connection_host(str(network[key]), machine, domain)
    results: list[tuple[str, str, str]] = []
    for label, host_key, port_key, path in (
        ("Ollama", "ollama_host", "ollama_port", "/api/tags"),
        ("Norm HTTP", "norm_host", "norm_port", "/health"),
        ("Activity", "activity_host", "activity_port", "/health"),
    ):
        status, detail = _http_probe(host(host_key), int(network[port_key]), path)
        results.append((label, status, detail))
    status, detail = _redis_probe(host("redis_host"), int(network["redis_port"]))
    results.append(("Redis", status, detail))
    pg = data["postgres"]
    status, detail = _postgres_probe(host("postgres_host"), int(network["postgres_port"]), str(pg["user"]), str(secrets.get("NORM_POSTGRES_PASSWORD", "")), str(pg["database"]), python_candidates)
    results.append(("PostgreSQL", status, detail))
    return results
