from __future__ import annotations

import base64
import sys
from pathlib import Path
from typing import Any

_PLUGIN_DIR = Path(__file__).resolve().parent
if str(_PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_DIR))

from _rotor5.core import decode_bytes as _decode_bytes, encode_bytes as _encode_bytes, inspect_envelope as _inspect


def encode_text(message: str, password: str) -> dict:
    """Encode UTF-8 text with the five-machine Rotor5 envelope and return Base64."""
    blob = _encode_bytes(str(message).encode("utf-8"), password)
    info = _inspect(blob)
    return {"ok": True, "encoded_base64": base64.b64encode(blob).decode("ascii"), "envelope": info.as_dict()}


def decode_text(encoded_base64: str, password: str) -> dict:
    """Decode a Base64 Rotor5 envelope to authenticated UTF-8 text."""
    blob = base64.b64decode(encoded_base64, validate=True)
    data = _decode_bytes(blob, password)
    try:
        message = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("decoded Rotor5 payload is not UTF-8; use decode_base64 or decode_file") from exc
    return {"ok": True, "message": message, "bytes": len(data)}


def encode_base64(message_base64: str, password: str) -> dict:
    """Encode arbitrary Base64 bytes with Rotor5 and return the encoded envelope as Base64."""
    data = base64.b64decode(message_base64, validate=True)
    blob = _encode_bytes(data, password)
    return {"ok": True, "encoded_base64": base64.b64encode(blob).decode("ascii"), "envelope": _inspect(blob).as_dict()}


def decode_base64(encoded_base64: str, password: str) -> dict:
    """Decode a Base64 Rotor5 envelope and return the recovered bytes as Base64."""
    blob = base64.b64decode(encoded_base64, validate=True)
    data = _decode_bytes(blob, password)
    return {"ok": True, "message_base64": base64.b64encode(data).decode("ascii"), "bytes": len(data)}


def encode_file(input_file: str, output_file: str, password: str) -> dict:
    """Encode a file into a binary Rotor5 envelope."""
    data = Path(input_file).expanduser().read_bytes()
    blob = _encode_bytes(data, password)
    out = Path(output_file).expanduser(); out.parent.mkdir(parents=True, exist_ok=True); out.write_bytes(blob)
    return {"ok": True, "output_file": str(out), "input_bytes": len(data), "output_bytes": len(blob), "envelope": _inspect(blob).as_dict()}


def decode_file(input_file: str, output_file: str, password: str) -> dict:
    """Decode a binary Rotor5 envelope into its original file bytes."""
    blob = Path(input_file).expanduser().read_bytes()
    data = _decode_bytes(blob, password)
    out = Path(output_file).expanduser(); out.parent.mkdir(parents=True, exist_ok=True); out.write_bytes(data)
    return {"ok": True, "output_file": str(out), "bytes": len(data)}


def envelope_info(encoded_base64: str) -> dict:
    """Inspect non-secret Rotor5 envelope metadata without decoding it."""
    blob = base64.b64decode(encoded_base64, validate=True)
    return {"ok": True, "envelope": _inspect(blob).as_dict()}


def run(payload: dict[str, Any]) -> dict[str, Any]:
    """Legacy action-based broker entrypoint."""
    try:
        if not isinstance(payload, dict):
            raise ValueError("payload must be a JSON object")
        action = str(payload.get("action", "")).strip().lower()
        password = str(payload.get("password", ""))
        if action in {"encode", "encode_text"}:
            return encode_text(str(payload.get("message", "")), password)
        if action in {"decode", "decode_text"}:
            return decode_text(str(payload.get("encoded_base64", "")), password)
        if action == "encode_base64":
            return encode_base64(str(payload.get("message_base64", "")), password)
        if action == "decode_base64":
            return decode_base64(str(payload.get("encoded_base64", "")), password)
        if action == "encode_file":
            return encode_file(str(payload.get("input_file", "")), str(payload.get("output_file", "")), password)
        if action == "decode_file":
            return decode_file(str(payload.get("input_file", "")), str(payload.get("output_file", "")), password)
        if action in {"info", "inspect"}:
            return envelope_info(str(payload.get("encoded_base64", "")))
        raise ValueError("unknown action; use encode, decode, encode_base64, decode_base64, encode_file, decode_file, or info")
    except Exception as exc:
        return {"ok": False, "error": str(exc), "error_type": type(exc).__name__}
