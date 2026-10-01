from __future__ import annotations

import base64
import sys
from pathlib import Path
from typing import Any

_PLUGIN_DIR = Path(__file__).resolve().parent
if str(_PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_DIR))

import _stegosplit_v2 as stegosplit_v2


def _ensure_parent(path: str | Path) -> Path:
    p = Path(path).expanduser()
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def embed_text(source_image: str, share_a: str, share_b: str, password: str, message: str, decoy_ratio: float = 1.0) -> dict:
    """Hide UTF-8 text across two lossless PNG shares using StegoSplit V2."""
    a, b = _ensure_parent(share_a), _ensure_parent(share_b)
    info = stegosplit_v2.embed(source_image, message, password, a, b, decoy_ratio=float(decoy_ratio))
    return {"ok": True, "share_a": str(a), "share_b": str(b), "engine_version": stegosplit_v2.__version__, "info": info.as_dict()}


def embed_base64(source_image: str, share_a: str, share_b: str, password: str, message_base64: str, decoy_ratio: float = 1.0) -> dict:
    """Hide arbitrary bytes supplied as Base64 across two lossless PNG shares."""
    data = base64.b64decode(message_base64, validate=True)
    a, b = _ensure_parent(share_a), _ensure_parent(share_b)
    info = stegosplit_v2.embed_bytes(source_image, data, password, a, b, decoy_ratio=float(decoy_ratio))
    return {"ok": True, "share_a": str(a), "share_b": str(b), "bytes": len(data), "engine_version": stegosplit_v2.__version__, "info": info.as_dict()}


def embed_file(source_image: str, share_a: str, share_b: str, password: str, message_file: str, decoy_ratio: float = 1.0) -> dict:
    """Hide an arbitrary file's bytes across two lossless PNG shares."""
    data = Path(message_file).expanduser().read_bytes()
    a, b = _ensure_parent(share_a), _ensure_parent(share_b)
    info = stegosplit_v2.embed_bytes(source_image, data, password, a, b, decoy_ratio=float(decoy_ratio))
    return {"ok": True, "share_a": str(a), "share_b": str(b), "bytes": len(data), "engine_version": stegosplit_v2.__version__, "info": info.as_dict()}


def extract_text(share_a: str, share_b: str, password: str) -> dict:
    """Recover authenticated UTF-8 text from a StegoSplit pair supplied in either order."""
    message = stegosplit_v2.extract(share_a, share_b, password)
    return {"ok": True, "message": message, "engine_version": stegosplit_v2.__version__}


def extract_base64(share_a: str, share_b: str, password: str) -> dict:
    """Recover arbitrary authenticated bytes from a StegoSplit pair as Base64."""
    data = stegosplit_v2.extract_bytes(share_a, share_b, password)
    return {"ok": True, "message_base64": base64.b64encode(data).decode("ascii"), "bytes": len(data), "engine_version": stegosplit_v2.__version__}


def extract_file(share_a: str, share_b: str, password: str, output_file: str) -> dict:
    """Recover arbitrary authenticated bytes from a StegoSplit pair into a file."""
    data = stegosplit_v2.extract_bytes(share_a, share_b, password)
    out = _ensure_parent(output_file)
    out.write_bytes(data)
    return {"ok": True, "output_file": str(out), "bytes": len(data), "engine_version": stegosplit_v2.__version__}


def rotate_pair_password(share_a: str, share_b: str, old_password: str, new_password: str, output_a: str, output_b: str, decoy_ratio: float = 1.0) -> dict:
    """Re-key a StegoSplit pair without the original source image."""
    a, b = _ensure_parent(output_a), _ensure_parent(output_b)
    info = stegosplit_v2.rotate_password(share_a, share_b, old_password, new_password, a, b, decoy_ratio=float(decoy_ratio))
    return {"ok": True, "share_a": str(a), "share_b": str(b), "engine_version": stegosplit_v2.__version__, "info": info.as_dict()}


def rebuild_cover(share_a: str, share_b: str, password: str, output_image: str) -> dict:
    """Reconstruct the canonical conditioned cover after authenticating a StegoSplit pair."""
    out = _ensure_parent(output_image)
    image = stegosplit_v2.reconstruct_cover(share_a, share_b, password, out)
    try:
        width, height = image.size
    finally:
        image.close()
    return {"ok": True, "output_image": str(out), "width": width, "height": height, "engine_version": stegosplit_v2.__version__}


def pair_info(share_a: str, share_b: str, password: str) -> dict:
    """Return authenticated StegoSplit mapping/geometry/payload metadata."""
    return {"ok": True, "engine_version": stegosplit_v2.__version__, "info": stegosplit_v2.inspect_pair(share_a, share_b, password).as_dict()}


def pair_stats(share_a: str, share_b: str) -> dict:
    """Return raw differential statistics without authenticating the pair."""
    return {"ok": True, "engine_version": stegosplit_v2.__version__, "stats": stegosplit_v2.pair_stats(share_a, share_b)}


def run(payload: dict[str, Any]) -> dict[str, Any]:
    """Legacy broker entry point supporting action-based JSON payloads."""
    try:
        if not isinstance(payload, dict):
            raise ValueError("payload must be a JSON object")
        action = str(payload.get("action", "")).strip().lower()
        if action == "embed":
            common = dict(source_image=payload.get("source_image") or payload.get("source"), share_a=payload.get("share_a") or payload.get("output_a"), share_b=payload.get("share_b") or payload.get("output_b"), password=payload.get("password"), decoy_ratio=float(payload.get("decoy_ratio", 1.0)))
            if payload.get("message_base64") is not None:
                return embed_base64(message_base64=str(payload["message_base64"]), **common)
            if payload.get("message_file") is not None:
                return embed_file(message_file=str(payload["message_file"]), **common)
            return embed_text(message=str(payload.get("message", "")), **common)
        if action == "extract":
            a = payload.get("share_a") or payload.get("image_a") or payload.get("image_1")
            b = payload.get("share_b") or payload.get("image_b") or payload.get("image_2")
            if payload.get("output_file") or payload.get("output"):
                return extract_file(str(a), str(b), str(payload.get("password")), str(payload.get("output_file") or payload.get("output")))
            if payload.get("as_bytes"):
                return extract_base64(str(a), str(b), str(payload.get("password")))
            return extract_text(str(a), str(b), str(payload.get("password")))
        if action in {"rotate", "rotate_password", "rekey"}:
            return rotate_pair_password(str(payload.get("share_a") or payload.get("image_1")), str(payload.get("share_b") or payload.get("image_2")), str(payload.get("old_password")), str(payload.get("new_password")), str(payload.get("output_a") or payload.get("new_share_a")), str(payload.get("output_b") or payload.get("new_share_b")), float(payload.get("decoy_ratio", 1.0)))
        if action in {"rebuild", "reconstruct", "reconstruct_cover"}:
            return rebuild_cover(str(payload.get("share_a") or payload.get("image_1")), str(payload.get("share_b") or payload.get("image_2")), str(payload.get("password")), str(payload.get("output") or payload.get("output_image")))
        if action in {"info", "inspect"}:
            return pair_info(str(payload.get("share_a") or payload.get("image_1")), str(payload.get("share_b") or payload.get("image_2")), str(payload.get("password")))
        if action in {"stats", "pair_stats"}:
            return pair_stats(str(payload.get("share_a") or payload.get("image_1")), str(payload.get("share_b") or payload.get("image_2")))
        raise ValueError("unknown action; use embed, extract, rotate, rebuild, info, or stats")
    except Exception as exc:
        return {"ok": False, "error": str(exc), "error_type": type(exc).__name__, "engine_version": stegosplit_v2.__version__}
