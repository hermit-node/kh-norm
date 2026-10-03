from __future__ import annotations

from pathlib import Path
import hashlib as _hashlib


def append_text(path: str, content: str) -> dict:
    """Append exact UTF-8 text to a file, creating parent directories and the file when needed."""
    target = Path(path).expanduser()
    target.parent.mkdir(parents=True, exist_ok=True)
    before = target.stat().st_size if target.exists() else 0
    with target.open("a", encoding="utf-8", newline="") as handle:
        handle.write(str(content))
    return {"ok": True, "path": str(target.resolve()), "sha256": _hashlib.sha256(target.read_bytes()).hexdigest(), "file_mutation": True, "mode": "append", "bytes_before": before, "bytes_after": target.stat().st_size}


def insert_text(path: str, content: str, line: int) -> dict:
    """Insert exact UTF-8 text before a one-based line number, or at EOF when the line is beyond EOF."""
    if int(line) < 1:
        raise ValueError("line must be 1 or greater")
    target = Path(path).expanduser()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.touch(exist_ok=True)
    with target.open("r", encoding="utf-8", newline="") as handle:
        existing = handle.readlines()
    incoming = str(content).splitlines(keepends=True)
    if content and not incoming:
        incoming = [str(content)]
    index = min(int(line) - 1, len(existing))
    existing[index:index] = incoming
    with target.open("w", encoding="utf-8", newline="") as handle:
        handle.writelines(existing)
    return {"ok": True, "path": str(target.resolve()), "sha256": _hashlib.sha256(target.read_bytes()).hexdigest(), "file_mutation": True, "mode": "insert", "line": index + 1, "inserted_lines": len(incoming), "bytes_after": target.stat().st_size}


def run(payload: dict) -> dict:
    """Legacy broker entrypoint: payload action=append|insert with path/content and optional line."""
    payload = dict(payload or {})
    action = str(payload.get("action") or "append").strip().lower()
    if action == "append":
        return append_text(str(payload["path"]), str(payload.get("content") or ""))
    if action == "insert":
        return insert_text(str(payload["path"]), str(payload.get("content") or ""), int(payload["line"]))
    raise ValueError("action must be append or insert")
