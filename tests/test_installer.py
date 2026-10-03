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
sys.path.insert(0, str(HERE))
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
        "imprint": "norm-imprint.json",
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
        zf.writestr(f"{root}/norm-imprint.json", json.dumps({"schema": 1, "postgres": {"database": "postgres"}}))
        zf.writestr(f"{root}/tools/build_norm.py", "print('build')\n")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    (folder / (path.name + ".sha256")).write_text(f"{digest}  {path.name}\n", encoding="utf-8")
    return path


with tempfile.TemporaryDirectory() as td_name:
    td = Path(td_name)
    older = make_pkg(td, "0.53.8")
    newest = make_pkg(td, "0.53.11")
    now = time.time()
    os.utime(newest, (now - 100, now - 100))
    os.utime(older, (now, now))
    found = m.find_latest_source(td)
    assert found == newest
    info = m.inspect_package(found)
    assert info.version == "0.53.11"
    assert m.read_package_public_imprint(info)["postgres"]["database"] == "postgres"
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
verbatim_writer = plugins/verbatim_lines/src/_cli.py

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
    assert "NORM_POSTGRES_USER=normuser" in secrets_text
    assert "[postgres]" in installed_settings
    assert "user = normuser" in installed_settings
    assert "database = normdb" in installed_settings
    assert "stocks_database = stocks" in installed_settings

assert m.INSTALLER_VERSION == "1.6.5-unified"
assert "imprint" in m.InstallOptions.__dataclass_fields__
assert "secret_values" in m.InstallOptions.__dataclass_fields__

print("PASS: version-first package discovery")
print("PASS: companion SHA verification/rejection")
print("PASS: imprint merge and save")
print("PASS: secret-like imprint keys rejected")
print("PASS: secret values separated from imprint/log")
print("PASS: installer version 1.6.5")

# Plugin root is persistent, but each package-managed built-in is mirrored independently.
package_manifest = json.loads((HERE / "src" / "Norm" / "package-manifest.json").read_text(encoding="utf-8"))
managed_plugins = set(package_manifest.get("managed_persistent_subtrees") or [])
assert len(managed_plugins) == 9
assert "plugins/postgres_pool" in managed_plugins
assert package_manifest["built_in_plugins"]["postgres_pool"] == "plugins/postgres_pool"

with tempfile.TemporaryDirectory() as td_name:
    td = Path(td_name)
    source = td / "source"
    target = td / "target"
    new_pool = source / "plugins" / "postgres_pool"
    (new_pool / "src").mkdir(parents=True)
    (new_pool / "plugin.json").write_text('{"schema_version":2}\n', encoding="utf-8")
    (new_pool / "src" / "main.py").write_text("def status(): return {}\n", encoding="utf-8")
    old_pool = target / "plugins" / "postgres_pool"
    old_pool.mkdir(parents=True)
    for old_name in ("init.py", "SHA256SUMS", "postgres_pool.py", "_pool.py"):
        (old_pool / old_name).write_text("old\n", encoding="utf-8")
    custom = target / "plugins" / "my_private_plugin"
    custom.mkdir(parents=True)
    (custom / "custom.py").write_text("def mine(): return True\n", encoding="utf-8")

    # Main package mirror preserves all plugins.
    m._sync_tree_contents(source, target, protected={"plugins"}, log=lambda _: None)
    assert (old_pool / "postgres_pool.py").exists()
    assert (custom / "custom.py").exists()

    # Managed built-in pass migrates only postgres_pool to the new src/ layout.
    m._sync_tree_contents(new_pool, old_pool, protected=set(), log=lambda _: None)
    assert (old_pool / "src" / "main.py").is_file()
    assert not (old_pool / "init.py").exists()
    assert not (old_pool / "SHA256SUMS").exists()
    assert not (old_pool / "postgres_pool.py").exists()
    assert not (old_pool / "_pool.py").exists()
    assert (custom / "custom.py").exists()

print("PASS: managed built-in plugin migration preserves unrelated user plugins")


with tempfile.TemporaryDirectory() as td_name:
    td = Path(td_name)
    target = td / "Norm"
    config = target / "config"
    config.mkdir(parents=True)
    (config / "settings.ini").write_text(
        "[project]\nversion = 0.53.9\n\n[worker]\ncustom = keep-me\n\n[paths]\nruntime_root = C:\\\\OldNorm\n",
        encoding="utf-8",
    )
    (config / "runtime.json").write_text(json.dumps({"worker": {"poll_ms": 1234}, "custom": {"keep": True}}), encoding="utf-8")
    (config / "network-map.json").write_text(json.dumps({"schema": 1, "targets": [{"name": "custom-target", "host": "example.invalid"}]}), encoding="utf-8")
    (config / "custom-sidecar.json").write_text(json.dumps({"hello": "world"}), encoding="utf-8")
    snap = m._snapshot_existing_config(target, lambda _: None)
    assert snap is not None

    # Simulate package mirror output.
    (config / "settings.ini").write_text(
        "[project]\nversion = 0.53.11\n\n[paths]\nruntime_root = .\n\n[new_section]\nnew_key = default\n",
        encoding="utf-8",
    )
    (config / "runtime.json").write_text(json.dumps({"worker": {"poll_ms": 5000, "task_round_limit": 128}, "new": 1}), encoding="utf-8")
    (config / "network-map.json").write_text(json.dumps({"schema": 1, "targets": []}), encoding="utf-8")
    (config / "custom-sidecar.json").unlink(missing_ok=True)
    m._restore_operator_config(target, snap, package_version="0.53.11", log=lambda _: None)

    settings_text = (config / "settings.ini").read_text(encoding="utf-8")
    assert "version = 0.53.11" in settings_text
    assert "custom = keep-me" not in settings_text
    assert "new_key = default" in settings_text
    runtime = json.loads((config / "runtime.json").read_text(encoding="utf-8"))
    assert runtime["worker"]["poll_ms"] == 1234
    assert runtime["worker"]["task_round_limit"] == 128
    assert runtime["new"] == 1
    assert "custom" not in runtime
    net = json.loads((config / "network-map.json").read_text(encoding="utf-8"))
    assert net["targets"][0]["name"] == "custom-target"
    assert not (config / "custom-sidecar.json").exists()
    audit = json.loads((snap / "migration.json").read_text(encoding="utf-8"))
    assert "worker.custom" in audit["settings"]["dropped"]
    assert "custom" in audit["json"]["runtime.json"]["dropped"]
    assert "custom-sidecar.json" in audit["dropped_files"]

print("PASS: in-place config backup + schema migration")

# Legacy 0.53.x installs kept non-secret PostgreSQL routing in the external .env.
with tempfile.TemporaryDirectory() as td_name:
    td = Path(td_name)
    target = td / "Norm"
    (target / "config").mkdir(parents=True)
    secrets_path = td / "legacy.env"
    (target / "config" / "settings.ini").write_text(
        f"[environment]\nsecrets_file = {secrets_path.as_posix()}\n", encoding="utf-8"
    )
    secrets_path.write_text(
        "NORM_POSTGRES_USER=legacy-user\nNORM_POSTGRES_DB=postgres\n"
        "NORM_POSTGRES_SCHEMA=norm_runtime\nNORM_STOCKS_DB=stocks_api\n"
        "NORM_POSTGRES_PASSWORD=legacy-password\n", encoding="utf-8"
    )
    current, secrets = m.envtools.read_installed_environment(target)
    assert current["postgres.user"] == "legacy-user"
    assert current["postgres.database"] == "postgres"
    assert current["postgres.schema"] == "norm_runtime"
    assert current["postgres.stocks_database"] == "stocks_api"
    assert secrets["NORM_POSTGRES_PASSWORD"] == "legacy-password"
print("PASS: legacy external .env PostgreSQL routing + password migration read")

# Environment-page prefill and private persistent-imprint behavior.
with tempfile.TemporaryDirectory() as td_name:
    td = Path(td_name)
    target = td / "Norm"
    config = target / "config"
    config.mkdir(parents=True)
    secrets_path = td / "private.env"
    (config / "settings.ini").write_text(
        f"""[paths]
documents_root = {td.as_posix()}/docs
workspace_root = workspace
temp_root = temp

[network]
current_machine = current-host
current_domain = current.example
require_tailscale = true
ollama_host = loopback
ollama_port = 11434
norm_host = loopback
norm_port = 12543
activity_host = loopback
activity_port = 8766
postgres_host = db.current.example
postgres_port = 5432
redis_host = loopback
redis_port = 6379

[postgres]
user = custom-user
database = current-db
schema = current_schema
stocks_database = current-stocks

[ssh]
enabled = false
ca8d_host =
ca8d_docker_host =
ca8d_port = 22
ca8d_user =
ca8d_identity_file = norm_remote_ed25519

[environment]
secrets_file = {secrets_path.as_posix()}
""",
        encoding="utf-8",
    )
    (config / "runtime.json").write_text(
        json.dumps({"tools": {"allowed_roots": [str(td / "docs")]}}),
        encoding="utf-8",
    )
    (config / "network-map.json").write_text(
        json.dumps({"schema": 1, "targets": []}),
        encoding="utf-8",
    )
    secrets_path.write_text(
        "NORM_POSTGRES_PASSWORD=current-password\n"
        "NORM_ROTOR5_SECRET=current-rotor\n"
        "NORM_ROTOR5_PREVIOUS_SECRETS=older-rotor\n",
        encoding="utf-8",
    )
    raw = {
        "schema": 1,
        "network": {"postgres_port": 55432},
        "postgres": {"user": "imprint-user"},
    }
    merged = m.validate_imprint(raw)
    previous = {
        "schema": 1,
        "network": {"postgres_port": 5432},
        "postgres": {"user": "norm"},
    }
    resolved, secrets, origins = m.envtools.resolve_environment_prefill(
        target, merged, raw, m.DEFAULT_IMPRINT, previous
    )
    assert resolved["postgres"]["user"] == "custom-user"
    assert origins["postgres.user"] == "current-custom"
    assert resolved["postgres"]["database"] == "current-db"  # old imprint had no database entry: compare to nothing
    assert origins["postgres.database"] == "current-custom"
    assert resolved["network"]["postgres_port"] == 55432
    assert origins["network.postgres_port"] == "private-imprint"
    state = m._write_install_state(target, "0.53.11", raw)
    assert state.is_file()
    assert m.envtools.read_installed_imprint_baseline(target) == raw
    assert resolved["network"]["current_machine"] == "current-host"
    assert secrets["NORM_POSTGRES_PASSWORD"] == "current-password"
    assert secrets["NORM_ROTOR5_SECRET"] == "current-rotor"
    assert secrets["NORM_ROTOR5_PREVIOUS_SECRETS"] == "older-rotor"

with tempfile.TemporaryDirectory() as td_name:
    td = Path(td_name)
    old_appdata = os.environ.get("APPDATA")
    os.environ["APPDATA"] = str(td)
    try:
        saved = m.save_local_imprint(valid)
        assert saved == (td / "Norm" / m.LOCAL_IMPRINT_NAME).resolve()
        assert saved.is_file()
        assert "password" not in saved.read_text(encoding="utf-8").lower()
    finally:
        if old_appdata is None:
            os.environ.pop("APPDATA", None)
        else:
            os.environ["APPDATA"] = old_appdata

assert m.envtools.connection_host("loopback", "host", "example") == "127.0.0.1"
assert m.envtools.connection_host("current", "host", "example") == "host.example"

print("PASS: Environment prefill manual/current-custom/new-imprint precedence")
print("PASS: package-imprint baseline persistence and absent-old-default comparison")
print("PASS: current secrets available for masked prefill")
print("PASS: persistent local imprint is outside package tree and non-secret")
print("PASS: connection host resolution")

# Connection test must use only the explicitly configured endpoints.
calls = []
orig_http = m.envtools._http_probe
orig_redis = m.envtools._redis_probe
orig_pg = m.envtools._postgres_probe
try:
    m.envtools._http_probe = lambda host, port, path, timeout=2.5: (calls.append(("http", host, port, path)) or ("ok", "stub"))
    m.envtools._redis_probe = lambda host, port, timeout=2.5: (calls.append(("redis", host, port)) or ("ok", "stub"))
    m.envtools._postgres_probe = lambda host, port, user, password, database, python_candidates, timeout=3.0: (
        calls.append(("postgres", host, port, user, database)) or ("ok", "stub")
    )
    probe_data = m.validate_imprint({
        "schema": 1,
        "network": {
            "current_machine": "box",
            "current_domain": "mesh.test",
            "ollama_host": "ollama.test",
            "ollama_port": 1111,
            "norm_host": "norm.test",
            "norm_port": 2222,
            "activity_host": "activity.test",
            "activity_port": 3333,
            "redis_host": "redis.test",
            "redis_port": 4444,
            "postgres_host": "postgres.test",
            "postgres_port": 5555,
        },
        "postgres": {
            "user": "pg-user",
            "database": "pg-db",
            "schema": "pg-schema",
            "stocks_database": "stocks-db",
        },
    })
    probe_results = m.envtools.test_environment_connections(
        probe_data,
        {"NORM_POSTGRES_PASSWORD": "test-only"},
        [],
    )
    assert len(probe_results) == 5
    assert calls == [
        ("http", "ollama.test", 1111, "/api/tags"),
        ("http", "norm.test", 2222, "/health"),
        ("http", "activity.test", 3333, "/health"),
        ("redis", "redis.test", 4444),
        ("postgres", "postgres.test", 5555, "pg-user", "pg-db"),
    ]
finally:
    m.envtools._http_probe = orig_http
    m.envtools._redis_probe = orig_redis
    m.envtools._postgres_probe = orig_pg

print("PASS: Test connections uses only configured endpoints")
