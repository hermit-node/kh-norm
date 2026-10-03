from __future__ import annotations

import base64
from pathlib import Path

from norm_runtime.file_access_policy import authorize_path, load_file_access_policy
from norm_runtime.secret_redaction import is_secret_file


def _root() -> Path:
    return Path(__file__).resolve().parents[3]


def _resolve(path: str) -> tuple[Path, object]:
    policy = load_file_access_policy(_root())
    target = authorize_path(path, policy.read_roots, access="read", hardlock=policy.enforce_read_directories)
    if is_secret_file(target):
        raise PermissionError("Secret files must be loaded internally; raw reads are disabled")
    if not target.is_file():
        raise FileNotFoundError(target)
    return target, policy


def _cap(policy, max_bytes: int, *, text: bool) -> int:
    requested = int(max_bytes or policy.read_chunk_bytes)
    if requested <= 0:
        requested = int(policy.read_chunk_bytes)
    return max(1, min(requested, int(policy.read_chunk_max_bytes)))


def read_text(path: str, start_byte: int = 0, max_bytes: int = 0) -> dict:
    """Read a bounded UTF-8 chunk with byte continuation metadata."""
    target, policy = _resolve(path)
    cap = _cap(policy, max_bytes, text=True)
    size = target.stat().st_size
    start = max(0, min(int(start_byte), size))
    with target.open("rb", buffering=policy.read_processing_buffer_bytes) as handle:
        handle.seek(start)
        raw = handle.read(cap)
        nxt = handle.tell()
    return {
        "path": str(target), "mode": "text", "start_byte": start, "next_byte": nxt,
        "returned_bytes": len(raw), "source_size_bytes": size, "truncated": nxt < size,
        "content": raw.decode("utf-8", errors="replace"),
    }


def read_lines(path: str, start_line: int = 1, end_line: int = 0, max_bytes: int = 0, start_byte: int = 0) -> dict:
    """Read an inclusive UTF-8 line range with bounded output and resumable continuation metadata.

    ``start_byte`` is normally zero. When a prior result stopped in the middle of an
    oversized line, resume with both ``next_start_line`` and ``next_byte`` from that
    result so the same logical line continues without being reread from its beginning.
    """
    target, policy = _resolve(path)
    cap = _cap(policy, max_bytes, text=True)
    start = max(1, int(start_line))
    end = int(end_line or 0)
    if end and end < start:
        raise ValueError("end_line must be at or after start_line")
    size = target.stat().st_size
    resume_byte = max(0, min(int(start_byte or 0), size))
    chunks: list[bytes] = []
    returned = 0
    current = start
    truncated = False
    next_line: int | None = None
    first_byte = 0
    next_byte = 0
    last_line: int | None = None
    partial_line = False
    with target.open("rb", buffering=policy.read_processing_buffer_bytes) as handle:
        if resume_byte:
            handle.seek(resume_byte)
        else:
            current = 1
            while current < start:
                if not handle.readline():
                    break
                current += 1
        first_byte = handle.tell()
        while not end or current <= end:
            pos = handle.tell()
            line = handle.readline()
            if not line:
                break
            if returned and returned + len(line) > cap:
                handle.seek(pos)
                truncated = True
                next_line = current
                break
            if not returned and len(line) > cap:
                line = line[:cap]
                handle.seek(pos + len(line))
                truncated = True
                next_line = current
                partial_line = True
            chunks.append(line)
            returned += len(line)
            last_line = current
            if partial_line:
                break
            current += 1
            if returned >= cap:
                truncated = handle.tell() < size and (not end or current <= end)
                if truncated:
                    next_line = current
                break
        next_byte = handle.tell()
    raw = b"".join(chunks)
    return {
        "path": str(target), "mode": "lines", "start_line": start,
        "end_line": last_line, "next_start_line": next_line,
        "start_byte": first_byte, "next_byte": next_byte, "returned_bytes": len(raw),
        "source_size_bytes": size, "truncated": truncated,
        "partial_line": partial_line,
        "content": raw.decode("utf-8", errors="replace"),
    }


def read_bytes(path: str, start_byte: int = 0, max_bytes: int = 0) -> dict:
    """Read literal bytes as Base64 with byte continuation metadata."""
    target, policy = _resolve(path)
    cap = _cap(policy, max_bytes, text=False)
    size = target.stat().st_size
    start = max(0, min(int(start_byte), size))
    with target.open("rb", buffering=policy.read_processing_buffer_bytes) as handle:
        handle.seek(start)
        raw = handle.read(cap)
        nxt = handle.tell()
    return {
        "path": str(target), "mode": "bytes_base64", "start_byte": start, "next_byte": nxt,
        "returned_bytes": len(raw), "source_size_bytes": size, "truncated": nxt < size,
        "base64": base64.b64encode(raw).decode("ascii"),
    }
