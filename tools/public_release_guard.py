from __future__ import annotations

import argparse
import re
import zipfile
from pathlib import Path, PurePosixPath

BLOCKED_BASENAMES={".env","norm-imprint.local.json","id_rsa","id_dsa","id_ecdsa","id_ed25519","norm-installer.exe","publish-to-github.ps1"}
BLOCKED_SUFFIXES={".key",".pem",".pfx",".p12",".dump",".sql"}
BLOCKED_PATH_PARTS={".ssh","secrets","state","logs","workspace"}
PRIVATE_KEY_MARKERS=(b"-----BEGIN OPENSSH PRIVATE KEY-----",b"-----BEGIN RSA PRIVATE KEY-----",b"-----BEGIN EC PRIVATE KEY-----",b"-----BEGIN PRIVATE KEY-----")
SECRET_ASSIGNMENT_RE=re.compile(rb"(?mi)^\s*(?:export\s+)?(?:NORM_POSTGRES_PASSWORD|NORM_ROTOR5_SECRET|NORM_ROTOR5_PREVIOUS_SECRETS|OPENAI_API_KEY|GITHUB_TOKEN|TAILSCALE_AUTHKEY|AWS_SECRET_ACCESS_KEY)\s*=\s*([^\r\n]*)")
DEPLOYMENT_PATTERNS=(
    re.compile(rb"\\\\100\.(?:6[4-9]|[7-9][0-9]|1[01][0-9]|12[0-7])\.",re.I),
    re.compile(rb"\b100\.(?!64\.0\.0\b)(?:6[4-9]|[7-9][0-9]|1[01][0-9]|12[0-7])\.(?:\d{1,3})\.(?:\d{1,3})\b"),
    re.compile(rb"\b[a-z0-9-]+(?:\.[a-z0-9-]+)*\.ts\.net\b",re.I),
    re.compile(rb"[A-Za-z]:\\LOCAL_Share\\",re.I),
    re.compile(rb"C:\\Users\\(?!%USERPROFILE%)",re.I),
)
CREDENTIAL_PATTERNS=(
    re.compile(rb"\btskey-[A-Za-z0-9_-]{10,}"),
    re.compile(rb"\bghp_[A-Za-z0-9]{20,}"),
    re.compile(rb"\bgithub_pat_[A-Za-z0-9_]{20,}"),
    re.compile(rb"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(rb"\bsk-[A-Za-z0-9]{24,}\b"),
)

class PublicReleaseGuardError(RuntimeError):
    pass

def blocked_name(relative: PurePosixPath):
    parts=[p.lower() for p in relative.parts]
    name=relative.name.lower()
    if any(p in BLOCKED_PATH_PARTS for p in parts[:-1]): return "private/runtime directory"
    if name in BLOCKED_BASENAMES or name.startswith(".env."): return "private/generated filename"
    if name.startswith("norm-imprint.") and name.endswith(".local.json"): return "local imprint"
    if relative.suffix.lower() in BLOCKED_SUFFIXES: return "private/export extension"
    if name.startswith("norm-") and name.endswith(".zip"): return "generated release archive"
    return None

def blocked_content(data: bytes):
    if any(m in data for m in PRIVATE_KEY_MARKERS): return "private-key material"
    for pattern in DEPLOYMENT_PATTERNS:
        if pattern.search(data): return "deployment-specific topology"
    for pattern in CREDENTIAL_PATTERNS:
        if pattern.search(data): return "credential-like material"
    for match in SECRET_ASSIGNMENT_RE.finditer(data):
        value=match.group(1).strip().strip(b"''\"")
        if value and value not in {b"<redacted>",b"<placeholder>",b"CHANGEME",b"changeme"}:
            return "non-empty secret assignment"
    return None

SELF_CONTENT_EXCLUSIONS={"tools/public_release_guard.py"}

def check(relative,data):
    reason=blocked_name(relative)
    if reason:
        raise PublicReleaseGuardError(f"{relative.as_posix()}: {reason}")
    if relative.as_posix() in SELF_CONTENT_EXCLUSIONS:
        return
    reason=blocked_content(data)
    if reason:
        raise PublicReleaseGuardError(f"{relative.as_posix()}: {reason}")

def scan_tree(root: Path) -> int:
    root=root.resolve(); checked=0
    for path in sorted(root.rglob("*")):
        if not path.is_file(): continue
        rel=PurePosixPath(path.relative_to(root).as_posix())
        if "__pycache__" in rel.parts or path.suffix.lower()==".pyc": continue
        check(rel,path.read_bytes()); checked+=1
    return checked

def scan_zip(path: Path) -> int:
    checked=0
    with zipfile.ZipFile(path) as zf:
        for info in zf.infolist():
            if info.is_dir(): continue
            check(PurePosixPath(info.filename),zf.read(info)); checked+=1
    return checked

def main() -> int:
    parser=argparse.ArgumentParser()
    group=parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--tree",type=Path)
    group.add_argument("--zip",dest="zip_path",type=Path)
    args=parser.parse_args()
    try:
        count=scan_tree(args.tree) if args.tree else scan_zip(args.zip_path)
        print(f"PUBLIC_RELEASE_GUARD_PASS files={count}")
        return 0
    except PublicReleaseGuardError as exc:
        print("PUBLIC_RELEASE_GUARD_FAIL",exc)
        return 2

if __name__=="__main__":
    raise SystemExit(main())
