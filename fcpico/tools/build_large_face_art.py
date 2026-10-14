#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Extract 28x32 Doom face art and pack equal/flipped 8x8 tiles."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re

import numpy as np
from PIL import Image

from build_doomguy_faces import WHDATA, Whx, patch_pixels
from build_doom_menu_art import encode_tiles
from native_status_art import FACE_NAMES

ROOT = Path(__file__).resolve().parents[2]
PALETTE = np.array(((0, 0, 0, 0), (131, 31, 31, 255),
                    (135, 87, 51, 255), (207, 131, 83, 255)), dtype=np.uint8)


def face_pixels() -> np.ndarray:
    whx = Whx(ROOT / "doom1.whx")
    lookup = np.frombuffer(whx.named("P_START"), dtype="<u2")
    labels = re.findall(r"VPATCH_NAME\(([^)]+)\)",
                        WHDATA.read_text().split("#define VPATCH_LIST \\", 1)[1]
                        .split("\n\nenum", 1)[0])
    playpal = np.frombuffer(whx.named("PLAYPAL")[:768], dtype=np.uint8).reshape(256, 3)
    faces = []
    for name in FACE_NAMES:
        source = patch_pixels(whx.lump(int(lookup[labels.index(name) + 1])))
        ys = np.rint(np.linspace(0, source.shape[0] - 1, 32)).astype(int)
        xs = np.rint(np.linspace(0, source.shape[1] - 1, 28)).astype(int)
        scaled = source[np.ix_(ys, xs)]
        indexed = np.zeros((32, 32), dtype=np.uint8)
        opaque = scaled >= 0
        colors = playpal[scaled[opaque]].astype(np.int32)
        delta = colors[:, None, :] - PALETTE[None, 1:, :3].astype(np.int32)
        indexed[:, 2:30][opaque] = np.argmin(np.sum(delta * delta, axis=2), axis=1) + 1
        faces.append(indexed)
    return np.stack(faces)


def pack(faces: np.ndarray) -> tuple[bytes, bytes, bytes]:
    tiles: list[bytes] = []
    seen: dict[bytes, int] = {}
    numbers = bytearray()
    flips = bytearray()
    for face in faces:
        for y in range(0, 32, 8):
            for x in range(0, 32, 8):
                tile = face[y:y + 8, x:x + 8]
                variants = ((tile, 0), (tile[:, ::-1], 0x40),
                            (tile[::-1], 0x80), (tile[::-1, ::-1], 0xC0))
                key, flag = min((candidate.tobytes(), flag)
                                for candidate, flag in variants)
                if key not in seen:
                    seen[key] = len(tiles)
                    tiles.append(encode_tiles(np.frombuffer(key, dtype=np.uint8).reshape(8, 8)))
                index = seen[key]
                # 128 tiles at $1800..$1fff, then free tiles at $1280 onward.
                numbers.append(0x80 + index if index < 128 else 0x28 + index - 128)
                flips.append(flag)
    return b"".join(tiles), bytes(numbers), bytes(flips)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    faces = face_pixels()
    art, numbers, flips = pack(faces)
    count = len(art) // 16
    if count > 128 + (96 - 40):
        raise ValueError(f"face atlas uses {count} tiles; only 184 are free")
    sheet = np.zeros((3 * 32, 4 * 32, 4), dtype=np.uint8)
    for i, face in enumerate(faces):
        y, x = divmod(i, 4)
        sheet[y * 32:y * 32 + 32, x * 32:x * 32 + 32] = PALETTE[face]
    Image.fromarray(sheet, "RGBA").save(args.output / "doomguy_faces_large_edit.png")
    (args.output / "doomguy_faces_large.chr").write_bytes(art)
    (args.output / "doomguy_faces_large_tiles.bin").write_bytes(numbers)
    (args.output / "doomguy_faces_large_flips.bin").write_bytes(flips)
    (args.output / "doomguy_faces_large.json").write_text(json.dumps({
        "source": "doom1.whx", "face_names": FACE_NAMES,
        "face_size": [32, 32], "visible_art_width": 28, "tile_count": count,
        "tile_placement": {"main": [128, min(count, 128)],
                           "spare": [40, max(0, count - 128)]},
        "mapping_order": "face, tile row, tile column",
    }, indent=2) + "\n")
    print(f"{len(faces)} faces, {count} unique tiles, {len(numbers)} mappings")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
