# SPDX-License-Identifier: BSD-3-Clause
"""1-bit delta ("DPCM") encoder/decoder for NES DMC sample data.

Offline pipeline: resample the source to one of the sixteen NTSC DMC rates by linear
interpolation, scale the unsigned 8-bit source into the 7-bit level range
the delta encoder works in, then run the standard delta encoder (level
starts at 64, step +/-2, clamped to 0..127) and pad the bitstream to the
APU's ``$4013`` length rule (byte count of the form ``16n + 1``).
"""

from __future__ import annotations

import numpy as np

# NTSC DMC rates by $4010 rate index (Hz).
RATE_TABLE_HZ = (
    4181.71,
    4709.93,
    5264.04,
    5593.04,
    6257.95,
    7046.35,
    7919.35,
    8363.42,
    9419.86,
    11186.1,
    12604.0,
    13982.6,
    16884.6,
    21306.8,
    24858.0,
    33143.9,
)

START_LEVEL = 64
LEVEL_MIN = 0
LEVEL_MAX = 127
LEVEL_STEP = 2

_SOURCE_BITS_MAX = 255  # unsigned 8-bit source full scale
_TARGET_BITS_MAX = 127  # 7-bit target full scale

_DMC_LENGTH_BLOCK = 16  # $4013 encodes (len - 1) >> 4: valid lengths are 16n + 1


def _rate_hz(rate_index: int) -> float:
    if not (0 <= rate_index < len(RATE_TABLE_HZ)):
        raise ValueError(
            f"rate_index {rate_index} out of range 0..{len(RATE_TABLE_HZ) - 1}"
        )
    return RATE_TABLE_HZ[rate_index]


def resample_linear(samples: np.ndarray, src_rate: float, dst_rate: float) -> np.ndarray:
    """Linearly resample a 1-D array from ``src_rate`` to ``dst_rate`` Hz.

    The output length is ``round(len(samples) * dst_rate / src_rate)``
    (minimum 1 for non-empty input); values beyond the source's edges are
    clamped to the first/last source sample, matching ``numpy.interp``'s
    default behaviour. Returns a ``float64`` array in the same amplitude
    domain as the input.
    """
    samples = np.asarray(samples, dtype=np.float64)
    n_src = len(samples)
    if n_src == 0:
        return np.zeros(0, dtype=np.float64)
    if src_rate <= 0 or dst_rate <= 0:
        raise ValueError("sample rates must be positive")
    n_dst = max(1, int(round(n_src * dst_rate / src_rate)))
    src_index = np.arange(n_dst, dtype=np.float64) * (src_rate / dst_rate)
    return np.interp(src_index, np.arange(n_src, dtype=np.float64), samples)


def to_target_levels(samples_u8: np.ndarray, src_rate: float, dst_rate: float) -> np.ndarray:
    """Resample ``samples_u8`` to ``dst_rate`` and scale 0..255 to 0..127.

    This is the delta encoder's target curve -- the same array
    :func:`encode_dpcm` feeds bit-by-bit into the level tracker, and the
    reference to compare a decoded stream against (see :func:`rms_error`).
    Returned as ``int64``, rounded and clamped to 0..127.
    """
    resampled = resample_linear(samples_u8, src_rate, dst_rate)
    scaled = resampled * (_TARGET_BITS_MAX / _SOURCE_BITS_MAX)
    return np.clip(np.round(scaled), LEVEL_MIN, LEVEL_MAX).astype(np.int64)


def _step_level(level: int, bit: int) -> int:
    if bit:
        return min(LEVEL_MAX, level + LEVEL_STEP)
    return max(LEVEL_MIN, level - LEVEL_STEP)


def encode_dpcm(samples_u8: np.ndarray, src_rate: int, rate_index: int) -> bytes:
    """Encode unsigned 8-bit ``samples_u8`` (any input rate) to DMC bytes.

    Steps: resample by linear interpolation to ``RATE_TABLE_HZ[rate_index]``
    and scale to the 7-bit target range (:func:`to_target_levels`); run the
    standard 1-bit delta encoder (level starts at 64; per sample, emit 1 and
    ``level = min(127, level + 2)`` if the target is greater than the
    current level, else emit 0 and ``level = max(0, level - 2)``); pack bits
    LSB-first into bytes; pad with alternating 1/0 bits (which move the
    level by only +/-2 each and so keep it steady rather than drifting or
    slamming into a rail) until the byte count is of the form ``16n + 1``,
    the form the APU's ``$4013`` length register requires.
    """
    dst_rate = _rate_hz(rate_index)
    levels = to_target_levels(samples_u8, src_rate, dst_rate)

    bits: list[int] = []
    level = START_LEVEL
    for target in levels.tolist():
        bit = 1 if target > level else 0
        bits.append(bit)
        level = _step_level(level, bit)

    # Pad, alternating 1/0, first to a whole byte and then in whole bytes
    # until the total satisfies the 16n+1 rule (this also produces the
    # correct single minimal byte for empty input).
    next_bit = 1
    while len(bits) % 8 != 0:
        bits.append(next_bit)
        level = _step_level(level, next_bit)
        next_bit ^= 1
    n_bytes = len(bits) // 8
    while n_bytes % _DMC_LENGTH_BLOCK != 1:
        for _ in range(8):
            bits.append(next_bit)
            level = _step_level(level, next_bit)
            next_bit ^= 1
        n_bytes += 1

    data = bytearray(n_bytes)
    for i, bit in enumerate(bits):
        if bit:
            data[i // 8] |= 1 << (i % 8)
    return bytes(data)


def decode_dpcm(data: bytes, start_level: int = START_LEVEL) -> np.ndarray:
    """Decode DMC ``data`` back to a 7-bit level curve, for verification.

    Bits are read LSB-first within each byte (mirroring
    :func:`encode_dpcm`'s packing); each bit updates ``level`` by the same
    +/-2 clamped-to-0..127 rule the encoder used, and the level *after*
    that update is the reconstructed sample -- so ``decode_dpcm(data)[i]``
    approximates whatever target value produced bit ``i``. Returns an
    ``int64`` array of length ``8 * len(data)``.
    """
    out = np.empty(8 * len(data), dtype=np.int64)
    level = start_level
    i = 0
    for byte in data:
        for bit_pos in range(8):
            bit = (byte >> bit_pos) & 1
            level = _step_level(level, bit)
            out[i] = level
            i += 1
    return out


def rms_error(src: np.ndarray, decoded: np.ndarray) -> float:
    """RMS difference between ``src`` and ``decoded`` over their overlap.

    Both are expected in the same domain (typically the 7-bit level range
    :func:`to_target_levels` and :func:`decode_dpcm` share). ``decoded`` is
    usually longer than ``src`` because of the encoder's length-rule
    padding; only the first ``min(len(src), len(decoded))`` samples of each
    are compared. Returns 0.0 for an empty overlap.
    """
    src = np.asarray(src, dtype=np.float64)
    decoded = np.asarray(decoded, dtype=np.float64)
    n = min(len(src), len(decoded))
    if n == 0:
        return 0.0
    diff = src[:n] - decoded[:n]
    return float(np.sqrt(np.mean(diff * diff)))
