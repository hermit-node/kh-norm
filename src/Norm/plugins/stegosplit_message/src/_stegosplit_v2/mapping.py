from __future__ import annotations

import hashlib
import hmac
import math
from dataclasses import dataclass

FORMULA_COUNT = 16
FORMULA_NAMES = (
    "affine",
    "affine-rotate",
    "xor-affine",
    "xorshift-r",
    "xorshift-l-affine",
    "gray-affine",
    "bitreverse-affine",
    "rotate-xorshift-affine",
    "dual-xorshift",
    "mul-rotate-add-xor",
    "gray-rotate-mul",
    "bitreverse-xor-mul",
    "xorshift-triple",
    "arx-mix",
    "double-affine-rotate",
    "deep-mix",
)


def _hmac(key: bytes, *parts: bytes) -> bytes:
    return hmac.new(key, b"".join(parts), hashlib.sha256).digest()


def _u64(key: bytes, label: bytes, counter: int = 0) -> int:
    return int.from_bytes(_hmac(key, label, counter.to_bytes(8, "big"))[:8], "big")


def _mask(bits: int) -> int:
    return (1 << bits) - 1


def _rotl(x: int, shift: int, bits: int) -> int:
    if bits <= 1:
        return x & _mask(bits)
    shift %= bits
    if shift == 0:
        return x & _mask(bits)
    m = _mask(bits)
    return ((x << shift) | (x >> (bits - shift))) & m


def _rotr(x: int, shift: int, bits: int) -> int:
    if bits <= 1:
        return x & _mask(bits)
    shift %= bits
    if shift == 0:
        return x & _mask(bits)
    m = _mask(bits)
    return ((x >> shift) | (x << (bits - shift))) & m


def _bitreverse(x: int, bits: int) -> int:
    out = 0
    for _ in range(bits):
        out = (out << 1) | (x & 1)
        x >>= 1
    return out


def _shift(seed: int, bits: int, salt: int) -> int:
    if bits <= 1:
        return 1
    return 1 + ((seed >> (salt % 47)) % (bits - 1))


def _odd(seed: int, bits: int, salt: int = 0) -> int:
    m = _mask(bits)
    value = ((seed ^ (0x9E3779B97F4A7C15 * (salt + 1))) | 1) & m
    return value or 1


def _xorshift_r(x: int, shift: int, bits: int) -> int:
    return (x ^ (x >> shift)) & _mask(bits)


def _xorshift_l(x: int, shift: int, bits: int) -> int:
    m = _mask(bits)
    return (x ^ ((x << shift) & m)) & m


def _transform(formula_id: int, x: int, bits: int, seed_a: int, seed_b: int, offset: int) -> int:
    """Apply one of 16 bijections on a 2**bits domain.

    Every operation used here is individually invertible on a fixed-width bit word:
    modular addition, XOR, odd multiplication, rotation, bit reversal, Gray mapping,
    and xorshift. Their compositions are therefore permutations. Cycle-walking in
    ``permute_index`` restricts each permutation to [0, N) without collisions.
    """
    if not 0 <= formula_id < FORMULA_COUNT:
        raise ValueError(f"formula_id must be 0..{FORMULA_COUNT - 1}")
    m = _mask(bits)
    x &= m
    a1 = _odd(seed_a, bits, 1)
    a2 = _odd(seed_b, bits, 2)
    c1 = (seed_b ^ offset ^ 0xA5A5A5A5A5A5A5A5) & m
    c2 = (seed_a + offset + 0x6A09E667F3BCC909) & m
    s1 = _shift(seed_a, bits, 5)
    s2 = _shift(seed_b, bits, 17)
    r1 = (seed_a ^ seed_b ^ 7) % max(bits, 1)
    r2 = (seed_b ^ offset ^ 13) % max(bits, 1)

    if formula_id == 0:  # cheapest: one affine permutation
        return (x * a1 + c1) & m
    if formula_id == 1:
        return _rotl((x * a1 + c1) & m, r1, bits)
    if formula_id == 2:
        return (((x ^ c2) * a1) + c1) & m
    if formula_id == 3:
        return (_xorshift_r(x, s1, bits) + c1) & m
    if formula_id == 4:
        return (_xorshift_l(x, s1, bits) * a1 + c2) & m
    if formula_id == 5:
        x = _xorshift_r(x, 1, bits)  # binary -> Gray, itself bijective
        return (x * a1 + c1) & m
    if formula_id == 6:
        return (_bitreverse(x, bits) * a1 + c2) & m
    if formula_id == 7:
        x = _rotl(x, r1, bits)
        x = _xorshift_r(x, s1, bits)
        return (x * a1 + c1) & m
    if formula_id == 8:
        x = _xorshift_r(x, s1, bits)
        x = _xorshift_l(x, s2, bits)
        return (x + c1) & m
    if formula_id == 9:
        x = (x * a1) & m
        x = _rotl(x, r1, bits)
        x = (x + c2) & m
        return x ^ c1
    if formula_id == 10:
        x = _xorshift_r(x, 1, bits)
        x = _rotl(x, r1, bits)
        return (x * a2 + c1) & m
    if formula_id == 11:
        x = _bitreverse(x, bits)
        x ^= c2
        return (x * a1 + c1) & m
    if formula_id == 12:
        x = _xorshift_l(x, s1, bits)
        x = _xorshift_r(x, s2, bits)
        x = _xorshift_l(x, _shift(seed_a ^ seed_b, bits, 29), bits)
        return (x * a1 + c1) & m
    if formula_id == 13:
        x = (x + c1) & m
        x = _rotl(x, r1, bits)
        x ^= c2
        x = (x * a1) & m
        return _rotr(x, r2, bits)
    if formula_id == 14:
        x = (x * a1 + c1) & m
        x = _rotl(x, r1, bits)
        return (x * a2 + c2) & m

    # formula 15: longest reversible chain
    x ^= c1
    x = (x * a1) & m
    x = _xorshift_r(x, s1, bits)
    x = (x + c2) & m
    x = _rotl(x, r2, bits)
    x = (x * a2) & m
    x = _xorshift_l(x, s2, bits)
    return x & m


@dataclass(frozen=True)
class FormulaChoice:
    formula_id: int
    seed_a: int
    seed_b: int
    offset: int

    @property
    def name(self) -> str:
        return FORMULA_NAMES[self.formula_id]

    def index(self, n: int, size: int) -> int:
        return permute_index(n, size, self)


def permute_index(n: int, size: int, choice: FormulaChoice) -> int:
    """Map n in [0,size) to a unique index in [0,size).

    A power-of-two permutation is cycle-walked back into the requested domain.
    This keeps every formula collision-free before cross-stream collision filtering.
    """
    if size <= 0:
        raise ValueError("size must be positive")
    if not 0 <= n < size:
        raise ValueError(f"n must be in [0,{size})")
    bits = max(1, (size - 1).bit_length())
    x = n
    # The starting x belongs to the accepted subset, so a permutation cycle must
    # eventually return to that subset. Guard anyway in case a future formula is bad.
    for _ in range((1 << min(bits, 20)) + 4):
        x = _transform(choice.formula_id, x, bits, choice.seed_a, choice.seed_b, choice.offset)
        if x < size:
            return x
    raise RuntimeError("formula cycle-walk did not return to the target domain")


@dataclass(frozen=True)
class MappingPlan:
    angle_cdeg: int
    a_region: int
    real_formula_id: int
    decoy_formula_ids: tuple[int, int, int, int]

    @property
    def angle_degrees(self) -> float:
        return self.angle_cdeg / 100.0

    @property
    def real_formula_name(self) -> str:
        return FORMULA_NAMES[self.real_formula_id]

    @property
    def decoy_formula_names(self) -> tuple[str, str, str, str]:
        return tuple(FORMULA_NAMES[i] for i in self.decoy_formula_ids)  # type: ignore[return-value]


def derive_plan(
    map_key: bytes,
    nonce: bytes,
    real_formula_id: int,
    *,
    angle_cdeg: int | None = None,
) -> MappingPlan:
    """Derive geometry/decoys while keeping the public real formula fixed."""
    if not 0 <= real_formula_id < FORMULA_COUNT:
        raise ValueError(f"real_formula_id must be 0..{FORMULA_COUNT - 1}")
    if angle_cdeg is None:
        angle_raw = int.from_bytes(_hmac(map_key, b"angle", nonce)[:4], "big")
        angle_cdeg = 4500 + (angle_raw % 4500)
    if not 4500 <= angle_cdeg <= 8999:
        raise ValueError("angle_cdeg must be in [4500, 8999]")
    a_region = _hmac(map_key, b"a-region", nonce, bytes([real_formula_id]))[0] & 1

    ranked = sorted(
        (i for i in range(FORMULA_COUNT) if i != real_formula_id),
        key=lambda i: _hmac(map_key, b"formula-rank", nonce, bytes([real_formula_id, i])),
    )
    return MappingPlan(angle_cdeg, a_region, real_formula_id, tuple(ranked[:4]))


def derive_bootstrap_plan(bootstrap_key: bytes, context_nonce: bytes, real_formula_id: int) -> MappingPlan:
    """Password-derived plan used only to locate the fixed-size hidden control block."""
    return derive_plan(bootstrap_key, context_nonce, real_formula_id)

def derive_choice(map_key: bytes, nonce: bytes, formula_id: int, label: bytes) -> FormulaChoice:
    digest = _hmac(map_key, b"choice", nonce, bytes([formula_id]), label)
    return FormulaChoice(
        formula_id=formula_id,
        seed_a=int.from_bytes(digest[0:8], "big"),
        seed_b=int.from_bytes(digest[8:16], "big"),
        offset=int.from_bytes(digest[16:24], "big"),
    )


def geometric_region(x: int, y: int, width: int, height: int, plan: MappingPlan) -> int:
    theta = math.radians(plan.angle_degrees)
    cx = (width - 1) / 2.0
    cy = (height - 1) / 2.0
    signed = (x - cx) * math.cos(theta) + (y - cy) * math.sin(theta)
    return 0 if signed <= 0.0 else 1


def owner_for_pixel(x: int, y: int, width: int, height: int, plan: MappingPlan) -> int:
    """Return 0 if share A owns this body pixel, 1 if share B owns it."""
    region = geometric_region(x, y, width, height, plan)
    return 0 if region == plan.a_region else 1


def candidate_pixels(width: int, height: int, owner: int, plan: MappingPlan) -> list[int]:
    if owner not in (0, 1):
        raise ValueError("owner must be 0 (A) or 1 (B)")

    # Precompute the line once. The old implementation recalculated radians, sin,
    # cos and center values for every pixel.
    theta = math.radians(plan.angle_degrees)
    cos_t = math.cos(theta)
    sin_t = math.sin(theta)
    cx = (width - 1) / 2.0
    cy = (height - 1) / 2.0

    out: list[int] = []
    for y in range(1, height):  # row 0 is public bootstrap only
        dy = y - cy
        row_start = y * width
        for x in range(width):
            signed = (x - cx) * cos_t + dy * sin_t
            region = 0 if signed <= 0.0 else 1
            pixel_owner = 0 if region == plan.a_region else 1
            if pixel_owner == owner:
                out.append(row_start + x)
    return out

def allocate_pixels(
    candidates: list[int],
    choice: FormulaChoice,
    count: int,
    used: set[int] | None = None,
) -> list[int]:
    """Take `count` pixels from a formula permutation, skipping globally used pixels."""
    if count < 0:
        raise ValueError("count cannot be negative")
    used = used if used is not None else set()
    available = sum(1 for p in candidates if p not in used)
    if count > available:
        raise ValueError(f"carrier region too small: need {count} free pixels, have {available}")
    out: list[int] = []
    for n in range(len(candidates)):
        pixel = candidates[choice.index(n, len(candidates))]
        if pixel in used:
            continue
        used.add(pixel)
        out.append(pixel)
        if len(out) == count:
            return out
    raise RuntimeError("formula stream exhausted before requested allocation completed")


def formula_self_test(sizes: tuple[int, ...] = (2, 3, 17, 127, 1000)) -> None:
    """Raise if any formula stops being a permutation on representative domain sizes."""
    for formula_id in range(FORMULA_COUNT):
        choice = FormulaChoice(formula_id, 0x123456789ABCDEF1, 0x0FEDCBA987654321, 0xA55A)
        for size in sizes:
            values = [choice.index(i, size) for i in range(size)]
            if len(set(values)) != size or min(values) != 0 or max(values) != size - 1:
                raise AssertionError(f"formula {formula_id} is not a permutation for size={size}")
