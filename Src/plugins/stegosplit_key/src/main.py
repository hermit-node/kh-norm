from __future__ import annotations

from pathlib import Path

from _stegosplit.core import create_pair, recover_pair, reconstruct_cover, rotate_pair, pair_stats as _pair_stats


def _hex_bytes(value: str, *, label: str, minimum: int = 0, exact: int | None = None) -> bytes:
    try:
        raw = bytes.fromhex(str(value or "").strip())
    except ValueError as exc:
        raise ValueError(f"{label} must be hexadecimal") from exc
    if exact is not None and len(raw) != exact:
        raise ValueError(f"{label} must decode to exactly {exact} bytes")
    if len(raw) < minimum:
        raise ValueError(f"{label} must decode to at least {minimum} bytes")
    return raw


def create_key_pair(source_image: str, share_a: str, share_b: str, password: str, map_key_hex: str, key_hex: str = "", width: int = 512, height: int = 512) -> dict:
    """Create two PNG shares carrying one 256-bit key; omit key_hex to generate a random key."""
    map_key = _hex_bytes(map_key_hex, label="map_key_hex", minimum=16)
    key = _hex_bytes(key_hex, label="key_hex", exact=32) if str(key_hex or "").strip() else None
    Path(share_a).expanduser().parent.mkdir(parents=True, exist_ok=True)
    Path(share_b).expanduser().parent.mkdir(parents=True, exist_ok=True)
    recovered = create_pair(source_image, share_a, share_b, password, map_key, key=key, size=(int(width), int(height)))
    return {"ok": True, "share_a": str(Path(share_a).expanduser().resolve()), "share_b": str(Path(share_b).expanduser().resolve()), "key_hex": recovered.hex(), "width": int(width), "height": int(height)}


def recover_key(share_a: str, share_b: str, password: str, map_key_hex: str) -> dict:
    """Recover and authenticate the 256-bit key from a StegoSplit image pair."""
    key = recover_pair(share_a, share_b, password, _hex_bytes(map_key_hex, label="map_key_hex", minimum=16))
    return {"ok": True, "key_hex": key.hex(), "share_a": str(Path(share_a).expanduser().resolve()), "share_b": str(Path(share_b).expanduser().resolve())}


def reconstruct_key_cover(share_a: str, share_b: str, output_image: str) -> dict:
    """Reconstruct the reversible cover image from the two StegoSplit key shares."""
    Path(output_image).expanduser().parent.mkdir(parents=True, exist_ok=True)
    image = reconstruct_cover(share_a, share_b, output_image)
    return {"ok": True, "output_image": str(Path(output_image).expanduser().resolve()), "width": image.width, "height": image.height}


def rotate_key_pair(share_a: str, share_b: str, output_a: str, output_b: str, old_password: str, old_map_key_hex: str, new_password: str, new_map_key_hex: str, new_key_hex: str = "") -> dict:
    """Rotate password/map-key protection, optionally replacing the carried 256-bit key."""
    old_map = _hex_bytes(old_map_key_hex, label="old_map_key_hex", minimum=16)
    new_map = _hex_bytes(new_map_key_hex, label="new_map_key_hex", minimum=16)
    new_key = _hex_bytes(new_key_hex, label="new_key_hex", exact=32) if str(new_key_hex or "").strip() else None
    Path(output_a).expanduser().parent.mkdir(parents=True, exist_ok=True)
    Path(output_b).expanduser().parent.mkdir(parents=True, exist_ok=True)
    key = rotate_pair(share_a, share_b, output_a, output_b, old_password, old_map, new_password, new_map, new_key=new_key)
    return {"ok": True, "output_a": str(Path(output_a).expanduser().resolve()), "output_b": str(Path(output_b).expanduser().resolve()), "key_hex": key.hex()}


def key_pair_stats(share_a: str, share_b: str) -> dict:
    """Return pixel-channel difference statistics for a StegoSplit key pair."""
    return {"ok": True, **_pair_stats(share_a, share_b)}


def run(payload: dict) -> dict:
    """Legacy broker entrypoint for create/recover/reconstruct/stats actions."""
    p = dict(payload or {})
    action = str(p.pop("action", "")).strip().lower()
    if action in {"create", "embed", "create_key_pair"}:
        return create_key_pair(**p)
    if action in {"recover", "extract", "recover_key"}:
        return recover_key(**p)
    if action in {"reconstruct", "cover"}:
        return reconstruct_key_cover(**p)
    if action in {"stats", "pair_stats"}:
        return key_pair_stats(**p)
    raise ValueError("action must be create, recover, reconstruct, or stats")
