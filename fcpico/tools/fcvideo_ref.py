#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-3-Clause
"""fcvideo_ref.py -- reference implementation of fcvideo's stages B-E.

A pure-Python, readable-over-fast twin of the device-side video pipeline, used
by the unit tests as the ground truth ``tests/fcvideo/`` checks the C code
against. Covers:

  B. :func:`decimate_320_to_256` -- horizontal nearest-neighbour decimation.
  .  :func:`place_letterbox` -- vertical placement into the 240-line frame.
  C. :func:`choose_block_palettes` -- per-16x16-block sub-palette choice
     with hysteresis.
  D. :func:`quantize` -- ordered-dither quantisation to 2 bits/pixel.
  E. :func:`build_err_and_lut` -- the per-(sub-palette, Doom colour) search
     over dither pairs that stages C and D both consume, so they agree by
     construction.

:func:`convert` chains all of the above (plus attribute/palette packing into
a v2 mailbox) into one call: an 8-bit 320x200 Doom frame in, a stream buffer
out. Nothing here talks to hardware or depends on the engine; stages B-E are
pure functions over byte arrays.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from fcpico import nes_palette, protocol, stream  # noqa: E402

__all__ = [
    "SRC_X",
    "BAYER4X4",
    "BAYER_FLAT",
    "decimate_320_to_256",
    "place_letterbox",
    "build_err_and_lut",
    "choose_block_palettes",
    "quantize",
    "convert",
]

#: Stage B's whole implementation: the source column (0..319) kept for each
#: of the 256 destination columns (nearest-neighbour, drop every 5th column).
SRC_X: tuple[int, ...] = tuple(x for x in range(320) if x % 5 != 4)
assert len(SRC_X) == 256

#: The standard order-4 Bayer ordered-dither matrix (threshold 0..15).
BAYER4X4: tuple[tuple[int, int, int, int], ...] = (
    (0, 8, 2, 10),
    (12, 4, 14, 6),
    (3, 11, 1, 9),
    (15, 7, 13, 5),
)
#: :data:`BAYER4X4` flattened row-major, indexed by ``(x & 3) | ((y & 3) << 2)``.
BAYER_FLAT: tuple[int, ...] = tuple(v for row in BAYER4X4 for v in row)

DOOM_HEIGHT = 200
SCALED_HEIGHT = 224
LETTERBOX_TOP = 8  # preserve one blank tile row on each side
LETTERBOX_BOTTOM = stream.VRAM_LINES - LETTERBOX_TOP - SCALED_HEIGHT

N_BLOCK_X = stream.VRAM_WIDTH // 16  # 16
N_BLOCK_Y = stream.VRAM_LINES // 16  # 15


def decimate_320_to_256(line320) -> np.ndarray:
    """Stage B, horizontal: nearest-neighbour 320 -> 256 columns.

    ``line320`` is indexed ``[..., x]`` for ``x`` in 0..319 (a single line,
    or a stack of lines/planes sharing the last axis); returns the same
    shape with the last axis replaced by :data:`SRC_X`'s 256 columns.
    """
    arr = np.asarray(line320)
    if arr.shape[-1] != 320:
        raise ValueError(f"expected width 320 on the last axis, got {arr.shape[-1]}")
    return arr[..., list(SRC_X)]


def place_letterbox(frame_200x256, backdrop: int = 0) -> np.ndarray:
    """Stage B, vertical: scale a 200x256 Doom frame to 224 lines.

    Console lines 0..7 and 232..239 are filled with ``backdrop``.
    Source row for scaled row d is floor(d * 200 / 224).
    """
    frame = np.asarray(frame_200x256)
    if frame.shape[0] != DOOM_HEIGHT:
        raise ValueError(f"expected {DOOM_HEIGHT} source lines, got {frame.shape[0]}")
    out = np.full((stream.VRAM_LINES,) + frame.shape[1:], backdrop, dtype=frame.dtype)
    src_y = np.arange(SCALED_HEIGHT) * DOOM_HEIGHT // SCALED_HEIGHT
    out[LETTERBOX_TOP : LETTERBOX_TOP + SCALED_HEIGHT] = frame[src_y]
    return out


def build_err_and_lut(playpal_rgb, subpalettes, backdrop: int, shadow_lift: bool = False):
    """Stage E: the dither-pair search shared by stage C's cost and stage D's LUT.

    ``playpal_rgb``: ``(256, 3)`` Doom ``PLAYPAL`` colours, 0..255 per
    channel. ``subpalettes``: 4 sequences of 3 NES palette indices each (the
    sub-palette's entries 1..3; entry 0 is always ``backdrop``, an NES
    palette index shared by all four sub-palettes).

    For every sub-palette ``p`` and Doom colour ``idx``, searches all 4x4
    ordered pairs ``(a, b)`` of that sub-palette's 4 entries (0 = backdrop,
    1..3 = ``subpalettes[p]``) and all 17 mix ratios ``r`` (``r/16``, 0..16)
    for the pair+ratio whose linear RGB blend is closest to
    ``playpal_rgb[idx]``. The optional ``shadow_lift`` applies the device's
    integer shadow curve to PLAYPAL before the search.

    Returns ``(err, lut)``:

    - ``err``: ``uint8[4][256]``, the winning blend's residual distance
      (per-channel RMS, clipped to 0..255) -- stage C sums this over a
      block's pixels to cost a candidate sub-palette.
    - ``lut``: ``uint8[4][256][16]``, the winning ``(a, b)`` resolved through
      the 4x4 Bayer matrix at each of its 16 flattened positions (``b`` where
      ``BAYER_FLAT[t] < r``, else ``a``) -- stage D looks this up directly
      with the pixel's Bayer position to get its 2-bit sub-palette-entry
      value.
    """
    playpal_rgb = np.asarray(playpal_rgb, dtype=np.float64)
    if playpal_rgb.shape != (256, 3):
        raise ValueError(f"playpal_rgb must have shape (256, 3), got {playpal_rgb.shape}")
    if shadow_lift:
        # Small integer shadow lift, identical to the device table builder.
        values = playpal_rgb.astype(np.int32)
        playpal_rgb = values + (values * (255 - values) + 384) // 768
    if len(subpalettes) != 4:
        raise ValueError(f"subpalettes must have 4 entries, got {len(subpalettes)}")

    ratios = np.arange(17, dtype=np.float64) / 16.0  # r/16 for r = 0..16
    bayer = np.asarray(BAYER_FLAT, dtype=np.int64)  # (16,)

    err = np.zeros((4, 256), dtype=np.uint8)
    lut = np.zeros((4, 256, 16), dtype=np.uint8)

    for p, entries3 in enumerate(subpalettes):
        entries3 = list(entries3)
        if len(entries3) != 3:
            raise ValueError(f"subpalettes[{p}] must list 3 NES indices, got {len(entries3)}")
        rgb4 = nes_palette.NES_PALETTE_RGB_ARRAY[[backdrop, *entries3]].astype(np.float64)  # (4, 3)

        # mixed[a, b, r, channel] = rgb4[a] * (1 - r/16) + rgb4[b] * (r/16)
        mixed = (
            rgb4[:, None, None, :] * (1.0 - ratios)[None, None, :, None]
            + rgb4[None, :, None, :] * ratios[None, None, :, None]
        )  # (4, 4, 17, 3)
        diff = mixed[:, :, :, None, :] - playpal_rgb[None, None, None, :, :]  # (4, 4, 17, 256, 3)
        dist2 = np.einsum("abric,abric->abri", diff, diff)  # (4, 4, 17, 256)

        flat = dist2.reshape(4 * 4 * 17, 256)
        best_flat = np.argmin(flat, axis=0)  # (256,): index of the winning (a, b, r) per idx
        best_dist2 = flat[best_flat, np.arange(256)]
        a_idx, b_idx, r_idx = np.unravel_index(best_flat, (4, 4, 17))

        err[p] = np.clip(np.round(np.sqrt(best_dist2 / 3.0)), 0, 255).astype(np.uint8)

        use_b = bayer[None, :] < r_idx[:, None]  # (256, 16)
        lut[p] = np.where(use_b, b_idx[:, None], a_idx[:, None]).astype(np.uint8)

    return err, lut


def choose_block_palettes(frame_idx, err, prev_attr=None, hyst_pct: float = 12.0) -> bytearray:
    """Stage C: pick a sub-palette per 16x16 block, with hysteresis.

    ``frame_idx`` is a ``(240, 256)`` array of ``PLAYPAL`` indices (the full
    letterboxed console frame -- unlike a device-side shortcut of
    only costing the 200-line Doom band, this reference costs all 16x15
    blocks uniformly, which is simpler and gives the same answer since a
    block that is pure backdrop costs the same under every sub-palette).
    ``err`` is stage E's ``uint8[4][256]``.

    ``prev_attr`` is the previous frame's 64-byte attribute table (``None``
    for the first frame, which then has no hysteresis to apply). A block
    keeps its previous sub-palette unless another one costs less by more
    than ``hyst_pct`` percent.

    Returns a new 64-byte attribute table (packed via ``stream.attr_set``,
    so byte-identical to what ``rp_system::setAtr()`` would produce for the
    same choices).
    """
    frame_idx = np.asarray(frame_idx)
    if frame_idx.shape != (stream.VRAM_LINES, stream.VRAM_WIDTH):
        raise ValueError(
            f"frame_idx must have shape ({stream.VRAM_LINES}, {stream.VRAM_WIDTH}), got {frame_idx.shape}"
        )
    err = np.asarray(err)
    if err.shape != (4, 256):
        raise ValueError(f"err must have shape (4, 256), got {err.shape}")

    prev = None
    if prev_attr is not None:
        prev = bytes(prev_attr)
        if len(prev) != protocol.MBX_ATTR_LEN:
            raise ValueError(f"prev_attr must be {protocol.MBX_ATTR_LEN} bytes, got {len(prev)}")

    new_attr = bytearray(protocol.MBX_ATTR_LEN)
    for by in range(N_BLOCK_Y):
        y0 = by * 16
        for bx in range(N_BLOCK_X):
            x0 = bx * 16
            block = frame_idx[y0 : y0 + 16, x0 : x0 + 16]
            costs = err[:, block].astype(np.int64).sum(axis=(1, 2))  # (4,), one sum per sub-palette
            best_p = int(np.argmin(costs))

            if prev is not None:
                old_p = stream.attr_get(prev, x0, y0)
                if best_p != old_p:
                    threshold = costs[old_p] * (1.0 - hyst_pct / 100.0)
                    if not (costs[best_p] < threshold):
                        best_p = old_p  # not cheaper by more than hyst_pct%: keep the old choice

            stream.attr_set(new_attr, bx, by, best_p)
    return new_attr


def quantize(frame_idx, lut, attr64) -> np.ndarray:
    """Stage D: ordered-dither quantisation to a 240x256 array of 2-bit values.

    ``frame_idx``: ``(240, 256)`` ``PLAYPAL`` indices. ``lut``: stage E's
    ``uint8[4][256][16]``. ``attr64``: the 64-byte attribute table (stage
    C's output) selecting each pixel's sub-palette. Each pixel looks up
    ``lut[sub_palette][frame_idx][bayer_position]`` where
    ``bayer_position = (x & 3) | ((y & 3) << 2)``. The result is what :func:`fcpico.stream.encode_frame` wants
    as its ``pix256x240`` argument.
    """
    frame_idx = np.asarray(frame_idx)
    if frame_idx.shape != (stream.VRAM_LINES, stream.VRAM_WIDTH):
        raise ValueError(
            f"frame_idx must have shape ({stream.VRAM_LINES}, {stream.VRAM_WIDTH}), got {frame_idx.shape}"
        )
    lut = np.asarray(lut)
    if lut.shape != (4, 256, 16):
        raise ValueError(f"lut must have shape (4, 256, 16), got {lut.shape}")
    attr_arr = np.frombuffer(bytes(attr64), dtype=np.uint8)
    if attr_arr.size != protocol.MBX_ATTR_LEN:
        raise ValueError(f"attr64 must be {protocol.MBX_ATTR_LEN} bytes, got {attr_arr.size}")

    ys, xs = np.indices(frame_idx.shape)
    tx, ty = xs >> 3, ys >> 3
    sub_pal = (attr_arr[stream.attr_index(tx, ty)] >> stream.attr_shift(tx, ty)) & 0x3  # (240, 256)
    bayer_pos = (xs & 3) | ((ys & 3) << 2)  # (240, 256)

    pix = lut[sub_pal, frame_idx.astype(np.int64), bayer_pos]
    return pix.astype(np.uint8)


def convert(frame320x200_idx, playpal, preset):
    """Run stages B-E and pack the result into a v2 stream buffer.

    ``frame320x200_idx``: ``(200, 320)`` ``PLAYPAL`` indices (a composed
    Doom frame, stage A's output). ``playpal``: ``(256, 3)`` RGB. ``preset``:
    an object (or mapping) with ``subpalettes`` (4 sequences of 3 NES
    indices), ``backdrop`` (one NES index), and optional ``shadow_lift`` --
    see ``FCVIDEO_PRESET_*`` in port/video/fcvideo.h.

    Returns ``(stream_bytes, attr64, pal16, pix)``:

    - ``stream_bytes``: a 17,408-byte v2 buffer with the attribute table,
      palette and validity flags already written into its mailbox
      (``MBX_FLAG_ATTR_VALID | MBX_FLAG_PAL_VALID | MBX_FLAG_V2``).
    - ``attr64``: the 64-byte attribute table chosen (no hysteresis --
      there is no previous frame here; call :func:`choose_block_palettes`
      directly for a multi-frame sequence).
    - ``pal16``: the 16-byte BG palette (backdrop + 3 sub-palettes x 4,
      with the mirrored-backdrop layout).
    - ``pix``: the ``(240, 256)`` 2-bit pixel array passed to
      :func:`fcpico.stream.encode_frame`.
    """
    frame320x200_idx = np.asarray(frame320x200_idx)
    if frame320x200_idx.shape != (DOOM_HEIGHT, 320):
        raise ValueError(
            f"frame320x200_idx must have shape ({DOOM_HEIGHT}, 320), got {frame320x200_idx.shape}"
        )

    subpalettes = preset["subpalettes"] if isinstance(preset, dict) else preset.subpalettes
    backdrop = preset["backdrop"] if isinstance(preset, dict) else preset.backdrop

    frame256 = decimate_320_to_256(frame320x200_idx)
    frame_idx = place_letterbox(frame256, backdrop=0)  # PLAYPAL index 0 is Doom's own backdrop/black

    shadow_lift = (preset.get("shadow_lift", False) if isinstance(preset, dict)
                   else getattr(preset, "shadow_lift", False))
    err, lut = build_err_and_lut(playpal, subpalettes, backdrop, shadow_lift)
    attr64 = choose_block_palettes(frame_idx, err, prev_attr=None)
    pix = quantize(frame_idx, lut, attr64)
    pix[:LETTERBOX_TOP] = 0
    pix[stream.VRAM_LINES - LETTERBOX_BOTTOM:] = 0

    pal16 = bytearray(protocol.MBX_PAL_LEN)
    pal16[0] = backdrop & 0xFF
    for p, entries3 in enumerate(subpalettes):
        for i, nes_idx in enumerate(entries3, start=1):
            pal16[p * 4 + i] = nes_idx & 0xFF
        pal16[p * 4] = backdrop & 0xFF  # entries 4, 8, 12: mirrored backdrops

    mailbox = bytearray(protocol.FC_COM_BUF_SIZE_V2)
    mailbox[protocol.MBX_MAGIC] = protocol.PF_MAGIC_NO
    mailbox[protocol.MBX_PAL : protocol.MBX_PAL + protocol.MBX_PAL_LEN] = pal16
    mailbox[protocol.MBX_ATTR : protocol.MBX_ATTR + protocol.MBX_ATTR_LEN] = attr64
    mailbox[protocol.MBX_FLAGS] = (
        protocol.MBX_FLAG_V2 | protocol.MBX_FLAG_ATTR_VALID | protocol.MBX_FLAG_PAL_VALID
    )

    stream_bytes = stream.encode_frame(pix, bytes(mailbox))
    return stream_bytes, attr64, bytes(pal16), pix
