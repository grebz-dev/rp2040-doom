# SPDX-License-Identifier: BSD-3-Clause
"""Independent sprite compositing checks for the proposed native UI."""

import numpy as np
import pytest

from fcpico import nes_palette, protocol, stream
from ppu_decode import PpuDecodeError, decode_with_sprites


def frame(pixels=None):
    if pixels is None:
        pixels = np.zeros((240, 256), dtype=np.uint8)
    return bytes(stream.encode_frame(pixels, bytes(protocol.FC_COM_BUF_SIZE_V1)))


def color(index):
    return nes_palette.NES_PALETTE_RGB_ARRAY[index].tolist()


def oam_entry(oam, slot, y, tile, flags, x):
    oam[slot * 4:slot * 4 + 4] = bytes((y, tile, flags, x))


def fixtures():
    oam = bytearray([0xFF, 0, 0, 0] * 64)
    chr_data = bytearray(4096)
    sprite_pal = bytearray(16)
    sprite_pal[1] = 0x16
    sprite_pal[5] = 0x27
    return oam, chr_data, sprite_pal


def test_y_offset_transparency_and_palette_alias():
    oam, chr_data, sprite_pal = fixtures()
    oam_entry(oam, 0, 9, 0, 1, 20)
    chr_data[0] = 0x80  # left pixel only, colour 1
    sprite_pal[4] = 0x30  # transparent entry must have no effect
    rgb = decode_with_sprites(frame(), oam, chr_data, sprite_pal)
    assert rgb[9, 20].tolist() == color(0x0F)
    assert rgb[10, 20].tolist() == color(0x27)
    assert rgb[10, 21].tolist() == color(0x0F)


def test_oam_order_and_priority_behind_background():
    pixels = np.zeros((240, 256), dtype=np.uint8)
    pixels[10, 20] = 1
    oam, chr_data, sprite_pal = fixtures()
    chr_data[0] = 0x80
    oam_entry(oam, 0, 9, 0, 0x20, 20)  # behind opaque BG
    oam_entry(oam, 1, 9, 0, 0, 20)  # cannot replace first sprite
    rgb = decode_with_sprites(frame(pixels), oam, chr_data, sprite_pal)
    assert rgb[10, 20].tolist() == color(0x00)
    pixels[10, 20] = 0
    rgb = decode_with_sprites(frame(pixels), oam, chr_data, sprite_pal)
    assert rgb[10, 20].tolist() == color(0x16)


def test_flips_and_eighth_sprite_limit():
    oam, chr_data, sprite_pal = fixtures()
    chr_data[7] = 0x80  # bottom left pixel
    for slot in range(9):
        oam_entry(oam, slot, 9, 0, 0xC0, slot * 8)
    rgb = decode_with_sprites(frame(), oam, chr_data, sprite_pal)
    for slot in range(8):
        assert rgb[10, slot * 8 + 7].tolist() == color(0x16)
    assert rgb[10, 71].tolist() == color(0x0F)


@pytest.mark.parametrize("field,size", [("oam", 255), ("chr_data", 4095), ("sprite_pal", 15)])
def test_rejects_incomplete_sprite_data(field, size):
    oam, chr_data, sprite_pal = fixtures()
    values = {"oam": oam, "chr_data": chr_data, "sprite_pal": sprite_pal}
    values[field] = bytes(size)
    with pytest.raises(PpuDecodeError):
        decode_with_sprites(frame(), values["oam"], values["chr_data"], values["sprite_pal"])
