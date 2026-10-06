from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tarfile
import zipfile
from collections import Counter
from pathlib import Path, PurePosixPath
from typing import Iterable

_ARCHIVE_SUFFIXES = (
    '.7z', '.zip', '.rar', '.tar', '.tgz', '.tbz', '.tbz2', '.txz', '.tzst',
    '.tar.gz', '.tar.bz2', '.tar.xz', '.tar.zst', '.gz', '.bz2', '.xz', '.zst',
)


def _norm_member(value: str) -> str:
    text = str(value or '').replace('\\', '/').strip('/')
    parts = [p for p in PurePosixPath(text).parts if p not in ('', '.')]
    if any(p == '..' for p in parts):
        raise ValueError(f'unsafe archive member path: {value!r}')
    return '/'.join(parts)


def looks_like_archive(path: str | Path) -> bool:
    name = str(Path(path).name).lower()
    return any(name.endswith(suffix) for suffix in _ARCHIVE_SUFFIXES)


def find_7zip() -> Path | None:
    configured = str(os.environ.get('NORM_7ZIP') or '').strip()
    candidates: list[Path] = []
    if configured:
        candidates.append(Path(configured))
    # Prefer Norm's package-managed private copy before ambient system installs.
    # This keeps archive behavior portable and independent of target-machine PATH.
    candidates.append(Path(__file__).resolve().parents[2] / 'tools' / '7zip' / '7z.exe')
    for name in ('7z.exe', '7zz.exe', '7za.exe', '7z', '7zz', '7za'):
        found = shutil.which(name)
        if found:
            candidates.append(Path(found))
    if os.name == 'nt':
        candidates.extend([
            Path(os.environ.get('ProgramFiles', r'C:\Program Files')) / '7-Zip' / '7z.exe',
            Path(os.environ.get('ProgramFiles(x86)', r'C:\Program Files (x86)')) / '7-Zip' / '7z.exe',
        ])
    seen: set[str] = set()
    for candidate in candidates:
        try:
            resolved = candidate.expanduser().resolve()
        except OSError:
            continue
        key = str(resolved).lower()
        if key in seen:
            continue
        seen.add(key)
        if resolved.is_file():
            return resolved
    return None


def _run_7z(args: list[str], *, timeout: int = 120) -> subprocess.CompletedProcess:
    exe = find_7zip()
    if exe is None:
        raise FileNotFoundError('7-Zip CLI not found; install 7-Zip or set NORM_7ZIP')
    return subprocess.run(
        [str(exe), '-sccUTF-8', *args], capture_output=True, timeout=max(1, int(timeout)), check=False,
        encoding='utf-8', errors='replace',
    )


def _parse_7z_slt(text: str) -> list[dict]:
    rows: list[dict] = []
    current: dict[str, str] = {}
    for raw in str(text or '').splitlines():
        line = raw.rstrip('\r\n')
        if not line.strip():
            if current:
                rows.append(current)
                current = {}
            continue
        if ' = ' not in line:
            continue
        key, value = line.split(' = ', 1)
        current[key.strip()] = value.strip()
    if current:
        rows.append(current)
    return rows


def _manifest_7z(path: Path) -> list[dict]:
    completed = _run_7z(['l', '-slt', '-ba', str(path)])
    if completed.returncode not in (0, 1):
        raise RuntimeError(f'7-Zip list failed exit={completed.returncode}: {(completed.stderr or completed.stdout)[-2000:]}')
    entries: list[dict] = []
    for row in _parse_7z_slt(completed.stdout):
        raw_path = row.get('Path')
        if not raw_path:
            continue
        member = _norm_member(raw_path)
        if not member:
            continue
        attrs = str(row.get('Attributes') or '')
        is_dir = attrs.startswith('D') or raw_path.endswith(('/', '\\'))
        try:
            size = int(row.get('Size') or 0)
        except ValueError:
            size = 0
        try:
            packed = int(row.get('Packed Size') or 0)
        except ValueError:
            packed = 0
        entries.append({
            'path': member,
            'type': 'directory' if is_dir else 'file',
            'size': size if not is_dir else 0,
            'packed_size': packed if not is_dir else 0,
            'crc': str(row.get('CRC') or ''),
            'modified': str(row.get('Modified') or ''),
            'method': str(row.get('Method') or ''),
            'encrypted': str(row.get('Encrypted') or '-').strip() == '+',
        })
    return entries


def _manifest_zip(path: Path) -> list[dict]:
    rows = []
    with zipfile.ZipFile(path) as archive:
        for info in archive.infolist():
            member = _norm_member(info.filename)
            if not member:
                continue
            is_dir = info.is_dir()
            rows.append({
                'path': member,
                'type': 'directory' if is_dir else 'file',
                'size': 0 if is_dir else int(info.file_size),
                'packed_size': 0 if is_dir else int(info.compress_size),
                'crc': '' if is_dir else f'{int(info.CRC):08X}',
                'modified': '',
                'method': str(info.compress_type),
                'encrypted': bool(info.flag_bits & 0x1),
            })
    return rows


def _manifest_tar(path: Path) -> list[dict]:
    rows = []
    with tarfile.open(path, 'r:*') as archive:
        for info in archive.getmembers():
            member = _norm_member(info.name)
            if not member:
                continue
            is_dir = info.isdir()
            rows.append({
                'path': member,
                'type': 'directory' if is_dir else 'file',
                'size': 0 if is_dir else int(info.size),
                'packed_size': None,
                'crc': '',
                'modified': str(info.mtime or ''),
                'method': 'tar',
                'encrypted': False,
            })
    return rows


def archive_manifest(path: str | Path, *, max_entries: int = 5000) -> dict:
    target = Path(path).resolve()
    if not target.is_file():
        raise FileNotFoundError(target)
    backend = '7zip'
    try:
        entries = _manifest_7z(target)
        exe = find_7zip()
        backend_detail = str(exe) if exe else ''
    except Exception:
        lower = target.name.lower()
        if lower.endswith('.zip'):
            entries = _manifest_zip(target)
            backend = 'python-zipfile-fallback'
            backend_detail = 'zipfile'
        elif any(lower.endswith(s) for s in ('.tar', '.tar.gz', '.tgz', '.tar.bz2', '.tbz', '.tbz2', '.tar.xz', '.txz')):
            entries = _manifest_tar(target)
            backend = 'python-tarfile-fallback'
            backend_detail = 'tarfile'
        else:
            raise
    entries.sort(key=lambda row: (row['path'].lower(), row['path']))
    files = [row for row in entries if row['type'] == 'file']
    dirs = [row for row in entries if row['type'] == 'directory']
    canonical = '\n'.join(f"{row['path']}\0{int(row['size'])}" for row in files).encode('utf-8', errors='surrogatepass')
    fingerprint = hashlib.sha256(canonical).hexdigest()
    cap = max(1, min(int(max_entries or 5000), 20000))
    return {
        'archive_path': str(target),
        'archive_backend': backend,
        'archive_backend_detail': backend_detail,
        'file_count': len(files),
        'directory_count': len(dirs),
        'total_uncompressed_bytes': sum(int(row['size']) for row in files),
        'encrypted_members': sum(1 for row in files if row.get('encrypted')),
        'tree_size_fingerprint': fingerprint,
        'entries': entries[:cap],
        'entries_truncated': len(entries) > cap,
        'entry_count': len(entries),
    }


def _stream_member_7z(path: Path, member: str):
    exe = find_7zip()
    if exe is None:
        raise FileNotFoundError('7-Zip CLI not found; install 7-Zip or set NORM_7ZIP')
    normalized = _norm_member(member)
    if not normalized:
        raise ValueError('member must be a non-empty archive path')
    proc = subprocess.Popen(
        [str(exe), '-sccUTF-8', 'x', '-so', '-bd', '-y', '-spd', str(path), normalized],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    return proc


def _skip_stream(handle, count: int) -> int:
    remaining = max(0, int(count))
    skipped = 0
    while remaining > 0:
        chunk = handle.read(min(262144, remaining))
        if not chunk:
            break
        skipped += len(chunk)
        remaining -= len(chunk)
    return skipped


def _fallback_read_member(path: Path, member: str, *, start: int, cap: int) -> bytes:
    lower = path.name.lower()
    normalized = _norm_member(member)
    if lower.endswith('.zip'):
        with zipfile.ZipFile(path) as archive, archive.open(normalized, 'r') as handle:
            _skip_stream(handle, start)
            return handle.read(cap + 1)
    if any(lower.endswith(s) for s in ('.tar', '.tar.gz', '.tgz', '.tar.bz2', '.tbz', '.tbz2', '.tar.xz', '.txz')):
        with tarfile.open(path, 'r:*') as archive:
            info = archive.getmember(normalized)
            handle = archive.extractfile(info)
            if handle is None:
                raise FileNotFoundError(f'archive member not found: {normalized}')
            with handle:
                _skip_stream(handle, start)
                return handle.read(cap + 1)
    raise FileNotFoundError('7-Zip CLI is required for this archive format')


def _fallback_hash_member(path: Path, member: str) -> tuple[str, int]:
    lower = path.name.lower()
    normalized = _norm_member(member)
    digest = hashlib.sha256(); size = 0
    def consume(handle):
        nonlocal size
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk); size += len(chunk)
    if lower.endswith('.zip'):
        with zipfile.ZipFile(path) as archive, archive.open(normalized, 'r') as handle:
            consume(handle)
    elif any(lower.endswith(s) for s in ('.tar', '.tar.gz', '.tgz', '.tar.bz2', '.tbz', '.tbz2', '.tar.xz', '.txz')):
        with tarfile.open(path, 'r:*') as archive:
            info = archive.getmember(normalized)
            handle = archive.extractfile(info)
            if handle is None:
                raise FileNotFoundError(f'archive member not found: {normalized}')
            with handle:
                consume(handle)
    else:
        raise FileNotFoundError('7-Zip CLI is required for this archive format')
    return digest.hexdigest(), size

def read_archive_member(path: str | Path, member: str, *, start_byte: int = 0, max_bytes: int = 393216, mode: str = 'text') -> dict:
    target = Path(path).resolve()
    manifest = archive_manifest(target, max_entries=20000)
    normalized = _norm_member(member)
    row = next((item for item in manifest['entries'] if item['path'] == normalized and item['type'] == 'file'), None)
    if row is None:
        raise FileNotFoundError(f'archive member not found: {normalized}')
    if row.get('encrypted'):
        raise PermissionError(f'archive member is encrypted/password-protected: {normalized}')
    cap = max(1, min(int(max_bytes or 393216), 4_194_304))
    start = max(0, int(start_byte or 0))
    if find_7zip() is None:
        data = _fallback_read_member(target, normalized, start=start, cap=cap)
        truncated = len(data) > cap
        data = data[:cap]
    else:
        proc = _stream_member_7z(target, normalized)
        assert proc.stdout is not None
        try:
            remaining_skip = start
            while remaining_skip > 0:
                chunk = proc.stdout.read(min(262144, remaining_skip))
                if not chunk:
                    break
                remaining_skip -= len(chunk)
            data = proc.stdout.read(cap + 1)
            truncated = len(data) > cap
            data = data[:cap]
        finally:
            if proc.poll() is None:
                proc.terminate()
            try:
                _out, err = proc.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill(); _out, err = proc.communicate()
        if proc.returncode not in (0, -15, 1) and not data:
            raise RuntimeError(f'7-Zip member read failed exit={proc.returncode}: {err.decode("utf-8", errors="replace")[-2000:]}')
    result = {
        'archive_path': str(target), 'member': normalized, 'member_size': int(row['size']),
        'mode': mode, 'start_byte': start, 'next_byte': start + len(data),
        'returned_bytes': len(data), 'truncated': bool(truncated or start + len(data) < int(row['size'])),
        'archive_backend': manifest['archive_backend'],
    }
    if mode == 'bytes_base64':
        import base64
        result['base64'] = base64.b64encode(data).decode('ascii')
    else:
        result['content'] = data.decode('utf-8', errors='replace')
    return result


def hash_archive_member(path: str | Path, member: str) -> dict:
    target = Path(path).resolve()
    manifest = archive_manifest(target, max_entries=20000)
    normalized = _norm_member(member)
    row = next((item for item in manifest['entries'] if item['path'] == normalized and item['type'] == 'file'), None)
    if row is None:
        raise FileNotFoundError(f'archive member not found: {normalized}')
    if row.get('encrypted'):
        raise PermissionError(f'archive member is encrypted/password-protected: {normalized}')
    digest = hashlib.sha256(); size = 0
    if find_7zip() is None:
        value, size = _fallback_hash_member(target, normalized)
        return {'archive_path': str(target), 'member': normalized, 'size': size, 'sha256': value}
    else:
        proc = _stream_member_7z(target, normalized)
        assert proc.stdout is not None
        while True:
            chunk = proc.stdout.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk); size += len(chunk)
        _out, err = proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError(f'7-Zip member hash failed exit={proc.returncode}: {err.decode("utf-8", errors="replace")[-2000:]}')
    return {'archive_path': str(target), 'member': normalized, 'size': size, 'sha256': digest.hexdigest()}


def _directory_manifest(directory: Path) -> dict[str, int]:
    rows: dict[str, int] = {}
    for child in directory.rglob('*'):
        if child.is_file():
            rel = child.relative_to(directory).as_posix()
            rows[_norm_member(rel)] = int(child.stat().st_size)
    return rows


def compare_archive_to_directory(path: str | Path, directory: str | Path, *, hash_if_tree_matches: bool = True, max_mismatches: int = 100) -> dict:
    target = Path(path).resolve(); folder = Path(directory).resolve()
    if not folder.is_dir():
        raise NotADirectoryError(folder)
    manifest = archive_manifest(target, max_entries=20000)
    archive_files = {row['path']: int(row['size']) for row in manifest['entries'] if row['type'] == 'file'}
    folder_files = _directory_manifest(folder)
    archive_paths = set(archive_files); folder_paths = set(folder_files)
    missing_from_folder = sorted(archive_paths - folder_paths)
    extra_in_folder = sorted(folder_paths - archive_paths)
    size_mismatches = sorted(
        (name, archive_files[name], folder_files[name])
        for name in archive_paths & folder_paths
        if archive_files[name] != folder_files[name]
    )
    tree_size_match = not missing_from_folder and not extra_in_folder and not size_mismatches
    sha_mismatches: list[dict] = []
    hashed = 0
    content_identical: bool | None = None
    if tree_size_match and hash_if_tree_matches:
        content_identical = True
        for name in sorted(archive_paths):
            archive_hash = hash_archive_member(target, name)['sha256']
            digest = hashlib.sha256()
            with (folder / Path(*PurePosixPath(name).parts)).open('rb') as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b''):
                    digest.update(chunk)
            folder_hash = digest.hexdigest(); hashed += 1
            if archive_hash != folder_hash:
                content_identical = False
                if len(sha_mismatches) < max(1, int(max_mismatches)):
                    sha_mismatches.append({'path': name, 'archive_sha256': archive_hash, 'directory_sha256': folder_hash})
    return {
        'archive_path': str(target), 'directory_path': str(folder),
        'archive_file_count': len(archive_files), 'directory_file_count': len(folder_files),
        'tree_size_match': tree_size_match,
        'missing_from_directory': missing_from_folder[:max_mismatches],
        'extra_in_directory': extra_in_folder[:max_mismatches],
        'size_mismatches': [ {'path': p, 'archive_size': a, 'directory_size': b} for p,a,b in size_mismatches[:max_mismatches] ],
        'sha_files_checked': hashed,
        'sha_mismatches': sha_mismatches,
        'content_identical': content_identical,
        'comparison_order': ['tree', 'uncompressed_size', 'sha256' if hash_if_tree_matches else 'sha256_skipped'],
        'archive_tree_size_fingerprint': manifest['tree_size_fingerprint'],
    }
