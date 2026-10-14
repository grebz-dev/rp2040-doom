#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-3-Clause
"""ppu_decode.py -- reconstruct the picture an fcbus stream buffer produces.

Pure-Python equivalent of what the console's PPU actually draws: unpack the
stream's 2-bit-per-pixel picture (``fcpico.stream.decode_frame``), apply an
attribute table (sub-palette per 16x16 block) and a 16-byte BG palette, and
map through the NES's NTSC colour table (``fcpico.nes_palette``) to produce
a 256x240 RGB image. Used by the L2/L3 golden-frame PSNR gate and
as a human-readable "what does this stream look like" tool.
Optional OAM/CHR/sprite-palette dumps add the current ROM's 8x8 sprite layer
for independent UI reference images.

Usage:
    ppu_decode.py <stream.bin> [--attr attr.bin] [--pal pal.bin]
                  [--mailbox-v2]
                  [--oam oam.bin --sprite-chr chr.bin --sprite-pal pal.bin]
                  -o out.png

``stream.bin`` is a raw 17,344-byte (v1) or 17,408-byte (v2) buffer as
produced by ``fcpico.stream.encode_frame`` / ``fcvideo_ref.convert``.
``--attr``/``--pal`` load a standalone 64-byte attribute table / 16-byte BG
palette (``$23C0-$23FF`` / ``$3F00-$3F0F``); without them the attribute
table defaults to all zero (sub-palette 0 everywhere) and the palette to the
Phase 1 boot default, the grey ramp ``0x0F 0x00 0x10 0x30`` repeated in
every sub-palette. ``--mailbox-v2`` reads
the attribute table and palette out of the stream's own v2 mailbox instead
(bytes 64..127 and 48..63 of the 128-byte mailbox at offset 15426), each
only if its mailbox flag bit (``MBX_FLAG_ATTR_VALID`` / ``MBX_FLAG_PAL_VALID``
in mailbox byte 0) is set; an explicit ``--attr``/``--pal`` file still wins
over the mailbox for that half.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np

# So `from fcpico import ...` resolves whether this script is run directly,
# imported as a module, or loaded by path (importlib.util.spec_from_file_location).
TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from fcpico import nes_palette, protocol, stream as stream_mod  # noqa: E402

DEFAULT_PAL16 = bytes([0x0F, 0x00, 0x10, 0x30] * 4)  # Phase 1 boot default: a grey ramp everywhere


class PpuDecodeError(Exception):
    """Raised for a malformed stream buffer, attribute table or palette."""


def decode_to_rgb(stream: bytes, attr: bytes | None = None, pal: bytes | None = None,
                  rgb_palette: np.ndarray | None = None) -> np.ndarray:
    """Decode a stream buffer to a ``(240, 256, 3)`` ``uint8`` RGB array.

    ``attr`` (64 bytes, ``$23C0-$23FF``) defaults to all zero. ``pal`` (16
    bytes, ``$3F00-$3F0F``) defaults to :data:`DEFAULT_PAL16`. Pixel value 0
    always shows the shared backdrop ``pal[0]``, regardless of the block's
    sub-palette (matches the PPU: BG colour 0 of every sub-palette is the
    same backdrop cell).
    """
    pix = stream_mod.decode_frame(stream).astype(np.int64)  # (240, 256), values 0..3

    if attr is None:
        attr = bytes(protocol.MBX_ATTR_LEN)
    if len(attr) != protocol.MBX_ATTR_LEN:
        raise PpuDecodeError(f"attr must be {protocol.MBX_ATTR_LEN} bytes, got {len(attr)}")
    if pal is None:
        pal = DEFAULT_PAL16
    if len(pal) != protocol.MBX_PAL_LEN:
        raise PpuDecodeError(f"pal must be {protocol.MBX_PAL_LEN} bytes, got {len(pal)}")

    attr_arr = np.frombuffer(bytes(attr), dtype=np.uint8)
    pal_arr = np.frombuffer(bytes(pal), dtype=np.uint8).astype(np.int64)

    ys, xs = np.indices(pix.shape)
    tx, ty = xs >> 3, ys >> 3
    # attr_index()/attr_shift() are plain bit ops, so they work unchanged on
    # numpy arrays -- reuse them instead of re-deriving the formula here.
    byte_idx = stream_mod.attr_index(tx, ty)
    shift = stream_mod.attr_shift(tx, ty)
    sub_pal = (attr_arr[byte_idx] >> shift) & 0x3  # (240, 256), 0..3

    pal_index = np.where(pix == 0, 0, sub_pal.astype(np.int64) * 4 + pix)
    nes_index = pal_arr[pal_index]  # (240, 256), 0..63
    palette_rgb = nes_palette.NES_PALETTE_RGB_ARRAY if rgb_palette is None else np.asarray(rgb_palette)
    if palette_rgb.shape != (64, 3):
        raise PpuDecodeError("RGB calibration must contain 64 RGB colors")
    return palette_rgb[nes_index]  # (240, 256, 3)


def decode_with_sprites(
    stream: bytes, oam: bytes, sprite_chr: bytes, sprite_pal: bytes,
    attr: bytes | None = None, pal: bytes | None = None,
    rgb_palette: np.ndarray | None = None,
) -> np.ndarray:
    """Composite 8x8 NES sprites over a decoded background frame.

    ``sprite_chr`` is the 4 KiB pattern table selected by PPUCTRL=$88.
    This reference models the current ROM's enabled left-edge rendering and
    hardware eight-sprites-per-scanline limit. OAM Y is one less than the first
    visible scanline. The first eight in-range entries win evaluation even if
    their pixels are transparent or off the right edge.
    """
    if len(oam) != 256:
        raise PpuDecodeError(f"OAM must be 256 bytes, got {len(oam)}")
    if len(sprite_chr) != 4096:
        raise PpuDecodeError(f"sprite CHR must be 4096 bytes, got {len(sprite_chr)}")
    if len(sprite_pal) != 16:
        raise PpuDecodeError(f"sprite palette must be 16 bytes, got {len(sprite_pal)}")

    rgb = decode_to_rgb(stream, attr, pal, rgb_palette).copy()
    bg = stream_mod.decode_frame(stream)
    palette = nes_palette.NES_PALETTE_RGB_ARRAY if rgb_palette is None else np.asarray(rgb_palette)
    for y in range(240):
        selected = 0
        claimed = np.zeros(256, dtype=bool)
        # Selection precedes pixel transparency, priority and clipping.
        for offset in range(0, 256, 4):
            top = oam[offset] + 1
            if top > y or y >= top + 8:
                continue
            selected += 1
            if selected > 8:
                break
            tile, flags, x = oam[offset + 1:offset + 4]
            row = y - top
            if flags & 0x80:
                row = 7 - row
            base = tile * 16
            low = sprite_chr[base + row]
            high = sprite_chr[base + row + 8]
            for dx in range(8):
                px = x + dx
                if px >= 256:
                    break
                bit = dx if flags & 0x40 else 7 - dx
                color = ((low >> bit) & 1) | (((high >> bit) & 1) << 1)
                if color == 0:
                    continue
                # Earlier OAM entries win even if the later sprite is in front
                # of the background and the earlier one is behind it.
                if claimed[px]:
                    continue
                claimed[px] = True
                if flags & 0x20 and bg[y, px] != 0:
                    continue
                rgb[y, px] = palette[sprite_pal[(flags & 3) * 4 + color] & 0x3F]
    return rgb


def _mailbox_from_stream(buf: bytes):
    """(attr_or_None, pal_or_None) from a v2 stream's own mailbox, per its flags."""
    if len(buf) not in (protocol.VRAM_BUF_BYTES_V2, protocol.VRAM_BUF_BYTES_V3):
        raise PpuDecodeError(
            f"--mailbox-v2 needs a v2/v3 stream, got {len(buf)} bytes"
        )
    off = protocol.VRAM_MAILBOX_OFF_V2
    mailbox = buf[off : off + protocol.FC_COM_BUF_SIZE_V2]
    flags = mailbox[protocol.MBX_FLAGS]
    attr = None
    pal = None
    if flags & protocol.MBX_FLAG_ATTR_VALID:
        attr = mailbox[protocol.MBX_ATTR : protocol.MBX_ATTR + protocol.MBX_ATTR_LEN]
    if flags & protocol.MBX_FLAG_PAL_VALID:
        pal = mailbox[protocol.MBX_PAL : protocol.MBX_PAL + protocol.MBX_PAL_LEN]
    return attr, pal


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Decode an fcbus stream buffer to a PNG image."
    )
    parser.add_argument("stream", help="path to the raw stream buffer (17344 or 17408 bytes)")
    parser.add_argument("--attr", metavar="FILE", help="64-byte attribute table ($23C0-$23FF)")
    parser.add_argument("--pal", metavar="FILE", help="16-byte BG palette ($3F00-$3F0F)")
    parser.add_argument("--oam", metavar="FILE", help="256-byte OAM dump for sprite compositing")
    parser.add_argument("--sprite-chr", metavar="FILE", help="4096-byte sprite pattern table ($1000-$1FFF)")
    parser.add_argument("--sprite-pal", metavar="FILE", help="16-byte sprite palette ($3F10-$3F1F)")
    parser.add_argument(
        "--mailbox-v2",
        action="store_true",
        help="take attributes/palette from the stream's own v2 mailbox where its flags say they are valid",
    )
    parser.add_argument("-o", "--output", required=True, metavar="FILE", help="output PNG path")
    args = parser.parse_args(argv)

    try:
        buf = Path(args.stream).read_bytes()

        attr = pal = None
        if args.mailbox_v2:
            attr, pal = _mailbox_from_stream(buf)
        if args.attr is not None:
            attr = Path(args.attr).read_bytes()
        if args.pal is not None:
            pal = Path(args.pal).read_bytes()

        sprite_files = (args.oam, args.sprite_chr, args.sprite_pal)
        if any(sprite_files) and not all(sprite_files):
            raise PpuDecodeError("--oam, --sprite-chr and --sprite-pal must be supplied together")
        if all(sprite_files):
            rgb = decode_with_sprites(
                buf, Path(args.oam).read_bytes(), Path(args.sprite_chr).read_bytes(),
                Path(args.sprite_pal).read_bytes(), attr, pal,
            )
        else:
            rgb = decode_to_rgb(buf, attr, pal)
    except (OSError, PpuDecodeError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    from PIL import Image

    Image.fromarray(rgb, mode="RGB").save(args.output)
    print(f"wrote {args.output} ({rgb.shape[1]}x{rgb.shape[0]})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
