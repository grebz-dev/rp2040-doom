# SPDX-License-Identifier: BSD-3-Clause
"""Hardware-validation capture tools against the firmware's own barcode code."""
import ctypes
from pathlib import Path
import subprocess

import numpy as np
import pytest
from PIL import Image

from capture import analyze, barcode, trace_log
from capture.reference import render_stream
from fcpico import nes_palette, protocol, stream

ROOT = Path(__file__).resolve().parents[2]
U8_PTR = ctypes.POINTER(ctypes.c_uint8)


@pytest.fixture(scope="module")
def fcvideo(tmp_path_factory):
    output = tmp_path_factory.mktemp("fcvideo") / "libfcvideo.so"
    subprocess.run(["cc", "-shared", "-fPIC", "-std=c11", "-O2", "-Wall", "-Wextra", "-Werror",
                    "-I", str(ROOT / "port/video"), "-I", str(ROOT / "fcbus"),
                    str(ROOT / "port/video/fcvideo.c"), "-lm", "-o", str(output)], check=True)
    lib = ctypes.CDLL(str(output))
    lib.fcvideo_trace_cells.argtypes = [ctypes.c_uint32, ctypes.c_uint8, ctypes.c_bool,
                                        ctypes.c_uint32, U8_PTR]
    lib.fcvideo_stamp_trace.argtypes = [U8_PTR, U8_PTR, U8_PTR]
    return lib


def c_cells(lib, code):
    cells = (ctypes.c_uint8 * barcode.CELLS)()
    lib.fcvideo_trace_cells(code.id, code.generation, code.timeout, code.tic, cells)
    return list(cells)


CODES = [barcode.Code(0, 0, False, 0), barcode.Code(4095, 15, True, 127),
         barcode.Code(1234, 9, False, 77), barcode.Code(5000 % 4096, 3, True, 300 % 128)]


@pytest.mark.parametrize("code", CODES)
def test_c_cells_match_python_and_parse_back(fcvideo, code):
    cells = c_cells(fcvideo, code)
    assert cells == barcode.encode(code)
    assert barcode.parse(cells) == code


def test_parity_rejects_a_flipped_cell():
    cells = barcode.encode(barcode.Code(100, 2, False, 5))
    for index in range(2, 27):
        flipped = list(cells)
        flipped[index] ^= 1
        assert barcode.parse(flipped) is None


HUD = (0x00, 0x30, 0x16)
PALETTE = [0x0F, 0x1D, 0x00, 0x30, 0x0F, 0x2D, 0x16, 0x26, 0x0F, *HUD, 0x0F, 0x02, 0x12, 0x30]


def stamped_stream(lib, code, rng):
    """A v2 stream of random scene pixels and attributes, stamped by the C code."""
    pixels = rng.integers(0, 4, (240, 256), dtype=np.uint8)
    mailbox = bytearray(protocol.FC_COM_BUF_SIZE_V2)
    mailbox[protocol.MBX_PAL:protocol.MBX_PAL + 16] = bytes(PALETTE)
    attr = bytearray(rng.integers(0, 256, 64, dtype=np.uint8).tobytes())
    mailbox[protocol.MBX_ATTR:protocol.MBX_ATTR + 64] = attr
    data = bytearray(stream.encode_frame(pixels, bytes(mailbox)))
    buf = (ctypes.c_uint8 * len(data)).from_buffer(data)
    attr_ptr = ctypes.cast(ctypes.addressof(buf) + protocol.VRAM_MAILBOX_OFF_V2 + protocol.MBX_ATTR, U8_PTR)
    cells = (ctypes.c_uint8 * barcode.CELLS)(*c_cells(lib, code))
    lib.fcvideo_stamp_trace(buf, attr_ptr, cells)
    return bytes(data)


def test_stamped_stream_reads_back_in_hud_palette(fcvideo):
    rng = np.random.default_rng(1)
    data = stamped_stream(fcvideo, CODES[2], rng)
    assert barcode.read_nes(stream.decode_frame(data)) == CODES[2]
    attr = data[protocol.VRAM_MAILBOX_OFF_V2 + protocol.MBX_ATTR:][:64]
    for x in range(16, 240, 16):
        assert stream.attr_get(attr, x, 16) == 2
    rgb = render_stream(data)
    band = rgb[16:32, 16:240].reshape(16, 28, 8, 3)
    colours = {tuple(c) for c in band.reshape(-1, 3)}
    assert colours <= {tuple(nes_palette.NES_PALETTE_RGB_ARRAY[0x0F]),
                       tuple(nes_palette.NES_PALETTE_RGB_ARRAY[0x30])}


def simulated_field(rgb, rng, width=704, height=243, x0=28.0, y0=2.0, sx=2.58):
    """A composite-like capture field: scaled, offset, blurred, noisy, gain-shifted."""
    luma = rgb.astype(np.float64) @ np.array([0.299, 0.587, 0.114])
    scaled = np.asarray(Image.fromarray(luma.astype(np.uint8)).resize(
        (int(round(256 * sx)), 240), Image.BILINEAR), np.float64)
    field = np.full((height, width), 16.0)
    left, top = int(round(x0)), int(round(y0))
    field[top:top + 240, left:left + scaled.shape[1]] = scaled[:, :width - left]
    kernel = np.ones(5) / 5
    field = np.apply_along_axis(lambda row: np.convolve(row, kernel, mode="same"), 1, field)
    field = 16 + field * 0.82 + rng.normal(0, 5, field.shape)
    return np.clip(field, 0, 255).astype(np.uint8)


def test_calibrates_and_decodes_a_simulated_composite_capture(fcvideo):
    rng = np.random.default_rng(7)
    codes = [barcode.Code(i % 4096, i % 16, i % 9 == 0, (i * 2) % 128) for i in range(4090, 4150)]
    fields = [simulated_field(render_stream(stamped_stream(fcvideo, c, rng)), rng) for c in codes]
    geometry, rate = barcode.calibrate(fields)
    assert rate >= 0.95
    assert abs(geometry.x0 - 28) < 3 and abs(geometry.sx - 2.58) < 0.03
    readings = [barcode.read(f, geometry).code for f in fields]
    assert sum(r is not None for r in readings) >= 57
    assert all(r == c for r, c in zip(readings, codes) if r is not None)


def trace(id, lt, **fields):
    values = dict(id=id, tic=lt + 1000, lt=lt, us=id * 50000, conv=7400, wait=0, tmo=0, drops=0,
                  gen=id % 256, shown=0, oam=4, pkt=3, tiles=2, q=0, ab=0, pal=0, key=1, err=0)
    values.update(fields)
    return trace_log.Trace(id, values)


def test_firmware_trace_line_parses():
    line = ("T id=4097 tic=1500 lt=320 us=48211903 conv=7437 wait=22140 tmo=1 drops=12 gen=37 "
            "shown=36 oam=9 pkt=3 tiles=5 q=1 ab=0 pal=3 key=5 err=0")
    fields = trace_log.parse_line(line)
    assert fields["id"] == 4097 and fields["tmo"] == 1 and fields["key"] == 5
    assert trace_log.parse_line("video fps=16.6 rendered=19.2") is None


def test_analysis_finds_a_hitch_and_its_timeout_across_the_id_wrap():
    traces = [trace(i, i * 2) for i in range(4090, 4110)]
    traces[4101 - 4090] = trace(4101, 4101 * 2, tmo=1, wait=50004, pkt=9)
    traces[4105 - 4090] = trace(4105, 4105 * 2, key=2)
    fields = []
    for t in traces:
        if t.id == 4103:
            continue  # published but never shown
        held = 6 if t.id == 4100 else 3
        code = barcode.Code(t.id % 4096, t.gen % 16, bool(t.tmo), t.lt % 128)
        fields += [code] * held
    fields[10] = None  # an unreadable field inside a picture
    report = analyze.analyze(fields, traces, hitch_fields=4)
    assert report["id_offset"] == 0
    assert report["hitches"] == 1
    assert report["hitch_pictures"][0]["id"] == 4100
    assert report["hitch_causes"]["timeout"] == 1.0
    assert report["unseen_pictures"] == 1
    assert report["barcode_trace_mismatches"] == []
    assert report["shown_out_of_order"] == []
    assert (4105, 2) in report["palette_key_changes"]
    assert report["fields_per_picture"][3] >= 15
