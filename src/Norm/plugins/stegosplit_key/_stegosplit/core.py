from __future__ import annotations

import hashlib
import hmac
import math
import os
import random
import tempfile
from dataclasses import dataclass
from pathlib import Path

from PIL import Image

KEY_BYTES = 32
TAG_BYTES = 16
REPEAT = 8
DECOY_MULTIPLIER = 4
PROJECT_SALT = b"StegoSplit-v0.1-prototype"


@dataclass(frozen=True)
class Geometry:
    angle: float
    offset: float


def _kdf(password: str, map_key: bytes) -> bytes:
    if len(map_key) < 16:
        raise ValueError("map key must be at least 16 bytes")
    password_root = hashlib.scrypt(
        password.encode("utf-8"), salt=PROJECT_SALT,
        n=1 << 14, r=8, p=1, dklen=32,
    )
    return hmac.new(password_root, b"map-key:" + map_key, hashlib.sha256).digest()


def _derive(root: bytes, label: bytes) -> bytes:
    return hmac.new(root, label, hashlib.sha256).digest()


def _u64(root: bytes, label: bytes, counter: int = 0) -> int:
    msg = label + counter.to_bytes(8, "big")
    return int.from_bytes(hmac.new(root, msg, hashlib.sha256).digest()[:8], "big")


def _geometry(root: bytes, width: int, height: int) -> Geometry:
    scale = float(1 << 64)
    angle = (_u64(root, b"angle") / scale) * math.pi
    centered = (_u64(root, b"offset") / scale) - 0.5
    offset = centered * 0.30 * min(width, height)
    return Geometry(angle=angle, offset=offset)


def _region(x: int, y: int, width: int, height: int, g: Geometry) -> int:
    cx = (width - 1) / 2.0
    cy = (height - 1) / 2.0
    nx = math.cos(g.angle)
    ny = math.sin(g.angle)
    signed = (x - cx) * nx + (y - cy) * ny
    return 0 if signed <= g.offset else 1


def _payload(root: bytes, key: bytes) -> bytes:
    auth_key = _derive(root, b"auth")
    tag = hmac.new(auth_key, b"StegoSplit-key" + key, hashlib.sha256).digest()[:TAG_BYTES]
    return key + tag


def _to_bits(data: bytes) -> list[int]:
    return [(byte >> shift) & 1 for byte in data for shift in range(7, -1, -1)]


def _from_bits(bits: list[int]) -> bytes:
    out = bytearray()
    for start in range(0, len(bits), 8):
        value = 0
        for bit in bits[start:start + 8]:
            value = (value << 1) | bit
        out.append(value)
    return bytes(out)


def _candidate_channels(width: int, height: int, region: int, g: Geometry) -> list[int]:
    channels: list[int] = []
    for y in range(height):
        for x in range(width):
            if _region(x, y, width, height, g) == region:
                base = (y * width + x) * 3
                channels.extend((base, base + 1, base + 2))
    return channels


def _select(root: bytes, label: bytes, candidates: list[int], count: int) -> list[int]:
    if count > len(candidates):
        raise ValueError(f"carrier region too small: need {count}, have {len(candidates)}")
    chosen: list[int] = []
    used: set[int] = set()
    counter = 0
    while len(chosen) < count:
        idx = _u64(root, label, counter) % len(candidates)
        counter += 1
        if idx in used:
            continue
        used.add(idx)
        chosen.append(candidates[idx])
    return chosen


def _altered_pair(base: int, alter_a: bool) -> tuple[int, int]:
    if base >= 255:
        raise ValueError("carrier channel must be below 255")
    changed = base + 1
    return (changed, base) if alter_a else (base, changed)


def _real_positions(root: bytes, label: bytes, candidates: list[int], bit_count: int) -> list[int]:
    return _select(root, label, candidates, bit_count * REPEAT * 2)


def _embed_region(
    root: bytes, label: bytes, original: bytearray,
    share_a: bytearray, share_b: bytearray,
    candidates: list[int], bits: list[int], reverse: bool,
) -> list[int]:
    positions = _real_positions(root, label, candidates, len(bits))
    for bit_index, bit in enumerate(bits):
        for repeat_index in range(REPEAT):
            pair_start = (bit_index * REPEAT + repeat_index) * 2
            channel = positions[pair_start + bit]
            a, b = _altered_pair(original[channel], alter_a=not reverse)
            share_a[channel] = a
            share_b[channel] = b
    return positions


def _decoy_positions(
    root: bytes, protected: set[int], region0: list[int], region1: list[int], count: int,
) -> tuple[list[int], list[int]]:
    count0 = count // 2
    count1 = count - count0
    pool0 = [i for i in region0 if i not in protected]
    pool1 = [i for i in region1 if i not in protected]
    return (
        _select(root, b"decoy-region-0", pool0, count0),
        _select(root, b"decoy-region-1", pool1, count1),
    )


def _embed_decoys(
    root: bytes, original: bytearray, share_a: bytearray, share_b: bytearray,
    protected: set[int], region0: list[int], region1: list[int], count: int,
) -> tuple[list[int], list[int]]:
    decoy0, decoy1 = _decoy_positions(root, protected, region0, region1, count)
    for channel in decoy0:
        share_a[channel], share_b[channel] = _altered_pair(original[channel], True)
    for channel in decoy1:
        share_a[channel], share_b[channel] = _altered_pair(original[channel], False)
    return decoy0, decoy1


def _extract_region(
    root: bytes, label: bytes, share_a: bytes, share_b: bytes,
    candidates: list[int], bit_count: int, reverse: bool,
) -> list[int]:
    positions = _real_positions(root, label, candidates, bit_count)
    bits: list[int] = []
    for bit_index in range(bit_count):
        votes = 0
        valid = 0
        for repeat_index in range(REPEAT):
            pair_start = (bit_index * REPEAT + repeat_index) * 2
            c0, c1 = positions[pair_start], positions[pair_start + 1]
            d0 = (share_b[c0] - share_a[c0]) if reverse else (share_a[c0] - share_b[c0])
            d1 = (share_b[c1] - share_a[c1]) if reverse else (share_a[c1] - share_b[c1])
            if d0 == 1 and d1 == 0:
                valid += 1
            elif d0 == 0 and d1 == 1:
                votes += 1
                valid += 1
        if valid <= REPEAT // 2:
            raise ValueError("damaged or mismatched mapped pixels")
        bits.append(1 if votes * 2 > valid else 0)
    return bits


def create_pair(
    source: str | Path,
    output_a: str | Path,
    output_b: str | Path,
    password: str,
    map_key: bytes,
    key: bytes | None = None,
    size: tuple[int, int] = (512, 512),
) -> bytes:
    key = os.urandom(KEY_BYTES) if key is None else key
    if len(key) != KEY_BYTES:
        raise ValueError(f"key must be {KEY_BYTES} bytes")

    image = Image.open(source).convert("RGB")
    if image.size != size:
        image = image.resize(size, Image.Resampling.LANCZOS)
    width, height = image.size
    root = _kdf(password, map_key)
    g = _geometry(root, width, height)
    tagged = _payload(root, key)
    tag = tagged[KEY_BYTES:]
    half_key = KEY_BYTES // 2
    half_tag = TAG_BYTES // 2
    part0 = key[:half_key] + tag[:half_tag]
    part1 = key[half_key:] + tag[half_tag:]
    bits0 = _to_bits(part0)
    bits1 = _to_bits(part1)

    original = bytearray(image.tobytes())
    share_a = bytearray(original)
    share_b = bytearray(original)
    region0 = [i for i in _candidate_channels(width, height, 0, g) if original[i] < 255]
    region1 = [i for i in _candidate_channels(width, height, 1, g) if original[i] < 255]
    real0 = _embed_region(root, b"region-0", original, share_a, share_b, region0, bits0, False)
    real1 = _embed_region(root, b"region-1", original, share_a, share_b, region1, bits1, True)
    protected = set(real0) | set(real1)
    _embed_decoys(root, original, share_a, share_b, protected, region0, region1, (len(bits0) + len(bits1)) * REPEAT * DECOY_MULTIPLIER)

    Image.frombytes("RGB", image.size, bytes(share_a)).save(output_a, format="PNG", optimize=True)
    Image.frombytes("RGB", image.size, bytes(share_b)).save(output_b, format="PNG", optimize=True)
    return key


def recover_pair(image_a: str | Path, image_b: str | Path, password: str, map_key: bytes) -> bytes:
    a = Image.open(image_a).convert("RGB")
    b = Image.open(image_b).convert("RGB")
    if a.size != b.size:
        raise ValueError("share images must have identical dimensions")

    width, height = a.size
    root = _kdf(password, map_key)
    g = _geometry(root, width, height)
    part_bytes = (KEY_BYTES + TAG_BYTES) // 2
    part_bits = part_bytes * 8
    raw_a = a.tobytes()
    raw_b = b.tobytes()
    cover = reconstruct_cover(image_a, image_b)
    carrier = cover.tobytes()
    region0 = [i for i in _candidate_channels(width, height, 0, g) if carrier[i] < 255]
    region1 = [i for i in _candidate_channels(width, height, 1, g) if carrier[i] < 255]

    bits0 = _extract_region(root, b"region-0", raw_a, raw_b, region0, part_bits, False)
    bits1 = _extract_region(root, b"region-1", raw_a, raw_b, region1, part_bits, True)
    part0 = _from_bits(bits0)
    part1 = _from_bits(bits1)
    half_key = KEY_BYTES // 2
    key = part0[:half_key] + part1[:half_key]
    tag = part0[half_key:] + part1[half_key:]
    expected = _payload(root, key)[KEY_BYTES:]
    if not hmac.compare_digest(tag, expected):
        raise ValueError("wrong password, mismatched shares, or damaged images")
    return key


def reconstruct_cover(image_a: str | Path, image_b: str | Path, output: str | Path | None = None) -> Image.Image:
    a = Image.open(image_a).convert("RGB")
    b = Image.open(image_b).convert("RGB")
    if a.size != b.size:
        raise ValueError("share images must have identical dimensions")
    rebuilt = bytearray()
    for av, bv in zip(a.tobytes(), b.tobytes()):
        delta = abs(av - bv)
        if delta not in (0, 1):
            raise ValueError("shares are not a reversible StegoSplit pair")
        rebuilt.append(min(av, bv))
    image = Image.frombytes("RGB", a.size, bytes(rebuilt))
    if output is not None:
        image.save(output, format="PNG", optimize=True)
    return image


def rotate_pair(
    image_a: str | Path, image_b: str | Path, output_a: str | Path, output_b: str | Path,
    old_password: str, old_map_key: bytes, new_password: str, new_map_key: bytes,
    new_key: bytes | None = None,
) -> bytes:
    cover = reconstruct_cover(image_a, image_b)
    key = recover_pair(image_a, image_b, old_password, old_map_key) if new_key is None else new_key
    with tempfile.TemporaryDirectory() as td:
        source = Path(td) / "cover.png"
        cover.save(source, format="PNG", optimize=True)
        return create_pair(source, output_a, output_b, new_password, new_map_key, key=key, size=cover.size)


def pair_stats(image_a: str | Path, image_b: str | Path) -> dict[str, float | int]:
    a = Image.open(image_a).convert("RGB")
    b = Image.open(image_b).convert("RGB")
    if a.size != b.size:
        raise ValueError("share images must have identical dimensions")
    diffs = [abs(x - y) for x, y in zip(a.tobytes(), b.tobytes())]
    changed = sum(1 for d in diffs if d)
    return {
        "changed_channels": changed,
        "total_channels": len(diffs),
        "max_channel_delta": max(diffs, default=0),
        "mean_abs_channel_delta": sum(diffs) / len(diffs),
    }

