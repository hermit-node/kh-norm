from __future__ import annotations

import math
import struct
from dataclasses import dataclass
from typing import Callable

import numpy as np

MASK64 = (1 << 64) - 1
MAGIC24 = 0x535347  # "SSG"
CLUE_BITS = 30
CHOICE_FMT = ">BBIII"
CHOICE_SIZE = struct.calcsize(CHOICE_FMT)


def _coprime_step(value: int, n: int) -> int:
    if n <= 1:
        return 1
    step = (value | 1) % n or 1
    while math.gcd(step, n) != 1:
        step = (step + 2) % n or 1
    return step


def _gray(x: int) -> int:
    return x ^ (x >> 1)


def _rev64(x: int) -> int:
    x &= MASK64
    x = int(f"{x:064b}"[::-1], 2)
    return x


def _mix64(x: int) -> int:
    x = (x + 0x9E3779B97F4A7C15) & MASK64
    x = ((x ^ (x >> 30)) * 0xBF58476D1CE4E5B9) & MASK64
    x = ((x ^ (x >> 27)) * 0x94D049BB133111EB) & MASK64
    return x ^ (x >> 31)


Formula = Callable[[int, int, int, int, int], int]


def _linear(mult: int) -> Formula:
    def f(n: int, N: int, o: int, a: int, b: int) -> int:
        step = _coprime_step(mult * a + b + 1, N)
        return (o + n * step) % N
    return f


def _quadratic(mult: int) -> Formula:
    def f(n: int, N: int, o: int, a: int, b: int) -> int:
        step = _coprime_step(a + mult * 2 + 1, N)
        q = (b % 97) + mult
        return (o + n * step + q * n * (n + 1) // 2) % N
    return f


def _prime_stride(prime: int) -> Formula:
    def f(n: int, N: int, o: int, a: int, b: int) -> int:
        step = _coprime_step(prime * ((a % 31) + 1) + b, N)
        return (o + n * step) % N
    return f


def _poly(power: int) -> Formula:
    def f(n: int, N: int, o: int, a: int, b: int) -> int:
        step = _coprime_step(a + 1, N)
        return (o + n * step + (pow(n, power, N) * ((b % 127) + 1))) % N
    return f


def _bitwise(kind: int) -> Formula:
    def f(n: int, N: int, o: int, a: int, b: int) -> int:
        x = (n + a) & MASK64
        if kind == 0:
            x = _gray(x)
        elif kind == 1:
            x = _rev64(x)
        elif kind == 2:
            x = _rev64(_gray(x))
        elif kind == 3:
            x ^= (x << 13) & MASK64; x ^= x >> 7; x ^= (x << 17) & MASK64
        else:
            x = ((x << 23) | (x >> 41)) & MASK64
        return (o + x + b) % N
    return f


def _mixed(salt: int) -> Formula:
    def f(n: int, N: int, o: int, a: int, b: int) -> int:
        return (o + _mix64(n + a + salt) + b) % N
    return f


def _weyl(constant: int) -> Formula:
    def f(n: int, N: int, o: int, a: int, b: int) -> int:
        x = (a + n * constant) & MASK64
        return (o + x + (b * (n + 1))) % N
    return f


FORMULAS: dict[str, list[Formula]] = {
    "A": [_linear(v) for v in (1, 3, 5, 7, 11)],
    "B": [_quadratic(v) for v in (1, 2, 3, 5, 8)],
    "C": [_prime_stride(v) for v in (17, 31, 47, 61, 73)],
    "D": [_poly(v) for v in (2, 3, 4, 5, 6)],
    "E": [_bitwise(v) for v in range(5)],
    "F": [_mixed(v) for v in (0x11, 0x2B, 0x47, 0x6D, 0x9D)],
    "G": [_weyl(v) for v in (
        0x9E3779B97F4A7C15, 0xD1B54A32D192ED03, 0x94D049BB133111EB,
        0xBF58476D1CE4E5B9, 0x632BE59BD9B4E019,
    )],
}

FAMILIES = tuple(FORMULAS)


@dataclass(frozen=True)
class FormulaChoice:
    family: str
    item: int
    offset: int
    seed_a: int
    seed_b: int

    def __post_init__(self) -> None:
        if self.family not in FORMULAS:
            raise ValueError(f"unknown family {self.family!r}")
        if not 0 <= self.item < 5:
            raise ValueError("formula item must be 0..4")

    def index(self, n: int, N: int) -> int:
        return FORMULAS[self.family][self.item](n, N, self.offset, self.seed_a, self.seed_b)

    def to_bytes(self) -> bytes:
        return struct.pack(CHOICE_FMT, FAMILIES.index(self.family), self.item,
                           self.offset & 0xFFFFFFFF, self.seed_a & 0xFFFFFFFF,
                           self.seed_b & 0xFFFFFFFF)

    @classmethod
    def from_bytes(cls, data: bytes) -> "FormulaChoice":
        fam, item, offset, a, b = struct.unpack(CHOICE_FMT, data)
        return cls(FAMILIES[fam], item, offset, a, b)


@dataclass(frozen=True)
class MapSpec:
    version: int
    start_side: int
    split_angle_cdeg: int
    split_offset: int
    real_hits: int
    decoy_hits_each: int
    real: FormulaChoice
    decoys: tuple[FormulaChoice, ...]
    metadata: bytes = b""

    def __post_init__(self) -> None:
        if self.start_side not in (0, 1):
            raise ValueError("start_side must be 0 or 1")
        if len(self.decoys) != 5:
            raise ValueError("exactly five decoy formula choices are required")

    def to_bytes(self) -> bytes:
        head = struct.pack(">BBHhHH", self.version, self.start_side,
                           self.split_angle_cdeg % 36000, self.split_offset,
                           self.real_hits, self.decoy_hits_each)
        body = self.real.to_bytes() + b"".join(x.to_bytes() for x in self.decoys)
        if len(self.metadata) > 65535:
            raise ValueError("metadata too large")
        return head + body + struct.pack(">H", len(self.metadata)) + self.metadata

    @classmethod
    def from_bytes(cls, data: bytes) -> "MapSpec":
        head_size = struct.calcsize(">BBHhHH")
        version, start_side, angle, split_offset, real_hits, decoy_hits = struct.unpack(
            ">BBHhHH", data[:head_size])
        pos = head_size
        choices = []
        for _ in range(6):
            choices.append(FormulaChoice.from_bytes(data[pos:pos + CHOICE_SIZE]))
            pos += CHOICE_SIZE
        meta_len = struct.unpack(">H", data[pos:pos + 2])[0]
        pos += 2
        metadata = data[pos:pos + meta_len]
        if len(metadata) != meta_len:
            raise ValueError("truncated map metadata")
        return cls(version, start_side, angle, split_offset, real_hits, decoy_hits,
                   choices[0], tuple(choices[1:]), metadata)

    def to_hex(self) -> str:
        return self.to_bytes().hex()

    @classmethod
    def from_hex(cls, value: str) -> "MapSpec":
        return cls.from_bytes(bytes.fromhex(value))


@dataclass
class Allocation:
    mask: np.ndarray
    real_positions: list[tuple[int, int, int]]
    decoy_positions: list[list[tuple[int, int, int]]]


def _side_of(x: int, y: int, w: int, h: int, spec: MapSpec) -> int:
    theta = math.radians(spec.split_angle_cdeg / 100.0)
    cx, cy = (w - 1) / 2.0, (h - 1) / 2.0
    signed = (x - cx) * math.cos(theta) + (y - cy) * math.sin(theta)
    return 0 if signed <= spec.split_offset else 1


def _body_yxc(index: int, width: int) -> tuple[int, int, int]:
    pixel, channel = divmod(index, 3)
    y0, x = divmod(pixel, width)
    return y0 + 1, x, channel


def _direction(choice: FormulaChoice, n: int) -> int:
    x = _mix64(choice.seed_a ^ choice.seed_b ^ n ^ choice.offset)
    return 1 if x & 1 else -1


def _fill(mask: np.ndarray, choice: FormulaChoice, count: int, target_side: int,
          spec: MapSpec, n_start: int = 0) -> tuple[list[tuple[int, int, int]], int]:
    h, w, _ = mask.shape
    N = (h - 1) * w * 3
    if N <= 0:
        raise ValueError("image must have at least two rows")
    out: list[tuple[int, int, int]] = []
    n = n_start
    attempts = 0
    limit = max(N * 8, count * 100)
    while len(out) < count:
        idx = choice.index(n, N)
        direction = _direction(choice, n)
        n += 1
        attempts += 1
        if attempts > limit:
            raise RuntimeError("formula stream could not find enough unused positions")
        y, x, c = _body_yxc(idx, w)
        if _side_of(x, y, w, h, spec) != target_side:
            continue
        if mask[y, x, c] != 0:
            continue
        mask[y, x, c] = direction
        out.append((y, x, c))
    return out, n


def build_memory_map(width: int, height: int, spec: MapSpec) -> Allocation:
    mask = np.zeros((height, width, 3), dtype=np.int8)
    real: list[tuple[int, int, int]] = []
    first = spec.start_side
    counts = (spec.real_hits // 2 + spec.real_hits % 2, spec.real_hits // 2)
    stream_n = 0
    for i, count in enumerate(counts):
        side = first if i == 0 else 1 - first
        hits, stream_n = _fill(mask, spec.real, count, side, spec, stream_n)
        real.extend(hits)

    decoys: list[list[tuple[int, int, int]]] = []
    for stream_id, choice in enumerate(spec.decoys):
        positions: list[tuple[int, int, int]] = []
        n = 0
        left = spec.decoy_hits_each // 2 + spec.decoy_hits_each % 2
        right = spec.decoy_hits_each // 2
        side0 = (first + stream_id) & 1
        p, n = _fill(mask, choice, left, side0, spec, n)
        positions.extend(p)
        p, n = _fill(mask, choice, right, 1 - side0, spec, n)
        positions.extend(p)
        decoys.append(positions)
    return Allocation(mask, real, decoys)


def _bytes_to_bits(data: bytes) -> list[int]:
    return [(byte >> shift) & 1 for byte in data for shift in range(7, -1, -1)]


def _bits_to_bytes(bits: list[int]) -> bytes:
    out = bytearray()
    for i in range(0, len(bits), 8):
        value = 0
        for bit in bits[i:i + 8]:
            value = (value << 1) | bit
        out.append(value)
    return bytes(out)


def _clue_bits(spec: MapSpec) -> list[int]:
    value = (MAGIC24 << 5) | ((spec.version & 0xF) << 1) | spec.start_side
    bits = [(value >> shift) & 1 for shift in range(28, -1, -1)]
    bits.append(sum(bits) & 1)
    return bits


def _decode_clue(bits: list[int]) -> tuple[int, int]:
    if len(bits) != CLUE_BITS or (sum(bits[:-1]) & 1) != bits[-1]:
        raise ValueError("bad clue parity")
    value = 0
    for bit in bits[:-1]:
        value = (value << 1) | bit
    start_side = value & 1
    version = (value >> 1) & 0xF
    magic = value >> 5
    if magic != MAGIC24:
        raise ValueError("not a StegoSplit clue")
    return version, start_side


def encode_header(cover: np.ndarray, spec: MapSpec) -> tuple[np.ndarray, np.ndarray, int]:
    if cover.dtype != np.uint8 or cover.ndim != 3 or cover.shape[2] != 3:
        raise ValueError("cover must be uint8 RGB array")
    payload = spec.to_bytes()
    body = len(payload).to_bytes(2, "big") + payload
    bits = _clue_bits(spec) + _bytes_to_bits(body)
    row = cover[0].reshape(-1)
    candidates_plus = [i for i, v in enumerate(row) if v < 255]
    candidates_minus = [i for i, v in enumerate(row) if v > 0]
    if len(candidates_plus) >= len(bits):
        mode, candidates = 1, candidates_plus
    elif len(candidates_minus) >= len(bits):
        mode, candidates = -1, candidates_minus
    else:
        raise ValueError("first row lacks capacity for header")
    a = cover.copy()
    b = cover.copy()
    af, bf = a[0].reshape(-1), b[0].reshape(-1)
    for bit, pos in zip(bits, candidates):
        if bit:
            af[pos] = np.uint8(int(af[pos]) + mode)
        else:
            bf[pos] = np.uint8(int(bf[pos]) + mode)
    return a, b, mode


def decode_header(a: np.ndarray, b: np.ndarray) -> tuple[MapSpec, int]:
    if a.shape != b.shape or a.ndim != 3 or a.shape[2] != 3:
        raise ValueError("share arrays must be matching RGB images")
    for mode in (1, -1):
        try:
            base = np.minimum(a[0], b[0]) if mode == 1 else np.maximum(a[0], b[0])
            flat_base = base.reshape(-1)
            candidates = [i for i, v in enumerate(flat_base) if (v < 255 if mode == 1 else v > 0)]
            diffs = a[0].astype(np.int16).reshape(-1) - b[0].astype(np.int16).reshape(-1)
            def read_bits(count: int, start: int = 0) -> list[int]:
                out = []
                for pos in candidates[start:start + count]:
                    d = int(diffs[pos])
                    if d == mode:
                        out.append(1)
                    elif d == -mode:
                        out.append(0)
                    else:
                        raise ValueError("header carrier mismatch")
                if len(out) != count:
                    raise ValueError("truncated header")
                return out
            clue = read_bits(CLUE_BITS)
            version, side = _decode_clue(clue)
            length_bits = read_bits(16, CLUE_BITS)
            payload_len = int.from_bytes(_bits_to_bytes(length_bits), "big")
            payload_bits = read_bits(payload_len * 8, CLUE_BITS + 16)
            spec = MapSpec.from_bytes(_bits_to_bytes(payload_bits))
            if spec.version != version or spec.start_side != side:
                raise ValueError("clue/map mismatch")
            return spec, mode
        except (ValueError, IndexError, struct.error):
            continue
    raise ValueError("pair does not contain a valid StegoSplit header")


def apply_memory_map(cover: np.ndarray, spec: MapSpec) -> tuple[np.ndarray, np.ndarray, Allocation, np.ndarray]:
    """Apply header + body mutation map. Row 0 is header only; body begins at row 1."""
    if cover.dtype != np.uint8 or cover.ndim != 3 or cover.shape[2] != 3:
        raise ValueError("cover must be uint8 RGB array")
    h, w, _ = cover.shape
    allocation = build_memory_map(w, h, spec)
    a, b, _mode = encode_header(cover, spec)
    final_mask = allocation.mask.copy()
    ys, xs, cs = np.nonzero(final_mask)
    for y, x, c in zip(ys.tolist(), xs.tolist(), cs.tolist()):
        if y == 0:
            raise AssertionError("body map must never use header row")
        base = int(cover[y, x, c])
        direction = int(final_mask[y, x, c])
        if base + direction < 0 or base + direction > 255:
            direction = -direction
            final_mask[y, x, c] = direction
        changed = np.uint8(base + direction)
        side = _side_of(x, y, w, h, spec)
        if side == spec.start_side:
            a[y, x, c] = changed
        else:
            b[y, x, c] = changed
    return a, b, allocation, final_mask


def reconstruct_pair(a: np.ndarray, b: np.ndarray) -> tuple[np.ndarray, MapSpec]:
    """Rebuild the exact cover from the pair alone; no password/key is needed."""
    spec, header_mode = decode_header(a, b)
    if a.shape != b.shape:
        raise ValueError("share shapes differ")
    h, w, _ = a.shape
    cover = np.empty_like(a)
    cover[0] = np.minimum(a[0], b[0]) if header_mode == 1 else np.maximum(a[0], b[0])
    for y in range(1, h):
        for x in range(w):
            side = _side_of(x, y, w, h, spec)
            a_owns = side == spec.start_side
            for c in range(3):
                av, bv = int(a[y, x, c]), int(b[y, x, c])
                if av == bv:
                    cover[y, x, c] = a[y, x, c]
                    continue
                if abs(av - bv) != 1:
                    raise ValueError("body differential exceeds one level")
                cover[y, x, c] = b[y, x, c] if a_owns else a[y, x, c]
    return cover, spec
