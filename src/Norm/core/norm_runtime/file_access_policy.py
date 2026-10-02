from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from .settings import load_path_settings, load_plugin_settings, load_settings


@dataclass(frozen=True)
class FileAccessPolicy:
    read_roots: tuple[Path, ...]
    write_roots: tuple[Path, ...]
    enforce_read_directories: bool
    enforce_write_directories: bool
    read_chunk_bytes: int
    read_chunk_max_bytes: int
    read_processing_buffer_bytes: int


def _split_dirs(raw: str) -> list[str]:
    return [part.strip() for part in str(raw or "").split(";") if part.strip()]


def _resolve_token(root: Path, token: str, *, paths: dict[str, Path], plugin_root: Path) -> Path:
    aliases = {
        "@runtime": root,
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


def load_file_access_policy(root: Path) -> FileAccessPolicy:
    root = Path(root).resolve()
    parser = load_settings(root)
    paths = load_path_settings(root)
    plugin_root = load_plugin_settings(root)["plugin_root"]
    defaults = r"@workspace;@temp;@docs;@plugins;@documents"
    read_raw = parser.get("file_access", "read_directories", fallback=defaults)
    write_raw = parser.get("file_access", "write_directories", fallback=defaults)
    read_roots = _dedupe([_resolve_token(root, item, paths=paths, plugin_root=plugin_root) for item in _split_dirs(read_raw)])
    write_roots = _dedupe([_resolve_token(root, item, paths=paths, plugin_root=plugin_root) for item in _split_dirs(write_raw)])
    if not read_roots or not write_roots:
        raise ValueError("settings.ini [file_access] read/write directories must not be empty")
    default_chunk = max(1024, parser.getint("file_access", "read_chunk_bytes", fallback=393_216))
    max_chunk = max(default_chunk, parser.getint("file_access", "read_chunk_max_bytes", fallback=4_194_304))
    processing = max(max_chunk, parser.getint("file_access", "read_processing_buffer_bytes", fallback=25_165_824))
    return FileAccessPolicy(
        read_roots=read_roots,
        write_roots=write_roots,
        enforce_read_directories=parser.getboolean("file_access", "enforce_read_directories", fallback=False),
        enforce_write_directories=parser.getboolean("file_access", "enforce_write_directories", fallback=False),
        read_chunk_bytes=default_chunk,
        read_chunk_max_bytes=max_chunk,
        read_processing_buffer_bytes=processing,
    )


def authorize_path(raw_path: str | Path, roots: tuple[Path, ...], *, access: Literal["read", "write"], hardlock: bool) -> Path:
    if not isinstance(raw_path, (str, Path)) or not str(raw_path).strip():
        raise ValueError("path must be a non-empty string")
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
    return resolved
