#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Extract Doom's face patches from WHX into 24x24, 3x3 NES sprite sheets."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import struct

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
WHDATA = ROOT / "src/whddata.h"


class Whx:
    def __init__(self, path: Path):
        self.data = path.read_bytes()
        ident, self.count, self.table = struct.unpack_from("<4sII", self.data)
        if ident != b"IWHX":
            raise ValueError("expected a tiny Doom IWHX archive")
        named = struct.unpack_from("<H", self.data, 34)[0]
        start = self.table + (self.count + 1) * 4
        self.names = {}
        for at in range(start, start + named * 12, 12):
            name = self.data[at:at + 10].split(b"\0")[0].decode("ascii")
            self.names[name] = struct.unpack_from("<H", self.data, at + 10)[0]

    def lump(self, number: int) -> bytes:
        if not 0 <= number < self.count:
            raise ValueError("bad WHX lump number")
        first, end = struct.unpack_from("<II", self.data, self.table + number * 4)
        start = first & 0xFFFFFF
        length = ((end - first) & 0xFFFFFF) - (first >> 30)
        return self.data[start:start + length]

    def named(self, name: str) -> bytes:
        return self.lump(self.names[name.lower()])


def face_names() -> list[str]:
    source = WHDATA.read_text()
    block = source.split("#define VPATCH_LIST \\", 1)[1].split("\n\nenum", 1)[0]
    names = re.findall(r"VPATCH_NAME\(([^)]+)\)", block)
    return [name for name in names if name.startswith(("STFST", "STFTR", "STFTL",
        "STFOUCH", "STFEVL", "STFKILL", "STFGOD", "STFDEAD"))]


def patch_pixels(patch: bytes) -> np.ndarray:
    width = patch[0] | ((patch[3] & 2) << 7)
    height = patch[1]
    colors = patch[2]
    kind = patch[3] >> 2
    if kind != 3 or patch[3] & 1:
        raise ValueError("face patch must be standalone vp6_runs")
    palette = patch[6:6 + colors]
    data = memoryview(patch)[6 + colors:]
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
            used = (length * 6 + 7) // 8
            packed = int.from_bytes(data[offset:offset + used], "little")
            offset += used
            for i in range(length):
                if x < width:
                    pixels[y, x] = palette[(packed >> (i * 6)) & 63]
                x += 1
    return pixels


def resize_face(pixels: np.ndarray) -> np.ndarray:
    # Original face patches are 24x29 or 24x31. Preserve all rows in 24x24.
    ys = np.rint(np.linspace(0, pixels.shape[0] - 1, 24)).astype(int)
    xs = np.rint(np.linspace(0, pixels.shape[1] - 1, 24)).astype(int)
    return pixels[np.ix_(ys, xs)]


def nes_tiles(indexed: np.ndarray) -> bytes:
    art = bytearray()
    for ty in range(3):
        for tx in range(3):
            tile = indexed[ty * 8:ty * 8 + 8, tx * 8:tx * 8 + 8]
            for plane in (0, 1):
                for row in tile:
                    art.append(sum(((int(pixel) >> plane) & 1) << (7 - x)
                                   for x, pixel in enumerate(row)))
    return bytes(art)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--whx", type=Path, default=ROOT / "doom1.whx")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    whx = Whx(args.whx)
    lookup = np.frombuffer(whx.named("P_START"), dtype="<u2")
    labels = re.findall(r"VPATCH_NAME\(([^)]+)\)",
                        WHDATA.read_text().split("#define VPATCH_LIST \\", 1)[1]
                        .split("\n\nenum", 1)[0])
    names = face_names()
    playpal = np.frombuffer(whx.named("PLAYPAL")[:768], dtype=np.uint8).reshape(256, 3)
    originals = []
    for name in names:
        handle = labels.index(name) + 1  # INVALID is handle 0.
        original = patch_pixels(whx.lump(int(lookup[handle])))
        originals.append(resize_face(original))

    # Shared three-color face palette: darkest, middle, brightest Doom colors.
    # Select medoids by luminance quantiles, then assign each opaque pixel to
    # the nearest one in RGB. All faces use one NES sprite palette.
    rgb = np.concatenate([playpal[face[face >= 0]] for face in originals])
    unique, counts = np.unique(rgb, axis=0, return_counts=True)
    luma = unique @ np.array([0.2126, 0.7152, 0.0722])
    order = np.argsort(luma)
    cumulative = np.cumsum(counts[order])
    total = cumulative[-1]
    centers = []
    for fraction in (0.15, 0.50, 0.85):
        at = np.searchsorted(cumulative, fraction * total)
        centers.append(unique[order[min(at, len(order) - 1)]])
    centers = np.array(centers, dtype=np.uint8)
    columns = 7
    rgba = np.zeros((((len(names) + columns - 1) // columns) * 24,
                     columns * 24, 4), dtype=np.uint8)
    indexed_faces = []
    for i, face in enumerate(originals):
        indexed = np.zeros((24, 24), dtype=np.uint8)
        opaque = face >= 0
        difference = playpal[face[opaque]].astype(np.int32)[:, None, :] - centers[None, :, :]
        indexed[opaque] = np.argmin(np.sum(difference * difference, axis=2), axis=1) + 1
        indexed_faces.append(indexed)
        y0, x0 = divmod(i, columns)
        cell = rgba[y0 * 24:(y0 + 1) * 24, x0 * 24:(x0 + 1) * 24]
        cell[:, :, :3][opaque] = centers[indexed[opaque] - 1]
        cell[:, :, 3][opaque] = 255
    chr_data = b"".join(nes_tiles(face) for face in indexed_faces)
    (output / "doomguy_faces.chr").write_bytes(chr_data)
    sheet = Image.fromarray(rgba, "RGBA")
    sheet.save(output / "doomguy_faces.png")
    sheet.resize((sheet.width * 4, sheet.height * 4), Image.Resampling.NEAREST).save(
        output / "doomguy_faces_preview.png")
    try:
        source_name = str(args.whx.resolve().relative_to(ROOT))
    except ValueError:
        source_name = str(args.whx)
    (output / "doomguy_faces.json").write_text(json.dumps({
        "source": source_name, "size": [24, 24], "tiles_per_face": 9,
        "columns": columns,
        "order": names, "rgb_palette": centers.tolist(),
        "chr_bytes": len(chr_data),
    }, indent=2) + "\n")
    print(f"{len(names)} faces, {len(chr_data)} CHR bytes, palette {centers.tolist()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
