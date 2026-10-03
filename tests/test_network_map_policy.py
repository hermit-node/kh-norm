from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import zipfile
from pathlib import Path
from unittest.mock import patch

HERE = Path(__file__).resolve().parents[1]
SOURCE_ZIP = HERE / "Norm-0.53.11-portable-source.zip"

with tempfile.TemporaryDirectory() as td_name:
    td = Path(td_name)
    with zipfile.ZipFile(SOURCE_ZIP, "r") as zf:
        member = "Norm-0.53.11/tools/norm_network_map.py"
        zf.extract(member, td)
    module_path = td / member
    spec = importlib.util.spec_from_file_location("norm_network_map_release_test", module_path)
    assert spec and spec.loader
    nm = importlib.util.module_from_spec(spec)
    sys.modules["norm_network_map_release_test"] = nm
    spec.loader.exec_module(nm)

    fake_status = {
        "BackendState": "Running",
        "Self": {
            "HostName": "norm-host",
            "DNSName": "norm-host.example.ts.net.",
            "TailscaleIPs": ["192.0.2.10"],
            "Online": True,
        },
        "Peer": {
            "approved": {
                "HostName": "remote-host",
                "DNSName": "remote-host.example.ts.net.",
                "TailscaleIPs": ["192.0.2.11"],
                "Online": True,
            },
            "decoy": {
                "HostName": "kh-mesh-cache",
                "DNSName": "kh-mesh-cache.example.ts.net.",
                "TailscaleIPs": ["192.0.2.12"],
                "Online": True,
            },
            "passive": {
                "HostName": "phone",
                "DNSName": "phone.example.ts.net.",
                "TailscaleIPs": ["192.0.2.13"],
                "Online": True,
            },
        },
    }

    config = {
        "schema": 1,
        "never_probe_name_patterns": ["*decoy*", "*honeypot*", "*mesh-cache*"],
        "never_probe_cidrs": [],
        "targets": [{
            "name": "remote-host",
            "host": "remote-host.example.ts.net",
            "aliases": ["remote-host"],
            "probes": [{"kind": "tcp", "port": 443}],
        }],
    }
    config_path = td / "network-map.json"
    config_path.write_text(json.dumps(config), encoding="utf-8")

    fake_probe = {
        "ok": True,
        "kind": "tcp",
        "host": "remote-host.example.ts.net",
        "port": 443,
    }

    with patch.object(nm, "_config_path", return_value=config_path), \
         patch.object(nm, "read_tailscale_status", return_value=fake_status), \
         patch.object(nm, "_run_probe", return_value=fake_probe) as run_probe:
        snapshot = nm.build_network_map()

    assert run_probe.call_count == 1
    decoy = next(node for node in snapshot["nodes"] if node["host_name"] == "kh-mesh-cache")
    passive = next(node for node in snapshot["nodes"] if node["host_name"] == "phone")
    approved = next(node for node in snapshot["nodes"] if node["host_name"] == "remote-host")
    assert decoy["observed"] is True
    assert decoy["probe_policy"] == "never"
    assert decoy["probe_performed"] is False
    assert passive["probe_policy"] == "passive_only"
    assert passive["probe_performed"] is False
    assert approved["probe_policy"] == "approved"
    assert approved["probe_performed"] is True

    forbidden_target = {
        "name": "decoy",
        "host": "kh-mesh-cache.example.ts.net",
        "probes": [{"kind": "tcp", "port": 80}],
    }
    decision = nm._safe_probe_target(forbidden_target, config)
    assert decision.allowed is False


    cidr_config = {
        "never_probe_name_patterns": [],
        "never_probe_cidrs": ["10.66.0.0/16"],
    }
    forbidden_info = [
        (nm.socket.AF_INET, nm.socket.SOCK_STREAM, 6, "", ("10.66.7.9", 443)),
    ]
    with patch.object(nm.socket, "getaddrinfo", return_value=forbidden_info):
        try:
            nm._resolve_probe_addresses("apparently-safe.example", 443, cidr_config)
        except nm.ProbePolicyError:
            pass
        else:
            raise AssertionError("hostname resolving into never-probe CIDR was not rejected")

    class FakeResponse:
        status = 302
        def read(self, _limit):
            return b"redirect intentionally not followed"

    class FakeConnection:
        instances = 0
        def __init__(self, *args, **kwargs):
            type(self).instances += 1
            self.requests = []
        def request(self, method, path, headers=None):
            self.requests.append((method, path, headers))
        def getresponse(self):
            return FakeResponse()
        def close(self):
            return None

    redirect_config = {
        "never_probe_name_patterns": ["*decoy*"],
        "never_probe_cidrs": [],
    }
    with patch.object(nm, "_resolve_probe_addresses", return_value=["203.0.113.7"]), \
         patch.object(nm.http.client, "HTTPConnection", FakeConnection):
        redirect_result = nm._probe_http("http://allowed.example/health", 1.0, redirect_config)

    assert redirect_result["status"] == 302
    assert redirect_result["redirect_followed"] is False
    assert FakeConnection.instances == 1

print("PASS: passive peers do not widen active probe set")
print("PASS: honeypot/decoy node observed but never probed")
print("PASS: only explicit target probed")
print("PASS: DNS resolution into never-probe CIDR rejected")
print("PASS: HTTP probes do not follow redirects")
