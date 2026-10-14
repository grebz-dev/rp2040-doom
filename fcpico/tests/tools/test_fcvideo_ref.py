# SPDX-License-Identifier: BSD-3-Clause
"""Tests for the pure-Python reference video conversion pipeline."""

from __future__ import annotations

import numpy as np

from fcpico import nes_palette, protocol, stream

import fcvideo_ref
import ppu_decode


PRESET = {
    "backdrop": 0x0F,
    "subpalettes": (
        (0x00, 0x10, 0x30),
        (0x06, 0x16, 0x26),
        (0x09, 0x19, 0x29),
        (0x02, 0x12, 0x22),
    ),
}


def test_decimate_drops_every_fifth_column_in_order():
    source = np.arange(2 * 320, dtype=np.uint16).reshape(2, 320)

    result = fcvideo_ref.decimate_320_to_256(source)

    expected_columns = [x for x in range(320) if x % 5 != 4]
    assert result.shape == (2, 256)
    np.testing.assert_array_equal(result, source[:, expected_columns])


def test_vertical_scale_places_source_on_lines_8_through_231():
    source = np.arange(200, dtype=np.uint8)[:, None] * np.ones((1, 256), dtype=np.uint8)

    result = fcvideo_ref.place_letterbox(source, backdrop=231)

    assert result.shape == (240, 256)
    np.testing.assert_array_equal(result[8:232, 0], np.arange(224) * 200 // 224)
    assert np.all(result[:8] == 231)
    assert np.all(result[232:] == 231)


def _playpal_filled(rgb) -> np.ndarray:
    return np.tile(np.asarray(rgb, dtype=np.uint8), (256, 1))


def test_exact_palette_colour_has_zero_error_and_constant_lut():
    entries = PRESET["subpalettes"]
    exact_entry = 2
    exact_nes_index = entries[0][exact_entry - 1]
    rgb = nes_palette.NES_PALETTE_RGB_ARRAY[exact_nes_index]
    playpal = _playpal_filled(rgb)

    err, lut = fcvideo_ref.build_err_and_lut(playpal, entries, PRESET["backdrop"])

    assert err[0, 0] == 0
    np.testing.assert_array_equal(lut[0, 0], np.full(16, exact_entry, dtype=np.uint8))


def test_halfway_colour_alternates_between_palette_entries():
    entries = PRESET["subpalettes"]
    first = nes_palette.NES_PALETTE_RGB_ARRAY[entries[0][0]].astype(np.uint16)
    second = nes_palette.NES_PALETTE_RGB_ARRAY[entries[0][2]].astype(np.uint16)
    midpoint = ((first + second) // 2).astype(np.uint8)
    playpal = _playpal_filled(midpoint)

    _, lut = fcvideo_ref.build_err_and_lut(playpal, entries, PRESET["backdrop"])

    values, counts = np.unique(lut[0, 0], return_counts=True)
    assert set(values) == {1, 3}
    assert sorted(counts.tolist()) == [8, 8]


def test_single_colour_block_selects_a_palette_containing_it():
    frame = np.zeros((stream.VRAM_LINES, stream.VRAM_WIDTH), dtype=np.uint8)
    frame[:16, :16] = 42
    err = np.full((4, 256), 20, dtype=np.uint8)
    err[2, 42] = 0

    attr = fcvideo_ref.choose_block_palettes(frame, err)

    assert stream.attr_get(attr, 0, 0) == 2


def test_palette_hysteresis_keeps_close_challenger_and_accepts_clear_winner():
    frame = np.zeros((stream.VRAM_LINES, stream.VRAM_WIDTH), dtype=np.uint8)
    previous = bytearray(protocol.MBX_ATTR_LEN)
    stream.attr_set(previous, 0, 0, 1)
    err = np.full((4, 256), 200, dtype=np.uint8)
    err[1, 0] = 100
    err[0, 0] = 89

    close = fcvideo_ref.choose_block_palettes(frame, err, previous, hyst_pct=12.0)
    assert stream.attr_get(close, 0, 0) == 1

    err[0, 0] = 87
    clear = fcvideo_ref.choose_block_palettes(frame, err, previous, hyst_pct=12.0)
    assert stream.attr_get(clear, 0, 0) == 0


def test_quantize_uses_row_major_bayer_position():
    frame = np.full((stream.VRAM_LINES, stream.VRAM_WIDTH), 7, dtype=np.uint8)
    lut = np.zeros((4, 256, 16), dtype=np.uint8)
    lut[0, 7] = np.asarray(fcvideo_ref.BAYER_FLAT, dtype=np.uint8) % 4

    result = fcvideo_ref.quantize(frame, lut, bytes(protocol.MBX_ATTR_LEN))

    expected_tile = np.asarray(fcvideo_ref.BAYER_FLAT, dtype=np.uint8).reshape(4, 4) % 4
    np.testing.assert_array_equal(result[:4, :4], expected_tile)
    np.testing.assert_array_equal(result[4:8, 4:8], expected_tile)


def test_gradient_conversion_round_trips_through_ppu_decoder():
    x = np.arange(320, dtype=np.uint16)
    gradient = np.tile((x * 255 // 319).astype(np.uint8), (200, 1))
    grey = np.arange(256, dtype=np.uint8)
    playpal = np.column_stack((grey, grey, grey))

    encoded, attr, palette, pixels = fcvideo_ref.convert(gradient, playpal, PRESET)
    decoded = ppu_decode.decode_to_rgb(bytes(encoded), attr, palette)

    reference_indices = fcvideo_ref.place_letterbox(
        fcvideo_ref.decimate_320_to_256(gradient), backdrop=0
    )
    reference = playpal[reference_indices]
    mse = np.mean((decoded.astype(np.float64) - reference.astype(np.float64)) ** 2)
    psnr = 10.0 * np.log10((255.0**2) / mse)

    # The 224-line image occupies more of the measured frame than the old
    # 200-line image. Its deterministic whole-frame result is 12.55 dB.
    assert psnr >= 12.5
    assert len(encoded) == protocol.VRAM_BUF_BYTES_V2
    assert pixels.shape == (stream.VRAM_LINES, stream.VRAM_WIDTH)
    mailbox = encoded[
        protocol.VRAM_MAILBOX_OFF_V2 : protocol.VRAM_MAILBOX_OFF_V2
        + protocol.FC_COM_BUF_SIZE_V2
    ]
    expected_flags = (
        protocol.MBX_FLAG_V2 | protocol.MBX_FLAG_ATTR_VALID | protocol.MBX_FLAG_PAL_VALID
    )
    assert mailbox[protocol.MBX_FLAGS] == expected_flags
