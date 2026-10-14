# SPDX-License-Identifier: BSD-3-Clause
"""stream.py -- pure-Python model of the fcbus frame-stream buffer.

Reproduces, byte for byte, the layout ``rp_system::convVram()`` and
``rp_system::ppu_dma()`` build in ``tutorial_project/tuto1_hw/sys/rp_system.cpp``
and that ``fcbus_stream_word(line, tile)`` exposes on the cartridge: a flat
buffer of 16-bit little-endian words, 32 selected words per visible scanline
(two bitplanes of an 8-pixel tile run each), with a
command/attribute/palette mailbox spliced into its tail. See
``fcpico/README.md`` ("Bus and stream facts") for the numbers this
module is built from; ``fcpico/fcbus/fcbus_protocol.h`` is the single source of
truth for the constants themselves (imported below, never re-typed as
literals).

Word addressing
----------------
``word_index(y, x)`` for scanline ``y`` (0..239) and visible tile column
``x`` (0..31) is ``VRAM_HEAD_WORDS + VRAM_TILE_COLS * y + x``. The NES-001
trace shows 64 selected bytes (32 words) on every visible line. The original
``convVram()`` loops in batches of 34 words but advances one flat source
index throughout, so those batches are not physical scanline strides. Its
first 7,680 converted words are simply the row-major 256x240 canvas.

Bit packing
-----------
Each word packs one 8-pixel run of 2-bit palette indices exactly as
``convVram()``'s inner loop does: starting from ``dt = 0``, for each pixel
(left to right) ``dt = (dt << 1) | conv_tbl[c]`` with
``conv_tbl = (0x0000, 0x0001, 0x0100, 0x0101)``. After 8 iterations the low
byte holds bitplane 0 and the high byte holds bitplane 1, MSB (bit 7) first
-- i.e. the leftmost pixel of the run lands in bit 7 of both bytes.

Mailbox
-------
``rp_system::ppu_dma()`` copies the outgoing mailbox to byte offset
``PPU_COUNT_VAL - FC_COM_BUF_SIZE`` = 15426 (``VRAM_MAILBOX_OFF_V1`` /
``VRAM_MAILBOX_OFF_V2``, both 15426 -- the mailbox tail sits at the same
byte position regardless of protocol version, only its length changes) of
the same buffer. The visible picture ends at byte 15,422, leaving four DMA
bytes before the mailbox at 15,426; the mailbox does not overwrite picture
data.

Attribute table
----------------
The 64-byte attribute table packs a 2-bit sub-palette choice per 16x16-pixel
block, four blocks (a 2x2 quadrant) per byte, exactly as
``rp_system::setAtr()``: byte index ``(tx >> 2) + ((ty >> 2) << 3)`` for tile
coordinates ``tx``, ``ty`` (0..31, 0..29), quadrant
``((tx >> 1) & 1) + (((ty >> 1) & 1) << 1)`` selecting a 2-bit field at shift
0/2/4/6.
"""

from __future__ import annotations

import numpy as np

from fcpico import protocol

__all__ = [
    "word_index",
    "pack_run",
    "unpack_run",
    "encode_frame",
    "decode_frame",
    "attr_index",
    "attr_shift",
    "attr_get",
    "attr_set",
]

# Re-exported for readability below; fcbus/fcbus_protocol.h remains the
# single source of truth (see tools/gen_protocol.py).
VRAM_HEAD_WORDS = protocol.VRAM_HEAD_WORDS  # 31
VRAM_LINE_WORDS = protocol.VRAM_LINE_WORDS  # 34-word converter batch, not a bus stride
VRAM_LINES = protocol.VRAM_LINES  # 240
VRAM_TILE_COLS = protocol.VRAM_TILE_COLS  # 32 visible tiles/line
VRAM_WIDTH = VRAM_TILE_COLS * 8  # 256 visible pixels/line
VRAM_MAILBOX_OFF = protocol.VRAM_MAILBOX_OFF_V1  # 15426; == VRAM_MAILBOX_OFF_V2

_CONV_TBL = (0x0000, 0x0001, 0x0100, 0x0101)  # convVram()'s conv_tbl, indexed by 2-bit colour

_ATTR_MASK_TBL = (0b11111100, 0b11110011, 0b11001111, 0b00111111)
_ATTR_SET_TBL = (0b00000001, 0b00000100, 0b00010000, 0b01000000)


def word_index(y: int, x: int) -> int:
    """Word index of scanline ``y`` (0..239), tile column ``x`` (0..31).

    ``31 + 32*y + x`` -- the measured selected-read stream, also
    ``fcbus_stream_word(line, tile)`` on the cartridge.
    """
    if not (0 <= y < VRAM_LINES):
        raise ValueError(f"y must be 0..{VRAM_LINES - 1}, got {y}")
    if not (0 <= x < VRAM_TILE_COLS):
        raise ValueError(f"x must be 0..{VRAM_TILE_COLS - 1}, got {x}")
    return VRAM_HEAD_WORDS + VRAM_TILE_COLS * y + x


def pack_run(pixels8) -> int:
    """Pack 8 left-to-right 2-bit colours into one 16-bit stream word.

    Bit-for-bit what ``convVram()``'s inner loop computes: the leftmost
    pixel ends up in bit 7 of both the low byte (bitplane 0) and the high
    byte (bitplane 1).
    """
    pixels8 = list(pixels8)
    if len(pixels8) != 8:
        raise ValueError(f"pack_run needs exactly 8 pixels, got {len(pixels8)}")
    dt = 0
    for c in pixels8:
        dt = ((dt << 1) | _CONV_TBL[c & 3]) & 0xFFFF
    return dt


def unpack_run(word: int):
    """Inverse of :func:`pack_run`: 16-bit word -> list of 8 2-bit colours, left to right."""
    lo = word & 0xFF
    hi = (word >> 8) & 0xFF
    return [((lo >> (7 - i)) & 1) | (((hi >> (7 - i)) & 1) << 1) for i in range(8)]


def _buf_layout(mailbox_len: int):
    """(total buffer bytes, mailbox byte offset) for a v1/v2/v3 mailbox."""
    if mailbox_len == protocol.FC_COM_BUF_SIZE_V1:
        return protocol.VRAM_BUF_BYTES_V1, protocol.VRAM_MAILBOX_OFF_V1
    if mailbox_len == protocol.FC_COM_BUF_SIZE_V2:
        return protocol.VRAM_BUF_BYTES_V2, protocol.VRAM_MAILBOX_OFF_V2
    if mailbox_len == protocol.FC_COM_BUF_SIZE_V3:
        return protocol.VRAM_BUF_BYTES_V3, protocol.VRAM_MAILBOX_OFF_V3
    raise ValueError(
        f"mailbox must be {protocol.FC_COM_BUF_SIZE_V1} (v1) or "
        f"{protocol.FC_COM_BUF_SIZE_V2} (v2) or "
        f"{protocol.FC_COM_BUF_SIZE_V3} (v3) bytes, got {mailbox_len}"
    )


def encode_frame(pix256x240, mailbox: bytes, *, native_text: bool = False) -> bytearray:
    """Encode a 256x240 2-bit-per-pixel frame into an fcbus stream buffer.

    ``pix256x240`` is indexed ``[y][x]`` (row-major, matching ``numpy``'s
    default), values 0..3; anything ``numpy.asarray`` accepts works. The
    buffer length is picked from ``len(mailbox)`` (64, 128 or 144); the mailbox is spliced in at byte
    :data:`VRAM_MAILBOX_OFF` (15426), verbatim, after the picture words are
    written.
    """
    pix = np.asarray(pix256x240, dtype=np.uint8) & 0x03
    if pix.shape != (VRAM_LINES, VRAM_WIDTH):
        raise ValueError(f"pix256x240 must have shape ({VRAM_LINES}, {VRAM_WIDTH}), got {pix.shape}")
    mailbox = bytes(mailbox)
    buf_bytes, mailbox_off = _buf_layout(len(mailbox))
    if native_text and len(mailbox) != protocol.FC_COM_BUF_SIZE_V4:
        raise ValueError("native text needs the 144-byte v4 mailbox")

    # One flat canvas, row-major: hardware consumes exactly 32 words per line.
    flat = pix.reshape(-1)

    starts = (
        np.arange(VRAM_LINES, dtype=np.int64)[:, None] * VRAM_WIDTH
        + np.arange(VRAM_TILE_COLS, dtype=np.int64)[None, :] * 8
    )  # (240, 32): flat-canvas index of each tile run's first pixel
    runs = flat[starts[:, :, None] + np.arange(8)]  # (240, 32, 8), values 0..3

    shifts = np.arange(7, -1, -1, dtype=np.uint32)  # bit 7 (leftmost pixel) .. bit 0
    plane0 = ((runs.astype(np.uint32) & 1) << shifts).sum(axis=-1)
    plane1 = (((runs.astype(np.uint32) >> 1) & 1) << shifts).sum(axis=-1)
    words = (plane0 | (plane1 << 8)).astype("<u2")  # (240, 32)

    n_words = buf_bytes // 2
    word_arr = np.zeros(n_words, dtype="<u2")
    first = word_index(0, 0)
    word_arr[first : first + words.size] = words.reshape(-1)

    if native_text:
        picture_words = protocol.PPU_PICTURE_COUNT // 2
        keep = np.ones(picture_words, dtype=bool)
        for row in range(protocol.NATIVE_TEXT_ROW * 8,
                         (protocol.NATIVE_TEXT_ROW + 1) * 8):
            start = first + row * VRAM_TILE_COLS + protocol.NATIVE_TEXT_COL
            keep[start:start + protocol.NATIVE_TEXT_TILES] = False
        compact = word_arr[:picture_words][keep]
        assert compact.nbytes == protocol.PPU_PICTURE_COUNT_V4
        word_arr[:len(compact)] = compact
        word_arr[len(compact):] = 0
        mailbox_off = protocol.VRAM_MAILBOX_OFF_V4
    buf = bytearray(word_arr.tobytes())
    buf[mailbox_off : mailbox_off + len(mailbox)] = mailbox
    return buf


def expand_native_text_stream(buf: bytes) -> bytearray:
    """Restore skipped row words as zeroes for the BG-only image decoder."""
    if len(buf) != protocol.VRAM_BUF_BYTES_V4:
        raise ValueError("v4 stream length required")
    picture_words = protocol.PPU_PICTURE_COUNT // 2
    keep = np.ones(picture_words, dtype=bool)
    for row in range(protocol.NATIVE_TEXT_ROW * 8,
                     (protocol.NATIVE_TEXT_ROW + 1) * 8):
        start = VRAM_HEAD_WORDS + row * VRAM_TILE_COLS + protocol.NATIVE_TEXT_COL
        keep[start:start + protocol.NATIVE_TEXT_TILES] = False
    expanded = np.zeros(protocol.VRAM_BUF_BYTES_V3 // 2, dtype="<u2")
    expanded[:picture_words][keep] = np.frombuffer(
        buf, dtype="<u2", count=protocol.PPU_PICTURE_COUNT_V4 // 2)
    result = bytearray(expanded.tobytes())
    result[protocol.VRAM_MAILBOX_OFF_V3:
           protocol.VRAM_MAILBOX_OFF_V3 + protocol.FC_COM_BUF_SIZE_V3] = \
        buf[protocol.VRAM_MAILBOX_OFF_V4:
            protocol.VRAM_MAILBOX_OFF_V4 + protocol.FC_COM_BUF_SIZE_V4]
    return result


def decode_frame(buf) -> np.ndarray:
    """Decode an fcbus stream buffer back to a 256x240 array of 2-bit colours.

    Works for either buffer length as long as it is at least long enough to
    hold all picture words.
    """
    buf = bytes(buf)
    n_words_needed = word_index(VRAM_LINES - 1, VRAM_TILE_COLS - 1) + 1
    min_bytes = n_words_needed * 2
    if len(buf) < min_bytes:
        raise ValueError(f"buffer too short: need at least {min_bytes} bytes, got {len(buf)}")

    word_arr = np.frombuffer(buf, dtype="<u2", count=n_words_needed)
    first = word_index(0, 0)
    visible = word_arr[first:].reshape(VRAM_LINES, VRAM_TILE_COLS).astype(np.uint32)

    lo = visible & 0xFF
    hi = (visible >> 8) & 0xFF
    shifts = np.arange(7, -1, -1, dtype=np.uint32)
    p0 = (lo[:, :, None] >> shifts) & 1
    p1 = (hi[:, :, None] >> shifts) & 1
    pixels = (p0 | (p1 << 1)).astype(np.uint8)  # (240, 32, 8): colour per pixel
    return pixels.reshape(VRAM_LINES, VRAM_WIDTH)


def attr_index(tx: int, ty: int) -> int:
    """Attribute-table byte index for tile coordinates ``tx`` (0..31), ``ty`` (0..29)."""
    return ((tx >> 2) + ((ty >> 2) << 3)) & 0x3F


def attr_shift(tx: int, ty: int) -> int:
    """Bit shift (0, 2, 4 or 6) of the 2-bit field for tile coordinates ``tx``, ``ty``."""
    quadrant = ((tx >> 1) & 1) + (((ty >> 1) & 1) << 1)
    return quadrant * 2


def attr_get(attr64, px: int, py: int) -> int:
    """Sub-palette (0..3) selected for pixel ``(px, py)`` by a 64-byte attribute table."""
    tx, ty = px >> 3, py >> 3
    idx = attr_index(tx, ty)
    shift = attr_shift(tx, ty)
    return (attr64[idx] >> shift) & 0x3


def attr_set(attr64, block_x: int, block_y: int, p: int) -> None:
    """Set the sub-palette of one 16x16-pixel block (in place).

    ``block_x`` 0..15, ``block_y`` 0..14 (16x15 blocks cover 256x240
    pixels); ``p`` 0..3. Same packing as ``rp_system::setAtr()``: a block is
    two tile columns/rows wide, so this is :func:`attr_index`/
    :func:`attr_shift` evaluated at tile coordinates ``2*block_x``,
    ``2*block_y``.
    """
    idx = ((block_x >> 1) + ((block_y >> 1) << 3)) & 0x3F
    sel = (block_x & 1) + ((block_y & 1) << 1)
    attr64[idx] = (attr64[idx] & _ATTR_MASK_TBL[sel]) | (_ATTR_SET_TBL[sel] * (p & 3))
