from __future__ import annotations

from pathlib import Path

ROOT = Path(r"D:\LOCAL_Share\Code Projects\Norm.temp\github-public-worktree")

executor_path = ROOT / "src/Norm/core/norm_runtime/file_tool_executor.py"
executor = executor_path.read_text(encoding="utf-8-sig")
pairs = [
    ("automatically fall back to the KHzz backup", "automatically fall back to the configured backup storage"),
    ('["ca8d", "khzz_docs", "postgres", "redis", "prompt_queue", "ollama", "tailscale", "dropbox"]',
     '["ca8d", "workspace", "postgres", "redis", "prompt_queue", "ollama", "tailscale", "dropbox"]'),
    ('{"ca8d_smb": "ca8d", "local": "khzz_docs", "postgresql": "postgres", "pg": "postgres"}',
     '{"ca8d_smb": "ca8d", "local": "workspace", "postgresql": "postgres", "pg": "postgres"}'),
    ('if target in {"ca8d", "khzz_docs"}:', 'if target in {"ca8d", "workspace"}:'),
]
for old, new in pairs:
    if executor.count(old) != 1:
        raise RuntimeError(f"unexpected executor occurrence count for {old!r}: {executor.count(old)}")
    executor = executor.replace(old, new)
executor_path.write_text(executor, encoding="utf-8", newline="\n")

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
        raise RuntimeError(f"unexpected norm_e2e occurrence count for {old!r}: {e2e.count(old)}")
    e2e = e2e.replace(old, new)
e2e_path.write_text(e2e, encoding="utf-8", newline="\n")

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
    raise RuntimeError(f"public guard anchor count={guard.count(anchor)}")
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
    raise RuntimeError(f"public guard hook count={guard.count(old)}")
guard_path.write_text(guard.replace(old, new).rstrip() + "\n", encoding="utf-8", newline="\n")

print("PUBLIC_SANITIZE_REMAINING_PASS")
