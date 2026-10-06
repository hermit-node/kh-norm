from __future__ import annotations

import base64
import configparser
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


def runtime_root() -> Path:
    return Path(__file__).resolve().parents[3]


def settings() -> configparser.ConfigParser:
    parser = configparser.ConfigParser(interpolation=None)
    parser.read(runtime_root() / "config" / "settings.ini", encoding="utf-8-sig")
    return parser


def _env_file() -> dict[str, str]:
    parser = settings()
    raw = parser.get("environment", "secrets_file", fallback="").strip()
    if not raw:
        return {}
    expanded = os.path.expandvars(os.path.expanduser(raw))
    path = Path(expanded)
    out: dict[str, str] = {}
    if not path.is_file():
        return out
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        out[k.strip()] = v.strip()
    return out


def secret(name: str) -> str:
    return os.environ.get(name, "") or _env_file().get(name, "")


def remote(name: str) -> dict[str, Any]:
    p = settings()
    section = "remote_agents"
    enabled = p.getboolean(section, f"{name}_enabled", fallback=False)
    host = p.get(section, f"{name}_host", fallback="").strip()
    port = p.getint(section, f"{name}_port", fallback={"jan": 1337, "vane": 7789, "opencode": 4096}[name])
    scheme = p.get(section, f"{name}_scheme", fallback="http").strip() or "http"
    return {"enabled": enabled, "host": host, "port": port, "scheme": scheme, "base": f"{scheme}://{host}:{port}" if host else ""}


def model_config() -> dict[str, Any]:
    p = settings()
    s = "model_provider"
    return {
        "chat_base_url": p.get(s, "chat_base_url", fallback="http://127.0.0.1:8080/v1").rstrip("/"),
        "primary_model": p.get(s, "primary_model", fallback="").strip(),
        "secondary_model": p.get(s, "secondary_model", fallback="").strip(),
        "embedding_base_url": p.get(s, "embedding_base_url", fallback="http://127.0.0.1:11434/v1").rstrip("/"),
        "embedding_model": p.get(s, "embedding_model", fallback="nomic-embed-text").strip(),
        "timeout_seconds": p.getint(s, "timeout_seconds", fallback=180),
    }


def resolve_model(value: str) -> str:
    raw = str(value or "").strip()
    cfg = model_config()
    if not raw or raw.lower() == "primary":
        return str(cfg["primary_model"])
    if raw.lower() == "secondary":
        return str(cfg["secondary_model"] or cfg["primary_model"])
    return raw


def request_json(url: str, *, method: str = "GET", body: Any = None, headers: dict[str, str] | None = None, timeout: int = 30) -> Any:
    data = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
    req_headers = {"Accept": "application/json"}
    if body is not None:
        req_headers["Content-Type"] = "application/json"
    if headers:
        req_headers.update(headers)
    req = urllib.request.Request(url, data=data, headers=req_headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=max(1, min(int(timeout), 900))) as resp:
            raw = resp.read()
            if not raw:
                return None
            return json.loads(raw.decode("utf-8", errors="replace"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[-4000:]
        raise RuntimeError(f"HTTP {exc.code} from {url}: {detail}") from exc
    except Exception as exc:
        raise RuntimeError(f"Request failed for {url}: {exc}") from exc


def bearer(secret_name: str) -> dict[str, str]:
    token = secret(secret_name).strip()
    return {"Authorization": f"Bearer {token}"} if token else {}


def basic(username: str, password_secret_name: str) -> dict[str, str]:
    password = secret(password_secret_name)
    if not password:
        return {}
    encoded = base64.b64encode(f"{username}:{password}".encode()).decode()
    return {"Authorization": f"Basic {encoded}"}
