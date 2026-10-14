# SPDX-License-Identifier: BSD-3-Clause
"""Tests for tools/ppu_decode.py: the stream -> RGB decoder.

conftest.py puts fcpico/tools on sys.path, so `import ppu_decode` and
`from fcpico import ...` work directly, same as production code (ppu_decode.py
itself is a top-level script under tools/, like respack.py).

Builds synthetic 256x240 2-bit frames with fcpico.stream.encode_frame(), decodes
them with ppu_decode.decode_to_rgb(), and asserts the result is exactly the NES
palette entries the attribute table and BG palette select.

Test frames deliberately keep all "interesting" (non-backdrop) pixel data in
lines 0..191 (block rows 0..11), well clear of the region where the mailbox
tail overlaps picture words for the last few lines (see fcpico/stream.py's
module docstring) -- so no mailbox-overlap mask bookkeeping is needed here.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

from fcpico import nes_palette, protocol, stream

import ppu_decode

SCRIPT_PATH = Path(__file__).resolve().parents[2] / "tools" / "ppu_decode.py"


def _blank_pix() -> np.ndarray:
    return np.zeros((stream.VRAM_LINES, stream.VRAM_WIDTH), dtype=np.uint8)


def _encode(pix, mailbox_len=protocol.FC_COM_BUF_SIZE_V1, mailbox=None):
    if mailbox is None:
        mailbox = bytes(mailbox_len)
    return bytes(stream.encode_frame(pix, mailbox))


def _expect_rgb(nes_index: int):
    return tuple(int(c) for c in nes_palette.NES_PALETTE_RGB_ARRAY[nes_index])


# ---------------------------------------------------------------------------
# Step 1/2 -- known 2-bit values against a known palette/attribute table
# ---------------------------------------------------------------------------


def test_default_grey_ramp_when_no_palette_given():
    # No --attr/--pal: attribute table is all zero (sub-palette 0 everywhere)
    # and the palette defaults to ppu_decode.DEFAULT_PAL16, the Phase 1 boot
    # default grey ramp 0x0F 0x00 0x10 0x30 repeated in every sub-palette.
    pix = _blank_pix()
    pix[0, 0:4] = [0, 1, 2, 3]
    buf = _encode(pix)

    rgb = ppu_decode.decode_to_rgb(buf)
    assert rgb.shape == (240, 256, 3)

    expected = [0x0F, 0x00, 0x10, 0x30]  # DEFAULT_PAL16[0..3], sub-palette 0
    for x, nes_idx in enumerate(expected):
        assert tuple(int(c) for c in rgb[0, x]) == _expect_rgb(nes_idx), f"pixel x={x}"


def test_neighbouring_blocks_use_different_sub_palettes():
    # Block (0,0) (pixels x 0..15, y 0..15) gets sub-palette 1; the immediately
    # neighbouring block (1,0) (pixels x 16..31) gets sub-palette 2.
    attr = bytearray(64)
    stream.attr_set(attr, 0, 0, 1)
    stream.attr_set(attr, 1, 0, 2)

    # A distinct, easily-checked BG palette: entry i holds NES colour index i.
    pal = bytes(range(16))

    pix = _blank_pix()
    pix[8, 8] = 2  # inside block (0,0): sub-palette 1, colour 2 -> pal[1*4+2]=pal[6]
    pix[8, 24] = 3  # inside block (1,0): sub-palette 2, colour 3 -> pal[2*4+3]=pal[11]
    buf = _encode(pix)

    rgb = ppu_decode.decode_to_rgb(buf, attr=bytes(attr), pal=pal)
    assert tuple(int(c) for c in rgb[8, 8]) == _expect_rgb(pal[6])
    assert tuple(int(c) for c in rgb[8, 24]) == _expect_rgb(pal[11])
    # And they really are different colours in this palette (pal is strictly increasing).
    assert rgb[8, 8].tolist() != rgb[8, 24].tolist()


def test_colour_zero_is_always_backdrop_regardless_of_sub_palette():
    # A block with a non-zero sub-palette selected; colour 0 within it must
    # still show pal[0] (the shared backdrop), not pal[sub_pal*4 + 0].
    attr = bytearray(64)
    stream.attr_set(attr, 0, 0, 3)  # sub-palette 3 for block (0,0)

    pal = bytearray(range(16))
    pal[0] = 0x20  # backdrop
    pal[12] = 0x21  # sub-palette 3, entry 0 -- deliberately different from pal[0]
    pal = bytes(pal)

    pix = _blank_pix()
    pix[4, 4] = 0  # colour 0, inside the sub-palette-3 block
    buf = _encode(pix)

    rgb = ppu_decode.decode_to_rgb(buf, attr=bytes(attr), pal=pal)
    assert tuple(int(c) for c in rgb[4, 4]) == _expect_rgb(0x20)
    assert tuple(int(c) for c in rgb[4, 4]) != _expect_rgb(0x21)


def test_explicit_attr_and_pal_override_the_defaults_everywhere():
    # Sanity check across every sub-palette / colour combination, at once,
    # confined to the safe (non-mailbox-overlapping) region.
    attr = bytearray(64)
    for by in range(12):  # block rows 0..11 -> pixel rows 0..191
        for bx in range(16):
            stream.attr_set(attr, bx, by, (bx + by) % 4)
    pal = bytes((i * 7 + 3) & 0x3F for i in range(16))

    rng = np.random.default_rng(123)
    pix = _blank_pix()
    pix[0:192, :] = rng.integers(0, 4, size=(192, stream.VRAM_WIDTH), dtype=np.uint8)
    buf = _encode(pix)

    rgb = ppu_decode.decode_to_rgb(buf, attr=bytes(attr), pal=pal)

    for y in (0, 37, 100, 191):
        for x in (0, 15, 16, 31, 100, 255):
            tx, ty = x >> 3, y >> 3
            sub_pal = stream.attr_get(attr, x, y)
            v = int(pix[y, x])
            pal_index = 0 if v == 0 else sub_pal * 4 + v
            assert tuple(int(c) for c in rgb[y, x]) == _expect_rgb(pal[pal_index]), (x, y)


def test_decode_to_rgb_validates_lengths():
    buf = _encode(_blank_pix())
    with pytest.raises(ppu_decode.PpuDecodeError):
        ppu_decode.decode_to_rgb(buf, attr=bytes(63))
    with pytest.raises(ppu_decode.PpuDecodeError):
        ppu_decode.decode_to_rgb(buf, pal=bytes(15))


# ---------------------------------------------------------------------------
# Step 3 -- the --mailbox-v2 path
# ---------------------------------------------------------------------------


def _make_v2_mailbox(*, attr=None, pal=None, attr_valid=False, pal_valid=False) -> bytes:
    mailbox = bytearray(protocol.FC_COM_BUF_SIZE_V2)
    flags = 0
    if attr_valid:
        flags |= protocol.MBX_FLAG_ATTR_VALID
    if pal_valid:
        flags |= protocol.MBX_FLAG_PAL_VALID
    mailbox[protocol.MBX_FLAGS] = flags
    mailbox[protocol.MBX_MAGIC] = protocol.PF_MAGIC_NO
    if attr is not None:
        mailbox[protocol.MBX_ATTR : protocol.MBX_ATTR + protocol.MBX_ATTR_LEN] = attr
    if pal is not None:
        mailbox[protocol.MBX_PAL : protocol.MBX_PAL + protocol.MBX_PAL_LEN] = pal
    return bytes(mailbox)


def test_mailbox_v2_used_only_when_flag_bits_set():
    attr = bytearray(64)
    stream.attr_set(attr, 0, 0, 2)
    pal = bytes(range(16))

    pix = _blank_pix()
    pix[4, 4] = 3  # block (0,0): sub-palette 2 if attr honoured -> pal[2*4+3] = pal[11]

    mailbox = _make_v2_mailbox(attr=bytes(attr), pal=pal, attr_valid=True, pal_valid=True)
    buf = _encode(pix, mailbox=mailbox)
    assert len(buf) == protocol.VRAM_BUF_BYTES_V2

    got_attr, got_pal = ppu_decode._mailbox_from_stream(buf)
    assert got_attr == bytes(attr)
    assert got_pal == pal

    rgb = ppu_decode.decode_to_rgb(buf, attr=got_attr, pal=got_pal)
    assert tuple(int(c) for c in rgb[4, 4]) == _expect_rgb(pal[11])


def test_mailbox_v2_ignored_when_flags_clear():
    attr = bytearray(64)
    stream.attr_set(attr, 0, 0, 2)
    pal = bytes(range(16))

    # Flags left clear even though attr/pal bytes are present in the mailbox --
    # they must be reported as absent (None), i.e. ignored.
    mailbox = _make_v2_mailbox(attr=bytes(attr), pal=pal, attr_valid=False, pal_valid=False)
    buf = _encode(_blank_pix(), mailbox=mailbox)

    got_attr, got_pal = ppu_decode._mailbox_from_stream(buf)
    assert got_attr is None
    assert got_pal is None


def test_mailbox_v2_flags_are_independent():
    attr = bytearray(64)
    stream.attr_set(attr, 0, 0, 1)
    pal = bytes(range(16))

    # Only the palette flag set: attr must come back None, pal must come back set.
    mailbox = _make_v2_mailbox(attr=bytes(attr), pal=pal, attr_valid=False, pal_valid=True)
    buf = _encode(_blank_pix(), mailbox=mailbox)
    got_attr, got_pal = ppu_decode._mailbox_from_stream(buf)
    assert got_attr is None
    assert got_pal == pal

    # Only the attribute flag set: pal must come back None, attr must come back set.
    mailbox = _make_v2_mailbox(attr=bytes(attr), pal=pal, attr_valid=True, pal_valid=False)
    buf = _encode(_blank_pix(), mailbox=mailbox)
    got_attr, got_pal = ppu_decode._mailbox_from_stream(buf)
    assert got_attr == bytes(attr)
    assert got_pal is None


def test_mailbox_v2_rejects_non_v2_length_buffer():
    buf = _encode(_blank_pix(), mailbox_len=protocol.FC_COM_BUF_SIZE_V1)
    with pytest.raises(ppu_decode.PpuDecodeError):
        ppu_decode._mailbox_from_stream(buf)


# ---------------------------------------------------------------------------
# Step 4 -- the CLI, through subprocess
# ---------------------------------------------------------------------------


def test_cli_writes_png_of_right_size(tmp_path):
    pix = _blank_pix()
    pix[0, 0:4] = [0, 1, 2, 3]
    buf = _encode(pix)
    stream_path = tmp_path / "stream.bin"
    stream_path.write_bytes(buf)
    out_path = tmp_path / "out.png"

    result = subprocess.run(
        [sys.executable, str(SCRIPT_PATH), str(stream_path), "-o", str(out_path)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert out_path.is_file()

    from PIL import Image

    with Image.open(out_path) as img:
        assert img.size == (256, 240)
        assert img.mode == "RGB"


def test_cli_with_explicit_attr_and_pal_files(tmp_path):
    attr = bytearray(64)
    stream.attr_set(attr, 0, 0, 1)
    pal = bytes(range(16))

    pix = _blank_pix()
    pix[4, 4] = 2  # sub-palette 1, colour 2 -> pal[1*4+2] = pal[6]
    buf = _encode(pix)

    stream_path = tmp_path / "stream.bin"
    stream_path.write_bytes(buf)
    attr_path = tmp_path / "attr.bin"
    attr_path.write_bytes(bytes(attr))
    pal_path = tmp_path / "pal.bin"
    pal_path.write_bytes(pal)
    out_path = tmp_path / "out.png"

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT_PATH),
            str(stream_path),
            "--attr",
            str(attr_path),
            "--pal",
            str(pal_path),
            "-o",
            str(out_path),
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr

    from PIL import Image

    with Image.open(out_path) as img:
        assert img.size == (256, 240)
        assert tuple(img.getpixel((4, 4))) == _expect_rgb(pal[6])


def test_cli_mailbox_v2_flag(tmp_path):
    attr = bytearray(64)
    stream.attr_set(attr, 0, 0, 3)
    pal = bytes(range(16))

    pix = _blank_pix()
    pix[4, 4] = 1  # sub-palette 3, colour 1 -> pal[3*4+1] = pal[13]

    mailbox = _make_v2_mailbox(attr=bytes(attr), pal=pal, attr_valid=True, pal_valid=True)
    buf = _encode(pix, mailbox=mailbox)
    stream_path = tmp_path / "stream_v2.bin"
    stream_path.write_bytes(buf)
    out_path = tmp_path / "out.png"

    result = subprocess.run(
        [sys.executable, str(SCRIPT_PATH), str(stream_path), "--mailbox-v2", "-o", str(out_path)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr

    from PIL import Image

    with Image.open(out_path) as img:
        assert img.size == (256, 240)
        assert tuple(img.getpixel((4, 4))) == _expect_rgb(pal[13])


def test_cli_rejects_wrong_length_buffer_for_mailbox_v2(tmp_path):
    buf = _encode(_blank_pix(), mailbox_len=protocol.FC_COM_BUF_SIZE_V1)
    stream_path = tmp_path / "stream_v1.bin"
    stream_path.write_bytes(buf)
    out_path = tmp_path / "out.png"

    result = subprocess.run(
        [sys.executable, str(SCRIPT_PATH), str(stream_path), "--mailbox-v2", "-o", str(out_path)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2
    assert "error" in result.stderr.lower()
    assert not out_path.exists()
