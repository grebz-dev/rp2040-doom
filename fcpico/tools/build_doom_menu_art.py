#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Derive a compact sprite Doom logo and paired-glyph episode labels."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re

import numpy as np
from PIL import Image

from build_doomguy_faces import ROOT, WHDATA, Whx

LOGO_SIZE = (64, 32)
SMALL_LOGO_SIZE = (56, 24)
MAIN_MENU_LABELS = ("NEW", "OPTIONS", "LOAD", "SAVE", "READ ME", "QUIT")
EPISODE_LINES = (
    (("KNEE DEEP IN", "THE DEAD"), ("SHORES OF HELL",), ("INFERNO",)),
)
FONT = {
    " ": ("...",) * 5,
    "A": (".#.", "#.#", "###", "#.#", "#.#"),
    "D": ("##.", "#.#", "#.#", "#.#", "##."),
    "E": ("###", "#..", "##.", "#..", "###"),
    "F": ("###", "#..", "##.", "#..", "#.."),
    "H": ("#.#", "#.#", "###", "#.#", "#.#"),
    "I": ("###", ".#.", ".#.", ".#.", "###"),
    "K": ("#.#", "#.#", "##.", "#.#", "#.#"),
    "L": ("#..", "#..", "#..", "#..", "###"),
    "N": ("#.#", "###", "###", "###", "#.#"),
    "O": ("###", "#.#", "#.#", "#.#", "###"),
    "P": ("##.", "#.#", "##.", "#..", "#.."),
    "Q": ("###", "#.#", "#.#", "###", "..#"),
    "R": ("##.", "#.#", "##.", "#.#", "#.#"),
    "S": (".##", "#..", ".#.", "..#", "##."),
    "T": ("###", ".#.", ".#.", ".#.", ".#."),
    "U": ("#.#", "#.#", "#.#", "#.#", "###"),
    "V": ("#.#", "#.#", "#.#", "#.#", ".#."),
    "W": ("#.#", "#.#", "###", "###", "#.#"),
}


def patch_pixels(patch: bytes) -> np.ndarray:
    width = patch[0] | ((patch[3] & 2) << 7)
    height = patch[1]
    if patch[3] >> 2 != 4:
        raise ValueError("M_DOOM is expected to use vp8_runs")
    palette = np.frombuffer(patch[6:6 + patch[2]], dtype=np.uint8)
    data = memoryview(patch)[6 + patch[2]:]
    pixels = np.full((height, width), -1, dtype=np.int16)
    offset = 0
    for y in range(height):
        x = 0
        while x < width:
            gap = data[offset]
            offset += 1
            if gap == 0xFF:
                break
            x += gap
            length = data[offset]
            offset += 1
            pixels[y, x:x + length] = palette[np.frombuffer(
                data[offset:offset + length], dtype=np.uint8)]
            offset += length
            x += length
    return pixels


def encode_tiles(pixels: np.ndarray) -> bytes:
    height, width = pixels.shape
    assert height % 8 == width % 8 == 0
    data = bytearray()
    for top in range(0, height, 8):
        for left in range(0, width, 8):
            tile = pixels[top:top + 8, left:left + 8]
            for plane in (0, 1):
                for row in tile:
                    data.append(sum(((int(value) >> plane) & 1) << (7 - x)
                                    for x, value in enumerate(row)))
    return bytes(data)


def logo_pixels(whx: Whx, size: tuple[int, int] = LOGO_SIZE) -> np.ndarray:
    source = WHDATA.read_text().split("#define VPATCH_LIST \\", 1)[1]
    labels = re.findall(r"VPATCH_NAME\(([^)]+)\)", source.split("\n\nenum", 1)[0])
    lookup = np.frombuffer(whx.named("P_START"), dtype="<u2")
    original = patch_pixels(whx.lump(int(lookup[labels.index("M_DOOM") + 1])))
    assert original.shape == (60, 123)
    # Both sprite layouts preserve the source patch's nearly 2:1 aspect ratio.
    # The title uses eight sprites per line; the paused menu uses seven.
    sample = np.asarray(Image.fromarray(original).resize(
        size, Image.Resampling.NEAREST))
    rgb = np.frombuffer(whx.named("PLAYPAL")[:768], dtype=np.uint8).reshape(256, 3)
    sampled_rgb = rgb[np.maximum(sample, 0)]
    red = sampled_rgb[:, :, 0].astype(np.int16)
    green = sampled_rgb[:, :, 1].astype(np.int16)
    blue = sampled_rgb[:, :, 2].astype(np.int16)
    opaque = sample >= 0
    result = np.zeros((size[1], size[0]), dtype=np.uint8)
    result[opaque] = 1  # source's dark blue edges and shadows
    result[opaque & (blue > red * 1.2) & (blue > green * 1.2) &
           (blue > 80)] = 2
    result[opaque & (red > 65) & (red > blue * 1.15)] = 3
    return result


def episode_tiles() -> tuple[bytes, dict[str, int]]:
    pairs = sorted({(line + " ")[at:at + 2]
                    for line in (line for episode in EPISODE_LINES[0] for line in episode)
                    for at in range(0, len(line), 2)})
    data = bytearray()
    for pair in pairs:
        tile = np.zeros((8, 8), dtype=np.uint8)
        for char_index, char in enumerate(pair):
            for row, source_row in enumerate((0, 1, 2, 2, 3, 4, 4)):
                pattern = FONT[char][source_row]
                for col, pixel in enumerate(pattern):
                    if pixel == "#":
                        x = char_index * 4 + col
                        tile[row, x] = 1
                        if row + 1 < 8 and x + 1 < 8:
                            tile[row + 1, x + 1] = 2
        data.extend(encode_tiles(tile))
    return bytes(data), {pair: index for index, pair in enumerate(pairs)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--whx", type=Path, default=ROOT / "doom1.whx")
    parser.add_argument("--output", type=Path, default=ROOT / "fcpico/assets")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    whx = Whx(args.whx)
    logo = logo_pixels(whx)
    small_logo = logo_pixels(whx, SMALL_LOGO_SIZE)
    pairs, lookup = episode_tiles()
    assert len(pairs) == 19 * 16
    (args.output / "doom_menu_logo.chr").write_bytes(encode_tiles(logo))
    (args.output / "doom_menu_logo_small.chr").write_bytes(encode_tiles(small_logo))
    (args.output / "doom_episode_pairs.chr").write_bytes(pairs)
    palette = np.array(((0, 0, 0, 0), (20, 24, 80, 255),
                        (80, 96, 216, 255), (232, 184, 64, 255)), dtype=np.uint8)
    preview = Image.fromarray(palette[logo], "RGBA")
    preview.resize((256, 128), Image.Resampling.NEAREST).save(
        args.output / "doom_menu_logo_preview.png")
    Image.fromarray(palette[small_logo], "RGBA").resize(
        (224, 96), Image.Resampling.NEAREST).save(
        args.output / "doom_menu_logo_small_preview.png")
    (args.output / "doom_menu_art.json").write_text(json.dumps({
        "source": str(args.whx.resolve().relative_to(ROOT)),
        "logo_source_patch": "M_DOOM", "logo_size": list(LOGO_SIZE),
        "logo_tiles": len(encode_tiles(logo)) // 16,
        "small_logo_size": list(SMALL_LOGO_SIZE),
        "small_logo_tiles": len(encode_tiles(small_logo)) // 16,
        "main_menu_labels": MAIN_MENU_LABELS,
        "episode_lines": EPISODE_LINES[0], "pair_tile_ids": lookup,
    }, indent=2) + "\n")
    print(f"logos: {len(encode_tiles(logo)) // 16}+{len(encode_tiles(small_logo)) // 16}"
          f" tiles; paired glyphs: {len(lookup)} tiles")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
