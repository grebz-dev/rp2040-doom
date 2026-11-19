#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-3-Clause
"""Choose the three world background palettes for each map of an IWAD.

The converter dithers every 16x16 block of the picture with one of four NES
sub-palettes: the native HUD palette (00 30 16, sub-palette 2) and three world
palettes. A fixed world set suits no map well, so this tool picks the three per
map from what the map is built of:

* every wall texture (by side area) and floor/ceiling flat (by sector area) the
  map uses, lit through the WAD's COLORMAP at its sector light level at three
  viewing distances, and sky at full brightness;
* cut into 16x16 tiles, which stand in for the picture's 16x16 blocks;
* scored with the converter's own per-colour dither error, and searched exactly
  like a picture: each tile takes its cheapest palette (or the HUD one), and a
  greedy pass plus two refinement passes minimise the total.

The output is a C header the firmware switches to when a level starts.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import struct

import numpy as np

import fcvideo_ref as ref
from build_world_sprite_art import decode_patch

BACKDROP = 0x0F
HUD = (0x00, 0x30, 0x16)
FIXED = ((0x1D, 0x00, 0x30), (0x08, 0x18, 0x30), (0x09, 0x19, 0x30))
WALL_SHARE = 0.55          # of the picture, against floors, ceilings and sky
DISTANCE_STEPS = (12, 6, 0)  # colormap rows brighter than the sector's far light
TILE = 16


def library() -> list[tuple[int, int, int]]:
    """Candidate world palettes: hue ramps over the NES columns, excluding the
    olive and cyan columns except where the fixed set already uses them."""
    lib = set(FIXED)
    for h in range(13):
        lib |= {(h, 0x10 + h, 0x20 + h), (h, 0x10 + h, 0x30), (0x10 + h, 0x20 + h, 0x30),
                (h, 0x10 + h, 0x20 + h if h else 0x3D), (0x2D, 0x10 + h, 0x20 + h)}
    return sorted(p for p in lib if p in FIXED or not any((c & 0x0F) in (0x08, 0x0C) for c in p))


def error_table(playpal: np.ndarray, palettes: list[tuple[int, int, int]]) -> np.ndarray:
    """Per-colour dither error (the converter's) of each palette: (len, 256)."""
    rows = []
    for i in range(0, len(palettes), 4):
        group = list(palettes[i:i + 4])
        group += [group[0]] * (4 - len(group))
        err, _ = ref.build_err_and_lut(playpal, group, BACKDROP, shadow_lift=True)
        rows += list(err[:len(palettes[i:i + 4])])
    return np.array(rows, np.int64)


def best_set(costs: np.ndarray, weights: np.ndarray, hud: np.ndarray) -> list[int]:
    """Three palette columns of ``costs`` minimising the weighted sum of each
    tile's cheapest cost, with ``hud`` always available."""
    k = costs.shape[1]

    def total(cols, extra=None):
        base = np.minimum.reduce([costs[:, c] for c in cols] + [hud])
        if extra is not None:
            base = np.minimum(base, costs[:, extra])
        return float((base * weights).sum())

    chosen: list[int] = []
    for _ in range(3):
        chosen.append(min((total(chosen, c), c) for c in range(k) if c not in chosen)[1])
    for _ in range(2):
        for slot in range(3):
            others = [c for i, c in enumerate(chosen) if i != slot]
            chosen[slot] = min((total(others, c), c) for c in range(k) if c not in others)[1]
    return sorted(chosen)


class Wad:
    def __init__(self, data: bytes):
        _, count, offset = struct.unpack_from("<4sii", data)
        self.data = data
        self.lumps = []
        for i in range(count):
            pos, size, raw = struct.unpack_from("<ii8s", data, offset + 16 * i)
            self.lumps.append((raw.split(b"\0")[0].decode("ascii", "replace").upper(), pos, size))
        self.index = {name: i for i, (name, _, _) in enumerate(self.lumps)}

    def lump(self, i: int) -> bytes:
        _, pos, size = self.lumps[i]
        return self.data[pos:pos + size]

    def named(self, name: str) -> bytes:
        return self.lump(self.index[name])

    def maps(self) -> dict[str, dict[str, bytes]]:
        out = {}
        for i, (name, _, _) in enumerate(self.lumps):
            if i + 1 < len(self.lumps) and self.lumps[i + 1][0] == "THINGS":
                out[name] = {self.lumps[j][0]: self.lump(j) for j in range(i + 1, min(i + 11, len(self.lumps)))}
        return out

    def flats(self) -> dict[str, np.ndarray]:
        out, inside = {}, False
        for i, (name, _, size) in enumerate(self.lumps):
            if name in ("F_START", "FF_START"):
                inside = True
            elif name in ("F_END", "FF_END"):
                inside = False
            elif inside and size == 4096:
                out[name] = np.frombuffer(self.lump(i), np.uint8).reshape(64, 64).astype(np.int16)
        return out

    def textures(self) -> dict[str, tuple[int, int, list]]:
        pnames = self.named("PNAMES")
        names = [pnames[4 + 8 * i:12 + 8 * i].split(b"\0")[0].decode().upper()
                 for i in range(struct.unpack_from("<i", pnames)[0])]
        out = {}
        for lump in ("TEXTURE1", "TEXTURE2"):
            if lump not in self.index:
                continue
            data = self.named(lump)
            for off in struct.unpack_from(f"<{struct.unpack_from('<i', data)[0]}i", data, 4):
                raw, _, width, height, _, count = struct.unpack_from("<8sihhih", data, off)
                patches = [struct.unpack_from("<hhh", data, off + 22 + 10 * p) for p in range(count)]
                out[raw.split(b"\0")[0].decode().upper()] = (width, height,
                                                             [(x, y, names[p]) for x, y, p in patches])
        return out


def compose(wad: Wad, texture: tuple[int, int, list], cache: dict) -> np.ndarray:
    width, height, patches = texture
    canvas = np.full((height, width), -1, np.int16)
    for x0, y0, name in patches:
        if name not in wad.index:
            continue
        if name not in cache:
            cache[name] = decode_patch(wad.named(name))[0]
        pix = cache[name]
        ys, xs = slice(max(0, y0), min(height, y0 + pix.shape[0])), slice(max(0, x0), min(width, x0 + pix.shape[1]))
        src = pix[ys.start - y0:ys.stop - y0, xs.start - x0:xs.stop - x0]
        dst = canvas[ys, xs]
        canvas[ys, xs] = np.where(src >= 0, src, dst)
    return canvas


def tile_histograms(image: np.ndarray) -> np.ndarray:
    """(tiles, 256) colour counts of an image's 16x16 tiles (transparent ignored)."""
    h, w = image.shape
    rows = []
    for y in range(0, h - h % TILE or h, TILE):
        for x in range(0, w - w % TILE or w, TILE):
            tile = image[y:y + TILE, x:x + TILE]
            tile = tile[tile >= 0]
            if tile.size:
                rows.append(np.bincount(tile, minlength=256))
    return np.array(rows, np.float64).reshape(-1, 256)


def light_rows(light: int) -> list[int]:
    """COLORMAP rows a surface of this sector light is drawn with, near to far."""
    far = (15 - min(15, max(0, light >> 4))) * 4
    return [min(31, max(0, far - step)) for step in DISTANCE_STEPS]


def map_surfaces(lumps: dict[str, bytes]) -> tuple[dict, dict]:
    """(texture, light) -> wall area and (flat, light) -> floor/ceiling area."""
    verts = np.frombuffer(lumps["VERTEXES"], "<i2").reshape(-1, 2).astype(np.float64)
    sides = [struct.unpack_from("<hh8s8s8sh", lumps["SIDEDEFS"], o) for o in range(0, len(lumps["SIDEDEFS"]), 30)]
    sectors = [struct.unpack_from("<hh8s8shhh", lumps["SECTORS"], o) for o in range(0, len(lumps["SECTORS"]), 26)]
    name = lambda raw: raw.split(b"\0")[0].decode().upper()
    walls: dict = {}
    area = np.zeros(len(sectors))
    for o in range(0, len(lumps["LINEDEFS"]), 14):
        v1, v2, _, _, _, right, left = struct.unpack_from("<hhhhhHH", lumps["LINEDEFS"], o)
        a, b = verts[v1], verts[v2]
        length = float(np.hypot(*(b - a)))
        cross = a[0] * b[1] - a[1] * b[0]
        front = sides[right] if right != 0xFFFF else None
        back = sides[left] if left != 0xFFFF else None
        if front:
            area[front[5]] += cross
        if back:
            area[back[5]] -= cross
        for side, other in ((front, back), (back, front)):
            if side is None:
                continue
            sec = sectors[side[5]]
            spans = []
            if other is None:
                spans.append((side[4], sec[1] - sec[0]))
            else:
                osec = sectors[other[5]]
                sky = name(sec[3]) == "F_SKY1" and name(osec[3]) == "F_SKY1"
                if not sky:
                    spans.append((side[2], sec[1] - osec[1]))
                spans.append((side[3], osec[0] - sec[0]))
            for raw, height in spans:
                tex = name(raw)
                if tex != "-" and height > 0:
                    walls[(tex, sec[4])] = walls.get((tex, sec[4]), 0.0) + length * height
    flats: dict = {}
    for s, sec in enumerate(sectors):
        size = abs(area[s]) / 2
        for pic in (name(sec[2]), name(sec[3])):
            flats[(pic, sec[4])] = flats.get((pic, sec[4]), 0.0) + size
    return walls, flats


def map_tiles(wad: Wad, lumps: dict, textures: dict, flats: dict, colormap: np.ndarray,
              sky: str, patch_cache: dict, image_cache: dict) -> tuple[np.ndarray, np.ndarray]:
    """Lit colour histograms of a map's tiles and their picture weights."""
    walls, floors = map_surfaces(lumps)
    hists, weights = [], []

    def add(image, use, share_total, share):
        base = tile_histograms(image)
        if not len(base):
            return
        hists.append(base)
        weights.append(np.full(len(base), share * use / share_total / len(base)))

    def lit(image, rows):
        for row in rows:
            yield np.where(image >= 0, colormap[row][np.clip(image, 0, 255)].astype(np.int16), -1)

    def image(kind, key):
        if (kind, key) not in image_cache:
            image_cache[(kind, key)] = (compose(wad, textures[key], patch_cache) if kind == "t"
                                        else flats.get(key))
        return image_cache[(kind, key)]

    wall_total = sum(walls.values()) or 1.0
    for (tex, light), use in walls.items():
        if tex in textures:
            for img in lit(image("t", tex), light_rows(light)):
                add(img, use / 3, wall_total, WALL_SHARE)
    floor_total = sum(floors.values()) or 1.0
    for (pic, light), use in floors.items():
        if pic == "F_SKY1":
            if sky in textures:
                add(image("t", sky), use, floor_total, 1 - WALL_SHARE)
        elif image("f", pic) is not None:
            for img in lit(image("f", pic), light_rows(light)):
                add(img, use / 3, floor_total, 1 - WALL_SHARE)
    return np.vstack(hists), np.concatenate(weights)


def sky_texture(map_name: str, gamemode_doom2: bool) -> str:
    if not gamemode_doom2 and len(map_name) == 4 and map_name[0] == "E":
        return f"SKY{map_name[1]}"
    number = int(map_name[3:]) if map_name.startswith("MAP") else 1
    return "SKY1" if number < 12 else "SKY2" if number < 21 else "SKY3"


def choose(wad_bytes: bytes) -> dict[str, list[tuple[int, int, int]]]:
    wad = Wad(wad_bytes)
    playpal = np.frombuffer(wad.named("PLAYPAL")[:768], np.uint8).reshape(256, 3)
    colormap = np.frombuffer(wad.named("COLORMAP")[:32 * 256], np.uint8).reshape(32, 256)
    lib = library()
    err = error_table(playpal, lib + [HUD])
    textures, flats = wad.textures(), wad.flats()
    maps = wad.maps()
    doom2 = any(m.startswith("MAP") for m in maps)
    patch_cache: dict = {}
    image_cache: dict = {}
    out = {}
    for name, lumps in maps.items():
        hists, weights = map_tiles(wad, lumps, textures, flats, colormap,
                                   sky_texture(name, doom2), patch_cache, image_cache)
        costs = hists @ err.T.astype(np.float64)
        out[name] = [lib[c] for c in best_set(costs[:, :-1], weights, costs[:, -1])]
    return out


def map_key(name: str) -> tuple[int, int]:
    """(episode, map) as the engine's gameepisode/gamemap; Doom II uses episode 1."""
    if name.startswith("MAP"):
        return 1, int(name[3:])
    return int(name[1]), int(name[3:])


def header(choices: dict[str, list[tuple[int, int, int]]]) -> str:
    lines = ["/* Generated by fcpico/tools/map_palettes.py. Do not edit. */",
             "#ifndef FCPICO_MAP_PALETTES_H", "#define FCPICO_MAP_PALETTES_H", "",
             "#include \"fcvideo_presets.h\"", "",
             "static const fcvideo_map_palette_t fcpico_map_palettes[] = {"]
    for name in sorted(choices, key=map_key):
        episode, number = map_key(name)
        body = ", ".join("{" + ", ".join(f"0x{c:02x}" for c in p) + "}" for p in choices[name])
        lines.append(f"    {{{episode}, {number}, {{{body}}}}}, /* {name} */")
    lines += ["};", "", "#endif", ""]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("wad", type=Path)
    parser.add_argument("-o", "--output", type=Path, required=True, help="C header to write")
    parser.add_argument("--json", type=Path, help="also write the choices as JSON")
    args = parser.parse_args()
    choices = choose(args.wad.read_bytes())
    args.output.write_text(header(choices))
    if args.json:
        args.json.write_text(json.dumps({m: [" ".join(f"{c:02X}" for c in p) for p in v]
                                         for m, v in choices.items()}, indent=1) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
