from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(r"D:\LOCAL_Share\Code Projects\Norm.temp\github-public-worktree")

# runtime.json: keep new 0.53.11 behavior, restore topology-neutral public storage defaults.
runtime_path = ROOT / "src/Norm/config/runtime.json"
runtime = json.loads(runtime_path.read_text(encoding="utf-8-sig"))
tools = runtime["tools"]
tools["allowed_roots"] = ["{documents_root}"]
storage = tools.setdefault("storage_context", {})
storage["primary_name"] = "documents"
storage["primary_root"] = "{documents_root}"
storage["backup_name"] = "workspace"
runtime_path.write_text(json.dumps(runtime, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")

# settings.ini: preserve layout/comments while replacing only deployment-specific values.
settings_path = ROOT / "src/Norm/config/settings.ini"
replacements = {
    ("file_access", "read_directories"): "@workspace;@temp;@docs;@plugins;@documents",
    ("file_access", "write_directories"): "@workspace;@temp;@docs;@plugins;@documents",
    ("network", "current_machine"): "norm-host",
    ("network", "current_domain"): "example.invalid",
    ("network", "require_tailscale"): "false",
    ("network", "norm_host"): "loopback",
    ("network", "activity_host"): "loopback",
    ("network", "postgres_host"): "loopback",
    ("network", "postgres_port"): "5432",
    ("network", "redis_host"): "loopback",
    ("network", "redis_port"): "6379",
    ("ssh", "enabled"): "false",
    ("ssh", "executable"): r"C:\Windows\System32\OpenSSH\ssh.exe",
    ("ssh", "ca8d_host"): "",
    ("ssh", "ca8d_docker_host"): "",
    ("ssh", "ca8d_port"): "22",
    ("ssh", "ca8d_user"): "",
    ("ssh", "ca8d_identity_file"): "norm_remote_ed25519",
    ("project", "author"): "hermit-node",
    ("project", "repository"): "https://github.com/hermit-node/kh-norm",
}
lines = settings_path.read_text(encoding="utf-8-sig").splitlines()
section = ""
out: list[str] = []
seen: set[tuple[str, str]] = set()
for line in lines:
    stripped = line.strip()
    if stripped.startswith("[") and stripped.endswith("]"):
        section = stripped[1:-1].strip().lower()
        out.append(line)
        continue
    if "=" in line and not stripped.startswith((";", "#")):
        key = line.split("=", 1)[0].strip().lower()
        marker = (section, key)
        if marker in replacements:
            out.append(f"{key} = {replacements[marker]}")
            seen.add(marker)
            continue
    out.append(line)
missing = set(replacements) - seen
if missing:
    raise RuntimeError(f"settings keys not found: {sorted(missing)}")
settings_path.write_text("\n".join(out).rstrip() + "\n", encoding="utf-8", newline="\n")

# New file-access defaults must be generic in the public package.
policy_path = ROOT / "src/Norm/core/norm_runtime/file_access_policy.py"
policy = policy_path.read_text(encoding="utf-8-sig")
old = r'defaults = r"@workspace;@temp;@docs;@plugins;\\KH-CA8D\Local1675;D:\LOCAL_Share\Code Projects"'
new = r'defaults = r"@workspace;@temp;@docs;@plugins;@documents"'
if policy.count(old) != 1:
    raise RuntimeError("unexpected file_access_policy defaults occurrence count")
policy_path.write_text(policy.replace(old, new), encoding="utf-8", newline="\n")

# Keep new executor behavior while restoring generic public storage labels.
executor_path = ROOT / "src/Norm/core/norm_runtime/file_tool_executor.py"
executor = executor_path.read_text(encoding="utf-8-sig")
pairs = [
    ("automatically falls back to the KHzz backup", "automatically falls back to the configured backup storage"),
    ('["ca8d", "khzz_docs", "postgres", "redis", "prompt_queue", "ollama", "tailscale", "dropbox"]',
     '["ca8d", "workspace", "postgres", "redis", "prompt_queue", "ollama", "tailscale", "dropbox"]'),
    ('{"ca8d_smb": "ca8d", "local": "khzz_docs", "postgresql": "postgres", "pg": "postgres"}',
     '{"ca8d_smb": "ca8d", "local": "workspace", "postgresql": "postgres", "pg": "postgres"}'),
    ('if target in {"ca8d", "khzz_docs"}:', 'if target in {"ca8d", "workspace"}:'),
]
for old, new in pairs:
    if executor.count(old) != 1:
        raise RuntimeError(f"unexpected executor occurrence count for {old!r}")
    executor = executor.replace(old, new)
executor_path.write_text(executor, encoding="utf-8", newline="\n")

# E2E paths remain operator-configurable; only the new postgres settings behavior is published.
e2e_path = ROOT / "src/Norm/tools/norm_e2e.py"
e2e = e2e_path.read_text(encoding="utf-8-sig")
pairs = [
    ('NAS_ROOT = Path(r"\\\\KH-CA8D\\Local1675\\Docker\\ca8d-tailnet-host\\e2e\\norm")',
     'NAS_ROOT = Path(os.environ.get("NORM_E2E_NAS_ROOT", r"\\\\REMOTE-HOST\\Share\\Norm-E2E"))'),
    ('REMOTE_ROOT = "/share/Local1675/Docker/ca8d-tailnet-host/e2e/norm"',
     'REMOTE_ROOT = os.environ.get("NORM_E2E_REMOTE_ROOT", "/share/Norm-E2E")'),
    ('author = KernelHermit', 'author = hermit-node'),
]
for old, new in pairs:
    if e2e.count(old) != 1:
        raise RuntimeError(f"unexpected norm_e2e occurrence count for {old!r}")
    e2e = e2e.replace(old, new)
e2e_path.write_text(e2e, encoding="utf-8", newline="\n")

# Strengthen public artifact guard with the repository's known deployment-leak signatures.
guard_path = ROOT / "tools/public_release_guard.py"
guard = guard_path.read_text(encoding="utf-8-sig")
anchor = '''SECRET_ASSIGNMENT_RE = re.compile(
    rb"(?mi)^\\s*(?:export\\s+)?"
    rb"(?:NORM_POSTGRES_PASSWORD|NORM_ROTOR5_SECRET|NORM_ROTOR5_PREVIOUS_SECRETS)"
    rb"\\s*=\\s*([^\\r\\n]*)"
)
'''
insert = anchor + '''
DEPLOYMENT_CONTENT_PATTERNS = (
    re.compile(rb"\\bkh-?ca8d(?:-docker)?\\b", re.I),
    re.compile(rb"\\bkhzz\\b", re.I),
    re.compile(rb"\\bboga-dace\\.ts\\.net\\b", re.I),
    re.compile(rb"\\bLocal1675\\b", re.I),
    re.compile(rb"\\bconsole\\.1675m\\b", re.I),
    re.compile(rb"\\b100\\.(?!64\\.0\\.0\\b)(?:6[4-9]|[7-9][0-9]|1[01][0-9]|12[0-7])\\.(?:\\d{1,3})\\.(?:\\d{1,3})\\b"),
)
'''
if guard.count(anchor) != 1:
    raise RuntimeError("public guard anchor not found exactly once")
guard = guard.replace(anchor, insert)
old = '''def _blocked_content(data: bytes) -> str | None:
    if any(marker in data for marker in PRIVATE_KEY_MARKERS):
        return "private-key material"
    for match in SECRET_ASSIGNMENT_RE.finditer(data):
'''
new = '''def _blocked_content(data: bytes) -> str | None:
    if any(marker in data for marker in PRIVATE_KEY_MARKERS):
        return "private-key material"
    for pattern in DEPLOYMENT_CONTENT_PATTERNS:
        if pattern.search(data):
            return f"deployment-specific topology matched {pattern.pattern!r}"
    for match in SECRET_ASSIGNMENT_RE.finditer(data):
'''
if guard.count(old) != 1:
    raise RuntimeError("public guard content hook not found exactly once")
guard_path.write_text(guard.replace(old, new).rstrip() + "\n", encoding="utf-8", newline="\n")

print("PUBLIC_SANITIZE_PASS")
