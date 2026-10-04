from __future__ import annotations

import struct
from dataclasses import dataclass

FORMAT_VERSION = 2
FORMULA_COUNT = 16
FLAG_COMPRESSED = 0x01
KNOWN_FLAGS = FLAG_COMPRESSED
SALT_SIZE = 16
NONCE_SIZE = 12
AEAD_TAG_SIZE = 16

# Row-0 public bootstrap. Pixels are zero-based: (row 0, col 2) is the local
# reference and (0,3) is the role marker. Three more ref/data pairs encode the
# 8-bit version/formula nibble pair. Nothing here identifies a particular pair.
MIN_HEADER_WIDTH = 10
ROLE_REF_PIXEL = 2
ROLE_DATA_PIXEL = 3
META_PAIRS = ((4, 5), (6, 7), (8, 9))

# Password-gated body control block. It is NOT in the public row-0 header.
CONTROL_MAGIC = b"C2"
CONTROL_VERSION = 1
CONTROL_FORMAT = ">2sBHB16s12sII"
CONTROL_SIZE = struct.calcsize(CONTROL_FORMAT)  # 42 bytes / 336 bits
CONTROL_BITS = CONTROL_SIZE * 8
CONTROL_BITS_PER_SHARE = CONTROL_BITS // 2


@dataclass(frozen=True)
class PublicHeader:
    role: int  # 0=A, 1=B
    formula_id: int
    version: int = FORMAT_VERSION

    def __post_init__(self) -> None:
        if self.role not in (0, 1):
            raise ValueError("role must be 0 (A) or 1 (B)")
        if self.version != FORMAT_VERSION:
            raise ValueError(f"unsupported StegoSplit format version {self.version}")
        if not 0 <= self.formula_id < FORMULA_COUNT:
            raise ValueError(f"formula_id must be 0..{FORMULA_COUNT - 1}")

    @property
    def role_name(self) -> str:
        return "A" if self.role == 0 else "B"

    @property
    def version_formula_byte(self) -> int:
        return ((self.version & 0x0F) << 4) | (self.formula_id & 0x0F)


@dataclass(frozen=True)
class ControlBlock:
    angle_cdeg: int
    flags: int
    salt: bytes
    nonce: bytes
    ciphertext_length: int
    original_length: int

    def __post_init__(self) -> None:
        if not 4500 <= self.angle_cdeg <= 8999:
            raise ValueError("control angle must be between 45.00 and 89.99 degrees")
        if self.flags & ~KNOWN_FLAGS:
            raise ValueError("control block contains unsupported flags")
        if len(self.salt) != SALT_SIZE:
            raise ValueError(f"salt must be {SALT_SIZE} bytes")
        if len(self.nonce) != NONCE_SIZE:
            raise ValueError(f"nonce must be {NONCE_SIZE} bytes")
        if self.ciphertext_length < AEAD_TAG_SIZE:
            raise ValueError("ciphertext length is too small")
        if self.original_length < 0:
            raise ValueError("original length cannot be negative")

    @property
    def compressed(self) -> bool:
        return bool(self.flags & FLAG_COMPRESSED)

    @property
    def angle_degrees(self) -> float:
        return self.angle_cdeg / 100.0

    def to_bytes(self) -> bytes:
        return struct.pack(
            CONTROL_FORMAT,
            CONTROL_MAGIC,
            CONTROL_VERSION,
            self.angle_cdeg,
            self.flags,
            self.salt,
            self.nonce,
            self.ciphertext_length,
            self.original_length,
        )

    @classmethod
    def from_bytes(cls, data: bytes) -> "ControlBlock":
        if len(data) != CONTROL_SIZE:
            raise ValueError(f"control block must be exactly {CONTROL_SIZE} bytes")
        magic, control_version, angle, flags, salt, nonce, ct_len, original_len = struct.unpack(
            CONTROL_FORMAT, data
        )
        if magic != CONTROL_MAGIC or control_version != CONTROL_VERSION:
            raise ValueError("wrong password, mismatched shares, or damaged hidden control block")
        return cls(
            angle_cdeg=angle,
            flags=flags,
            salt=salt,
            nonce=nonce,
            ciphertext_length=ct_len,
            original_length=original_len,
        )


def bytes_to_bits(data: bytes) -> list[int]:
    return [(byte >> shift) & 1 for byte in data for shift in range(7, -1, -1)]


def bits_to_bytes(bits: list[int]) -> bytes:
    if len(bits) % 8:
        raise ValueError("bit count must be divisible by 8")
    out = bytearray()
    for start in range(0, len(bits), 8):
        value = 0
        for bit in bits[start : start + 8]:
            value = (value << 1) | bit
        out.append(value)
    return bytes(out)


def _pixel_start(pixel: int) -> int:
    return pixel * 3


def canonicalize_row0(raw: bytes | bytearray, width: int) -> bytes:
    """Return canonical row 0 with each header data pixel copied from its ref pixel."""
    if width < MIN_HEADER_WIDTH:
        raise ValueError(f"image is too narrow for V2 header: need width >= {MIN_HEADER_WIDTH}")
    row = bytearray(raw[: width * 3])
    for ref_pixel, data_pixel in ((ROLE_REF_PIXEL, ROLE_DATA_PIXEL), *META_PAIRS):
        r = _pixel_start(ref_pixel)
        d = _pixel_start(data_pixel)
        row[d : d + 3] = row[r : r + 3]
    return bytes(row)


def canonicalize_base(raw: bytes, width: int) -> bytes:
    if width < MIN_HEADER_WIDTH:
        raise ValueError(f"image is too narrow for V2 header: need width >= {MIN_HEADER_WIDTH}")
    out = bytearray(raw)
    out[: width * 3] = canonicalize_row0(out, width)
    return bytes(out)


def encode_public_header(base: bytes, share: bytearray, width: int, public: PublicHeader) -> None:
    """Encode role first, then 4-bit V2 + 4-bit formula, independently in each share."""
    if width < MIN_HEADER_WIDTH:
        raise ValueError(f"image is too narrow for V2 header: need width >= {MIN_HEADER_WIDTH}")

    # (0,3) is a copy of (0,2), with red +1 for A and -1 for B.
    ref = _pixel_start(ROLE_REF_PIXEL)
    data = _pixel_start(ROLE_DATA_PIXEL)
    share[data : data + 3] = base[ref : ref + 3]
    share[data] = base[ref] + (1 if public.role == 0 else -1)

    # Encode version/formula as 8 +/-1 channel deltas across three ref/data pairs.
    byte = public.version_formula_byte
    bits = [(byte >> shift) & 1 for shift in range(7, -1, -1)]
    bit_index = 0
    for ref_pixel, data_pixel in META_PAIRS:
        r = _pixel_start(ref_pixel)
        d = _pixel_start(data_pixel)
        share[d : d + 3] = base[r : r + 3]
        for channel in range(3):
            if bit_index >= len(bits):
                break
            share[d + channel] = base[r + channel] + (1 if bits[bit_index] else -1)
            bit_index += 1


def decode_public_header(raw: bytes, width: int) -> PublicHeader:
    if width < MIN_HEADER_WIDTH:
        raise ValueError(f"image is too narrow for V2 header: need width >= {MIN_HEADER_WIDTH}")

    ref = _pixel_start(ROLE_REF_PIXEL)
    data = _pixel_start(ROLE_DATA_PIXEL)
    role_delta = raw[data] - raw[ref]
    if role_delta == 1:
        role = 0
    elif role_delta == -1:
        role = 1
    else:
        raise ValueError("not a valid StegoSplit V2 share: row-0 role marker is absent")

    bits: list[int] = []
    for ref_pixel, data_pixel in META_PAIRS:
        r = _pixel_start(ref_pixel)
        d = _pixel_start(data_pixel)
        for channel in range(3):
            if len(bits) == 8:
                break
            delta = raw[d + channel] - raw[r + channel]
            if delta == 1:
                bits.append(1)
            elif delta == -1:
                bits.append(0)
            else:
                raise ValueError("not a valid StegoSplit V2 share: public header is damaged")

    value = 0
    for bit in bits:
        value = (value << 1) | bit
    version = (value >> 4) & 0x0F
    formula_id = value & 0x0F
    return PublicHeader(role=role, formula_id=formula_id, version=version)


def associated_data(public: PublicHeader, control: ControlBlock) -> bytes:
    # Role is intentionally omitted: A and B have different roles but share one ciphertext.
    return b"StegoSplit-v2\x00" + bytes([public.version, public.formula_id]) + control.to_bytes()
