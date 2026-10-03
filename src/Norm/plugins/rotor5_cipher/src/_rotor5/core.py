from __future__ import annotations

import hashlib
import hmac
import os
import re
import struct
import zlib
from dataclasses import dataclass

MAGIC = b"R5E2"
VERSION = 2
FLAG_EXTERNAL_SECRET = 0x01
FLAG_COMPRESSED = 0x02
MACHINE_COUNT = 5
ALPHABET_SIZE = 256
MAX_ORIGINAL_BYTES = 64 * 1024 * 1024
ENV_CURRENT_SECRET = "NORM_ROTOR5_SECRET"
ENV_PREVIOUS_SECRETS = "NORM_ROTOR5_PREVIOUS_SECRETS"
HEADER_NO_TAG = ">4sBB16s8sQQ"
HEADER_FORMAT = ">4sBB16s8sQQ16s"
HEADER_SIZE = struct.calcsize(HEADER_FORMAT)


@dataclass(frozen=True)
class EnvelopeInfo:
    version: int
    original_bytes: int
    encoded_bytes: int
    compressed: bool
    external_secret_required: bool

    def as_dict(self) -> dict:
        return {
            "version": self.version,
            "original_bytes": self.original_bytes,
            "encoded_bytes": self.encoded_bytes,
            "compressed": self.compressed,
            "external_secret_required": self.external_secret_required,
        }


@dataclass(frozen=True)
class _Machine:
    table: tuple[bytes, ...]
    start: int
    step: int
    wobble: int


def _secret_tag(secret: bytes) -> bytes:
    if not secret:
        return b"\x00" * 8
    return hashlib.sha256(b"Norm-Rotor5-secret-tag\x00" + secret).digest()[:8]


def _current_secret() -> bytes:
    return os.environ.get(ENV_CURRENT_SECRET, "").encode("utf-8")


def _previous_secrets() -> list[bytes]:
    raw = os.environ.get(ENV_PREVIOUS_SECRETS, "")
    if not raw:
        return []
    return [part.strip().encode("utf-8") for part in re.split(r"[;\r\n]+", raw) if part.strip()]


def _resolve_secret(required: bool, expected_tag: bytes) -> bytes:
    if not required:
        return b""
    candidates: list[bytes] = []
    current = _current_secret()
    if current:
        candidates.append(current)
    for secret in _previous_secrets():
        if secret not in candidates:
            candidates.append(secret)
    if not candidates:
        raise ValueError(f"this envelope requires {ENV_CURRENT_SECRET} or a matching prior secret in {ENV_PREVIOUS_SECRETS}")
    for secret in candidates:
        if hmac.compare_digest(_secret_tag(secret), expected_tag):
            return secret
    raise ValueError("configured Rotor5 live secret does not match this envelope")


def _master_key(password: str, nonce: bytes, external_secret: bytes) -> bytes:
    if not isinstance(password, str) or not password:
        raise ValueError("password must be a non-empty string")
    if len(nonce) != 16:
        raise ValueError("Rotor5 nonce must be 16 bytes")
    password_key = hashlib.sha256(password.encode("utf-8")).digest()
    secret_hash = hashlib.sha256(external_secret).digest()
    return hmac.new(password_key, b"Norm-Rotor5-v2\x00" + nonce + secret_hash, hashlib.sha256).digest()


def _stream(seed: bytes, label: bytes):
    counter = 0
    pool = b""
    while True:
        if len(pool) < 8:
            pool += hmac.new(seed, label + counter.to_bytes(8, "big"), hashlib.sha256).digest()
            counter += 1
        value = int.from_bytes(pool[:8], "big")
        pool = pool[8:]
        yield value


def _permutation(seed: bytes, label: bytes) -> tuple[int, ...]:
    values = list(range(ALPHABET_SIZE))
    rng = _stream(seed, label)
    for i in range(ALPHABET_SIZE - 1, 0, -1):
        j = next(rng) % (i + 1)
        values[i], values[j] = values[j], values[i]
    return tuple(values)


def _inverse(perm: tuple[int, ...]) -> tuple[int, ...]:
    out = [0] * ALPHABET_SIZE
    for i, value in enumerate(perm):
        out[value] = i
    return tuple(out)


def _reflector(seed: bytes, label: bytes) -> tuple[int, ...]:
    shuffled = list(_permutation(seed, label))
    out = [0] * ALPHABET_SIZE
    for i in range(0, ALPHABET_SIZE, 2):
        a, b = shuffled[i], shuffled[i + 1]
        out[a] = b
        out[b] = a
    return tuple(out)


def _machine_table(rotor: tuple[int, ...], inverse: tuple[int, ...], reflector: tuple[int, ...]) -> tuple[bytes, ...]:
    rows: list[bytes] = []
    for pos in range(ALPHABET_SIZE):
        row = bytearray(ALPHABET_SIZE)
        for value in range(ALPHABET_SIZE):
            x = rotor[(value + pos) & 0xFF]
            x = reflector[x]
            x = inverse[x]
            row[value] = (x - pos) & 0xFF
        rows.append(bytes(row))
    return tuple(rows)


def _derive_machines(master: bytes) -> tuple[_Machine, ...]:
    machines: list[_Machine] = []
    for index in range(MACHINE_COUNT):
        label = b"machine-" + bytes([index])
        digest = hmac.new(master, label + b"-settings", hashlib.sha256).digest()
        rotor = _permutation(master, label + b"-rotor")
        inverse = _inverse(rotor)
        reflector = _reflector(master, label + b"-reflector")
        machines.append(_Machine(table=_machine_table(rotor, inverse, reflector), start=digest[0], step=(digest[1] | 1), wobble=(digest[2] | 1)))
    return tuple(machines)


def _transform(data: bytes, master: bytes, *, decrypt: bool) -> bytes:
    m0, m1, m2, m3, m4 = _derive_machines(master)
    p0, p1, p2, p3, p4 = m0.start, m1.start, m2.start, m3.start, m4.start
    out = bytearray(len(data))
    for ordinal, value in enumerate(data):
        if decrypt:
            x = m4.table[p4][value]
            x = m3.table[p3][x]
            x = m2.table[p2][x]
            x = m1.table[p1][x]
            out[ordinal] = m0.table[p0][x]
        else:
            x = m0.table[p0][value]
            x = m1.table[p1][x]
            x = m2.table[p2][x]
            x = m3.table[p3][x]
            out[ordinal] = m4.table[p4][x]
        p0 = (p0 + m0.step) & 0xFF
        p1 = (p1 + m1.step) & 0xFF
        p2 = (p2 + m2.step) & 0xFF
        p3 = (p3 + m3.step) & 0xFF
        p4 = (p4 + m4.step) & 0xFF
        if ((ordinal + 1) & 0xFF) == 0:
            p0 = (p0 + m0.wobble) & 0xFF
            p1 = (p1 + m1.wobble) & 0xFF
            p2 = (p2 + m2.wobble) & 0xFF
            p3 = (p3 + m3.wobble) & 0xFF
            p4 = (p4 + m4.wobble) & 0xFF
    return bytes(out)


def _compress(data: bytes) -> tuple[bytes, bool]:
    packed = zlib.compress(data, 9)
    return (packed, True) if len(packed) < len(data) else (data, False)


def _decompress(data: bytes, expected_length: int) -> bytes:
    if expected_length > MAX_ORIGINAL_BYTES:
        raise ValueError("Rotor5 envelope declares an unsafe original size")
    obj = zlib.decompressobj()
    out = obj.decompress(data, expected_length + 1)
    if len(out) > expected_length or not obj.eof or obj.unused_data or obj.unconsumed_tail:
        raise ValueError("Rotor5 compressed payload is malformed or exceeds its declared size")
    out += obj.flush()
    if len(out) != expected_length:
        raise ValueError("Rotor5 decompressed length does not match envelope")
    return out


def encode_bytes(data: bytes, password: str) -> bytes:
    data = bytes(data)
    if len(data) > MAX_ORIGINAL_BYTES:
        raise ValueError(f"payload exceeds {MAX_ORIGINAL_BYTES} bytes")
    payload, compressed = _compress(data)
    nonce = os.urandom(16)
    external_secret = _current_secret()
    flags = (FLAG_EXTERNAL_SECRET if external_secret else 0) | (FLAG_COMPRESSED if compressed else 0)
    master = _master_key(password, nonce, external_secret)
    encoded = _transform(payload, master, decrypt=False)
    meta = struct.pack(HEADER_NO_TAG, MAGIC, VERSION, flags, nonce, _secret_tag(external_secret), len(data), len(encoded))
    auth_key = hmac.new(master, b"auth", hashlib.sha256).digest()
    tag = hmac.new(auth_key, meta + encoded, hashlib.sha256).digest()[:16]
    return struct.pack(HEADER_FORMAT, MAGIC, VERSION, flags, nonce, _secret_tag(external_secret), len(data), len(encoded), tag) + encoded


def decode_bytes(blob: bytes, password: str) -> bytes:
    blob = bytes(blob)
    if len(blob) < HEADER_SIZE:
        raise ValueError("Rotor5 envelope is truncated")
    magic, version, flags, nonce, secret_tag, original_length, encoded_length, tag = struct.unpack(HEADER_FORMAT, blob[:HEADER_SIZE])
    if magic != MAGIC or version != VERSION:
        raise ValueError("Rotor5 envelope magic/version is invalid")
    if flags & ~(FLAG_EXTERNAL_SECRET | FLAG_COMPRESSED):
        raise ValueError("Rotor5 envelope contains unsupported flags")
    encoded = blob[HEADER_SIZE:]
    if len(encoded) != encoded_length:
        raise ValueError("Rotor5 envelope length is inconsistent")
    external_secret = _resolve_secret(bool(flags & FLAG_EXTERNAL_SECRET), secret_tag)
    master = _master_key(password, nonce, external_secret)
    meta = struct.pack(HEADER_NO_TAG, magic, version, flags, nonce, secret_tag, original_length, encoded_length)
    auth_key = hmac.new(master, b"auth", hashlib.sha256).digest()
    expected = hmac.new(auth_key, meta + encoded, hashlib.sha256).digest()[:16]
    if not hmac.compare_digest(tag, expected):
        raise ValueError("Rotor5 authentication failed (wrong password/secret or damaged data)")
    payload = _transform(encoded, master, decrypt=True)
    if flags & FLAG_COMPRESSED:
        return _decompress(payload, original_length)
    if len(payload) != original_length:
        raise ValueError("Rotor5 decoded length does not match envelope")
    return payload


def inspect_envelope(blob: bytes) -> EnvelopeInfo:
    if len(blob) < HEADER_SIZE:
        raise ValueError("Rotor5 envelope is truncated")
    magic, version, flags, _nonce, _secret_tag_value, original_length, encoded_length, _tag = struct.unpack(HEADER_FORMAT, blob[:HEADER_SIZE])
    if magic != MAGIC or version != VERSION:
        raise ValueError("Rotor5 envelope magic/version is invalid")
    if len(blob) != HEADER_SIZE + encoded_length:
        raise ValueError("Rotor5 envelope length is inconsistent")
    return EnvelopeInfo(version=version, original_bytes=original_length, encoded_bytes=encoded_length, compressed=bool(flags & FLAG_COMPRESSED), external_secret_required=bool(flags & FLAG_EXTERNAL_SECRET))
