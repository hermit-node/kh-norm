"""StegoSplit V3 private engine for the Norm 0.4.0 plugin."""

from .compression import compress_payload, decompress_payload
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
    probe_pair,
    reconstruct_cover,
    rotate_password,
)

__version__ = "3.0.0a1"
plugin_version = "0.4.0"

__all__ = [
    "DEFAULT_DECOY_RATIO",
    "MAX_ORIGINAL_BYTES",
    "PairInfo",
    "compress_payload",
    "decompress_payload",
    "embed",
    "embed_bytes",
    "extract",
    "extract_bytes",
    "inspect_pair",
    "pair_stats",
    "probe_pair",
    "reconstruct_cover",
    "rotate_password",
    "__version__",
    "plugin_version",
]
