from __future__ import annotations

import argparse
import configparser
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "core"
SETTINGS = ROOT / "config" / "settings.ini"
NAS_ROOT = Path(r"\\KH-CA8D\Local1675\Docker\ca8d-tailnet-host\e2e\norm")
REMOTE_ROOT = "/share/Local1675/Docker/ca8d-tailnet-host/e2e/norm"


def project_version() -> str:
    parser = configparser.ConfigParser(interpolation=None)
    parser.read(SETTINGS, encoding="utf-8-sig")
    return parser.get("project", "version", fallback="unknown").strip()


def ssh_defaults() -> dict:
    parser = configparser.ConfigParser(interpolation=None)
    parser.read(SETTINGS, encoding="utf-8-sig")
    if not parser.has_section("ssh") or not parser.getboolean("ssh", "enabled", fallback=True):
        return {}
    root_raw = parser.get("ssh", "root", fallback=".ssh").strip() or ".ssh"
    root_path = Path(os.path.expandvars(os.path.expanduser(root_raw)))
    root = root_path.resolve() if root_path.is_absolute() else (ROOT / root_path).resolve()

    def child(key: str, default: str) -> Path:
        raw = parser.get("ssh", key, fallback=default).strip() or default
        path = Path(os.path.expandvars(os.path.expanduser(raw)))
        return path.resolve() if path.is_absolute() else (root / path).resolve()

    user = parser.get("ssh", "ca8d_user", fallback="").strip()
    host = parser.get("ssh", "ca8d_host", fallback="").strip()
    return {
        "target": f"{user}@{host}" if user and host else "",
        "key": child("ca8d_identity_file", "ca8d_norm_ed25519"),
        "known_hosts": child("known_hosts_file", "known_hosts"),
        "executable": child("executable", r"C:\Windows\System32\OpenSSH\ssh.exe"),
        "strict": parser.get("ssh", "strict_host_key_checking", fallback="accept-new").strip() or "accept-new",
        "timeout": parser.getint("ssh", "connect_timeout_seconds", fallback=8),
        "port": parser.getint("ssh", "ca8d_port", fallback=22),
    }


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def copy_runtime(run_dir: Path) -> Path:
    runtime = run_dir / "runtime"
    app_dst = runtime / "core"
    if runtime.exists():
        shutil.rmtree(runtime)
    ignore = shutil.ignore_patterns("*.exe", "*.pyc", "__pycache__", ".pytest_cache")
    shutil.copytree(APP, app_dst, ignore=ignore)
    # Built-in plugins are part of the current runtime contract; user/private plugin
    # state is not mounted into E2E.
    shutil.copytree(ROOT / "plugins", runtime / "plugins", ignore=ignore)
    (runtime / "config").mkdir(parents=True, exist_ok=True)
    (runtime / "logs").mkdir(parents=True, exist_ok=True)
    (runtime / "state").mkdir(parents=True, exist_ok=True)
    return runtime


def settings_text(version: str) -> str:
    return (f"""[paths]
runtime_root = /sandbox/runtime
documents_root = /workspace
workspace_root = .
temp_root = temp
verbatim_writer = plugins/verbatim_lines/_cli.py

[network]
current_machine = norm-e2e
current_domain = local
require_tailscale = false
ollama_host = ollama-mock
ollama_port = 11434
norm_host = loopback
norm_port = 22543
activity_host = loopback
activity_port = 18766
postgres_host = postgres
postgres_port = 5432
redis_host = redis
redis_port = 6379
"""
    + f"""
[documentation]
readme = README.md
current_status = CURRENT_STATUS.md
development_notes = DEVELOPMENT_NOTES.md
release_notes = RELEASE_NOTES.md
future_implementation_notes = FUTURE_IMPLEMENTATION_NOTES.md

[environment]
secrets_file = /sandbox/runtime/config/e2e.env

[plugins]
root = plugins
registry_file = .registry.json

[project]
name = Norm
version = {version}
author = KernelHermit
repository = https://github.com/hermit-node
"""
    )


def runtime_config() -> dict:
    return {
        "redis": {"db": 0, "prefix": "e2e:task"},
        "prompt_queue": {
            "db": 1,
            "stream": "e2e:prompt:work",
            "group": "e2e-workers",
            "retry_stream": "e2e:prompt:retry",
            "escalation_stream": "e2e:prompt:escalate",
            "dead_letter_stream": "e2e:prompt:dead",
            "max_attempts": 3,
            "claim_idle_seconds": 30,
        },
        "worker": {
            "enabled": True,
            "poll_ms": 250,
            "model_timeout_seconds": 60,
            "step_round_limit": 12,
            "task_round_limit": 48,
            "max_subtask_depth": 2,
            "max_length_continuations_per_slice": 1,
            "max_oversize_recovery_cycles": 1,
        },
        "ollama": {"model": "norm"},
        "http": {"wait_timeout_seconds": 120},
        "activity": {},
        "tools": {
            "enabled": True,
            "allowed_roots": ["/workspace", "/fixtures/apache", "/fixtures/shared"],
            "backup_root": "/workspace/.backups",
            "audit_log": "/reports/tool-audit.jsonl",
            "max_read_bytes": 131072,
            "max_write_bytes": 1048576,
            "max_rounds": 8,
            "blocked_write_staging_root": "/workspace/.blocked",
            "write_retry_count": 2,
            "write_retry_delay_seconds": 0.1,
            "image_enabled": False,
            "shell_enabled": False,
        },
        "postgres": {"schema": "norm_runtime"},
        "heartbeat_seconds": 5,
        "memory": {
            "routing_threshold": 0.58,
            "candidate_threads": 4,
            "recent_messages": 4,
            "background_context_chars": 4000,
            "deep_history_enabled": False,
        },
        "maintenance": {
            "redis_reconcile_enabled": True,
            "redis_reconcile_seconds": 300,
            "startup_redis_reconcile": True,
            "weekly_cleanup_enabled": False,
        },
        "coordinator": {"mode": "local", "queue_protocol_version": 2, "remote_compatible": True},
        "deletion_queue": {"db": 2, "stream": "e2e:deletion:queue", "trash_root": "/workspace/.trash"},
        "persistent_instructions": [
            "Treat this as an isolated E2E sandbox and use only the configured sandbox services and allowed roots.",
            "Use native read/write tools when the task requests file work and verify observed results before answering.",
        ],
        "console_queue": {"db": 3},
        "rich_console_queue": {"db": 3},
    }


def secrets_text() -> str:
    return "\n".join([
        "NORM_POSTGRES_USER=norm_e2e",
        "NORM_POSTGRES_PASSWORD=norm_e2e",
        "NORM_POSTGRES_DB=norm_e2e",
        "NORM_POSTGRES_SCHEMA=norm_runtime",
        "NORM_STOCKS_DB=stocks_api",
        "",
    ])


def stage_run(run_id: str, jit: Path | None, scenario: Path | None) -> Path:
    run_dir = NAS_ROOT / "runs" / run_id
    if run_dir.exists():
        shutil.rmtree(run_dir)
    (run_dir / "jit").mkdir(parents=True)
    (run_dir / "reports").mkdir(parents=True)
    runtime = copy_runtime(run_dir)
    config = runtime / "config"
    version = project_version()
    (config / "settings.ini").write_text(settings_text(version), encoding="utf-8", newline="\n")
    (config / "runtime.json").write_text(json.dumps(runtime_config(), indent=2), encoding="utf-8", newline="\n")
    (config / "e2e.env").write_text(secrets_text(), encoding="utf-8", newline="\n")
    fixture = NAS_ROOT / "fixture"
    shutil.copy2(jit or fixture / "default_test.py", run_dir / "jit" / "test.py")
    shutil.copy2(scenario or fixture / "default_scenario.json", run_dir / "jit" / "scenario.json")
    manifest = {
        "run_id": run_id,
        "created_epoch": time.time(),
        "norm_version": version,
        "source": str(APP),
        "jit_sha256": sha256(run_dir / "jit" / "test.py"),
        "scenario_sha256": sha256(run_dir / "jit" / "scenario.json"),
    }
    (run_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return run_dir


def compose_command(run_id: str) -> str:
    project = f"norm-e2e-{run_id}"
    return (
        f"cd {REMOTE_ROOT} && "
        f"NORM_E2E_RUN_ID={run_id} docker compose -p {project} up --abort-on-container-exit --exit-code-from norm norm; "
        f"code=$?; NORM_E2E_RUN_ID={run_id} docker compose -p {project} down -v --remove-orphans; exit $code"
    )


def validate_compose(run_id: str) -> tuple[bool, str]:
    env = {**os.environ, "NORM_E2E_RUN_ID": run_id}
    cp = subprocess.run(
        ["docker", "compose", "-f", str(NAS_ROOT / "compose.yml"), "config"],
        env=env, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30,
    )
    text = (cp.stdout + "\n" + cp.stderr).strip()
    return cp.returncode == 0, text


def run_ssh(target: str, key: Path | None, run_id: str, *, executable: Path | None = None, known_hosts: Path | None = None, strict: str = "accept-new", connect_timeout: int = 8, port: int = 22) -> subprocess.CompletedProcess:
    cmd = [str(executable or "ssh"), "-o", "BatchMode=yes", "-o", f"ConnectTimeout={max(1, int(connect_timeout))}", "-o", f"StrictHostKeyChecking={strict}", "-p", str(int(port))]
    if known_hosts:
        cmd.extend(["-o", f"UserKnownHostsFile={known_hosts}"])
    if key:
        cmd.extend(["-i", str(key)])
    cmd.extend([target, compose_command(run_id)])
    return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600)


def main() -> int:
    parser = argparse.ArgumentParser(description="Stage and optionally run an isolated Norm Docker E2E sandbox.")
    parser.add_argument("--jit", type=Path, help="Custom JIT test.py; defaults to built-in smoke test")
    parser.add_argument("--scenario", type=Path, help="Custom scripted Ollama scenario JSON")
    parser.add_argument("--run-id", default="")
    parser.add_argument("--stage-only", action="store_true")
    ssh_cfg = ssh_defaults()
    parser.add_argument("--ssh-target", default=os.environ.get("NORM_E2E_SSH_TARGET", "") or str(ssh_cfg.get("target") or ""))
    parser.add_argument("--ssh-key", type=Path, default=ssh_cfg.get("key"))
    args = parser.parse_args()

    run_id = args.run_id.strip() or (time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6])
    if args.jit and not args.jit.is_file():
        raise FileNotFoundError(args.jit)
    if args.scenario and not args.scenario.is_file():
        raise FileNotFoundError(args.scenario)
    run_dir = stage_run(run_id, args.jit, args.scenario)
    ok, config_output = validate_compose(run_id)
    result = {
        "run_id": run_id,
        "run_dir": str(run_dir),
        "compose_valid": ok,
        "remote_command": compose_command(run_id),
    }
    if not ok:
        result["compose_output"] = config_output[-12000:]
        print(json.dumps(result, indent=2))
        return 1
    if args.stage_only or not args.ssh_target:
        result["execution"] = "staged"
        if not args.ssh_target:
            result["note"] = "No NORM_E2E_SSH_TARGET/--ssh-target is configured; sandbox was staged and Compose-validated only."
        print(json.dumps(result, indent=2))
        return 0

    cp = run_ssh(
        args.ssh_target, args.ssh_key, run_id,
        executable=ssh_cfg.get("executable"), known_hosts=ssh_cfg.get("known_hosts"),
        strict=str(ssh_cfg.get("strict") or "accept-new"), connect_timeout=int(ssh_cfg.get("timeout") or 8),
        port=int(ssh_cfg.get("port") or 22),
    )
    result["execution"] = "ran"
    result["runner_exit_code"] = cp.returncode
    result["runner_stdout"] = cp.stdout[-12000:]
    result["runner_stderr"] = cp.stderr[-12000:]
    report_path = run_dir / "reports" / "harness-result.json"
    if report_path.is_file():
        try:
            result["report"] = json.loads(report_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            result["report"] = {"result": "dead", "detail": "invalid harness-result.json"}
    else:
        result["report"] = {"result": "dead", "detail": "sandbox did not produce harness-result.json"}
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if cp.returncode == 0 and result["report"].get("result") == "working" else 1


if __name__ == "__main__":
    raise SystemExit(main())
