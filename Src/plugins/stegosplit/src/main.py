"""Public Norm plugin adapter: native implementation is compiled for Windows CPython 3.14.
For user help see ../help.txt; for Norm functionality see ../README.md.
"""
from __future__ import annotations
from typing import Any
from pathlib import Path
import message_api as _native
PLUGIN_VERSION='0.5.0-norm-adapter'

def default_prescription() -> dict:
    return _native.default_prescription()

def probe_pair(share_1: str, share_2: str) -> dict:
    return _native.probe_pair(share_1, share_2)

def compress_text(message: str, mode: str='auto') -> dict:
    return _native.compress_text(message, mode)

def compress_base64(message_base64: str, mode: str='auto') -> dict:
    return _native.compress_base64(message_base64, mode)

def embed_text(source_image: str, share_a: str, share_b: str, password: str, message: str, decoy_ratio: float=1.0, compression_mode: str='auto', formula_id: int | None=None, public_seed32: int | None=None, angle_degrees: float | None=None) -> dict:
    return _native.embed_text(source_image, share_a, share_b, password, message, decoy_ratio, compression_mode, formula_id, public_seed32, angle_degrees)

def embed_base64(source_image: str, share_a: str, share_b: str, password: str, message_base64: str, decoy_ratio: float=1.0, compression_mode: str='auto', formula_id: int | None=None, public_seed32: int | None=None, angle_degrees: float | None=None) -> dict:
    return _native.embed_base64(source_image, share_a, share_b, password, message_base64, decoy_ratio, compression_mode, formula_id, public_seed32, angle_degrees)

def embed_file(source_image: str, share_a: str, share_b: str, password: str, message_file: str, decoy_ratio: float=1.0, compression_mode: str='auto', formula_id: int | None=None, public_seed32: int | None=None, angle_degrees: float | None=None) -> dict:
    return _native.embed_file(source_image, share_a, share_b, password, message_file, decoy_ratio, compression_mode, formula_id, public_seed32, angle_degrees)

def extract_text(share_a: str, share_b: str, password: str) -> dict:
    return _native.extract_text(share_a, share_b, password)

def extract_base64(share_a: str, share_b: str, password: str) -> dict:
    return _native.extract_base64(share_a, share_b, password)

def extract_file(share_a: str, share_b: str, password: str, output_file: str) -> dict:
    return _native.extract_file(share_a, share_b, password, output_file)

def rotate_pair_password(share_a: str, share_b: str, old_password: str, new_password: str, output_a: str, output_b: str, decoy_ratio: float=1.0, compression_mode: str='auto', formula_id: int | None=None, public_seed32: int | None=None, angle_degrees: float | None=None) -> dict:
    return _native.rotate_pair_password(share_a, share_b, old_password, new_password, output_a, output_b, decoy_ratio, compression_mode, formula_id, public_seed32, angle_degrees)

def rebuild_cover(share_a: str, share_b: str, password: str, output_image: str) -> dict:
    return _native.rebuild_cover(share_a, share_b, password, output_image)

def pair_info(share_a: str, share_b: str, password: str) -> dict:
    return _native.pair_info(share_a, share_b, password)

def pair_stats(share_a: str, share_b: str) -> dict:
    return _native.pair_stats(share_a, share_b)

def create_unlock_pair(source_image: str, share_a: str, share_b: str, password: str, decoy_ratio: float=1.0, compression_mode: str='auto', formula_id: int | None=None, public_seed32: int | None=None, angle_degrees: float | None=None) -> dict:
    return _native.create_unlock_pair(source_image, share_a, share_b, password, decoy_ratio, compression_mode, formula_id, public_seed32, angle_degrees)

def unlock_image_pair(share_a: str, share_b: str, password: str, expected_sha256: str | None=None, reveal_key: bool=False) -> dict:
    return _native.unlock_image_pair(share_a, share_b, password, expected_sha256, reveal_key)

def embed_identity_pair(source_image: str, share_a: str, share_b: str, password: str, identifiers: dict[str, str], decoy_ratio: float=1.0, compression_mode: str='auto', formula_id: int | None=None, public_seed32: int | None=None, angle_degrees: float | None=None) -> dict:
    return _native.embed_identity_pair(source_image, share_a, share_b, password, identifiers, decoy_ratio, compression_mode, formula_id, public_seed32, angle_degrees)

def unlock_identity_pair(share_a: str, share_b: str, password: str, expected_identifiers: dict[str, str] | None=None) -> dict:
    return _native.unlock_identity_pair(share_a, share_b, password, expected_identifiers)

def embed_public_key(source_image: str, share_a: str, share_b: str, password: str, public_key_or_file: str) -> dict:
    return _native.embed_public_key(source_image, share_a, share_b, password, public_key_or_file)

def embed_sha256(source_image: str, share_a: str, share_b: str, password: str, sha256_hex_or_file: str) -> dict:
    return _native.embed_sha256(source_image, share_a, share_b, password, sha256_hex_or_file)

def embed_md5(source_image: str, share_a: str, share_b: str, password: str, md5_hex_or_file: str) -> dict:
    return _native.embed_md5(source_image, share_a, share_b, password, md5_hex_or_file)

def read_legacy_single_image_identity(image: str, method: str='auto') -> dict:
    return _native.read_legacy_single_image_identity(image, method)

def run(payload: dict[str, Any]) -> dict[str, Any]:
    if isinstance(payload, dict) and payload.get('action') == 'generate_base_image':
        return generate_base_image(payload['output_png'], payload['seed'],
                                   int(payload.get('width',512)),
                                   int(payload.get('height',512)),
                                   str(payload.get('object_type','random')))
    return _native.run(payload)


def generate_base_image(output_png: str, seed: str, width: int = 512,
                        height: int = 512, object_type: str = 'random') -> dict:
    """Generate a deterministic everyday-object PNG from large flat polygons (<=16 colors)."""
    from polygon_art import render
    return render(seed, output_png, width, height, object_type)
