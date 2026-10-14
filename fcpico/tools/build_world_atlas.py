#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-3-Clause
"""Build the runtime world sprite atlas from a Doom IWAD.

The binary is keyed by the engine's sprite enum (parsed from info.h), frame
and rotation, so neither WAD directory order nor sheet order becomes a runtime
index. The firmware additionally checks every lookup against the engine's own
sprite width and offsets. Generated WAD-derived bytes stay in build outputs.

Colour (format v2): each family keeps a 15-colour local NES palette and 4-bit
texels. Two candidate 3-colour palettes per family come from reviewed overrides
in the scope manifest or a block-clustered exhaustive fit. At runtime the frame
loads candidates into sprite palettes 0 and 2, and every 8x8 tile picks the best
of the four loaded palettes (1 and 3 are fixed by the native HUD) using the
NES colour-distance table stored here. Source colours are chroma-boosted before
matching: Doom's desaturated browns and greens otherwise land on NES greys.
"""
from __future__ import annotations

import argparse
import hashlib
import itertools
import json
from pathlib import Path
import re
import struct

import numpy as np
from PIL import Image, ImageDraw

from audio.wadextract import read_wad
from build_world_sprite_art import decode_patch

ROOT = Path(__file__).resolve().parents[2]
MAGIC, VERSION = 0x41574346, 2
HEADER = 48
LOCAL_COLORS = 15
FAMILY_RECORD = 16 + 2 * 15 * 64
TABLE_SCALE = 300.0
FIXED = ((0x07, 0x18, 0x26), (0x12, 0x38, 0x16))   # sprite palettes 1 and 3
# $0D is "blacker than black"; $xE/$xF other than $0F duplicate black.
CANDIDATES = [c for c in range(0x40) if c != 0x0D and (c & 0x0F) < 0x0E or c == 0x0F]
# Automatic fits skip NES olive ($08/$18) and dark teal ($0C/$1C): they are
# lightness-nearest to Doom's dark browns and gun-metal greys but read as green
# or cyan. Reviewed overrides may still name them.
AUTO_CANDIDATES = [c for c in CANDIDATES if c not in (0x08, 0x18, 0x0C, 0x1C)]
MAP_LIGHTNESS = 1.0   # lightness weight when mapping into a chosen 3-colour palette
CHROMA_BOOST = 1.8
NEUTRAL_CHROMA = 0.025
HUE_WEIGHT = 3.0
SALIENCE = 2.0


# MesenCE's default 2C02 palette (Core/NES/NesDefaultVideoFilter.cpp, unchanged since
# MesenCE 20f497c9). Atlas colour fitting targets what the emulator displays, so the
# table is copied here rather than read from an emulator checkout.
MESEN_2C02_RGB = (
    0x666666, 0x002A88, 0x1412A7, 0x3B00A4, 0x5C007E, 0x6E0040, 0x6C0600, 0x561D00,
    0x333500, 0x0B4800, 0x005200, 0x004F08, 0x00404D, 0x000000, 0x000000, 0x000000,
    0xADADAD, 0x155FD9, 0x4240FF, 0x7527FE, 0xA01ACC, 0xB71E7B, 0xB53120, 0x994E00,
    0x6B6D00, 0x388700, 0x0C9300, 0x008F32, 0x007C8D, 0x000000, 0x000000, 0x000000,
    0xFFFEFF, 0x64B0FF, 0x9290FF, 0xC676FF, 0xF36AFF, 0xFE6ECC, 0xFE8170, 0xEA9E22,
    0xBCBE00, 0x88D800, 0x5CE430, 0x45E082, 0x48CDDE, 0x4F4F4F, 0x000000, 0x000000,
    0xFFFEFF, 0xC0DFFF, 0xD3D2FF, 0xE8C8FF, 0xFBC2FF, 0xFEC4EA, 0xFECCC5, 0xF7D8A5,
    0xE4E594, 0xCFEF96, 0xBDF4AB, 0xB3F3CC, 0xB5EBF2, 0xB8B8B8, 0x000000, 0x000000,
)


def mesen_palette() -> np.ndarray:
    return np.array([[(v >> 16) & 255, (v >> 8) & 255, v & 255] for v in MESEN_2C02_RGB], np.uint8)


def oklab(rgb: np.ndarray) -> np.ndarray:
    c = rgb.astype(np.float64) / 255.0
    c = np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)
    lms = c @ np.array([[0.4122214708, 0.2119034982, 0.0883024619],
                        [0.5363325363, 0.6806995451, 0.2817188376],
                        [0.0514459929, 0.1073969566, 0.6299787005]])
    lms = np.cbrt(lms)
    return lms @ np.array([[0.2104542553, 1.9779984951, 0.0259040371],
                           [0.7936177850, -2.4285922050, 0.7827717662],
                           [-0.0040720468, 0.4505937099, -0.8086757660]])


def boost_chroma(lab: np.ndarray) -> np.ndarray:
    """Raise chroma of clearly coloured texels; keep near-greys neutral.

    Doom's browns/greens are desaturated and would otherwise match NES greys,
    but a uniform boost tints greys (armour, medkits, suits) olive or orange.
    """
    chroma = np.hypot(lab[:, 1], lab[:, 2])
    ramp = np.clip((chroma - NEUTRAL_CHROMA) / NEUTRAL_CHROMA, 0.0, 1.0)
    factor = 1.0 + (CHROMA_BOOST - 1.0) * ramp
    out = lab.copy()
    out[:, 1:] *= factor[:, None]
    return out


def distances(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """OKLab distance with extra weight on hue difference between coloured pairs.

    The NES has no dark neutral between $0F and $2D; without the hue term warm
    dark browns snap to olive $08 rather than brown $07.
    """
    dl = a[:, None, 0] - b[None, :, 0]
    ca, cb = np.hypot(a[:, 1], a[:, 2])[:, None], np.hypot(b[:, 1], b[:, 2])[None, :]
    dab = ((a[:, None, 1:] - b[None, :, 1:]) ** 2).sum(axis=2)
    dh2 = np.maximum(dab - (ca - cb) ** 2, 0.0)
    return dl ** 2 + (ca - cb) ** 2 + HUE_WEIGHT * dh2


def sprite_enum(info_h: Path) -> list[str]:
    body = info_h.read_text().split('typedef enum', 1)[1]
    names = re.findall(r'\bSPR_([A-Z0-9]+)\s*,', body.split('NUMSPRITES', 1)[0])
    if not names or names[0] != 'TROO':
        raise ValueError('unexpected sprite enum in info.h')
    return names


def sprite_lumps(data: bytes) -> dict[str, int]:
    _, count, directory = struct.unpack_from('<4sii', data)
    found, inside = {}, False
    for index in range(count):
        _, _, raw = struct.unpack_from('<ii8s', data, directory + 16 * index)
        name = raw.split(b'\0')[0].decode('ascii').upper()
        if name in ('S_START', 'SS_START'):
            inside = True
        elif name in ('S_END', 'SS_END'):
            inside = False
        elif inside:
            if not re.fullmatch(r'[A-Z0-9]{4}[A-Z\[\]\\][0-8]([A-Z\[\]\\][1-8])?', name):
                raise ValueError(f'unsupported sprite lump name {name}')
            found[name] = index  # last entry wins, like the engine
    return found


def family_views(family: str, names: list[str]) -> dict[tuple[int, int], tuple[str, bool]]:
    """(frame, rotation 0..7) -> (lump, flip), following R_InitSprites rules."""
    views: dict[tuple[int, int], tuple[str, bool]] = {}
    for name in sorted(names):
        for offset, flip in ((4, False), (6, True)):
            if len(name) <= offset:
                continue
            frame, rotation = ord(name[offset]) - ord('A'), int(name[offset + 1])
            for view in (range(8) if rotation == 0 else (rotation - 1,)):
                views[(frame, view)] = (name, flip)
    frames = 1 + max(frame for frame, _ in views)
    for frame in range(frames):
        if any((frame, view) in views for view in range(8)):
            missing = [view for view in range(8) if (frame, view) not in views]
            if missing:
                raise ValueError(f'{family} frame {chr(65 + frame)} lacks rotations {missing}')
    return views


def local_palette(colors: np.ndarray, weight: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Weighted k-means of a family's source colours (boosted OKLab) into 15.

    Returns centroids and the source-colour -> local-index (0-based) map.
    Deterministic: weighted farthest-point seeding, then Lloyd iterations.
    """
    k = min(LOCAL_COLORS, len(colors))
    centers = [colors[int(weight.argmax())]]
    for _ in range(1, k):
        d = ((colors[:, None] - np.array(centers)[None]) ** 2).sum(axis=2).min(axis=1)
        centers.append(colors[int((weight * d).argmax())])
    centers = np.array(centers)
    for _ in range(20):
        owner = ((colors[:, None] - centers[None]) ** 2).sum(axis=2).argmin(axis=1)
        for i in range(k):
            if (owner == i).any():
                w = weight[owner == i]
                centers[i] = (colors[owner == i] * w[:, None]).sum(axis=0) / w.sum()
    owner = ((colors[:, None] - centers[None]) ** 2).sum(axis=2).argmin(axis=1)
    return centers, owner


def fit_candidates(blocks: np.ndarray, sel: np.ndarray) -> tuple[tuple[int, ...], tuple[int, ...]]:
    """Two palettes for 8x8 blocks, alternating assignment and exhaustive refit.

    `blocks` are weighted local-colour histograms; `sel` is local x NES error.
    """
    combos = np.array(list(itertools.combinations(AUTO_CANDIDATES, 3)))
    cost = sel[:, combos].min(axis=2)                       # local x combos

    def fit(rows: np.ndarray) -> tuple[int, ...]:
        return tuple(int(c) for c in combos[int((rows.sum(axis=0) @ cost).argmin())])

    def block_error(palette: tuple[int, ...]) -> np.ndarray:
        return blocks @ sel[:, list(palette)].min(axis=1)

    first = fit(blocks)
    second = fit(blocks[block_error(first).argsort()[-max(1, len(blocks) // 3):]])
    for _ in range(4):
        table = np.stack([block_error(p) for p in (first, second, *FIXED)], axis=1)
        owner = table.argmin(axis=1)
        if (owner == 0).any():
            first = fit(blocks[owner == 0])
        if (owner == 1).any():
            second = fit(blocks[owner == 1])
    return first, second


def pack4(indexed: np.ndarray) -> bytes:
    height, width = indexed.shape
    stride = (width + 1) // 2
    out = bytearray(stride * height)
    for y in range(height):
        for x in range(width):
            out[y * stride + x // 2] |= int(indexed[y, x]) << ((x & 1) * 4)
    return bytes(out)


def color_weights(centers: np.ndarray) -> np.ndarray:
    """Runtime tile-choice weight per local colour (1..4): accents count more."""
    chroma = np.hypot(centers[:, 1], centers[:, 2])
    return np.clip(np.rint(1 + SALIENCE * chroma / 0.1), 1, 4).astype(int)


def tile_render(block: np.ndarray, sel: np.ndarray, mapping: np.ndarray, weights: np.ndarray,
                palettes) -> tuple[int, np.ndarray]:
    """fcworld_frame_build's choice: palette with least weighted selection error,
    then each local colour mapped by the mapping table. Returns (palette, NES colours)."""
    block = block.astype(np.int64)
    mask = block > 0
    local = block[mask] - 1
    errors = [int((weights[local] * sel[local][:, list(p)].min(axis=1)).sum()) for p in palettes]
    best = int(np.argmin(errors))
    colors = np.array(palettes[best])[mapping[np.maximum(block - 1, 0)][:, :, list(palettes[best])]
                                      .argmin(axis=2)]
    return best, colors


def preview(indexed: np.ndarray, sel, mapping, weights, palettes, nes) -> np.ndarray:
    rgba = np.zeros((*indexed.shape, 4), np.uint8)
    for y in range(0, indexed.shape[0], 8):
        for x in range(0, indexed.shape[1], 8):
            block = indexed[y:y + 8, x:x + 8]
            if not (block > 0).any():
                continue
            _, colors = tile_render(block, sel, mapping, weights, palettes)
            rgba[y:y + 8, x:x + 8, :3] = nes[colors]
            rgba[y:y + 8, x:x + 8, 3] = np.where(block > 0, 255, 0)
    return rgba


def tables(centers: np.ndarray, lab_nes: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """u8 selection and mapping errors, local colour x 64 NES colours."""
    sel = distances(centers, lab_nes)
    lightness = np.array([MAP_LIGHTNESS ** 0.5, 1.0, 1.0])
    mapping = distances(centers * lightness, lab_nes * lightness)
    # Square roots keep resolution for near matches in one byte.
    return (np.clip(np.rint(np.sqrt(sel) * TABLE_SCALE), 0, 255).astype(np.uint8),
            np.clip(np.rint(np.sqrt(mapping / MAP_LIGHTNESS) * TABLE_SCALE), 0, 255)
            .astype(np.uint8))


def build(wad: Path, scope: dict, info_h: Path, output: Path,
          review: list[str] | None = None) -> dict:
    data = wad.read_bytes()
    lumps = read_wad(data)
    if len(lumps.get('PLAYPAL', b'')) < 768:
        raise ValueError('missing PLAYPAL')
    playpal = np.frombuffer(lumps['PLAYPAL'][:768], np.uint8).reshape(256, 3)
    nes = mesen_palette()
    lab_nes = oklab(nes)
    lab_src = boost_chroma(oklab(playpal))
    chroma = np.hypot(lab_src[:, 1], lab_src[:, 2])
    enum = sprite_enum(info_h)
    names = sprite_lumps(data)
    overrides = {f: [tuple(int(c, 16) for c in p) for p in v]
                 for f, v in scope.get('palette_overrides', {}).items()}
    families, missing = {}, []
    for family in scope['families']:
        members = [name for name in names if name[:4] == family]
        if family not in enum or not members:
            missing.append(family)
            continue
        families[family] = family_views(family, members)

    family_records, family_report, review_rows = [], {}, []
    patch_ids: dict[str, int] = {}
    patch_records, pixel_blobs, previews = [], [], []
    for number, (family, views) in enumerate(families.items()):
        decoded = {}
        hist = np.zeros(256)
        for lump in sorted({lump for lump, _ in views.values()}):
            pixels, left, top = decode_patch(lumps[lump])
            if pixels.shape[0] > 255 or pixels.shape[1] > 255:
                raise ValueError(f'{lump} exceeds the atlas patch size')
            decoded[lump] = (pixels, left, top)
            hist += np.bincount(pixels[pixels >= 0].ravel(), minlength=256)
        used = np.nonzero(hist)[0]
        salience = 1.0 + SALIENCE * chroma / 0.1
        weight = hist / hist.sum() * salience
        centers, owner = local_palette(lab_src[used], weight[used])
        lut = np.zeros(256, np.int64)
        lut[used] = owner + 1
        sel, mapping = tables(centers, lab_nes)
        weights = color_weights(centers)
        blocks = []
        for pixels, _, _ in decoded.values():
            index = np.where(pixels < 0, 0, lut[np.maximum(pixels, 0)])
            for y in range(0, pixels.shape[0], 8):
                for x in range(0, pixels.shape[1], 8):
                    region = pixels[y:y + 8, x:x + 8]
                    mask = region >= 0
                    if mask.any():
                        blocks.append(np.bincount(index[y:y + 8, x:x + 8][mask] - 1,
                                                  weights=salience[region[mask]],
                                                  minlength=len(centers)))
        fitted = fit_candidates(np.array(blocks), sel.astype(np.float64))
        chosen = overrides.get(family, [])
        candidates = [tuple(sorted(p, key=lambda c: lab_nes[c, 0]))
                      for p in (list(chosen) + list(fitted))[:2]]
        exclusive = family in scope.get('palette_exclusive', []) and len(chosen) >= 2
        family_records.append((candidates, scope['families'][family], exclusive, weights,
                               sel, mapping, len(centers)))
        family_report[family] = {'sprite': enum.index(family), 'index': number,
            'candidates': [[f'{c:02X}' for c in p] for p in candidates],
            'overridden': len(chosen), 'exclusive': exclusive,
            'weights': [int(w) for w in weights], 'patches': len(decoded)}
        loaded = candidates if exclusive else [*candidates, *FIXED]
        for lump, (pixels, left, top) in decoded.items():
            indexed = np.where(pixels < 0, 0, lut[np.maximum(pixels, 0)]).astype(np.uint8)
            patch_ids[lump] = len(patch_records)
            patch_records.append((pixels.shape[1], pixels.shape[0], left, top, number))
            pixel_blobs.append(pack4(indexed))
            runtime = preview(indexed, sel, mapping, weights, loaded, nes)
            previews.append((lump, Image.fromarray(runtime)))
            if review and lump in review:
                source = np.zeros_like(runtime)
                source[..., :3] = playpal[np.maximum(pixels, 0)]
                source[..., 3] = np.where(pixels < 0, 0, 255)
                review_rows.append((lump, source, runtime, candidates))

    sprite_table = bytearray(len(enum) * 4)
    entries = bytearray()
    family_index = {family: n for n, family in enumerate(families)}
    for index, name in enumerate(enum):
        struct.pack_into('<HBB', sprite_table, index * 4, 0xFFFF, 0, 0)
        if name not in families:
            continue
        views = families[name]
        frames = 1 + max(frame for frame, _ in views)
        struct.pack_into('<HBB', sprite_table, index * 4, len(entries) // 2, frames,
                         family_index[name])
        for frame in range(frames):
            for view in range(8):
                if (frame, view) in views:
                    lump, flip = views[(frame, view)]
                    entries += struct.pack('<H', (patch_ids[lump] + 1) | (0x8000 if flip else 0))
                else:
                    entries += b'\0\0'
    family_table = bytearray()
    for candidates, role, exclusive, weights, sel, mapping, count in family_records:
        packed = bytearray(8)   # 4-bit weights for local colours 1..15
        for i, w in enumerate(weights):
            packed[(i + 1) // 2] |= (int(w) & 15) << (((i + 1) & 1) * 4)
        flags = (1 if role == 'enemy' else 0) | (2 if exclusive else 0)
        pad = np.full((LOCAL_COLORS - count, 64), 255, np.uint8)
        family_table += bytes((*candidates[0], *candidates[1], flags, count)) + bytes(packed)
        family_table += np.vstack([sel, pad]).tobytes() + np.vstack([mapping, pad]).tobytes()
    assert len(family_table) == FAMILY_RECORD * len(family_records)
    distance_table = b''

    sprite_off = HEADER
    entry_off = sprite_off + len(sprite_table)
    family_off = entry_off + len(entries)
    dist_off = family_off + len(family_table)
    patch_off = (dist_off + len(distance_table) + 3) & ~3
    pixel_off = patch_off + 12 * len(patch_records)
    body = bytearray(pixel_off)
    body[sprite_off:sprite_off + len(sprite_table)] = sprite_table
    body[entry_off:entry_off + len(entries)] = entries
    body[family_off:family_off + len(family_table)] = family_table
    body[dist_off:dist_off + len(distance_table)] = distance_table
    cursor = pixel_off
    for n, ((w, h, left, top, fam), blob) in enumerate(zip(patch_records, pixel_blobs)):
        struct.pack_into('<BBhhBBI', body, patch_off + 12 * n, w, h, left, top, fam, 0, cursor)
        cursor += len(blob)
    body += b''.join(pixel_blobs)
    struct.pack_into('<IHHIIHHIIIIII', body, 0, MAGIC, VERSION, len(enum), len(body), 0,
                     len(families), len(patch_records), sprite_off, entry_off,
                     len(entries) // 2, patch_off, family_off, dist_off)
    identity = int.from_bytes(hashlib.sha256(bytes(body)).digest()[:4], 'little') or 1
    struct.pack_into('<I', body, 12, identity)

    output.mkdir(parents=True, exist_ok=True)
    (output / 'world_atlas.bin').write_bytes(body)
    (output / 'world_atlas_identity.h').write_text(
        '/* Generated by tools/build_world_atlas.py; WAD-derived, do not commit. */\n'
        f'#define FCWORLD_ATLAS_IDENTITY 0x{identity:08X}u\n'
        f'#define FCWORLD_ATLAS_BYTES {len(body)}u\n')
    sheet = Image.new('RGBA', (12 * 72, ((len(previews) + 11) // 12) * 96), (40, 40, 40, 255))
    draw = ImageDraw.Draw(sheet)
    for n, (lump, image) in enumerate(previews):
        x, y = n % 12 * 72, n // 12 * 96
        image.thumbnail((70, 80))
        sheet.alpha_composite(image, (x, y + 14))
        draw.text((x, y), lump, fill='white')
    sheet.save(output / 'world_atlas_sheet.png')
    if review_rows:
        review_rows.sort(key=lambda row: review.index(row[0]))
        width = max(row[1].shape[1] for row in review_rows) * 3
        height = sum(row[1].shape[0] * 3 + 16 for row in review_rows)
        page = Image.new('RGBA', (width * 2 + 24, height), (40, 40, 40, 255))
        draw = ImageDraw.Draw(page)
        y = 0
        for lump, source, runtime, candidates in review_rows:
            draw.text((0, y), f"{lump}  candidates {' / '.join(' '.join(f'{c:02X}' for c in p) for p in candidates)}",
                      fill='white')
            y += 14
            for column, image in enumerate((source, runtime)):
                big = Image.fromarray(image).resize((image.shape[1] * 3, image.shape[0] * 3),
                                                    Image.NEAREST)
                page.alpha_composite(big, (column * (width + 24), y))
            y += source.shape[0] * 3 + 2
        page.save(output / 'world_atlas_review.png')
    manifest = {
        'wad_sha256': hashlib.sha256(data).hexdigest(),
        'atlas_sha256': hashlib.sha256(bytes(body)).hexdigest(),
        'identity': f'{identity:08x}', 'bytes': len(body), 'pixel_bytes': len(body) - pixel_off,
        'patches': len(patch_records), 'entries': len(entries) // 2,
        'fixed_palettes': {'1': [f'{c:02X}' for c in FIXED[0]], '3': [f'{c:02X}' for c in FIXED[1]]},
        'families': family_report, 'missing_families': missing,
    }
    (output / 'world_atlas.json').write_text(json.dumps(manifest, indent=2) + '\n')
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('wad', type=Path)
    parser.add_argument('--scope', type=Path, default=ROOT / 'fcpico/assets/world-sprite-scope.json')
    parser.add_argument('--info', type=Path, default=ROOT / 'src/doom/info.h')
    parser.add_argument('--layout', type=Path, default=ROOT / 'fcpico/port/flash_layout.h')
    parser.add_argument('-o', '--output', type=Path, required=True)
    parser.add_argument('--review', default='', help='comma-separated lumps for a 3x review sheet')
    args = parser.parse_args()
    manifest = build(args.wad, json.loads(args.scope.read_text()), args.info, args.output,
                     [n for n in args.review.split(',') if n])
    layout = dict(re.findall(r'#define\s+(FLASH_WORLD_MAX)\s+(0x[0-9A-Fa-f]+)u',
                             args.layout.read_text()))
    if manifest['bytes'] > int(layout['FLASH_WORLD_MAX'], 16):
        raise SystemExit(f"atlas {manifest['bytes']} bytes exceeds FLASH_WORLD_MAX")
    print(f"world atlas: {len(manifest['families'])} families, {manifest['patches']} patches, "
          f"{manifest['bytes']} bytes; missing {manifest['missing_families']}")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
