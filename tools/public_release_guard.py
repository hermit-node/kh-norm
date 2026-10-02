from __future__ import annotations

import argparse
import re
import zipfile
from pathlib import Path, PurePosixPath

BLOCKED_BASENAMES = {
    ".env",
    "norm-imprint.local.json",
    "id_rsa",
    "id_dsa",
    "id_ecdsa",
    "id_ed25519",
}
BLOCKED_SUFFIXES = {".key", ".pem", ".pfx", ".p12"}
BLOCKED_PATH_PARTS = {".ssh", "secrets"}
PRIVATE_KEY_MARKERS = (
    b"-----BEGIN OPENSSH PRIVATE KEY-----",
    b"-----BEGIN RSA PRIVATE KEY-----",
    b"-----BEGIN EC PRIVATE KEY-----",
    b"-----BEGIN PRIVATE KEY-----",
)
SECRET_ASSIGNMENT_RE = re.compile(
    rb"(?mi)^\s*(?:export\s+)?"
    rb"(?:NORM_POSTGRES_PASSWORD|NORM_ROTOR5_SECRET|NORM_ROTOR5_PREVIOUS_SECRETS)"
    rb"\s*=\s*([^\r\n]*)"
)

DEPLOYMENT_CONTENT_PATTERNS = (
    re.compile(rb"\bprivate-storage(?:-docker)?\b", re.I),
    re.compile(rb"\bnorm-host\b", re.I),
    re.compile(rb"\bprivate-tailnet\.example\b", re.I),
    re.compile(rb"\bLocalExample\b", re.I),
    re.compile(rb"\bconsole\.example\b", re.I),
    re.compile(rb"\b100\.(?!64\.0\.0\b)(?:6[4-9]|[7-9][0-9]|1[01][0-9]|12[0-7])\.(?:\d{1,3})\.(?:\d{1,3})\b"),
)


class PublicReleaseGuardError(RuntimeError):
    pass


def _blocked_name(relative: PurePosixPath) -> str | None:
    parts_lower = [part.lower() for part in relative.parts]
    name = relative.name.lower()
    if any(part in BLOCKED_PATH_PARTS for part in parts_lower[:-1]):
        return "private directory"
    if name in BLOCKED_BASENAMES or name.startswith(".env."):
        return "private/local filename"
    if name.startswith("norm-imprint.") and name.endswith(".local.json"):
        return "local imprint"
    if relative.suffix.lower() in BLOCKED_SUFFIXES:
        return "private-key/certificate extension"
    return None


def _blocked_content(data: bytes) -> str | None:
    if any(marker in data for marker in PRIVATE_KEY_MARKERS):
        return "private-key material"
    for pattern in DEPLOYMENT_CONTENT_PATTERNS:
        if pattern.search(data):
            return f"deployment-specific topology matched {pattern.pattern!r}"
    for match in SECRET_ASSIGNMENT_RE.finditer(data):
        value = match.group(1).strip().strip(b"''\"")
        if value and value not in {b"<redacted>", b"<placeholder>", b"CHANGEME", b"changeme"}:
            return "non-empty secret assignment"
    return None


def _check(relative: PurePosixPath, data: bytes) -> None:
    reason = _blocked_name(relative)
    if reason:
        raise PublicReleaseGuardError(f"{relative.as_posix()}: {reason}")
    reason = _blocked_content(data)
    if reason:
        raise PublicReleaseGuardError(f"{relative.as_posix()}: {reason}")


def scan_tree(root: Path) -> int:
    root = root.resolve()
    checked = 0
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        relative = PurePosixPath(path.relative_to(root).as_posix())
        if "__pycache__" in relative.parts or path.suffix.lower() == ".pyc":
            continue
        _check(relative, path.read_bytes())
        checked += 1
    return checked


def scan_zip(path: Path) -> int:
    checked = 0
    with zipfile.ZipFile(path, "r") as zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            relative = PurePosixPath(info.filename)
            _check(relative, zf.read(info))
            checked += 1
    return checked


def main() -> int:
    parser = argparse.ArgumentParser(description="Fail closed if a public Norm artifact contains local secrets/private material.")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--tree", type=Path)
    group.add_argument("--zip", dest="zip_path", type=Path)
    args = parser.parse_args()
    try:
        if args.tree:
            count = scan_tree(args.tree)
            print(f"PUBLIC_RELEASE_GUARD_PASS tree files={count}")
        else:
            count = scan_zip(args.zip_path)
            print(f"PUBLIC_RELEASE_GUARD_PASS zip files={count}")
    except PublicReleaseGuardError as exc:
        print(f"PUBLIC_RELEASE_GUARD_FAIL {exc}")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
