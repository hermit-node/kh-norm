from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import sys
import tempfile
import time
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("norm_installer_tested", HERE / "Norm-Installer.py")
assert SPEC and SPEC.loader
m = importlib.util.module_from_spec(SPEC)
sys.modules["norm_installer_tested"] = m
SPEC.loader.exec_module(m)


def make_pkg(folder: Path, version: str) -> Path:
    path = folder / f"Norm-{version}-portable-source.zip"
    manifest = {
        "package_schema": 1,
        "name": "Norm",
        "version": version,
        "package_type": "portable-source",
        "source_dir": "core",
        "entrypoint": "core/norm_main.py",
        "settings": "config/settings.ini",
        "runtime_config": "config/runtime.json",
        "requirements_lock": "tools/requirements-lock.txt",
        "build_script": "tools/build_norm.py",
    }
    root = f"Norm-{version}"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr(f"{root}/package-manifest.json", json.dumps(manifest))
        zf.writestr(f"{root}/tools/requirements-lock.txt", "aiohttp==3.14.3\nredis==8.1.0\n")
        zf.writestr(f"{root}/core/norm_main.py", "print('ok')\n")
        zf.writestr(
            f"{root}/config/settings.ini",
            "[environment]\npython_version=3.14\nvenv_path=.venv\n",
        )
        zf.writestr(f"{root}/config/runtime.json", "{}\n")
        zf.writestr(f"{root}/tools/build_norm.py", "print('build')\n")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    (folder / (path.name + ".sha256")).write_text(f"{digest}  {path.name}\n", encoding="utf-8")
    return path


with tempfile.TemporaryDirectory() as td_name:
    td = Path(td_name)
    older = make_pkg(td, "0.53.8")
    newest = make_pkg(td, "0.53.9")
    now = time.time()
    os.utime(newest, (now - 100, now - 100))
    os.utime(older, (now, now))
    found = m.find_latest_source(td)
    assert found == newest
    assert m.inspect_package(found).version == "0.53.9"
    m._verify_companion_sha256(found)
    newest.write_bytes(newest.read_bytes() + b"x")
    try:
        m._verify_companion_sha256(newest)
    except m.InstallerError:
        pass
    else:
        raise AssertionError("checksum mismatch was not rejected")

valid = m.validate_imprint({
    "schema": 1,
    "network": {"current_machine": "test-host", "norm_port": 13000},
})
assert valid["network"]["current_machine"] == "test-host"
assert valid["network"]["norm_port"] == 13000
assert valid["network"]["redis_port"] == 6379

try:
    m.validate_imprint({"schema": 1, "postgres": {"password": "do-not-accept"}})
except m.InstallerError:
    pass
else:
    raise AssertionError("secret-like imprint key was accepted")

with tempfile.TemporaryDirectory() as td_name:
    td = Path(td_name)
    saved = td / "norm-imprint.local.json"
    m.save_local_imprint(valid, saved)
    text = saved.read_text(encoding="utf-8")
    assert "password" not in text.lower()
    loaded, loaded_path = m.load_imprint(saved)
    assert loaded_path == saved.resolve()
    assert loaded["network"]["current_machine"] == "test-host"

with tempfile.TemporaryDirectory() as td_name:
    td = Path(td_name)
    target = td / "Norm"
    (target / "config").mkdir(parents=True)
    secrets_path = td / "secrets.env"
    settings = f"""[paths]
runtime_root = .
documents_root = {td.as_posix()}/docs
workspace_root = workspace
temp_root = temp
verbatim_writer = plugins/verbatim_lines/_cli.py

[network]
current_machine = norm-host
current_domain = example.invalid
require_tailscale = false
ollama_host = loopback
ollama_port = 11434
norm_host = loopback
norm_port = 12543
activity_host = loopback
activity_port = 8766
postgres_host = loopback
postgres_port = 5432
redis_host = loopback
redis_port = 6379

[ssh]
enabled = false
ca8d_host =
ca8d_docker_host =
ca8d_port = 22
ca8d_user =
ca8d_identity_file = key

[environment]
secrets_file = {secrets_path.as_posix()}
"""
    (target / "config" / "settings.ini").write_text(settings, encoding="utf-8")
    (target / "config" / "runtime.json").write_text(
        json.dumps({"tools": {"allowed_roots": [str(td)]}}),
        encoding="utf-8",
    )
    logs: list[str] = []
    imprint = m.validate_imprint({
        "schema": 1,
        "network": {
            "current_machine": "imprinted-host",
            "current_domain": "tail.example",
            "postgres_host": "db.tail.example",
            "postgres_port": 55432,
        },
        "postgres": {
            "user": "normuser",
            "database": "normdb",
            "schema": "norm_runtime",
            "stocks_database": "stocks",
        },
    })
    m._apply_imprint(
        target,
        {"settings": "config/settings.ini", "runtime_config": "config/runtime.json"},
        imprint,
        {"NORM_POSTGRES_PASSWORD": "test-only-password"},
        log=logs.append,
    )
    secrets_text = secrets_path.read_text(encoding="utf-8")
    assert "NORM_POSTGRES_PASSWORD=test-only-password" in secrets_text
    assert all("test-only-password" not in line for line in logs)
    installed_settings = (target / "config" / "settings.ini").read_text(encoding="utf-8")
    assert "current_machine = imprinted-host" in installed_settings
    assert "postgres_port = 55432" in installed_settings

assert m.INSTALLER_VERSION == "1.6.0-unified"
assert "imprint" in m.InstallOptions.__dataclass_fields__
assert "secret_values" in m.InstallOptions.__dataclass_fields__

print("PASS: version-first package discovery")
print("PASS: companion SHA verification/rejection")
print("PASS: imprint merge and save")
print("PASS: secret-like imprint keys rejected")
print("PASS: secret values separated from imprint/log")
print("PASS: installer version 1.6.0")
