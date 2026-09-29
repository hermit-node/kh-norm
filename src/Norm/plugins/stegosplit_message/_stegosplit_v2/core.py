from __future__ import annotations

import hashlib
import hmac
import math
import os
import secrets
import struct
import uuid
import zlib
from dataclasses import asdict, dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from PIL import Image

from . import cipher, header, kdf, mapping

DEFAULT_DECOY_RATIO = 1.0
MAX_ORIGINAL_BYTES = 64 * 1024 * 1024

# Maps 0..255 into 1..254 with <=1 change per channel. This makes every later
# +/-1 operation arithmetic-safe without clipping or wraparound.
_CONDITION_LUT = bytes(round(1 + value * 253 / 255) for value in range(256))


@dataclass(frozen=True)
class PairInfo:
    width: int
    height: int
    format_version: int
    ciphertext_bytes: int
    original_bytes: int
    control_bits_per_share: int
    real_bits_per_share: int
    decoy_pixels: int
    angle_degrees: float
    share_a_geometric_region: int
    real_formula_id: int
    real_formula: str
    decoy_formula_ids: tuple[int, int, int, int]
    decoy_formulas: tuple[str, str, str, str]
    compressed: bool

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _require_png(path: str | Path) -> None:
    if Path(path).suffix.lower() != ".png":
        raise ValueError("StegoSplit shares must be saved as lossless .png files")


def _condition_image(image: Image.Image) -> Image.Image:
    rgb = image.convert("RGB")
    raw = rgb.tobytes()
    conditioned = bytes(_CONDITION_LUT[value] for value in raw)
    width, _height = rgb.size
    conditioned = header.canonicalize_base(conditioned, width)
    return Image.frombytes("RGB", rgb.size, conditioned)


def _compress_payload(data: bytes) -> tuple[bytes, bool]:
    compressed = zlib.compress(data, level=9)
    if len(compressed) < len(data):
        return compressed, True
    return data, False


def _decompress_payload(data: bytes, compressed: bool, expected_length: int) -> bytes:
    if expected_length > MAX_ORIGINAL_BYTES:
        raise ValueError(f"declared payload is too large ({expected_length} bytes)")
    if not compressed:
        if len(data) != expected_length:
            raise ValueError("authenticated payload length does not match control metadata")
        return data

    try:
        obj = zlib.decompressobj()
        out = obj.decompress(data, expected_length + 1)
    except zlib.error as exc:
        raise ValueError("authenticated payload could not be decompressed") from exc
    if len(out) > expected_length or not obj.eof or obj.unconsumed_tail:
        raise ValueError("compressed payload exceeds its authenticated output bound")
    if len(out) != expected_length:
        raise ValueError("decompressed payload length does not match control metadata")
    return out


def _srgb_linear(value: int) -> float:
    c = value / 255.0
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def _rgb_to_lab(rgb: tuple[int, int, int]) -> tuple[float, float, float]:
    r, g, b = (_srgb_linear(v) for v in rgb)
    x = r * 0.4124564 + g * 0.3575761 + b * 0.1804375
    y = r * 0.2126729 + g * 0.7151522 + b * 0.0721750
    z = r * 0.0193339 + g * 0.1191920 + b * 0.9503041
    x /= 0.95047
    z /= 1.08883

    def f(t: float) -> float:
        d = 6.0 / 29.0
        return t ** (1 / 3) if t > d**3 else t / (3 * d * d) + 4 / 29

    fx, fy, fz = f(x), f(y), f(z)
    return 116 * fy - 16, 500 * (fx - fy), 200 * (fy - fz)


@lru_cache(maxsize=131072)
def _least_visible_channel(r: int, g: int, b: int, delta: int) -> int:
    if delta not in (-1, 1):
        raise ValueError("delta must be -1 or +1")
    base = (r, g, b)
    base_lab = _rgb_to_lab(base)
    tie_rank = {2: 0, 0: 1, 1: 2}
    candidates: list[tuple[float, int, int]] = []
    for channel in range(3):
        changed = list(base)
        changed[channel] += delta
        if not 0 <= changed[channel] <= 255:
            continue
        lab = _rgb_to_lab((changed[0], changed[1], changed[2]))
        cost = sum((a - b_) ** 2 for a, b_ in zip(base_lab, lab))
        candidates.append((cost, tie_rank[channel], channel))
    if not candidates:
        raise ValueError("no RGB channel can accept the requested +/-1 change")
    return min(candidates)[2]


def _pixel_rgb(raw: bytes | bytearray, pixel: int) -> tuple[int, int, int]:
    i = pixel * 3
    return raw[i], raw[i + 1], raw[i + 2]


def _write_bit(
    base: bytes,
    share_a: bytearray,
    share_b: bytearray,
    pixel: int,
    owner: int,
    bit: int,
) -> None:
    delta = 1 if bit else -1
    r, g, b = _pixel_rgb(base, pixel)
    channel = _least_visible_channel(r, g, b, delta)
    idx = pixel * 3 + channel
    value = base[idx]
    if not 1 <= value <= 254:
        raise ValueError("carrier channel escaped the conditioned 1..254 range")
    if owner == 0:
        share_a[idx] = value + delta
    elif owner == 1:
        share_b[idx] = value + delta
    else:
        raise ValueError("owner must be 0 or 1")


def _read_bit(raw_a: bytes, raw_b: bytes, pixel: int, owner: int, *, width: int | None = None) -> int:
    start = pixel * 3
    if owner == 0:
        diffs = [raw_a[start + c] - raw_b[start + c] for c in range(3)]
        direction = "A-B"
        owner_name = "A"
    else:
        diffs = [raw_b[start + c] - raw_a[start + c] for c in range(3)]
        direction = "B-A"
        owner_name = "B"
    nonzero = [d for d in diffs if d != 0]
    if len(nonzero) != 1 or nonzero[0] not in (-1, 1):
        location = f"flat={pixel}"
        if width:
            location += f", x={pixel % width}, y={pixel // width}"
        raise ValueError(
            "invalid differential payload pixel "
            f"({location}, owner={owner_name}, direction={direction}, RGB deltas={tuple(diffs)}); "
            "expected exactly one channel at -1 or +1"
        )
    return 1 if nonzero[0] == 1 else 0


def _fake_bit(map_key: bytes, nonce: bytes, formula_index: int, owner: int, ordinal: int) -> int:
    msg = b"fake-bit" + nonce + bytes([formula_index, owner]) + ordinal.to_bytes(8, "big")
    return hmac.new(map_key, msg, hashlib.sha256).digest()[0] & 1


def _split_counts(total: int, parts: int) -> list[int]:
    q, r = divmod(total, parts)
    return [q + (1 if i < r else 0) for i in range(parts)]


def _bootstrap_context(width: int, height: int, formula_id: int, raw: bytes) -> bytes:
    # Pixels 0 and 1 are never modified by V2 and make the bootstrap KDF image-local
    # without adding a pair identifier to the public header.
    return struct.pack(">IIB", width, height, formula_id) + raw[:6]


def _context_nonce(context: bytes) -> bytes:
    return hashlib.sha256(b"stegosplit-v2:context-nonce\x00" + context).digest()[:12]


def _bootstrap_positions(
    width: int,
    height: int,
    bootstrap_key: bytes,
    context_nonce: bytes,
    formula_id: int,
) -> tuple[mapping.MappingPlan, list[int], list[int], list[int], list[int]]:
    plan = mapping.derive_bootstrap_plan(bootstrap_key, context_nonce, formula_id)
    candidates_a = mapping.candidate_pixels(width, height, 0, plan)
    candidates_b = mapping.candidate_pixels(width, height, 1, plan)
    count = header.CONTROL_BITS_PER_SHARE
    if count > len(candidates_a) or count > len(candidates_b):
        raise ValueError("image is too small for the password-gated V2 control block")
    choice_a = mapping.derive_choice(bootstrap_key, context_nonce, formula_id, b"control-A")
    choice_b = mapping.derive_choice(bootstrap_key, context_nonce, formula_id, b"control-B")
    pos_a = mapping.allocate_pixels(candidates_a, choice_a, count)
    pos_b = mapping.allocate_pixels(candidates_b, choice_b, count)
    return plan, candidates_a, candidates_b, pos_a, pos_b


def _payload_plan(
    width: int,
    height: int,
    map_key: bytes,
    control: header.ControlBlock,
    formula_id: int,
) -> tuple[mapping.MappingPlan, list[int], list[int]]:
    plan = mapping.derive_plan(
        map_key,
        control.nonce,
        formula_id,
        angle_cdeg=control.angle_cdeg,
    )
    return (
        plan,
        mapping.candidate_pixels(width, height, 0, plan),
        mapping.candidate_pixels(width, height, 1, plan),
    )


def _payload_positions(
    map_key: bytes,
    control: header.ControlBlock,
    plan: mapping.MappingPlan,
    candidates_a: list[int],
    candidates_b: list[int],
    count_per_share: int,
    used: set[int],
) -> tuple[list[int], list[int]]:
    choice_a = mapping.derive_choice(map_key, control.nonce, plan.real_formula_id, b"payload-A")
    choice_b = mapping.derive_choice(map_key, control.nonce, plan.real_formula_id, b"payload-B")
    pos_a = mapping.allocate_pixels(candidates_a, choice_a, count_per_share, used)
    pos_b = mapping.allocate_pixels(candidates_b, choice_b, count_per_share, used)
    return pos_a, pos_b


def _embed_decoys(
    base: bytes,
    share_a: bytearray,
    share_b: bytearray,
    map_key: bytes,
    control: header.ControlBlock,
    plan: mapping.MappingPlan,
    candidates_a: list[int],
    candidates_b: list[int],
    used: set[int],
    total_decoys: int,
) -> int:
    per_formula = _split_counts(total_decoys, 4)
    written = 0
    for formula_index, (formula_id, formula_count) in enumerate(zip(plan.decoy_formula_ids, per_formula)):
        side_counts = _split_counts(formula_count, 2)
        for owner, (candidates, side_count) in enumerate(
            ((candidates_a, side_counts[0]), (candidates_b, side_counts[1]))
        ):
            choice = mapping.derive_choice(
                map_key,
                control.nonce,
                formula_id,
                f"decoy-{formula_index}-{owner}".encode("ascii"),
            )
            pixels = mapping.allocate_pixels(candidates, choice, side_count, used)
            for ordinal, pixel in enumerate(pixels):
                bit = _fake_bit(map_key, control.nonce, formula_index, owner, ordinal)
                _write_bit(base, share_a, share_b, pixel, owner, bit)
                written += 1
    return written


def _save_verified_pair(
    width: int,
    height: int,
    share_a: bytearray,
    share_b: bytearray,
    output_a: str | Path,
    output_b: str | Path,
    password: str,
    expected_data: bytes,
) -> None:
    out_a = Path(output_a)
    out_b = Path(output_b)
    out_a.parent.mkdir(parents=True, exist_ok=True)
    out_b.parent.mkdir(parents=True, exist_ok=True)
    token = uuid.uuid4().hex
    tmp_a = out_a.with_name(f".{out_a.stem}.{token}.tmp.png")
    tmp_b = out_b.with_name(f".{out_b.stem}.{token}.tmp.png")
    try:
        Image.frombytes("RGB", (width, height), bytes(share_a)).save(tmp_a, format="PNG", optimize=True)
        Image.frombytes("RGB", (width, height), bytes(share_b)).save(tmp_b, format="PNG", optimize=True)
        recovered = extract_bytes(tmp_a, tmp_b, password)
        if recovered != expected_data:
            raise RuntimeError("self-verification failed: temporary pair did not round-trip")
        os.replace(tmp_a, out_a)
        os.replace(tmp_b, out_b)
    finally:
        for path in (tmp_a, tmp_b):
            try:
                path.unlink()
            except FileNotFoundError:
                pass


def _embed_from_base(
    base_image: Image.Image,
    data: bytes,
    password: str,
    output_a: str | Path,
    output_b: str | Path,
    decoy_ratio: float,
) -> PairInfo:
    _require_png(output_a)
    _require_png(output_b)
    if Path(output_a).resolve() == Path(output_b).resolve():
        raise ValueError("output_a and output_b must be different files")
    if decoy_ratio < 0:
        raise ValueError("decoy_ratio cannot be negative")
    if len(data) > MAX_ORIGINAL_BYTES:
        raise ValueError(f"payload exceeds the {MAX_ORIGINAL_BYTES}-byte V2 safety bound")

    width, height = base_image.size
    if width < header.MIN_HEADER_WIDTH:
        raise ValueError(f"image width must be at least {header.MIN_HEADER_WIDTH} pixels")
    if height < 2:
        raise ValueError("image must have at least two rows")

    base = header.canonicalize_base(base_image.tobytes(), width)
    formula_id = secrets.randbelow(mapping.FORMULA_COUNT)
    public_a = header.PublicHeader(role=0, formula_id=formula_id)
    public_b = header.PublicHeader(role=1, formula_id=formula_id)

    context = _bootstrap_context(width, height, formula_id, base)
    context_nonce = _context_nonce(context)
    bootstrap_key = kdf.derive_bootstrap_key(password, context)
    _bootstrap_plan, _boot_ca, _boot_cb, control_pos_a, control_pos_b = _bootstrap_positions(
        width, height, bootstrap_key, context_nonce, formula_id
    )

    payload, compressed = _compress_payload(data)
    salt = kdf.generate_salt()
    nonce = os.urandom(header.NONCE_SIZE)
    angle_cdeg = 4500 + secrets.randbelow(4500)
    flags = header.FLAG_COMPRESSED if compressed else 0
    ciphertext_length = len(payload) + header.AEAD_TAG_SIZE
    control = header.ControlBlock(
        angle_cdeg=angle_cdeg,
        flags=flags,
        salt=salt,
        nonce=nonce,
        ciphertext_length=ciphertext_length,
        original_length=len(data),
    )

    root_key = kdf.derive_root_from_bootstrap(bootstrap_key, salt)
    enc_key = kdf.derive_subkey(root_key, kdf.LABEL_ENCRYPTION)
    map_key = kdf.derive_subkey(root_key, kdf.LABEL_MAPPING)
    ciphertext = cipher.encrypt(payload, enc_key, nonce, header.associated_data(public_a, control))

    control_bits = header.bytes_to_bits(control.to_bytes())
    control_half = len(control_bits) // 2
    control_bits_a = control_bits[:control_half]
    control_bits_b = control_bits[control_half:]

    cipher_bits = header.bytes_to_bits(ciphertext)
    if len(cipher_bits) % 2:
        raise AssertionError("ciphertext bit count unexpectedly odd")
    half = len(cipher_bits) // 2
    bits_a, bits_b = cipher_bits[:half], cipher_bits[half:]

    plan, candidates_a, candidates_b = _payload_plan(width, height, map_key, control, formula_id)

    share_a = bytearray(base)
    share_b = bytearray(base)
    header.encode_public_header(base, share_a, width, public_a)
    header.encode_public_header(base, share_b, width, public_b)

    used = set(control_pos_a)
    used.update(control_pos_b)
    for bit, pixel in zip(control_bits_a, control_pos_a):
        _write_bit(base, share_a, share_b, pixel, 0, bit)
    for bit, pixel in zip(control_bits_b, control_pos_b):
        _write_bit(base, share_a, share_b, pixel, 1, bit)

    pos_a, pos_b = _payload_positions(
        map_key, control, plan, candidates_a, candidates_b, half, used
    )
    for bit, pixel in zip(bits_a, pos_a):
        _write_bit(base, share_a, share_b, pixel, 0, bit)
    for bit, pixel in zip(bits_b, pos_b):
        _write_bit(base, share_a, share_b, pixel, 1, bit)

    total_real_body_bits = header.CONTROL_BITS + len(cipher_bits)
    total_decoys = int(math.ceil(total_real_body_bits * decoy_ratio))
    free_total = sum(1 for p in candidates_a if p not in used) + sum(
        1 for p in candidates_b if p not in used
    )
    if total_decoys > free_total:
        raise ValueError(
            f"decoy_ratio={decoy_ratio} needs {total_decoys} fake pixels but only {free_total} remain"
        )
    decoy_pixels = _embed_decoys(
        base,
        share_a,
        share_b,
        map_key,
        control,
        plan,
        candidates_a,
        candidates_b,
        used,
        total_decoys,
    )

    _save_verified_pair(width, height, share_a, share_b, output_a, output_b, password, data)

    return PairInfo(
        width=width,
        height=height,
        format_version=header.FORMAT_VERSION,
        ciphertext_bytes=len(ciphertext),
        original_bytes=len(data),
        control_bits_per_share=header.CONTROL_BITS_PER_SHARE,
        real_bits_per_share=half,
        decoy_pixels=decoy_pixels,
        angle_degrees=plan.angle_degrees,
        share_a_geometric_region=plan.a_region,
        real_formula_id=plan.real_formula_id,
        real_formula=plan.real_formula_name,
        decoy_formula_ids=plan.decoy_formula_ids,
        decoy_formulas=plan.decoy_formula_names,
        compressed=compressed,
    )


def embed_bytes(
    source: str | Path,
    data: bytes,
    password: str,
    output_a: str | Path,
    output_b: str | Path,
    *,
    decoy_ratio: float = DEFAULT_DECOY_RATIO,
) -> PairInfo:
    with Image.open(source) as image:
        base_image = _condition_image(image)
    try:
        return _embed_from_base(base_image, bytes(data), password, output_a, output_b, decoy_ratio)
    finally:
        base_image.close()


def embed(
    source: str | Path,
    message: str,
    password: str,
    output_a: str | Path,
    output_b: str | Path,
    *,
    decoy_ratio: float = DEFAULT_DECOY_RATIO,
) -> PairInfo:
    return embed_bytes(
        source,
        message.encode("utf-8"),
        password,
        output_a,
        output_b,
        decoy_ratio=decoy_ratio,
    )


def _load_ordered_pair(
    image_1: str | Path, image_2: str | Path
) -> tuple[Image.Image, Image.Image, bytes, bytes, header.PublicHeader]:
    if Path(image_1).resolve() == Path(image_2).resolve():
        raise ValueError("the two share paths must be different files")
    first = Image.open(image_1).convert("RGB")
    second = Image.open(image_2).convert("RGB")
    if first.size != second.size:
        first.close()
        second.close()
        raise ValueError("share images must have identical dimensions")
    width, _height = first.size
    raw_first = first.tobytes()
    raw_second = second.tobytes()
    try:
        h1 = header.decode_public_header(raw_first, width)
        h2 = header.decode_public_header(raw_second, width)
    except Exception:
        first.close()
        second.close()
        raise
    if h1.role == h2.role:
        first.close()
        second.close()
        raise ValueError("both supplied images claim the same A/B role")
    if h1.version != h2.version or h1.formula_id != h2.formula_id:
        first.close()
        second.close()
        raise ValueError("the two shares do not agree on V2 format/formula")
    if h1.role == 0:
        return first, second, raw_first, raw_second, h1
    return second, first, raw_second, raw_first, h2


def _decode_control(
    raw_a: bytes,
    raw_b: bytes,
    width: int,
    height: int,
    public_a: header.PublicHeader,
    password: str,
) -> tuple[header.ControlBlock, bytes, bytes, list[int], list[int], set[int]]:
    context = _bootstrap_context(width, height, public_a.formula_id, raw_a)
    context_nonce = _context_nonce(context)
    bootstrap_key = kdf.derive_bootstrap_key(password, context)
    _plan, _ca, _cb, pos_a, pos_b = _bootstrap_positions(
        width, height, bootstrap_key, context_nonce, public_a.formula_id
    )
    bits_a = [_read_bit(raw_a, raw_b, p, 0, width=width) for p in pos_a]
    bits_b = [_read_bit(raw_a, raw_b, p, 1, width=width) for p in pos_b]
    control = header.ControlBlock.from_bytes(header.bits_to_bytes(bits_a + bits_b))
    if control.original_length > MAX_ORIGINAL_BYTES:
        raise ValueError("hidden control block declares an unsafe original payload length")
    used = set(pos_a)
    used.update(pos_b)
    return control, bootstrap_key, context_nonce, pos_a, pos_b, used


def extract_bytes(image_a: str | Path, image_b: str | Path, password: str) -> bytes:
    a, b, raw_a, raw_b, public_a = _load_ordered_pair(image_a, image_b)
    try:
        width, height = a.size
        control, bootstrap_key, _context_nonce_value, _control_a, _control_b, used = _decode_control(
            raw_a, raw_b, width, height, public_a, password
        )
        root_key = kdf.derive_root_from_bootstrap(bootstrap_key, control.salt)
        enc_key = kdf.derive_subkey(root_key, kdf.LABEL_ENCRYPTION)
        map_key = kdf.derive_subkey(root_key, kdf.LABEL_MAPPING)
        plan, candidates_a, candidates_b = _payload_plan(
            width, height, map_key, control, public_a.formula_id
        )

        total_bits = control.ciphertext_length * 8
        if total_bits % 2:
            raise ValueError("invalid ciphertext length in hidden control block")
        half = total_bits // 2
        pos_a, pos_b = _payload_positions(
            map_key, control, plan, candidates_a, candidates_b, half, used
        )
        bits_a = [_read_bit(raw_a, raw_b, p, 0, width=width) for p in pos_a]
        bits_b = [_read_bit(raw_a, raw_b, p, 1, width=width) for p in pos_b]
        ciphertext = header.bits_to_bytes(bits_a + bits_b)
        plaintext = cipher.decrypt(
            ciphertext,
            enc_key,
            control.nonce,
            header.associated_data(public_a, control),
        )
        return _decompress_payload(plaintext, control.compressed, control.original_length)
    finally:
        a.close()
        b.close()


def extract(image_a: str | Path, image_b: str | Path, password: str) -> str:
    try:
        return extract_bytes(image_a, image_b, password).decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("payload is authenticated but is not valid UTF-8; use extract_bytes()") from exc


def reconstruct_cover(
    image_a: str | Path,
    image_b: str | Path,
    password: str,
    output: str | Path | None = None,
) -> Image.Image:
    a, b, raw_a, raw_b, public_a = _load_ordered_pair(image_a, image_b)
    try:
        width, height = a.size
        control, bootstrap_key, _cn, control_pos_a, control_pos_b, _used = _decode_control(
            raw_a, raw_b, width, height, public_a, password
        )
        # Authenticate before trusting the hidden geometry.
        extract_bytes(image_a, image_b, password)

        root_key = kdf.derive_root_from_bootstrap(bootstrap_key, control.salt)
        map_key = kdf.derive_subkey(root_key, kdf.LABEL_MAPPING)
        plan, _ca, _cb = _payload_plan(width, height, map_key, control, public_a.formula_id)

        control_owner: dict[int, int] = {p: 0 for p in control_pos_a}
        control_owner.update({p: 1 for p in control_pos_b})

        rebuilt = bytearray(header.canonicalize_row0(raw_a, width))
        for y in range(1, height):
            for x in range(width):
                pixel = y * width + x
                start = pixel * 3
                if pixel in control_owner:
                    owner = control_owner[pixel]
                else:
                    owner = mapping.owner_for_pixel(x, y, width, height, plan)
                source = raw_b if owner == 0 else raw_a
                rebuilt.extend(source[start : start + 3])

        image = Image.frombytes("RGB", (width, height), bytes(rebuilt))
        if output is not None:
            _require_png(output)
            Path(output).parent.mkdir(parents=True, exist_ok=True)
            image.save(output, format="PNG", optimize=True)
        return image
    finally:
        a.close()
        b.close()


def rotate_password(
    image_a: str | Path,
    image_b: str | Path,
    old_password: str,
    new_password: str,
    output_a: str | Path,
    output_b: str | Path,
    *,
    decoy_ratio: float = DEFAULT_DECOY_RATIO,
) -> PairInfo:
    data = extract_bytes(image_a, image_b, old_password)
    cover = reconstruct_cover(image_a, image_b, old_password)
    try:
        return _embed_from_base(cover, data, new_password, output_a, output_b, decoy_ratio)
    finally:
        cover.close()


def inspect_pair(image_a: str | Path, image_b: str | Path, password: str) -> PairInfo:
    a, b, raw_a, raw_b, public_a = _load_ordered_pair(image_a, image_b)
    try:
        width, height = a.size
        control, bootstrap_key, _cn, _control_a, _control_b, _used = _decode_control(
            raw_a, raw_b, width, height, public_a, password
        )
        extract_bytes(image_a, image_b, password)
        root_key = kdf.derive_root_from_bootstrap(bootstrap_key, control.salt)
        map_key = kdf.derive_subkey(root_key, kdf.LABEL_MAPPING)
        plan, _ca, _cb = _payload_plan(width, height, map_key, control, public_a.formula_id)

        different_body_pixels = 0
        for pixel in range(width, width * height):
            start = pixel * 3
            if raw_a[start : start + 3] != raw_b[start : start + 3]:
                different_body_pixels += 1
        real_body = header.CONTROL_BITS + control.ciphertext_length * 8
        decoys = max(0, different_body_pixels - real_body)
        return PairInfo(
            width=width,
            height=height,
            format_version=public_a.version,
            ciphertext_bytes=control.ciphertext_length,
            original_bytes=control.original_length,
            control_bits_per_share=header.CONTROL_BITS_PER_SHARE,
            real_bits_per_share=control.ciphertext_length * 4,
            decoy_pixels=decoys,
            angle_degrees=plan.angle_degrees,
            share_a_geometric_region=plan.a_region,
            real_formula_id=plan.real_formula_id,
            real_formula=plan.real_formula_name,
            decoy_formula_ids=plan.decoy_formula_ids,
            decoy_formulas=plan.decoy_formula_names,
            compressed=control.compressed,
        )
    finally:
        a.close()
        b.close()


def pair_stats(image_a: str | Path, image_b: str | Path) -> dict[str, float | int | str]:
    a, b, raw_a, raw_b, public_a = _load_ordered_pair(image_a, image_b)
    try:
        width, height = a.size
        differing_channels = 0
        differing_pixels = 0
        max_delta = 0
        multi_channel_pixels = 0
        header_differing_channels = 0
        header_differing_pixels = 0
        header_multi_channel_pixels = 0
        body_differing_channels = 0
        body_differing_pixels = 0
        body_multi_channel_pixels = 0
        channel_changes = [0, 0, 0]
        for pixel in range(width * height):
            start = pixel * 3
            deltas = [abs(raw_a[start + c] - raw_b[start + c]) for c in range(3)]
            count = sum(1 for d in deltas if d)
            for channel, delta in enumerate(deltas):
                if delta:
                    channel_changes[channel] += 1
            if count:
                differing_pixels += 1
            if count > 1:
                multi_channel_pixels += 1
            differing_channels += count
            max_delta = max(max_delta, *deltas)
            if pixel < width:
                header_differing_channels += count
                header_differing_pixels += int(count > 0)
                header_multi_channel_pixels += int(count > 1)
            else:
                body_differing_channels += count
                body_differing_pixels += int(count > 0)
                body_multi_channel_pixels += int(count > 1)
        return {
            "width": width,
            "height": height,
            "format_version": public_a.version,
            "formula_id": public_a.formula_id,
            "formula": mapping.FORMULA_NAMES[public_a.formula_id],
            "differing_pixels": differing_pixels,
            "differing_channels": differing_channels,
            "multi_channel_pixels": multi_channel_pixels,
            "max_channel_delta": max_delta,
            "fraction_pixels_different": differing_pixels / (width * height),
            "header_differing_pixels": header_differing_pixels,
            "header_differing_channels": header_differing_channels,
            "header_multi_channel_pixels": header_multi_channel_pixels,
            "body_differing_pixels": body_differing_pixels,
            "body_differing_channels": body_differing_channels,
            "body_multi_channel_pixels": body_multi_channel_pixels,
            "red_channel_changes": channel_changes[0],
            "green_channel_changes": channel_changes[1],
            "blue_channel_changes": channel_changes[2],
        }
    finally:
        a.close()
        b.close()
