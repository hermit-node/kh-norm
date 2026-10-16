from __future__ import annotations

from pathlib import Path

DEFAULT_BACKUP_LIMIT = 12


def _newest(paths: list[Path]) -> list[Path]:
    return sorted(paths, key=lambda p: (p.stat().st_mtime_ns, p.name), reverse=True)


def prune_backup_files(root: str | Path, *, pattern: str = "*", limit: int = DEFAULT_BACKUP_LIMIT) -> list[str]:
    """Keep only the newest N matching backup files in one backup class/location."""
    folder = Path(root)
    if not folder.is_dir():
        return []
    cap = max(1, int(limit))
    files = _newest([p for p in folder.glob(pattern) if p.is_file()])
    removed: list[str] = []
    for stale in files[cap:]:
        stale.unlink(missing_ok=True)
        removed.append(str(stale))
    return removed


def prune_backup_sets(
    root: str | Path,
    *,
    pattern: str = "Norm-*.zip",
    limit: int = DEFAULT_BACKUP_LIMIT,
    companion_suffix: str = ".sha256",
) -> list[str]:
    """Keep N primary backup archives; a checksum companion counts as part of its archive set."""
    folder = Path(root)
    if not folder.is_dir():
        return []
    cap = max(1, int(limit))
    primaries = _newest([p for p in folder.glob(pattern) if p.is_file()])
    removed: list[str] = []
    for stale in primaries[cap:]:
        companion = stale.with_name(stale.name + companion_suffix)
        stale.unlink(missing_ok=True)
        removed.append(str(stale))
        if companion.is_file():
            companion.unlink(missing_ok=True)
            removed.append(str(companion))
    return removed
