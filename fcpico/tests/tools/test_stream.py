# SPDX-License-Identifier: BSD-3-Clause
"""Tests for fcpico.stream (fcpico/tools/fcpico/stream.py).

conftest.py puts fcpico/tools on sys.path, so `from fcpico import ...` works
directly, same as production code.
"""

from __future__ import annotations

import random

import numpy as np
import pytest

from fcpico import protocol, stream


# ---------------------------------------------------------------------------
# word_index
# ---------------------------------------------------------------------------


def test_word_index_known_values():
    # Hardware selects 32 words per visible line. The original 34-iteration
    # converter loop advances one flat source index and does not define a
    # 32-word physical stride.
    assert stream.word_index(0, 0) == 31
    assert stream.word_index(1, 0) == 63
    assert stream.word_index(0, 31) == 31 + 31
    assert stream.word_index(239, 31) == 31 + 32 * 239 + 31


def test_word_index_matches_protocol_constants():
    for y in (0, 1, 100, 239):
        for x in (0, 17, 31):
            expected = protocol.VRAM_HEAD_WORDS + protocol.VRAM_TILE_COLS * y + x
            assert stream.word_index(y, x) == expected


def test_word_index_rejects_out_of_range():
    with pytest.raises(ValueError):
        stream.word_index(-1, 0)
    with pytest.raises(ValueError):
        stream.word_index(240, 0)
    with pytest.raises(ValueError):
        stream.word_index(0, 32)


# ---------------------------------------------------------------------------
# pack_run / unpack_run
# ---------------------------------------------------------------------------


def test_pack_run_matches_conv_vram_bit_order():
    # convVram(): dt = 0; for each pixel left-to-right: dt = (dt << 1) | conv_tbl[c].
    # All pixels colour 3 (both bitplane bits set) -> all-ones word.
    assert stream.pack_run([3] * 8) == 0xFFFF
    assert stream.pack_run([0] * 8) == 0x0000
    # Leftmost pixel lands in bit 7 of both bytes.
    assert stream.pack_run([1, 0, 0, 0, 0, 0, 0, 0]) == 0x0080  # plane 0 only, bit 7
    assert stream.pack_run([2, 0, 0, 0, 0, 0, 0, 0]) == 0x8000  # plane 1 only, bit 7
    assert stream.pack_run([0, 0, 0, 0, 0, 0, 0, 1]) == 0x0001  # rightmost pixel -> bit 0


def test_pack_unpack_round_trip_random():
    rng = random.Random(0)
    for _ in range(500):
        pixels = [rng.randint(0, 3) for _ in range(8)]
        word = stream.pack_run(pixels)
        assert 0 <= word <= 0xFFFF
        assert stream.unpack_run(word) == pixels


def test_unpack_run_all_words_round_trip():
    # Exhaustive over a stride so it stays fast, plus every all-same-nibble word.
    for word in list(range(0, 0x10000, 97)) + [0x0000, 0xFFFF, 0x00FF, 0xFF00]:
        pixels = stream.unpack_run(word)
        assert len(pixels) == 8
        assert all(0 <= p <= 3 for p in pixels)
        assert stream.pack_run(pixels) == word


def test_pack_run_requires_eight_pixels():
    with pytest.raises(ValueError):
        stream.pack_run([0] * 7)
    with pytest.raises(ValueError):
        stream.pack_run([0] * 9)


# ---------------------------------------------------------------------------
# encode_frame / decode_frame
# ---------------------------------------------------------------------------


def _mailbox_affected_pixels(mailbox_len: int) -> np.ndarray:
    """Boolean (240, 256) mask: pixels whose *visible* tile word overlaps the mailbox.

    The measured 32-word stride leaves picture data entirely before the
    mailbox. This helper remains as an explicit overlap check.
    """
    buf_bytes, mailbox_off = stream._buf_layout(mailbox_len)
    mailbox_end = mailbox_off + mailbox_len
    mask = np.zeros((stream.VRAM_LINES, stream.VRAM_WIDTH), dtype=bool)
    for y in range(stream.VRAM_LINES):
        for x in range(stream.VRAM_TILE_COLS):
            w = stream.word_index(y, x)
            byte_lo, byte_hi = w * 2, w * 2 + 2
            if byte_lo < mailbox_end and mailbox_off < byte_hi:
                mask[y, x * 8 : x * 8 + 8] = True
    return mask


@pytest.mark.parametrize("mailbox_len", [protocol.FC_COM_BUF_SIZE_V1, protocol.FC_COM_BUF_SIZE_V2])
def test_encode_decode_round_trip_random_frame(mailbox_len):
    rng = np.random.default_rng(42)
    pix = rng.integers(0, 4, size=(stream.VRAM_LINES, stream.VRAM_WIDTH), dtype=np.uint8)
    mailbox = bytes(rng.integers(0, 256, size=mailbox_len, dtype=np.uint8).tolist())

    buf = stream.encode_frame(pix, mailbox)
    expected_len = (
        protocol.VRAM_BUF_BYTES_V1 if mailbox_len == protocol.FC_COM_BUF_SIZE_V1 else protocol.VRAM_BUF_BYTES_V2
    )
    assert len(buf) == expected_len

    decoded = stream.decode_frame(buf)
    assert decoded.shape == (stream.VRAM_LINES, stream.VRAM_WIDTH)

    mask = _mailbox_affected_pixels(mailbox_len)
    assert not mask.any()
    assert np.array_equal(decoded[~mask], pix[~mask])
    # The mailbox itself always round-trips exactly, regardless of picture content.
    assert bytes(buf[stream.VRAM_MAILBOX_OFF : stream.VRAM_MAILBOX_OFF + mailbox_len]) == mailbox


def test_encode_decode_round_trip_full_when_bottom_letterboxed():
    rng = np.random.default_rng(7)
    pix = np.zeros((stream.VRAM_LINES, stream.VRAM_WIDTH), dtype=np.uint8)
    pix[16:216] = rng.integers(0, 4, size=(200, stream.VRAM_WIDTH), dtype=np.uint8)
    mailbox = bytes(protocol.FC_COM_BUF_SIZE_V1)

    buf = stream.encode_frame(pix, mailbox)
    decoded = stream.decode_frame(buf)
    assert np.array_equal(decoded, pix)


def test_encode_frame_buffer_sizes():
    pix = np.zeros((stream.VRAM_LINES, stream.VRAM_WIDTH), dtype=np.uint8)
    assert len(stream.encode_frame(pix, bytes(64))) == 17344 == protocol.VRAM_BUF_BYTES_V1
    assert len(stream.encode_frame(pix, bytes(128))) == 17408 == protocol.VRAM_BUF_BYTES_V2


def test_native_text_compaction_preserves_other_pixels_and_mailbox():
    rng = np.random.default_rng(4104)
    pix = rng.integers(0, 4, size=(240, 256), dtype=np.uint8)
    mailbox = bytes(rng.integers(0, 256, size=144, dtype=np.uint8))
    encoded = stream.encode_frame(pix, mailbox, native_text=True)
    assert len(encoded) == protocol.VRAM_BUF_BYTES_V4
    assert encoded[protocol.VRAM_MAILBOX_OFF_V4:
                   protocol.VRAM_MAILBOX_OFF_V4 + 144] == mailbox
    expanded = stream.expand_native_text_stream(encoded)
    decoded = stream.decode_frame(expanded)
    outside = np.ones((240, 256), dtype=bool)
    outside[protocol.NATIVE_TEXT_ROW * 8:(protocol.NATIVE_TEXT_ROW + 1) * 8,
            16:240] = False
    assert np.array_equal(decoded[outside], pix[outside])
    assert not decoded[~outside].any()


def test_encode_frame_rejects_bad_mailbox_length():
    pix = np.zeros((stream.VRAM_LINES, stream.VRAM_WIDTH), dtype=np.uint8)
    with pytest.raises(ValueError):
        stream.encode_frame(pix, bytes(63))


def test_encode_frame_rejects_bad_shape():
    with pytest.raises(ValueError):
        stream.encode_frame(np.zeros((239, 256), dtype=np.uint8), bytes(64))


def test_mailbox_at_expected_offset():
    assert protocol.VRAM_MAILBOX_OFF_V1 == 15426
    assert protocol.VRAM_MAILBOX_OFF_V2 == 15426
    assert stream.VRAM_MAILBOX_OFF == 15426

    pix = np.zeros((stream.VRAM_LINES, stream.VRAM_WIDTH), dtype=np.uint8)
    mailbox = bytes([protocol.PF_MAGIC_NO if i == 1 else i & 0xFF for i in range(64)])
    buf = stream.encode_frame(pix, mailbox)
    assert bytes(buf[15426 : 15426 + 64]) == mailbox


def test_decode_frame_rejects_short_buffer():
    with pytest.raises(ValueError):
        stream.decode_frame(bytes(100))


def test_picture_is_linear_and_leaves_four_bytes_before_mailbox():
    pix = np.zeros((stream.VRAM_LINES, stream.VRAM_WIDTH), dtype=np.uint8)
    pix[1, 0:8] = 3
    buf = stream.encode_frame(pix, bytes(protocol.FC_COM_BUF_SIZE_V1))
    words = np.frombuffer(bytes(buf), dtype="<u2")

    assert words[stream.word_index(1, 0)] == 0xFFFF
    picture_end = (stream.word_index(239, 31) + 1) * 2
    assert picture_end == 15422
    assert bytes(buf[picture_end:stream.VRAM_MAILBOX_OFF]) == b"\x00" * 4


# ---------------------------------------------------------------------------
# attribute table
# ---------------------------------------------------------------------------


def test_attr_index_and_shift_known_values():
    # Tile (0,0): byte 0, shift 0.
    assert stream.attr_index(0, 0) == 0
    assert stream.attr_shift(0, 0) == 0
    # Tile (2,0): still byte 0 (tx>>2==0), but quadrant x-bit flips -> shift 2.
    assert stream.attr_index(2, 0) == 0
    assert stream.attr_shift(2, 0) == 2
    # Tile (0,2): byte 0, shift 4 (quadrant y-bit).
    assert stream.attr_shift(0, 2) == 4
    # Tile (4,0): next byte column.
    assert stream.attr_index(4, 0) == 1
    # Tile (0,4): next byte row (8 bytes per row of the 8x8 byte grid).
    assert stream.attr_index(0, 4) == 8


def test_attr_set_get_round_trip_all_blocks():
    attr = bytearray(64)
    rng = random.Random(5)
    choices = {}
    for by in range(15):
        for bx in range(16):
            p = rng.randint(0, 3)
            choices[(bx, by)] = p
            stream.attr_set(attr, bx, by, p)

    for (bx, by), p in choices.items():
        # Sample every pixel of the 16x16 block; all must read back the same choice.
        px, py = bx * 16, by * 16
        for dx in (0, 15):
            for dy in (0, 15):
                assert stream.attr_get(attr, px + dx, py + dy) == p


def test_attr_set_does_not_disturb_other_quadrants():
    attr = bytearray(64)
    stream.attr_set(attr, 0, 0, 1)
    stream.attr_set(attr, 1, 0, 2)
    stream.attr_set(attr, 0, 1, 3)
    stream.attr_set(attr, 1, 1, 0)
    # These four blocks share attribute byte 0 (tile coords < 4 in both axes).
    # Fields, high to low: (bx=1,by=1)=0 -> 00, (bx=0,by=1)=3 -> 11,
    # (bx=1,by=0)=2 -> 10, (bx=0,by=0)=1 -> 01.
    assert attr[0] == 0b00_11_10_01
    assert stream.attr_get(attr, 0, 0) == 1
    assert stream.attr_get(attr, 16, 0) == 2
    assert stream.attr_get(attr, 0, 16) == 3
    assert stream.attr_get(attr, 16, 16) == 0


def test_attr_set_matches_rp_system_mask_and_set_tables():
    # Direct transcription of rp_system::setAtr()'s mask_tbl/set_tbl, as a
    # second, independent implementation to check attr_set against.
    mask_tbl = (0b11111100, 0b11110011, 0b11001111, 0b00111111)
    set_tbl = (0b00000001, 0b00000100, 0b00010000, 0b01000000)

    rng = random.Random(9)
    attr_a = bytearray(64)
    attr_b = bytearray(64)
    for _ in range(200):
        bx, by, p = rng.randint(0, 15), rng.randint(0, 14), rng.randint(0, 3)
        stream.attr_set(attr_a, bx, by, p)

        idx = ((bx >> 1) + ((by >> 1) << 3)) & 0x3F
        sel = (bx & 1) + ((by & 1) << 1)
        attr_b[idx] = (attr_b[idx] & mask_tbl[sel]) | (set_tbl[sel] * p)

    assert bytes(attr_a) == bytes(attr_b)
