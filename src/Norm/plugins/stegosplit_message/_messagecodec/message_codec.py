from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image

from .mapping import FAMILIES, FormulaChoice
from .perceptual import perceptual_channel

MAGIC = b"SMC"
VERSION = 1
HEADER_FMT = ">3sBBBBIIIII"
HEADER_SIZE = struct.calcsize(HEADER_FMT)
HEADER_BITS = HEADER_SIZE * 8
CONDITION_LUT = np.rint(1.0 + np.arange(256, dtype=np.float32) * (253.0 / 255.0)).clip(1, 254).astype(np.uint8)


@dataclass(frozen=True)
class MessageHeader:
    version: int
    choice: FormulaChoice
    start_share: int
    payload_bytes: int
    crc32: int

    def to_bytes(self) -> bytes:
        return struct.pack(
            HEADER_FMT,
            MAGIC,
            self.version,
            FAMILIES.index(self.choice.family),
            self.choice.item,
            self.start_share,
            self.choice.offset & 0xFFFFFFFF,
            self.choice.seed_a & 0xFFFFFFFF,
            self.choice.seed_b & 0xFFFFFFFF,
            self.payload_bytes & 0xFFFFFFFF,
            self.crc32 & 0xFFFFFFFF,
        )

    @classmethod
    def from_bytes(cls, data: bytes) -> "MessageHeader":
        if len(data) != HEADER_SIZE:
            raise ValueError("wrong header size")
        magic, version, family, item, start, offset, seed_a, seed_b, length, crc = struct.unpack(
            HEADER_FMT, data
        )
        if magic != MAGIC:
            raise ValueError("not a StegoSplit message pair")
        if start not in (0, 1):
            raise ValueError("invalid starting share")
        choice = FormulaChoice(FAMILIES[family], item, offset, seed_a, seed_b)
        return cls(version, choice, start, length, crc)

    def to_hex(self) -> str:
        return self.to_bytes().hex()


def _bytes_to_bits(data: bytes) -> list[int]:
    return [(byte >> shift) & 1 for byte in data for shift in range(7, -1, -1)]


def _bits_to_bytes(bits: list[int]) -> bytes:
    if len(bits) % 8:
        raise ValueError("bit count must be divisible by 8")
    out = bytearray()
    for i in range(0, len(bits), 8):
        value = 0
        for bit in bits[i : i + 8]:
            value = (value << 1) | bit
        out.append(value)
    return bytes(out)


def condition_rgb(array: np.ndarray) -> np.ndarray:
    """Compress the complete 0..255 RGB range into 1..254."""
    if array.dtype != np.uint8 or array.ndim != 3 or array.shape[2] != 3:
        raise ValueError("expected uint8 RGB array")
    return CONDITION_LUT[array]


def _load_rgb(source: str | Path) -> np.ndarray:
    with Image.open(source) as image:
        return np.array(image.convert("RGB"), dtype=np.uint8, copy=True)


def _save_rgb_png(array: np.ndarray, target: str | Path) -> None:
    if array.dtype != np.uint8 or array.ndim != 3 or array.shape[2] != 3:
        raise ValueError("expected uint8 RGB array")
    Image.fromarray(np.ascontiguousarray(array), "RGB").save(target, format="PNG", optimize=True)


def condition_image(source: str | Path) -> np.ndarray:
    return condition_rgb(_load_rgb(source))


def _body_pos(flat_pixel: int, width: int) -> tuple[int, int]:
    y0, x = divmod(flat_pixel, width)
    return y0 + 1, x


def _byte_groups(
    height: int,
    width: int,
    choice: FormulaChoice,
    byte_count: int,
) -> tuple[list[list[tuple[int, int]]], np.ndarray]:
    """One formula hit anchors 8 consecutive body pixels; channel is chosen perceptually."""
    if height < 2:
        raise ValueError("image must have at least two rows")
    N = (height - 1) * width
    if N < byte_count * 8:
        raise ValueError("image is too small for payload")
    occupied = np.zeros((height, width), dtype=np.int8)
    occupied[0, :] = 1  # row 0 belongs to the header
    groups: list[list[tuple[int, int]]] = []
    n = 0
    attempts = 0
    limit = max(N * 8, byte_count * 1000)
    while len(groups) < byte_count:
        anchor = choice.index(n, N)
        n += 1
        attempts += 1
        if attempts > limit:
            raise RuntimeError("formula could not allocate enough 8-pixel byte groups")
        if anchor + 7 >= N:
            continue
        coords = [_body_pos(anchor + j, width) for j in range(8)]
        if any(occupied[y, x] != 0 for y, x in coords):
            continue
        for y, x in coords:
            occupied[y, x] = 1
        groups.append(coords)
    return groups, occupied


def _encode_header(cover: np.ndarray, header: MessageHeader) -> tuple[np.ndarray, np.ndarray]:
    if cover.shape[1] < HEADER_BITS:
        raise ValueError(f"image needs at least {HEADER_BITS} columns for the red-channel header")
    a = cover.copy()
    b = cover.copy()
    for x, bit in enumerate(_bytes_to_bits(header.to_bytes())):
        base = int(cover[0, x, 0])
        a[0, x, 0] = np.uint8(base + (1 if bit else -1))
    return a, b


def _decode_header(a: np.ndarray, b: np.ndarray) -> MessageHeader:
    if a.shape != b.shape or a.ndim != 3 or a.shape[2] != 3:
        raise ValueError("share arrays must be matching RGB images")
    if a.shape[1] < HEADER_BITS:
        raise ValueError("image row is too short for header")
    bits: list[int] = []
    for x in range(HEADER_BITS):
        delta = int(a[0, x, 0]) - int(b[0, x, 0])
        if delta == 1:
            bits.append(1)
        elif delta == -1:
            bits.append(0)
        else:
            raise ValueError("invalid differential header")
    return MessageHeader.from_bytes(_bits_to_bytes(bits))


def _owner_for_byte(index: int, count: int, start_share: int) -> int:
    first_count = (count + 1) // 2
    return start_share if index < first_count else 1 - start_share


def encode_message_pair(
    source: str | Path,
    output_a: str | Path,
    output_b: str | Path,
    message: str,
    choice: FormulaChoice,
    start_share: int = 0,
) -> MessageHeader:
    payload = message.encode("utf-8")
    cover = condition_image(source)
    header = MessageHeader(
        VERSION,
        choice,
        start_share,
        len(payload),
        zlib.crc32(payload) & 0xFFFFFFFF,
    )
    a, b = _encode_header(cover, header)
    groups, _occupied = _byte_groups(*cover.shape[:2], choice, len(payload))
    for byte_index, (byte, coords) in enumerate(zip(payload, groups)):
        owner = _owner_for_byte(byte_index, len(payload), start_share)
        for shift, (y, x) in zip(range(7, -1, -1), coords):
            bit = (byte >> shift) & 1
            delta = 1 if bit else -1
            channel = perceptual_channel(cover[y, x], delta)
            if owner == 0:
                a[y, x, channel] = np.uint8(int(cover[y, x, channel]) + delta)
            else:
                b[y, x, channel] = np.uint8(int(cover[y, x, channel]) + delta)
    _save_rgb_png(a, output_a)
    _save_rgb_png(b, output_b)
    return header


def _center_crop(array: np.ndarray, height: int, width: int) -> np.ndarray:
    top = (array.shape[0] - height) // 2
    left = (array.shape[1] - width) // 2
    return np.ascontiguousarray(array[top : top + height, left : left + width])


def _load_pair(image_a: str | Path, image_b: str | Path) -> tuple[np.ndarray, np.ndarray]:
    a = _load_rgb(image_a)
    b = _load_rgb(image_b)
    target_height = min(a.shape[0], b.shape[0])
    target_width = min(a.shape[1], b.shape[1])
    if a.shape[:2] != (target_height, target_width):
        a = _center_crop(a, target_height, target_width)
    if b.shape[:2] != (target_height, target_width):
        b = _center_crop(b, target_height, target_width)
    return a, b


def decode_message_pair(image_a: str | Path, image_b: str | Path) -> tuple[str, MessageHeader]:
    a, b = _load_pair(image_a, image_b)
    header = _decode_header(a, b)
    groups, _occupied = _byte_groups(*a.shape[:2], header.choice, header.payload_bytes)
    payload = bytearray()
    for byte_index, coords in enumerate(groups):
        owner = _owner_for_byte(byte_index, header.payload_bytes, header.start_share)
        value = 0
        for y, x in coords:
            diffs = (a[y, x].astype(np.int16) - b[y, x].astype(np.int16)) if owner == 0 else (
                b[y, x].astype(np.int16) - a[y, x].astype(np.int16)
            )
            changed = np.flatnonzero(diffs)
            if len(changed) != 1:
                raise ValueError("invalid or damaged message pixel")
            delta = int(diffs[int(changed[0])])
            if delta not in (-1, 1):
                raise ValueError("invalid or damaged message bit")
            value = (value << 1) | (1 if delta == 1 else 0)
        payload.append(value)
    if (zlib.crc32(payload) & 0xFFFFFFFF) != header.crc32:
        raise ValueError("payload CRC mismatch")
    return payload.decode("utf-8"), header


def reconstruct_conditioned_cover(image_a: str | Path, image_b: str | Path) -> tuple[np.ndarray, MessageHeader]:
    a, b = _load_pair(image_a, image_b)
    header = _decode_header(a, b)
    cover = b.copy()  # B is pristine for the entire row-0 header.
    groups, _occupied = _byte_groups(*a.shape[:2], header.choice, header.payload_bytes)
    for byte_index, coords in enumerate(groups):
        owner = _owner_for_byte(byte_index, header.payload_bytes, header.start_share)
        for y, x in coords:
            cover[y, x] = b[y, x] if owner == 0 else a[y, x]
    # Everything outside header/payload must still match.
    return cover, header

