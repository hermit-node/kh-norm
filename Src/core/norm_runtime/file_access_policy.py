from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from .settings import load_path_settings, load_plugin_settings, load_settings


@dataclass(frozen=True)
class FileAccessPolicy:
    internal_roots: tuple[Path, ...]
    read_roots: tuple[Path, ...]
    write_roots: tuple[Path, ...]
    enforce_read_directories: bool
    enforce_write_directories: bool
    read_chunk_bytes: int
    read_chunk_max_bytes: int
    read_processing_buffer_bytes: int
    capability: str


def _split_dirs(raw: str) -> list[str]:
    return [part.strip() for part in str(raw or "").split(";") if part.strip()]


def _resolve_token(root: Path, token: str, *, paths: dict[str, Path], plugin_root: Path) -> Path:
    aliases = {
        "@runtime": root,
        "@state": paths["state_root"],
        "@workspace": paths["workspace_root"],
        "@temp": paths["temp_root"],
        "@documents": paths["documents_root"],
        "@docs": root / "docs",
        "@plugins": plugin_root,
    }
    lowered = token.strip().lower()
    if lowered in aliases:
        return aliases[lowered].resolve()
    expanded = Path(os.path.expandvars(os.path.expanduser(token.strip())))
    return expanded.resolve() if expanded.is_absolute() else (root / expanded).resolve()


def _dedupe(paths: list[Path]) -> tuple[Path, ...]:
    seen: set[str] = set()
    result: list[Path] = []
    for path in paths:
        key = str(path).casefold()
        if key in seen:
            continue
        seen.add(key)
        result.append(path)
    return tuple(result)


def _resolved_dirs(root: Path, raw: str, *, paths: dict[str, Path], plugin_root: Path) -> tuple[Path, ...]:
    return _dedupe([_resolve_token(root, item, paths=paths, plugin_root=plugin_root) for item in _split_dirs(raw)])


def load_file_access_policy(root: Path, *, capability: str = "core") -> FileAccessPolicy:
    """Load the single canonical filesystem policy for core tools and built-in plugins.

    ``internal_directories`` are descriptive/protective roots owned by trusted Norm
    runtime code. They are deliberately not merged into tool roots. ``read_directories``
    and ``write_directories`` are the shared model/native/plugin defaults. A capability
    may add roots through ``[file_access_overrides] <capability>.read_add/write_add``.
    """
    root = Path(root).resolve()
    parser = load_settings(root)
    paths = load_path_settings(root)
    plugin_root = load_plugin_settings(root)["plugin_root"]
    defaults = r"@workspace;@temp;@docs;@plugins;@documents"
    internal_raw = parser.get("file_access", "internal_directories", fallback="@state")
    read_raw = parser.get("file_access", "read_directories", fallback=defaults)
    write_raw = parser.get("file_access", "write_directories", fallback=defaults)
    internal_roots = _resolved_dirs(root, internal_raw, paths=paths, plugin_root=plugin_root)
    read_roots = list(_resolved_dirs(root, read_raw, paths=paths, plugin_root=plugin_root))
    write_roots = list(_resolved_dirs(root, write_raw, paths=paths, plugin_root=plugin_root))

    cap = str(capability or "core").strip().lower().replace(" ", "_") or "core"
    if parser.has_section("file_access_overrides"):
        read_add = parser.get("file_access_overrides", f"{cap}.read_add", fallback="")
        write_add = parser.get("file_access_overrides", f"{cap}.write_add", fallback="")
        read_roots.extend(_resolved_dirs(root, read_add, paths=paths, plugin_root=plugin_root))
        write_roots.extend(_resolved_dirs(root, write_add, paths=paths, plugin_root=plugin_root))

    read_roots_t = _dedupe(read_roots)
    write_roots_t = _dedupe(write_roots)
    if not read_roots_t or not write_roots_t:
        raise ValueError("settings.ini [file_access] read/write directories must not be empty")
    default_chunk = max(1024, parser.getint("file_access", "read_chunk_bytes", fallback=393_216))
    max_chunk = max(default_chunk, parser.getint("file_access", "read_chunk_max_bytes", fallback=4_194_304))
    processing = max(max_chunk, parser.getint("file_access", "read_processing_buffer_bytes", fallback=25_165_824))
    return FileAccessPolicy(
        internal_roots=internal_roots,
        read_roots=read_roots_t,
        write_roots=write_roots_t,
        enforce_read_directories=parser.getboolean("file_access", "enforce_read_directories", fallback=False),
        enforce_write_directories=parser.getboolean("file_access", "enforce_write_directories", fallback=False),
        read_chunk_bytes=default_chunk,
        read_chunk_max_bytes=max_chunk,
        read_processing_buffer_bytes=processing,
        capability=cap,
    )


def authorize_path(raw_path: str | Path, roots: tuple[Path, ...], *, access: Literal["read", "write"], hardlock: bool = False) -> Path:
    """Resolve a path and reject anything outside the supplied roots.

    ``hardlock`` changes the operator/model guidance only; it never disables the
    boundary. This makes core and plugin behavior identical.
    """
    if not isinstance(raw_path, (str, Path)) or not str(raw_path).strip():
        raise ValueError("path must be a non-empty string")
    if not roots:
        raise ValueError("at least one allowed root is required")
    candidate = Path(os.path.expandvars(os.path.expanduser(str(raw_path).strip())))
    if not candidate.is_absolute():
        candidate = roots[0] / candidate
    resolved = candidate.resolve(strict=False)
    if any(resolved == allowed or resolved.is_relative_to(allowed) for allowed in roots):
        return resolved
    if hardlock:
        raise PermissionError(
            f"HARDLOCKED out of directory for {access}: {resolved}. "
            "Do not retry this filesystem operation through another Norm Python/native/plugin file capability."
        )
    raise PermissionError(f"path is outside allowed {access} roots: {resolved}")
