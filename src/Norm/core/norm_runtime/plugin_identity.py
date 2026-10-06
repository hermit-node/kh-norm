from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path, PurePosixPath


def _source_files(folder: Path) -> list[Path]:
    """Return exact distributed source payload files for a schema-2 plugin."""
    src = folder / "src"
    if not src.is_dir():
        raise ValueError("schema-2 plugin requires src/ directory")
    files: list[Path] = []
    for path in sorted(src.rglob("*")):
        rel = path.relative_to(src)
        if any(part.startswith(".") or part == "__pycache__" for part in rel.parts):
            continue
        if path.is_symlink():
            raise ValueError(f"plugin source cannot contain symlink: src/{rel.as_posix()}")
        if path.is_file() and path.suffix != ".pyc":
            files.append(path)
    return files


def source_tree_sha256(folder: Path) -> str:
    """Hash relative path + exact bytes for every file under src/ deterministically."""
    src = folder / "src"
    h = hashlib.sha256()
    for path in _source_files(folder):
        rel = path.relative_to(src).as_posix().encode("utf-8")
        data = path.read_bytes()
        h.update(len(rel).to_bytes(4, "big"))
        h.update(rel)
        h.update(len(data).to_bytes(8, "big"))
        h.update(data)
    return h.hexdigest()


def identity_files(folder: Path) -> list[Path]:
    """Compatibility helper: schema 2 covers src/ only; legacy schema 1 covers old package files."""
    manifest = folder / "plugin.json"
    if manifest.is_file():
        try:
            meta = json.loads(manifest.read_text(encoding="utf-8-sig"))
        except Exception:
            meta = {}
        if isinstance(meta, dict) and meta.get("schema_version") == 2:
            return _source_files(folder)

    files: list[Path] = []
    for path in sorted(folder.rglob("*")):
        rel = path.relative_to(folder)
        if any(part.startswith(".") or part == "__pycache__" for part in rel.parts):
            continue
        if path.is_symlink():
            raise ValueError(f"identity cannot cover symlink: {rel}")
        if path.is_file() and rel.as_posix() != "SHA256SUMS" and path.suffix != ".pyc":
            files.append(path)
    return files


def _safe_entrypoint(folder: Path, value: str) -> Path:
    raw = str(value or "").strip()
    if not raw:
        raise ValueError("plugin identity requires entrypoint")
    if ":" in raw:
        raise ValueError("schema-2 entrypoint is a source module path, not path:function")
    rel = PurePosixPath(raw)
    if rel.is_absolute() or ".." in rel.parts or "\\" in raw or ":" in raw or rel.as_posix() != raw:
        raise ValueError("unsafe plugin entrypoint")
    if not rel.parts or rel.parts[0] != "src":
        raise ValueError("schema-2 entrypoint must be under src/")
    target = (folder / Path(*rel.parts)).resolve()
    if not target.is_relative_to((folder / "src").resolve()) or not target.is_file() or target.suffix.lower() != ".py":
        raise ValueError("schema-2 entrypoint must reference an existing .py under src/")
    return target


def _verify_schema2(folder: Path, meta: dict, legacy: dict | None) -> dict:
    for field in ("name", "version", "date", "entrypoint", "sha256"):
        if not isinstance(meta.get(field), str) or not meta[field].strip():
            raise ValueError(f"plugin identity requires {field}")
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", meta["date"]):
        raise ValueError("plugin identity date must be YYYY-MM-DD")
    if not re.fullmatch(r"[0-9a-f]{64}", meta["sha256"]):
        raise ValueError("plugin identity sha256 must be lowercase SHA-256")
    _safe_entrypoint(folder, meta["entrypoint"])

    legacy = legacy or {}
    for field, key in (("name", "NAME"), ("version", "VERSION")):
        if key in legacy and str(meta.get(field) or "") != str(legacy[key]):
            raise ValueError(f"plugin.json disagrees with legacy {key}")

    actual = source_tree_sha256(folder)
    if actual != meta["sha256"]:
        raise ValueError(f"plugin source sha256 mismatch: expected {meta['sha256']} got {actual}")
    return meta


def _verify_schema1(folder: Path, meta: dict, legacy: dict | None) -> dict:
    """Read-only compatibility for existing third-party plugins using SHA256SUMS."""
    sums = folder / "SHA256SUMS"
    if not sums.is_file():
        raise ValueError("schema-1 plugin requires SHA256SUMS")
    for field in ("name", "version", "id", "version_id", "folder"):
        if not isinstance(meta.get(field), str) or not meta[field].strip():
            raise ValueError(f"plugin identity requires {field}")
    if meta["folder"] != folder.name:
        raise ValueError("plugin identity folder mismatch")
    legacy = legacy or {}
    for field, key in (("name", "NAME"), ("version", "VERSION"), ("entrypoint", "ENTRYPOINT")):
        if key in legacy and str(meta.get(field) or "") != str(legacy[key]):
            raise ValueError(f"plugin.json disagrees with legacy {key}")
    expected: dict[str, str] = {}
    for line in sums.read_text(encoding="utf-8-sig").splitlines():
        if not line.strip():
            continue
        match = re.fullmatch(r"([0-9a-f]{64})  (.+)", line)
        if not match:
            raise ValueError("malformed SHA256SUMS line")
        digest, rel = match.groups()
        path = PurePosixPath(rel)
        if path.is_absolute() or ".." in path.parts or "\\" in rel or ":" in rel or path.as_posix() != rel:
            raise ValueError("unsafe checksum path")
        if rel in expected:
            raise ValueError("duplicate checksum path")
        expected[rel] = digest
    files = identity_files(folder)
    actual = {p.relative_to(folder).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
    if set(actual) != set(expected):
        raise ValueError("SHA256SUMS must cover every plugin file exactly once (except itself and generated state)")
    for rel, digest in actual.items():
        if digest != expected[rel]:
            raise ValueError(f"plugin checksum mismatch: {rel}")
    return meta


def verify_identity(folder: Path, legacy: dict | None = None) -> dict | None:
    """Verify local plugin identity/integrity, not publisher authenticity."""
    manifest = folder / "plugin.json"
    sums = folder / "SHA256SUMS"
    if not manifest.exists() and not sums.exists():
        return None  # Legacy third-party plugins remain supported.
    if not manifest.is_file():
        raise ValueError("plugin identity requires plugin.json")
    meta = json.loads(manifest.read_text(encoding="utf-8-sig"))
    if not isinstance(meta, dict):
        raise ValueError("plugin.json must contain an object")
    schema = meta.get("schema_version")
    if schema == 2:
        return _verify_schema2(folder, meta, legacy)
    if schema == 1:
        return _verify_schema1(folder, meta, legacy)
    raise ValueError("unsupported plugin identity schema")
