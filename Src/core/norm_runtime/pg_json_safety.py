"""Loss-aware, explicitly annotated normalization for PostgreSQL JSONB evidence.

PostgreSQL jsonb rejects U+0000 and unpaired UTF-16 surrogates even though
Python json.dumps can emit their escapes. Never modify the actual tool call,
filesystem arguments, or the in-memory tool result; normalize only the copy
being handed to PostgreSQL.
"""
from __future__ import annotations

import base64
import hashlib
import math
import logging
from typing import Any


def normalize_pg_jsonb(value: Any) -> tuple[Any, list[dict[str, Any]]]:
    """Return JSON-safe data and a bounded audit trail of necessary conversions.

    Affected strings remain strings with explicit literal Unicode escapes.
    Binary values are encoded as base64 objects, not decoded as arbitrary text.
    All changes are reported so that evidence is never presented as verbatim.
    """
    changes: list[dict[str, Any]] = []

    def issue(path: str, reason: str, original: Any) -> None:
        if len(changes) < 64:
            data = original.encode('utf-8', errors='surrogatepass') if isinstance(original, str) else bytes(original)
            changes.append({
                'path': path,
                'reason': reason,
                'original_bytes': len(data),
                'original_sha256': hashlib.sha256(data).hexdigest(),
            })

    def walk(item: Any, path: str) -> Any:
        if isinstance(item, str):
            if '\x00' not in item and not any(0xD800 <= ord(ch) <= 0xDFFF for ch in item):
                return item
            output = ''.join(
                ('\\u0000' if ch == '\x00' else f'\\u{ord(ch):04x}'
                 if 0xD800 <= ord(ch) <= 0xDFFF else ch)
                for ch in item
            )
            issue(path, 'PostgreSQL-incompatible Unicode escaped in text', item)
            return output
        if isinstance(item, (bytes, bytearray, memoryview)):
            raw = bytes(item)
            issue(path, 'binary content encoded as base64', raw)
            return {'_norm_encoding': 'base64', 'byte_length': len(raw), 'data': base64.b64encode(raw).decode('ascii')}
        if isinstance(item, dict):
            out: dict[str, Any] = {}
            for key, child in item.items():
                if not isinstance(key, str):
                    raise TypeError('JSON object keys must be strings')
                converted_key = walk(key, f'{path}.<key>')
                if converted_key in out:
                    raise ValueError('JSON key collision after Unicode normalization')
                out[converted_key] = walk(child, f'{path}.{converted_key}'[:512])
            return out
        if isinstance(item, (list, tuple)):
            return [walk(child, f'{path}[{index}]'[:512]) for index, child in enumerate(item)]
        if isinstance(item, float) and not math.isfinite(item):
            issue(path, 'non-finite number encoded as string', str(item))
            return {'_norm_encoding': 'non-finite-float', 'value': str(item)}
        return item

    return walk(value, '$'), changes


_LOG = logging.getLogger(__name__)


def safe_jsonb(value: Any):
    """Wrap any Norm-owned JSONB value with a safe psycopg 3 adapter.

    This is a persistence-only conversion. Unlike setting process-global JSON
    adapters, it cannot affect other PostgreSQL clients or execute-time values.
    Object and array shapes are preserved for ordinary JSON data. Unavoidable
    non-verbatim conversions emit a bounded warning with paths and hashes;
    task_evidence additionally persists its existing per-row audit notice.
    """
    normalized, changes = normalize_pg_jsonb(value)
    if changes:
        _LOG.warning(
            'PostgreSQL JSONB normalized %d invalid value(s); paths=%s; '
            'persistence copy only, original tool payload unchanged',
            len(changes),
            [entry['path'] for entry in changes[:8]],
        )
    # Import here so the pure sanitizer can be regression-tested without a
    # running PostgreSQL installation or the psycopg binary extension.
    from psycopg.types.json import Jsonb
    return Jsonb(normalized)


def dumps_pg_json(value: Any) -> str:
    """Canonical JSON/JSONB serializer for Norm-owned pooled connections.

    Psycopg's ``set_json_dumps(..., conn)`` installs this function on each
    connection once at pool creation. This protects direct ``Jsonb(value)``
    call sites across the process; ``safe_jsonb`` additionally protects
    direct/offline call sites not using the shared pool. The value supplied by
    the caller is never mutated. Never use a process-global psycopg adapter.
    """
    import json
    normalized, changes = normalize_pg_jsonb(value)
    if changes:
        _LOG.warning(
            'PostgreSQL JSON/JSONB adapted %d incompatible value(s); paths=%s; '
            'database representation differs from original',
            len(changes), [entry['path'] for entry in changes[:8]],
        )
    # Do not let NaN or Infinity survive into PostgreSQL JSONB.
    return json.dumps(normalized, ensure_ascii=False, allow_nan=False)
