# SPDX-License-Identifier: BSD-3-Clause
"""Differential host test: C frame conversion against the Python video oracle."""

from __future__ import annotations

import ctypes
from pathlib import Path
import subprocess

import numpy as np
import pytest

from fcpico import nes_palette, protocol, stream
import fcvideo_ref


ROOT = Path(__file__).resolve().parents[3]
PRESET = {
    "backdrop": 0x0F,
    "subpalettes": ((0x00, 0x10, 0x30), (0x06, 0x16, 0x26),
                    (0x09, 0x19, 0x29), (0x02, 0x12, 0x22)),
}
U8 = ctypes.c_uint8
U8_PTR = ctypes.POINTER(U8)


class Tables(ctypes.Structure):
    _fields_ = [("err", U8_PTR), ("lut", U8_PTR), ("palette", U8 * protocol.MBX_PAL_LEN)]


class Preset(ctypes.Structure):
    _fields_ = [("backdrop", U8), ("subpalettes", (U8 * 3) * 4),
                ("shadow_lift", U8)]


@pytest.fixture(scope="module")
def library(tmp_path_factory):
    output = tmp_path_factory.mktemp("fcvideo") / "libfcvideo.so"
    subprocess.run(
        ["cc", "-shared", "-fPIC", "-std=c11", "-O2", "-Wall", "-Wextra", "-Werror",
         "-I", str(ROOT / "fcpico/port/video"), "-I", str(ROOT / "fcpico/fcbus"),
         str(ROOT / "fcpico/port/video/fcvideo.c"), "-lm", "-o", str(output)],
        check=True,
    )
    lib = ctypes.CDLL(str(output))
    lib.fcvideo_sizeof.restype = ctypes.c_size_t
    lib.fcvideo_init.argtypes = [ctypes.c_void_p, ctypes.POINTER(Tables)]
    lib.fcvideo_convert.argtypes = [ctypes.c_void_p, U8_PTR, U8_PTR, U8_PTR, ctypes.c_bool]
    lib.fcvideo_set_palette.argtypes = [ctypes.c_void_p, U8_PTR]
    lib.fcvideo_build_tables.argtypes = [U8_PTR, ctypes.POINTER(Preset), U8_PTR,
                                         U8_PTR, U8_PTR]
    lib.fcvideo_build_palette_sets.argtypes = [ctypes.POINTER(Preset), U8_PTR]
    return lib


@pytest.mark.parametrize("preset_index,entries", [
    (0, ((0x00, 0x10, 0x30), (0x07, 0x17, 0x27),
         (0x06, 0x16, 0x26), (0x09, 0x19, 0x29))),
    (1, ((0x00, 0x10, 0x30), (0x07, 0x17, 0x30),
         (0x06, 0x16, 0x30), (0x09, 0x19, 0x30))),
    (2, ((0x10, 0x09, 0x2D), (0x07, 0x28, 0x18),
         (0x02, 0x01, 0x11), (0x06, 0x16, 0x3D))),
    (4, ((0x1D, 0x00, 0x30), (0x08, 0x18, 0x30),
         (0x00, 0x30, 0x16), (0x09, 0x19, 0x30))),
])
def test_c_table_builder_matches_python_reference(library, preset_index, entries):
    presets = (Preset * 5).in_dll(library, "fcvideo_presets")
    preset = presets[preset_index]
    assert preset.backdrop == 0x0F
    assert tuple(tuple(row) for row in preset.subpalettes) == entries

    rng = np.random.default_rng(23)
    playpal = rng.integers(0, 256, size=(256, 3), dtype=np.uint8)
    playpal[0] = (0, 0, 0)
    expected_err, expected_lut = fcvideo_ref.build_err_and_lut(
        playpal, entries, 0x0F, bool(preset.shadow_lift))
    err = (U8 * (4 * 256))()
    lut = (U8 * (4 * 256 * 16))()
    palette = (U8 * protocol.MBX_PAL_LEN)()
    library.fcvideo_build_tables(playpal.ctypes.data_as(U8_PTR), ctypes.byref(preset),
                                  err, lut, palette)
    assert bytes(err) == expected_err.tobytes()
    assert bytes(lut) == expected_lut.tobytes()
    assert bytes(palette) == bytes([x for row in entries for x in (0x0F, *row)])


def test_c_built_tables_feed_full_stream(library):
    presets = (Preset * 5).in_dll(library, "fcvideo_presets")
    preset = presets[1]
    grey = np.arange(256, dtype=np.uint8)
    playpal = np.ascontiguousarray(np.column_stack((grey, grey, grey)))
    err = (U8 * (4 * 256))()
    lut = (U8 * (4 * 256 * 16))()
    palette = (U8 * protocol.MBX_PAL_LEN)()
    library.fcvideo_build_tables(playpal.ctypes.data_as(U8_PTR), ctypes.byref(preset),
                                  err, lut, palette)
    tables = Tables(err, lut, palette)
    storage = (ctypes.c_uint64 * ((library.fcvideo_sizeof() + 7) // 8))()
    video = ctypes.cast(storage, ctypes.c_void_p)
    library.fcvideo_init(video, ctypes.byref(tables))

    x = np.arange(320, dtype=np.uint16)
    frame = np.tile((x * 255 // 319).astype(np.uint8), (200, 1))
    expected_err, expected_lut = fcvideo_ref.build_err_and_lut(
        playpal, tuple(tuple(row) for row in preset.subpalettes), preset.backdrop
    )
    expected_stream, expected_attr = _reference(frame, expected_err, expected_lut,
                                                 bytes(palette))
    actual_stream, actual_attr = _convert(library, video, frame, True)
    assert actual_attr == expected_attr
    assert actual_stream == expected_stream


@pytest.mark.parametrize("preset_index", range(3))
def test_synthetic_flash_palette_sets_match_engine_tint(library, preset_index):
    presets = (Preset * 5).in_dll(library, "fcvideo_presets")
    preset = presets[preset_index]
    sets = (U8 * (14 * protocol.MBX_PAL_LEN))()
    library.fcvideo_build_palette_sets(ctypes.byref(preset), sets)
    base = [x for row in preset.subpalettes for x in (preset.backdrop, *row)]
    assert bytes(sets[:16]) == bytes(base)
    for index in range(1, 14):
        if index < 9:
            mul, target = index * 65536 // 9, (255, 0, 0)
        elif index < 13:
            mul, target = (index - 8) * 65536 // 8, (215, 186, 69)
        else:
            mul, target = 65536 // 8, (0, 256, 0)
        expected = []
        for entry in base:
            anchor = nes_palette.NES_PALETTE_RGB[entry]
            rgb = [c + ((t - c) * mul // 65536) for c, t in zip(anchor, target)]
            expected.append(nes_palette.nearest_nes_color(rgb))
        assert bytes(sets[index * 16:(index + 1) * 16]) == bytes(expected)
        assert len({sets[index * 16 + offset] for offset in (0, 4, 8, 12)}) == 1


def _reference(frame, err, lut, palette, previous=None):
    reduced = fcvideo_ref.decimate_320_to_256(frame)
    image = fcvideo_ref.place_letterbox(reduced, backdrop=0)
    attr = fcvideo_ref.choose_block_palettes(image, err, prev_attr=previous)
    pixels = fcvideo_ref.quantize(image, lut, attr)
    pixels[:fcvideo_ref.LETTERBOX_TOP] = 0
    pixels[stream.VRAM_LINES - fcvideo_ref.LETTERBOX_BOTTOM:] = 0
    mailbox = bytearray(protocol.FC_COM_BUF_SIZE_V2)
    mailbox[protocol.MBX_FLAGS] = (
        protocol.MBX_FLAG_V2 | protocol.MBX_FLAG_ATTR_VALID | protocol.MBX_FLAG_PAL_VALID
    )
    mailbox[protocol.MBX_MAGIC] = protocol.PF_MAGIC_NO
    mailbox[protocol.MBX_PAL:protocol.MBX_PAL + protocol.MBX_PAL_LEN] = palette
    mailbox[protocol.MBX_ATTR:protocol.MBX_ATTR + protocol.MBX_ATTR_LEN] = attr
    return bytes(stream.encode_frame(pixels, mailbox)), bytes(attr)


def _convert(lib, video, frame, reset):
    encoded = np.zeros(protocol.VRAM_BUF_BYTES_V2, dtype=np.uint8)
    attr = np.zeros(protocol.MBX_ATTR_LEN, dtype=np.uint8)
    source = np.ascontiguousarray(frame, dtype=np.uint8)
    lib.fcvideo_convert(video, source.ctypes.data_as(U8_PTR),
                        encoded.ctypes.data_as(U8_PTR), attr.ctypes.data_as(U8_PTR), reset)
    return encoded.tobytes(), attr.tobytes()


def test_c_stream_matches_python_reference_for_distinct_frames(library):
    grey = np.arange(256, dtype=np.uint8)
    playpal = np.column_stack((grey, grey, grey))
    err, lut = fcvideo_ref.build_err_and_lut(
        playpal, PRESET["subpalettes"], PRESET["backdrop"]
    )
    err = np.ascontiguousarray(err)
    lut = np.ascontiguousarray(lut)
    palette = bytes([PRESET["backdrop"], *PRESET["subpalettes"][0],
                     PRESET["backdrop"], *PRESET["subpalettes"][1],
                     PRESET["backdrop"], *PRESET["subpalettes"][2],
                     PRESET["backdrop"], *PRESET["subpalettes"][3]])
    tables = Tables(err.ctypes.data_as(U8_PTR), lut.ctypes.data_as(U8_PTR),
                    (U8 * protocol.MBX_PAL_LEN).from_buffer_copy(palette))
    storage = (ctypes.c_uint64 * ((library.fcvideo_sizeof() + 7) // 8))()
    video = ctypes.cast(storage, ctypes.c_void_p)
    library.fcvideo_init(video, ctypes.byref(tables))

    x = np.arange(320, dtype=np.uint16)
    gradient = np.tile((x * 255 // 319).astype(np.uint8), (200, 1))
    checker = np.indices((200, 320)).sum(axis=0).astype(np.uint8) * 37
    random = np.random.default_rng(7).integers(0, 256, size=(200, 320), dtype=np.uint8)
    for frame in (gradient, checker, random):
        expected_stream, expected_attr = _reference(frame, err, lut, palette)
        actual_stream, actual_attr = _convert(library, video, frame, True)
        assert actual_attr == expected_attr
        assert actual_stream == expected_stream

    expected_stream, expected_attr = _reference(random, err, lut, palette, expected_attr)
    actual_stream, actual_attr = _convert(library, video, random, False)
    assert actual_attr == expected_attr
    assert actual_stream == expected_stream

    flashed = bytes([0x0f, 0x30, 0x30, 0x30] * 4)
    palette_c = (U8 * protocol.MBX_PAL_LEN).from_buffer_copy(flashed)
    library.fcvideo_set_palette(video, palette_c)
    expected_stream, expected_attr = _reference(random, err, lut, flashed, expected_attr)
    actual_stream, actual_attr = _convert(library, video, random, False)
    assert actual_attr == expected_attr
    assert actual_stream == expected_stream
