from __future__ import annotations

import hashlib
import json
import re
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
SOURCE = HERE / "Norm-0.53.11-portable-source.zip"
SHA = HERE / "Norm-0.53.11-portable-source.zip.sha256"

expected = SHA.read_text(encoding="utf-8").split()[0].lower()
actual = hashlib.sha256(SOURCE.read_bytes()).hexdigest()
assert expected == actual, (expected, actual)

deployment_leak_patterns = [
    re.compile(r"\\\\[^\\\n]+\\(?:Local|Private|Secrets)[^\\\n]*", re.I),
    re.compile(r"\b100\.(?!64\.0\.0\b)(?:6[4-9]|[7-9][0-9]|1[01][0-9]|12[0-7])\.(?:\d{1,3})\.(?:\d{1,3})\b"),
    re.compile(r"\bkh-?ca8d(?:-docker)?\b", re.I),
    re.compile(r"\bkhzz\b", re.I),
    re.compile(r"\bboga-dace\.ts\.net\b", re.I),
    re.compile(r"\bLocal1675\b", re.I),
    re.compile(r"\bconsole\.1675m\b", re.I),
]
credential_patterns = [
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"\btskey-[A-Za-z0-9_-]{10,}"),
    re.compile(r"\bghp_[A-Za-z0-9]{20,}"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bsk-[A-Za-z0-9]{24,}\b"),
]

def scan_text(label: str, text: str) -> None:
    for pattern in deployment_leak_patterns:
        assert not pattern.search(text), f"deployment-specific topology material matched {pattern.pattern!r} in {label}"
    for pattern in credential_patterns:
        assert not pattern.search(text), f"credential-like material matched {pattern.pattern!r} in {label}"

SCAN_EXCLUSIONS = {
    HERE / "Publish-To-GitHub.ps1",
    HERE / "tools" / "public_release_guard.py",
    HERE / "tests" / "test_public_release.py",
}

for path in HERE.rglob("*"):
    if not path.is_file() or path == SOURCE or path in SCAN_EXCLUSIONS:
        continue
    if path.name == "norm-imprint.local.json":
        raise AssertionError("local imprint must not be part of public release")
    if path.name in {".registry.json", ".registry.json.writing"} and path.parent.name == "plugins":
        continue
    if path.suffix.lower() in {".py", ".md", ".txt", ".json", ".ini", ".bat", ".ps1", ".gitignore", ".sha256"} or path.name == ".gitignore":
        scan_text(str(path.relative_to(HERE)), path.read_text(encoding="utf-8", errors="ignore"))

with zipfile.ZipFile(SOURCE, "r") as zf:
    names=zf.namelist()
    assert not any(name.endswith("/plugins/.registry.json") or name.endswith("/plugins/.registry.json.writing") for name in names), "runtime-generated plugin registry must not ship in public source ZIP"
    first_party = {"backup", "file_read", "postgres_pool", "rotor5_cipher", "soft_delete", "stegosplit_key", "stegosplit_message", "verbatim_lines", "vision_parse"}
    prefix = "Norm-0.53.11/plugins/"
    for plugin in first_party:
        meta_name = prefix + plugin + "/plugin.json"
        assert meta_name in names, meta_name
        assert prefix + plugin + "/src/main.py" in names
        assert prefix + plugin + "/SHA256SUMS" not in names
        assert prefix + plugin + "/init.py" not in names
        meta = json.loads(zf.read(meta_name).decode("utf-8"))
        assert meta["schema_version"] == 2
        assert meta["entrypoint"] == "src/main.py"
        assert len(meta["sha256"]) == 64
    for name in names:
        if name.endswith("/"):
            continue
        if Path(name).suffix.lower() in {".py", ".md", ".txt", ".json", ".ini", ".bat", ".cmd", ".ps1"}:
            scan_text(name, zf.read(name).decode("utf-8", errors="ignore"))

example = (HERE / "norm-imprint.example.json").read_text(encoding="utf-8")
assert "password" not in example.lower()
assert "token" not in example.lower()
assert "secret" not in example.lower()

print("PASS: source SHA-256")
print("PASS: no deployment-specific topology signatures")
print("PASS: no private-key/token signatures")
print("PASS: public imprint example contains no secret-like keys")
