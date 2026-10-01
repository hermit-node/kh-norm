from __future__ import annotations

import hashlib
import hmac
import os

SALT_SIZE = 16
KEY_SIZE = 32
SCRYPT_N = 1 << 14
SCRYPT_R = 8
SCRYPT_P = 1

LABEL_ENCRYPTION = b"stegosplit-v2:encryption"
LABEL_MAPPING = b"stegosplit-v2:mapping"
LABEL_ROOT = b"stegosplit-v2:root"


def generate_salt() -> bytes:
    return os.urandom(SALT_SIZE)


def _require_password(password: str) -> bytes:
    if not isinstance(password, str) or not password:
        raise ValueError("password must be a non-empty string")
    return password.encode("utf-8")


def derive_bootstrap_key(password: str, context: bytes) -> bytes:
    """Expensive password KDF used before hidden per-pair salt is available.

    The salt is derived from public, image-local context rather than stored in row 0.
    Correct decoding still requires the password; no pair identifier is introduced.
    """
    password_bytes = _require_password(password)
    salt = hashlib.sha256(b"stegosplit-v2:bootstrap\x00" + context).digest()[:16]
    return hashlib.scrypt(
        password_bytes,
        salt=salt,
        n=SCRYPT_N,
        r=SCRYPT_R,
        p=SCRYPT_P,
        dklen=KEY_SIZE,
    )


def derive_root_from_bootstrap(bootstrap_key: bytes, salt: bytes) -> bytes:
    if len(bootstrap_key) != KEY_SIZE:
        raise ValueError(f"bootstrap key must be {KEY_SIZE} bytes")
    if len(salt) != SALT_SIZE:
        raise ValueError(f"salt must be {SALT_SIZE} bytes")
    return hmac.new(bootstrap_key, LABEL_ROOT + b"\x00" + salt, hashlib.sha256).digest()


def derive_subkey(root_key: bytes, label: bytes) -> bytes:
    if len(root_key) != KEY_SIZE:
        raise ValueError(f"root key must be {KEY_SIZE} bytes")
    return hmac.new(root_key, label, hashlib.sha256).digest()
