from __future__ import annotations

import ipaddress
import re
import socket
from pathlib import Path
from urllib.parse import urlparse

_SESSION_RE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")


def validate_session_name(value: str) -> str:
    name = (value or "default").strip()
    if not _SESSION_RE.fullmatch(name):
        raise ValueError("session must contain only letters, numbers, dot, underscore, or hyphen (max 64 chars)")
    return name


def _is_forbidden_ip(value: str) -> bool:
    ip = ipaddress.ip_address(value)
    return (
        ip.is_loopback
        or ip.is_private
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


def validate_public_http_url(url: str) -> str:
    raw = str(url or "").strip()
    parsed = urlparse(raw)
    if parsed.scheme.lower() not in {"http", "https"}:
        raise ValueError("only http:// and https:// URLs are allowed")
    if not parsed.hostname:
        raise ValueError("URL must include a hostname")
    host = parsed.hostname.rstrip(".").lower()
    if host in {"localhost", "localhost.localdomain"} or host.endswith(".localhost"):
        raise PermissionError("localhost/private-network browsing is disabled in the web plugin")
    try:
        ipaddress.ip_address(host)
    except ValueError:
        try:
            infos = socket.getaddrinfo(host, parsed.port or (443 if parsed.scheme == "https" else 80), type=socket.SOCK_STREAM)
        except socket.gaierror as exc:
            raise ConnectionError(f"could not resolve host {host}: {exc}") from exc
        addresses = {info[4][0].split("%", 1)[0] for info in infos}
        if any(_is_forbidden_ip(addr) for addr in addresses):
            raise PermissionError("private/link-local/loopback destinations are disabled in the web plugin")
    else:
        if _is_forbidden_ip(host):
            raise PermissionError("private/link-local/loopback destinations are disabled in the web plugin")
    return raw


def safe_filename(value: str, fallback: str = "download.bin") -> str:
    name = Path(str(value or "")).name.strip().replace("\x00", "")
    name = re.sub(r"[<>:\\|?*\"\x00-\x1f]", "_", name).strip(" .")
    if not name:
        name = fallback
    return name[:180]
