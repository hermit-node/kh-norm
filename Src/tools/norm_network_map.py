from __future__ import annotations

import argparse
import fnmatch
import http.client
import ipaddress
import json
import os
import shutil
import socket
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.request
from urllib.parse import urlparse
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DEFAULT_CONFIG = {
    "schema": 1,
    "never_probe_name_patterns": [
        "*decoy*",
        "*honeypot*",
        "*mesh-canary*",
        "*mesh-cache*",
        "*decoy-guard*",
    ],
    "never_probe_cidrs": [],
    "targets": [
        {
            "name": "NORM-HOST",
            "host": "norm-host.example.invalid",
            "aliases": ["norm-host"],
            "probes": [
                {"kind": "tcp", "port": 22, "timeout_seconds": 1.5},
                {"kind": "http", "url": "http://norm-host.example.invalid:12543/health", "timeout_seconds": 2.0},
                {"kind": "http", "url": "http://norm-host.example.invalid:8766/health", "timeout_seconds": 2.0},
            ],
        },
        {
            "name": "REMOTE-HOST",
            "host": "remote-host.example.invalid",
            "aliases": ["remote-host"],
            "probes": [
                {"kind": "tcp", "port": 22, "timeout_seconds": 1.5}
            ],
        },
        {
            "name": "remote-host-docker",
            "host": "docker-host.example.invalid",
            "aliases": ["remote-host-docker"],
            "probes": [
                {"kind": "tcp", "port": 53, "timeout_seconds": 1.5},
                {"kind": "tcp", "port": 443, "timeout_seconds": 1.5},
                {"kind": "http", "url": "http://docker-host.example.invalid:17244/api/health", "timeout_seconds": 2.0},
            ],
        },
    ],
}


class NetworkMapError(RuntimeError):
    pass


class ProbePolicyError(NetworkMapError):
    pass


@dataclass(frozen=True)
class ProbeDecision:
    allowed: bool
    reason: str


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _runtime_root() -> Path:
    here = Path(__file__).resolve()
    return here.parent.parent


def _config_path(runtime_root: Path | None = None) -> Path:
    root = runtime_root or _runtime_root()
    return root / "config" / "network-map.json"


def load_config(runtime_root: Path | None = None) -> dict[str, Any]:
    path = _config_path(runtime_root)
    if not path.exists():
        return json.loads(json.dumps(DEFAULT_CONFIG))
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise NetworkMapError(f"network-map config must be a JSON object: {path}")
    merged = json.loads(json.dumps(DEFAULT_CONFIG))
    merged.update(raw)
    if not isinstance(merged.get("targets"), list):
        raise NetworkMapError("network-map targets must be a list")
    if not isinstance(merged.get("never_probe_name_patterns"), list):
        raise NetworkMapError("never_probe_name_patterns must be a list")
    if not isinstance(merged.get("never_probe_cidrs"), list):
        raise NetworkMapError("never_probe_cidrs must be a list")
    return merged


def _tailscale_executable() -> str:
    found = shutil.which("tailscale")
    if found:
        return found

    candidates: list[Path] = []
    for env_name in ("ProgramFiles", "ProgramW6432", "LOCALAPPDATA"):
        value = os.environ.get(env_name)
        if value:
            candidates.append(Path(value) / "Tailscale" / "tailscale.exe")

    for candidate in candidates:
        if candidate.exists():
            return str(candidate)

    raise NetworkMapError("tailscale CLI not found")


def read_tailscale_status(timeout_seconds: float = 5.0) -> dict[str, Any]:
    exe = _tailscale_executable()
    completed = subprocess.run(
        [exe, "status", "--json"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout_seconds,
        check=False,
    )
    if completed.returncode != 0:
        message = completed.stderr.strip() or completed.stdout.strip() or f"exit {completed.returncode}"
        raise NetworkMapError(f"tailscale status --json failed: {message}")
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise NetworkMapError(f"tailscale status returned invalid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise NetworkMapError("tailscale status JSON root is not an object")
    return payload


def _normalized_name(value: Any) -> str:
    text = str(value or "").strip().lower()
    return text[:-1] if text.endswith(".") else text


def _all_identity_strings(node: dict[str, Any]) -> list[str]:
    values: list[str] = []
    for key in (
        "HostName",
        "DNSName",
        "ComputedName",
        "ComputedNameWithHost",
        "host_name",
        "dns_name",
    ):
        value = node.get(key)
        if value:
            values.append(_normalized_name(value))
    for key in ("TailscaleIPs", "tailscale_ips"):
        for ip in node.get(key) or []:
            if ip:
                values.append(str(ip).strip())
    return values


def _matches_never_probe(node_or_host: dict[str, Any] | str, config: dict[str, Any]) -> ProbeDecision:
    if isinstance(node_or_host, dict):
        identities = _all_identity_strings(node_or_host)
    else:
        identities = [_normalized_name(node_or_host)]

    patterns = [str(p).strip().lower() for p in config.get("never_probe_name_patterns") or [] if str(p).strip()]
    for identity in identities:
        for pattern in patterns:
            if fnmatch.fnmatch(identity, pattern):
                return ProbeDecision(False, f"name matches never-probe pattern {pattern!r}")

    cidrs: list[ipaddress._BaseNetwork] = []
    for raw in config.get("never_probe_cidrs") or []:
        try:
            cidrs.append(ipaddress.ip_network(str(raw), strict=False))
        except ValueError as exc:
            raise NetworkMapError(f"invalid never_probe_cidrs entry {raw!r}: {exc}") from exc

    for identity in identities:
        try:
            addr = ipaddress.ip_address(identity)
        except ValueError:
            continue
        for network in cidrs:
            if addr in network:
                return ProbeDecision(False, f"address belongs to never-probe CIDR {network}")

    return ProbeDecision(True, "not forbidden")


def _never_probe_networks(config: dict[str, Any]) -> list[ipaddress._BaseNetwork]:
    networks: list[ipaddress._BaseNetwork] = []
    for raw in config.get("never_probe_cidrs") or []:
        try:
            networks.append(ipaddress.ip_network(str(raw), strict=False))
        except ValueError as exc:
            raise NetworkMapError(f"invalid never_probe_cidrs entry {raw!r}: {exc}") from exc
    return networks


def _address_probe_decision(address: str, config: dict[str, Any]) -> ProbeDecision:
    try:
        addr = ipaddress.ip_address(str(address).split("%", 1)[0])
    except ValueError:
        return ProbeDecision(False, f"resolved address is invalid: {address!r}")
    for network in _never_probe_networks(config):
        if addr in network:
            return ProbeDecision(False, f"resolved address {addr} belongs to never-probe CIDR {network}")
    return ProbeDecision(True, "resolved address allowed")


def _resolve_probe_addresses(host: str, port: int, config: dict[str, Any]) -> list[str]:
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise NetworkMapError(f"cannot resolve probe target {host!r}: {type(exc).__name__}: {exc}") from exc

    addresses: list[str] = []
    for info in infos:
        sockaddr = info[4]
        if not sockaddr:
            continue
        address = str(sockaddr[0])
        if address not in addresses:
            addresses.append(address)

    if not addresses:
        raise NetworkMapError(f"probe target {host!r} resolved to no TCP addresses")

    for address in addresses:
        decision = _address_probe_decision(address, config)
        if not decision.allowed:
            raise ProbePolicyError(
                f"active probing prohibited for {host!r}: {decision.reason}"
            )
    return addresses


def _target_aliases(target: dict[str, Any]) -> set[str]:
    aliases = {
        _normalized_name(target.get("name")),
        _normalized_name(target.get("host")),
    }
    for alias in target.get("aliases") or []:
        aliases.add(_normalized_name(alias))
    return {item for item in aliases if item}


def _node_matches_target(node: dict[str, Any], target: dict[str, Any]) -> bool:
    aliases = _target_aliases(target)
    identities = set(_all_identity_strings(node))
    for identity in identities:
        if identity in aliases:
            return True
        for alias in aliases:
            if alias and identity.startswith(alias + "."):
                return True
    return False


def _safe_probe_target(target: dict[str, Any], config: dict[str, Any]) -> ProbeDecision:
    host = str(target.get("host") or "").strip()
    if not host:
        return ProbeDecision(False, "target has no host")
    decision = _matches_never_probe(host, config)
    if not decision.allowed:
        return decision
    for alias in _target_aliases(target):
        decision = _matches_never_probe(alias, config)
        if not decision.allowed:
            return decision
    return ProbeDecision(True, "explicitly configured target")


def _probe_tcp(host: str, port: int, timeout_seconds: float, config: dict[str, Any]) -> dict[str, Any]:
    started = time.perf_counter()
    try:
        addresses = _resolve_probe_addresses(host, port, config)
        last_error: OSError | None = None
        for address in addresses:
            try:
                with socket.create_connection((address, port), timeout=timeout_seconds):
                    elapsed_ms = round((time.perf_counter() - started) * 1000, 1)
                    return {
                        "ok": True,
                        "kind": "tcp",
                        "host": host,
                        "resolved_ip": address,
                        "port": port,
                        "latency_ms": elapsed_ms,
                    }
            except OSError as exc:
                last_error = exc
        if last_error is None:
            raise OSError("no resolved addresses were attempted")
        raise last_error
    except Exception as exc:
        elapsed_ms = round((time.perf_counter() - started) * 1000, 1)
        return {
            "ok": False,
            "kind": "tcp",
            "host": host,
            "port": port,
            "latency_ms": elapsed_ms,
            "error": f"{type(exc).__name__}: {exc}",
        }


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(
        self,
        logical_host: str,
        resolved_ip: str,
        port: int,
        timeout: float,
        context: ssl.SSLContext | None = None,
    ) -> None:
        super().__init__(logical_host, port=port, timeout=timeout, context=context)
        self._resolved_ip = resolved_ip

    def connect(self) -> None:
        raw = socket.create_connection((self._resolved_ip, self.port), self.timeout, self.source_address)
        if self._tunnel_host:
            self.sock = raw
            self._tunnel()
        self.sock = self._context.wrap_socket(raw, server_hostname=self.host)


def _probe_http(url: str, timeout_seconds: float, config: dict[str, Any]) -> dict[str, Any]:
    started = time.perf_counter()
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise NetworkMapError(f"invalid HTTP probe URL: {url}")

    logical_host = parsed.hostname
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    addresses = _resolve_probe_addresses(logical_host, port, config)
    resolved_ip = addresses[0]
    path = parsed.path or "/"
    if parsed.query:
        path += "?" + parsed.query

    default_port = 443 if parsed.scheme == "https" else 80
    host_header = logical_host if port == default_port else f"{logical_host}:{port}"
    connection: http.client.HTTPConnection
    if parsed.scheme == "https":
        connection = _PinnedHTTPSConnection(
            logical_host=logical_host,
            resolved_ip=resolved_ip,
            port=port,
            timeout=timeout_seconds,
            context=ssl.create_default_context(),
        )
    else:
        connection = http.client.HTTPConnection(resolved_ip, port=port, timeout=timeout_seconds)

    try:
        connection.request(
            "GET",
            path,
            headers={
                "User-Agent": "Norm-Network-Map/0.53.19",
                "Host": host_header,
                "Connection": "close",
            },
        )
        response = connection.getresponse()
        body = response.read(4096)
        elapsed_ms = round((time.perf_counter() - started) * 1000, 1)
        return {
            "ok": 200 <= int(response.status) < 500,
            "kind": "http",
            "url": url,
            "resolved_ip": resolved_ip,
            "status": int(response.status),
            "latency_ms": elapsed_ms,
            "body_preview": body.decode("utf-8", errors="replace")[:300],
            "redirect_followed": False,
        }
    except Exception as exc:
        elapsed_ms = round((time.perf_counter() - started) * 1000, 1)
        return {
            "ok": False,
            "kind": "http",
            "url": url,
            "resolved_ip": resolved_ip,
            "latency_ms": elapsed_ms,
            "error": f"{type(exc).__name__}: {exc}",
            "redirect_followed": False,
        }
    finally:
        connection.close()


def _run_probe(target: dict[str, Any], probe: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    decision = _safe_probe_target(target, config)
    if not decision.allowed:
        raise ProbePolicyError(f"active probing prohibited for {target.get('name') or target.get('host')}: {decision.reason}")

    host = str(target.get("host") or "").strip()
    kind = str(probe.get("kind") or "").strip().lower()
    timeout_seconds = float(probe.get("timeout_seconds") or 1.5)

    if kind == "tcp":
        port = int(probe["port"])
        return _probe_tcp(host, port, timeout_seconds, config)
    if kind == "http":
        url = str(probe.get("url") or "").strip()
        if not url:
            raise NetworkMapError(f"HTTP probe for {target.get('name')} has no URL")
        # Validate the URL host independently before any network activity.
        parsed = urlparse(url)
        if not parsed.hostname:
            raise NetworkMapError(f"invalid HTTP probe URL: {url}")
        url_decision = _matches_never_probe(parsed.hostname, config)
        if not url_decision.allowed:
            raise ProbePolicyError(f"HTTP probe URL is forbidden: {url_decision.reason}")
        allowed_aliases = _target_aliases(target)
        parsed_host = _normalized_name(parsed.hostname)
        if parsed_host not in allowed_aliases and parsed_host != _normalized_name(host):
            raise ProbePolicyError(
                f"HTTP probe host {parsed.hostname!r} is not the configured target host/alias"
            )
        return _probe_http(url, timeout_seconds, config)

    raise NetworkMapError(f"unsupported probe kind {kind!r}")


def _peer_summary(node: dict[str, Any], config: dict[str, Any], targets: list[dict[str, Any]]) -> dict[str, Any]:
    never = _matches_never_probe(node, config)
    matched_target = next((target for target in targets if _node_matches_target(node, target)), None)

    if not never.allowed:
        policy = "never"
        policy_reason = never.reason
    elif matched_target is not None:
        policy = "approved"
        policy_reason = "explicitly configured active-probe target"
    else:
        policy = "passive_only"
        policy_reason = "observed from Tailscale; not explicitly allowlisted for active probing"

    return {
        "host_name": node.get("HostName"),
        "dns_name": node.get("DNSName"),
        "tailscale_ips": list(node.get("TailscaleIPs") or []),
        "online": node.get("Online"),
        "active": node.get("Active"),
        "exit_node": node.get("ExitNode"),
        "exit_node_capable": node.get("ExitNodeOption"),
        "relay": node.get("Relay"),
        "current_address": node.get("CurAddr"),
        "tx_bytes": node.get("TxBytes"),
        "rx_bytes": node.get("RxBytes"),
        "observed": True,
        "source": "tailscale_status",
        "probe_policy": policy,
        "probe_policy_reason": policy_reason,
        "probe_performed": False,
        "probes": [],
    }


def build_network_map(runtime_root: Path | None = None) -> dict[str, Any]:
    config = load_config(runtime_root)
    targets = [target for target in config.get("targets") or [] if isinstance(target, dict)]

    result: dict[str, Any] = {
        "schema": 1,
        "source": "norm",
        "generated_at": _utc_now(),
        "tailscale": {
            "available": False,
            "backend_state": None,
            "error": None,
        },
        "nodes": [],
        "targets": [],
        "safety": {
            "active_probe_mode": "explicit_allowlist_only",
            "broad_discovery": False,
            "subnet_sweeps": False,
            "port_scans": False,
            "never_probe_name_patterns": list(config.get("never_probe_name_patterns") or []),
            "never_probe_cidrs": list(config.get("never_probe_cidrs") or []),
        },
    }

    try:
        status = read_tailscale_status()
        result["tailscale"]["available"] = True
        result["tailscale"]["backend_state"] = status.get("BackendState")

        self_node = status.get("Self")
        if isinstance(self_node, dict):
            self_summary = _peer_summary(self_node, config, targets)
            self_summary["self"] = True
            result["nodes"].append(self_summary)

        peers = status.get("Peer") or {}
        peer_values = peers.values() if isinstance(peers, dict) else peers if isinstance(peers, list) else []
        for peer in peer_values:
            if isinstance(peer, dict):
                summary = _peer_summary(peer, config, targets)
                summary["self"] = False
                result["nodes"].append(summary)
    except Exception as exc:
        result["tailscale"]["error"] = f"{type(exc).__name__}: {exc}"

    for target in targets:
        name = str(target.get("name") or target.get("host") or "unnamed")
        decision = _safe_probe_target(target, config)
        target_result: dict[str, Any] = {
            "name": name,
            "host": target.get("host"),
            "probe_policy": "approved" if decision.allowed else "never",
            "probe_policy_reason": decision.reason,
            "probe_performed": False,
            "probes": [],
        }

        if decision.allowed:
            for probe in target.get("probes") or []:
                if not isinstance(probe, dict):
                    continue
                try:
                    probe_result = _run_probe(target, probe, config)
                except Exception as exc:
                    probe_result = {
                        "ok": False,
                        "kind": str(probe.get("kind") or "unknown"),
                        "error": f"{type(exc).__name__}: {exc}",
                    }
                target_result["probes"].append(probe_result)
                target_result["probe_performed"] = True

        target_result["reachable"] = (
            any(bool(p.get("ok")) for p in target_result["probes"])
            if target_result["probe_performed"]
            else None
        )
        result["targets"].append(target_result)

        for node in result["nodes"]:
            if _node_matches_target(node, target):
                node["probes"] = list(target_result["probes"])
                node["probe_performed"] = bool(target_result["probe_performed"])
                node["reachable"] = target_result["reachable"]

    result["nodes"].sort(
        key=lambda n: (
            0 if n.get("self") else 1,
            str(n.get("host_name") or n.get("dns_name") or "").lower(),
        )
    )
    return result


def render_network_map(snapshot: dict[str, Any]) -> str:
    lines: list[str] = []
    ts = snapshot.get("tailscale") or {}
    lines.append(
        f"Norm network map — Tailscale: "
        f"{'available' if ts.get('available') else 'unavailable'}"
        + (f" ({ts.get('backend_state')})" if ts.get("backend_state") else "")
    )
    if ts.get("error"):
        lines.append(f"Tailscale telemetry error: {ts['error']}")

    nodes = snapshot.get("nodes") or []
    if nodes:
        lines.append("")
        lines.append("Observed nodes:")
        for node in nodes:
            name = node.get("host_name") or node.get("dns_name") or "(unnamed)"
            ips = ",".join(node.get("tailscale_ips") or []) or "-"
            policy = node.get("probe_policy")
            observed_state = "online" if node.get("online") is True else "offline" if node.get("online") is False else "unknown"
            if node.get("probe_performed"):
                reach = "reachable" if node.get("reachable") else "probe-failed"
            elif policy == "never":
                reach = "observed / NOT PROBED"
            else:
                reach = "observed-only"
            lines.append(f"  {name:<28} {ips:<24} {observed_state:<8} {policy:<12} {reach}")

    targets = snapshot.get("targets") or []
    if targets:
        lines.append("")
        lines.append("Approved active targets:")
        for target in targets:
            name = target.get("name") or target.get("host")
            if target.get("probe_policy") == "never":
                lines.append(f"  {name}: NEVER PROBE ({target.get('probe_policy_reason')})")
                continue
            probes = target.get("probes") or []
            if not probes:
                lines.append(f"  {name}: no probes configured")
                continue
            for probe in probes:
                kind = probe.get("kind")
                destination = probe.get("url") or (
                    f"{probe.get('host')}:{probe.get('port')}" if probe.get("host") else ""
                )
                state = "OK" if probe.get("ok") else "FAIL"
                detail = f"HTTP {probe.get('status')}" if probe.get("status") else probe.get("error") or ""
                lines.append(f"  {name}: {kind} {destination} -> {state} {detail}".rstrip())

    lines.append("")
    lines.append("Safety: passive Tailscale inventory + explicit allowlist probes only; no scans or subnet sweeps.")
    return "\n".join(lines)


def emit_network_map(json_output: bool = False, runtime_root: Path | None = None) -> int:
    snapshot = build_network_map(runtime_root=runtime_root)
    if json_output:
        print(json.dumps(snapshot, ensure_ascii=False, separators=(",", ":")))
    else:
        print(render_network_map(snapshot))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Norm passive network map with fail-closed probe policy")
    parser.add_argument("--json", action="store_true", help="emit one-line machine-readable JSON")
    parser.add_argument("--config", type=Path, help="alternate network-map.json path for testing")
    args = parser.parse_args(argv)

    if args.config:
        original = _config_path
        config_path = args.config.resolve()

        def _override(_: Path | None = None) -> Path:
            return config_path

        globals()["_config_path"] = _override

    return emit_network_map(json_output=args.json)


if __name__ == "__main__":
    raise SystemExit(main())
