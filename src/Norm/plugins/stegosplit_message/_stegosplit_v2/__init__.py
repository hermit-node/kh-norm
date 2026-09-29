"""StegoSplit Message Codec V2 — paired differential image steganography."""

from .core import (
    DEFAULT_DECOY_RATIO,
    MAX_ORIGINAL_BYTES,
    PairInfo,
    embed,
    embed_bytes,
    extract,
    extract_bytes,
    inspect_pair,
    pair_stats,
    reconstruct_cover,
    rotate_password,
)

__version__ = "2.0.0a4"

__all__ = [
    "DEFAULT_DECOY_RATIO",
    "MAX_ORIGINAL_BYTES",
    "PairInfo",
    "embed",
    "embed_bytes",
    "extract",
    "extract_bytes",
    "inspect_pair",
    "pair_stats",
    "reconstruct_cover",
    "rotate_password",
    "__version__",
]
