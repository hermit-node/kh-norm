from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from threading import RLock
from urllib.parse import quote

REDACTED = '[REDACTED]'
_lock = RLock()
_values: set[str] = set()
_paths: set[Path] = set()
_SECRET_NAME = r'(?:[A-Za-z0-9_]*(?:password|passwd|pwd|secret|token|api_key|apikey|access_key|private_key|authorization)[A-Za-z0-9_]*)'
_KEY = re.compile(_SECRET_NAME, re.I)
_ASSIGNMENT = re.compile(
    r'''(?ix)(?P<prefix>(?<![\w])(?:\$env:)?["']?''' + _SECRET_NAME + r'''["']?\s*[:=]\s*)
    (?P<value>"(?:\\.|[^"\\])*"|'(?:\\.|''|[^'\\])*'|[^\s,;}\r\n]+)'''
)
_URL = re.compile(r'(\b[a-z][a-z0-9+.-]*://[^\s/@:]+:)[^\s/@]+(@)', re.I)
_BEARER = re.compile(r'\b(Bearer\s+)[A-Za-z0-9._~+/=-]+', re.I)


def register_secrets(values: dict, path: Path | None = None) -> None:
    """Register locally; never emit or persist secret material."""
    with _lock:
        if path is not None:
            _paths.add(path.resolve())
        for key, raw in values.items():
            if not _KEY.fullmatch(str(key)) or not isinstance(raw, str):
                continue
            value = raw.strip()
            for candidate in (value, value.strip('\"\'')):
                if candidate and candidate != REDACTED:
                    _values.update((candidate, json.dumps(candidate, ensure_ascii=False)[1:-1],
                                    quote(candidate, safe=''), candidate.replace("'", "''")))


def is_secret_file(path: Path) -> bool:
    with _lock:
        return path.name.lower() == '.env' or path.name.lower().startswith('.env.') or path.resolve() in _paths


def redact_text(text: str) -> str:
    with _lock:
        values = sorted(_values, key=len, reverse=True)
    for value in values:
        text = text.replace(value, REDACTED)
    text = _BEARER.sub(lambda m: m[1] + REDACTED, text)
    text = _ASSIGNMENT.sub(lambda m: m['prefix'] + REDACTED, text)
    text = _URL.sub(lambda m: m[1] + REDACTED + m[2], text)
    return text


def redact(value):
    """Copy presentation/evidence data; never mutate execution arguments."""
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, dict):
        return {k: REDACTED if _KEY.fullmatch(str(k)) else redact(v) for k, v in value.items()}
    if isinstance(value, list):
        return [redact(v) for v in value]
    if isinstance(value, tuple):
        return tuple(redact(v) for v in value)
    return value


class RedactingFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return redact_text(super().format(record))


register_secrets(dict(os.environ))
