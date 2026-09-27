from __future__ import annotations

import secrets
from pathlib import Path

from PIL import Image

from _messagecodec.mapping import FAMILIES, FormulaChoice
from _messagecodec.message_codec import encode_message_pair, decode_message_pair, reconstruct_conditioned_cover


def _choice(family: str, item: int, offset: int, seed_a: int, seed_b: int) -> FormulaChoice:
    fam = str(family or "").strip().upper()
    if not fam:
        fam = secrets.choice(tuple(FAMILIES))
    if fam not in FAMILIES:
        raise ValueError(f"family must be one of {', '.join(FAMILIES)}")
    chosen_item = int(item)
    if chosen_item < 0:
        chosen_item = secrets.randbelow(5)
    if not 0 <= chosen_item < 5:
        raise ValueError("item must be 0..4, or -1 for random")
    def pick(value: int) -> int:
        return secrets.randbits(32) if int(value) < 0 else int(value) & 0xFFFFFFFF
    return FormulaChoice(fam, chosen_item, pick(offset), pick(seed_a), pick(seed_b))


def embed_message(source_image: str, share_a: str, share_b: str, message: str, family: str = "", item: int = -1, offset: int = -1, seed_a: int = -1, seed_b: int = -1, start_share: int = -1) -> dict:
    """Embed UTF-8 text into two lossless PNG shares and return the encoded header/formula metadata."""
    choice = _choice(family, item, offset, seed_a, seed_b)
    start = secrets.randbelow(2) if int(start_share) < 0 else int(start_share)
    if start not in (0, 1):
        raise ValueError("start_share must be 0 or 1, or -1 for random")
    Path(share_a).expanduser().parent.mkdir(parents=True, exist_ok=True)
    Path(share_b).expanduser().parent.mkdir(parents=True, exist_ok=True)
    header = encode_message_pair(source_image, share_a, share_b, str(message), choice, start_share=start)
    return {"ok": True, "action": "embed", "share_a": str(Path(share_a).expanduser().resolve()), "share_b": str(Path(share_b).expanduser().resolve()), "header_hex": header.to_hex(), "message_length": len(str(message).encode("utf-8")), "formula": {"family": header.choice.family, "item": header.choice.item, "offset": header.choice.offset, "seed_a": header.choice.seed_a, "seed_b": header.choice.seed_b, "start_share": header.start_share}}


def extract_message(share_a: str, share_b: str) -> dict:
    """Extract and CRC-check the UTF-8 message from two StegoSplit MessageCodec PNG shares."""
    message, header = decode_message_pair(share_a, share_b)
    return {"ok": True, "action": "extract", "message": message, "header_hex": header.to_hex(), "share_a": str(Path(share_a).expanduser().resolve()), "share_b": str(Path(share_b).expanduser().resolve())}


def rebuild_cover(share_a: str, share_b: str, output_image: str) -> dict:
    """Reconstruct the conditioned carrier image from two message shares."""
    cover, header = reconstruct_conditioned_cover(share_a, share_b)
    target = Path(output_image).expanduser()
    target.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(cover, "RGB").save(target, format="PNG", optimize=True)
    return {"ok": True, "output_image": str(target.resolve()), "header_hex": header.to_hex()}


def run(payload: dict) -> dict:
    """Legacy broker entrypoint: payload action=embed|extract|reconstruct."""
    p = dict(payload or {})
    action = str(p.pop("action", "")).strip().lower()
    if action == "embed":
        return embed_message(**p)
    if action == "extract":
        return extract_message(**p)
    if action in {"reconstruct", "cover"}:
        return rebuild_cover(**p)
    raise ValueError("action must be embed, extract, or reconstruct")
