#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-3-Clause
"""Extract reviewed shareware sprite families and deterministic three-color art.

Generated WAD-derived assets belong in a local output directory, not git.
The first probe scopes TROO (imp, including death) and STIM (stimpack).
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import struct

import numpy as np
from PIL import Image
from audio.wadextract import read_wad
from fcpico.nes_palette import NES_PALETTE_RGB_ARRAY

ROOT = Path(__file__).resolve().parents[2]


def decode_patch(data: bytes) -> tuple[np.ndarray, int, int]:
    """Decode classic Doom posts; -1 is transparent, including empty columns."""
    if len(data) < 8:
        raise ValueError("truncated patch header")
    width, height, left, top = struct.unpack_from('<hhhh', data)
    if not (0 < width <= 320 and 0 < height <= 200) or len(data) < 8 + width * 4:
        raise ValueError("unsupported patch dimensions or truncated column table")
    pixels = np.full((height, width), -1, dtype=np.int16)
    for x in range(width):
        at = struct.unpack_from('<I', data, 8 + x * 4)[0]
        if at < 8 + width * 4 or at >= len(data):
            raise ValueError("patch column offset outside post data")
        previous_end = 0
        while True:
            if at >= len(data):
                raise ValueError("unterminated patch column")
            y = data[at]
            if y == 255:
                break
            if at + 4 > len(data):
                raise ValueError("truncated patch post")
            length = data[at + 1]
            if y < previous_end or y + length > height or at + length + 4 > len(data):
                raise ValueError("overlapping, tall-format or out-of-bounds patch post")
            pixels[y:y + length, x] = np.frombuffer(data[at + 3:at + 3 + length], np.uint8)
            previous_end = y + length
            at += length + 4
    return pixels, left, top


def quantize(pixels: np.ndarray, playpal: np.ndarray, colors: tuple[int, ...]) -> np.ndarray:
    """Nearest NES palette colors; transparent posts never use a visible index."""
    target = NES_PALETTE_RGB_ARRAY[list(colors)].astype(np.int32)
    rgb = playpal[np.maximum(pixels, 0)].astype(np.int32)
    distances = ((rgb[:, :, None] - target) ** 2).sum(axis=3)
    return np.where(pixels < 0, 0, distances.argmin(axis=2) + 1).astype(np.uint8)


def scale_nearest(pixels: np.ndarray, width: int, height: int) -> np.ndarray:
    """Independent scalar coordinate reference, with continuous sampling phase."""
    return pixels[(np.arange(height) * pixels.shape[0] // height)[:, None],
                  (np.arange(width) * pixels.shape[1] // width)[None, :]]


def pack_tile(pixels: np.ndarray) -> bytes:
    padded = np.zeros((8, 8), np.uint8)
    padded[:pixels.shape[0], :pixels.shape[1]] = pixels
    return b''.join(np.packbits((padded >> plane) & 1, axis=1).tobytes() for plane in (0, 1))


def row_recipe(pixels: np.ndarray, width: int, height: int, tile_x: int,
               tile_y: int, row: int, plane: int) -> list[tuple[int, tuple[int, ...]]]:
    """Packed source bytes and LUT bit routing for one destination tile row.

    Route slivers across output tile boundaries instead of padding each shrunk
    source sliver to eight pixels. This adapts the reference scaling-wip LUT
    idea to planar NES data and a global integer DDA phase. Each LUT is a
    256-byte byte-to-byte function, shared between planes and rows.
    """
    dy = tile_y + row
    if dy >= height:
        return []
    sy = dy * pixels.shape[0] // height
    groups: dict[int, list[int]] = {}
    for bit in range(8):
        dx = tile_x + bit
        if dx >= width:
            continue
        sx = dx * pixels.shape[1] // width
        route = groups.setdefault(sx // 8, [-1] * 8)
        route[bit] = sx % 8
    result = []
    for sliver, route in groups.items():
        value = 0
        for bit in range(8):
            sx = sliver * 8 + bit
            if sx < pixels.shape[1]:
                value |= ((int(pixels[sy, sx]) >> plane) & 1) << (7 - bit)
        result.append((value, tuple(route)))
    return result


def routing_table(route: tuple[int, ...]) -> bytes:
    return bytes(sum(((value >> (7 - src)) & 1) << (7 - dst)
                     for dst, src in enumerate(route) if src >= 0) for value in range(256))


def scale_lut(pixels: np.ndarray, width: int, height: int) -> np.ndarray:
    """Host cartridge-side LUT/DDA prototype, preserving bitplane alignment."""
    result = np.zeros(((height + 7) // 8 * 8, (width + 7) // 8 * 8), np.uint8)
    cache = {}
    for ty in range(0, height, 8):
        for tx in range(0, width, 8):
            for row in range(8):
                for plane in (0, 1):
                    value = 0
                    for source, route in row_recipe(pixels, width, height, tx, ty, row, plane):
                        if route not in cache:
                            cache[route] = routing_table(route)
                        value |= cache[route][source]
                    for bit in range(8):
                        result[ty + row, tx + bit] |= ((value >> (7 - bit)) & 1) << plane
    return result[:height, :width]


def build(wad: Path, scope: dict, output: Path) -> dict:
    # Scope v2 lists the runtime families; the review probe keeps its two.
    scope = {**scope, 'families': scope.get('probe_families', scope['families'])}
    data = wad.read_bytes()
    lumps = read_wad(data)  # Validate the entire directory; last entry wins.
    _, count, directory = struct.unpack_from('<4sii', data)
    selected = {}
    in_sprites = False
    # Retain directory indices separately; dict insertion order alone loses the
    # location of duplicate lumps and cannot supply runtime sprite identity.
    for index in range(count):
        _, _, raw = struct.unpack_from('<ii8s', data, directory + 16 * index)
        name = raw.split(b'\0')[0].decode('ascii').upper()
        if name in ('S_START', 'SS_START'):
            in_sprites = True
        elif name in ('S_END', 'SS_END'):
            in_sprites = False
        elif in_sprites and name[:4] in scope['families']:
            if not re.fullmatch(r'[A-Z0-9]{4}[A-Z][0-8]([A-Z][1-8])?', name):
                raise ValueError(f'unsupported sprite name {name}')
            selected[name] = index
    if not selected or len(lumps.get('PLAYPAL', b'')) < 768:
        raise ValueError('missing sprite namespace or PLAYPAL')
    output.mkdir(parents=True, exist_ok=True)
    playpal = np.frombuffer(lumps['PLAYPAL'][:768], np.uint8).reshape(256, 3)
    manifest = {'wad_sha256': hashlib.sha256(data).hexdigest(), 'scope': scope,
                'patches': [], 'views': {}, 'output_sha256': {}}
    previews = []
    originals = []
    generated = set()
    for name, index in selected.items():
        pixels, left, top = decode_patch(lumps[name])
        family = scope['families'][name[:4]]
        indexed = quantize(pixels, playpal, tuple(family['nes_colors']))
        rgba = np.zeros((*pixels.shape, 4), np.uint8)
        rgba[:, :, :3] = playpal[np.maximum(pixels, 0)]
        rgba[:, :, 3] = np.where(pixels < 0, 0, 255)
        Image.fromarray(rgba).save(output / f'{name}-source.png')
        np.save(output / f'{name}.npy', indexed, allow_pickle=False)
        reduced = np.zeros_like(rgba)
        reduced[:, :, :3] = NES_PALETTE_RGB_ARRAY[[0, *family['nes_colors']]][indexed]
        reduced[:, :, 3] = np.where(indexed == 0, 0, 255)
        Image.fromarray(reduced).save(output / f'{name}-indexed.png')
        previews.append((name, Image.fromarray(reduced)))
        originals.append((name, Image.fromarray(rgba)))
        generated.update((f'{name}-source.png', f'{name}-indexed.png', f'{name}.npy'))
        manifest['patches'].append({'lump': name, 'wad_directory_index': index,
            'width': pixels.shape[1], 'height': pixels.shape[0], 'left': left, 'top': top,
            'whx_identity': None, 'patch_sha256': hashlib.sha256(lumps[name]).hexdigest()})
        for offset, flip in ((4, False), (6, True)):
            if len(name) <= offset:
                continue
            frame, rotation = name[offset], int(name[offset + 1])
            views = range(1, 9) if rotation == 0 else (rotation,)
            for view in views:
                manifest['views'][f'{name[:4]}:{frame}:{view}'] = {'lump': name, 'flip': flip}
        for width in family['scale_widths']:
            height = max(1, pixels.shape[0] * width // pixels.shape[1])
            scaled = scale_nearest(indexed, width, height)
            if not np.array_equal(scaled, scale_lut(indexed, width, height)):
                raise AssertionError('LUT differs from independent scalar sampler')
            tiles = b''.join(pack_tile(scaled[y:y + 8, x:x + 8])
                             for y in range(0, height, 8) for x in range(0, width, 8))
            (output / f'{name}-{width}.chr').write_bytes(tiles)
            generated.add(f'{name}-{width}.chr')
    for family in scope['families']:
        frames = {key.split(':')[1] for key in manifest['views'] if key.startswith(family + ':')}
        if not frames:
            raise ValueError(f'missing scoped family {family}')
        for frame in frames:
            for view in range(1, 9):
                if f'{family}:{frame}:{view}' not in manifest['views']:
                    raise ValueError(f'missing view {family}:{frame}:{view}')
    from PIL import ImageDraw
    for sheet_name, collection in (('indexed-sheet.png', previews), ('source-sheet.png', originals)):
        sheet = Image.new('RGBA', (8 * 96, ((len(collection) + 7) // 8) * 112), (32, 32, 32, 255))
        draw = ImageDraw.Draw(sheet)
        for n, (name, image) in enumerate(collection):
            x, y = n % 8 * 96, n // 8 * 112
            sheet.alpha_composite(image, (x, y + 16))
            draw.text((x, y), name, fill='white')
        sheet.save(output / sheet_name)
        generated.add(sheet_name)
    for name in sorted(generated):
        manifest['output_sha256'][name] = hashlib.sha256((output / name).read_bytes()).hexdigest()
    (output / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--wad', required=True, type=Path)
    parser.add_argument('--scope', type=Path, default=ROOT / 'fcpico/assets/world-sprite-scope.json')
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    manifest = build(args.wad, json.loads(args.scope.read_text()), args.output)
    print(f"decoded {len(manifest['patches'])} patches, {len(manifest['views'])} frame/view mappings")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
