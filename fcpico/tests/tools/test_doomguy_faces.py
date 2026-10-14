# SPDX-License-Identifier: BSD-3-Clause
"""NES face-sheet geometry and WHX row decoding without a bundled game WAD."""

from __future__ import annotations

import numpy as np

from build_doomguy_faces import nes_tiles, patch_pixels, resize_face


def test_vp6_face_row_decodes_opaque_and_transparent_pixels():
    # Three palette indices in one six-bit run, one transparent-pixel gap.
    header = bytes((4, 1, 3, 3 << 2, 0, 0, 10, 20, 30))
    packed = 0 | (1 << 6) | (2 << 12)
    data = bytes((1, 3)) + packed.to_bytes(3, "little")
    pixels = patch_pixels(header + data)
    assert pixels.tolist() == [[-1, 10, 20, 30]]


def test_24_pixel_face_is_nine_row_major_tiles():
    original = np.zeros((29, 24), dtype=np.int16)
    original[:, :8] = 1
    original[:, 8:16] = 2
    original[:, 16:] = 3
    face = resize_face(original)
    assert face.shape == (24, 24)
    art = nes_tiles(face)
    assert len(art) == 9 * 16
    # First three tiles are color 1, 2, 3 respectively.
    assert art[:16] == bytes((0xFF,)) * 8 + bytes(8)
    assert art[16:32] == bytes(8) + bytes((0xFF,)) * 8
    assert art[32:48] == bytes((0xFF,)) * 16
