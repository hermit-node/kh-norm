from __future__ import annotations

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305


def encrypt(plaintext: bytes, key: bytes, nonce: bytes, aad: bytes) -> bytes:
    if len(key) != 32:
        raise ValueError("ChaCha20-Poly1305 key must be 32 bytes")
    if len(nonce) != 12:
        raise ValueError("ChaCha20-Poly1305 nonce must be 12 bytes")
    return ChaCha20Poly1305(key).encrypt(nonce, plaintext, aad)


def decrypt(ciphertext: bytes, key: bytes, nonce: bytes, aad: bytes) -> bytes:
    if len(key) != 32:
        raise ValueError("ChaCha20-Poly1305 key must be 32 bytes")
    try:
        return ChaCha20Poly1305(key).decrypt(nonce, ciphertext, aad)
    except InvalidTag as exc:
        raise ValueError("wrong password, mismatched pair, or damaged payload") from exc
