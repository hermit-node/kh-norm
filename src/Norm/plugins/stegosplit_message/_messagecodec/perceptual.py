from __future__ import annotations

import numpy as np

from .perceptual_lut import LUT_BYTES, LUT_SHAPE

ZONE_SHIFT = 5
ZONE_COUNT = 1 << (8 - ZONE_SHIFT)  # 8 bins/channel -> 512 RGB zones.
PERCEPTUAL_CHANNEL_LUT = np.frombuffer(LUT_BYTES, dtype=np.uint8).reshape(LUT_SHAPE)


def perceptual_channel(rgb: np.ndarray, delta: int) -> int:
    """O(1) channel choice from a frozen 512-zone perceptual codebook."""
    if delta not in (-1, 1):
        raise ValueError("delta must be -1 or +1")
    if rgb.shape != (3,):
        raise ValueError("rgb must contain exactly three channels")
    direction_index = 1 if delta > 0 else 0
    rz, gz, bz = (int(v) >> ZONE_SHIFT for v in rgb)
    return int(PERCEPTUAL_CHANNEL_LUT[direction_index, rz, gz, bz])
