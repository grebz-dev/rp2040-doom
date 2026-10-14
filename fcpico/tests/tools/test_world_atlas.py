# SPDX-License-Identifier: BSD-3-Clause
"""World sprite atlas: engine-keyed views, flips, palettes and flash packaging."""
import os
from pathlib import Path
import struct

import pytest

import build_world_atlas as atlas
from test_audio_convert import make_wad
import whx2uf2

ROOT = Path(__file__).resolve().parents[2]
INFO = ROOT.parent / 'src/doom/info.h'


def patch(width, height, left, top, colour):
    """Classic patch: one post per column; column 0 is empty (transparent)."""
    header = struct.pack('<hhhh', width, height, left, top)
    columns, offset = [], 8 + 4 * width
    for x in range(width):
        columns.append(offset)
        offset += 1 if x == 0 else height + 5
    body = b''.join(b'\xff' if x == 0 else bytes((0, height, 0)) + bytes([colour]) * height +
                    b'\0\xff' for x in range(width))
    return header + b''.join(struct.pack('<I', c) for c in columns) + body


def fixture():
    playpal = bytearray(768)
    playpal[3 * 1:3 * 2] = bytes((0, 160, 0))     # green
    playpal[3 * 2:3 * 3] = bytes((200, 30, 30))   # red
    lumps = [('PLAYPAL', bytes(playpal)), ('S_START', b'')]
    lumps += [(f'POSSA{r}', patch(6, 5, 3, 5, 1)) for r in (1, 5)]
    lumps += [('POSSA2A8', patch(7, 5, 2, 5, 1)), ('POSSA3A7', patch(6, 5, 3, 5, 1)),
              ('POSSA4A6', patch(6, 5, 3, 5, 1)), ('STIMA0', patch(4, 3, 2, 3, 2)),
              ('S_END', b'')]
    return make_wad(lumps)


def lookup(blob, sprite, frame, rotation):
    sprite_off, entry_off, _, patch_off = struct.unpack_from('<IIII', blob, 20)
    first, frames, _ = struct.unpack_from('<HBB', blob, sprite_off + 4 * sprite)
    if first == 0xFFFF or frame >= frames:
        return None
    entry = struct.unpack_from('<H', blob, entry_off + 2 * (first + frame * 8 + rotation))[0]
    if not entry & 0x7FFF:
        return None
    record = struct.unpack_from('<BBhhBBI', blob, patch_off + 12 * ((entry & 0x7FFF) - 1))
    return record, bool(entry & 0x8000)


def test_views_flips_palettes_and_identity(tmp_path):
    scope = {'families': {'POSS': 'enemy', 'STIM': 'pickup', 'HEAD': 'enemy'},
             'palette_overrides': {'STIM': [['00', '16', '30'], ['2D', '16', '10']]},
             'palette_exclusive': ['STIM']}
    manifest = atlas.build(_wad(tmp_path), scope, INFO,
                           tmp_path / 'out')
    blob = (tmp_path / 'out/world_atlas.bin').read_bytes()
    assert blob[:4] == b'FCWA' and struct.unpack_from('<I', blob, 8)[0] == len(blob)
    assert manifest['missing_families'] == ['HEAD']
    enum = atlas.sprite_enum(INFO)
    poss, stim = enum.index('POSS'), enum.index('STIM')
    (w, h, left, top, family, _, _), flip = lookup(blob, poss, 0, 1)   # A2, rotation 1
    assert (w, left, flip, family) == (7, 2, False, 0)
    (w, *_), flip = lookup(blob, poss, 0, 7)                         # A8 mirrors A2
    assert (w, flip) == (7, True)
    for rotation in range(8):
        record, flip = lookup(blob, stim, 0, rotation)                # A0: every view
        assert record[:2] == (4, 3) and not flip and record[4] == 1
    assert lookup(blob, enum.index('TROO'), 0, 0) is None
    # Column 0 is transparent; opaque texels use local colours 1..15 (4 bits).
    pixels_at = lookup(blob, stim, 0, 0)[0][6]
    assert blob[pixels_at] & 15 == 0 and blob[pixels_at] >> 4 != 0
    # Family records: candidates first (override, sorted by lightness), flags, count.
    family_off = struct.unpack_from('<I', blob, 36)[0]
    stim_family = blob[family_off + atlas.FAMILY_RECORD:family_off + 2 * atlas.FAMILY_RECORD]
    assert stim_family[:3] == bytes((0x00, 0x16, 0x30)) and stim_family[6] & 2
    assert 1 <= stim_family[7] <= 15
    assert manifest['families']['STIM']['candidates'][0] == ['00', '16', '30']
    identity = int((tmp_path / 'out/world_atlas_identity.h').read_text()
                   .split('IDENTITY 0x')[1][:8], 16)
    assert identity == struct.unpack_from('<I', blob, 12)[0] != 0
    again = atlas.build(_wad(tmp_path), scope, INFO, tmp_path / 'again')
    assert (tmp_path / 'again/world_atlas.bin').read_bytes() == blob and again == manifest


def _wad(tmp_path):
    path = tmp_path / 'fixture.wad'
    path.write_bytes(fixture())
    return path


def test_incomplete_rotations_are_rejected(tmp_path):
    playpal = bytes(768)
    wad = make_wad([('PLAYPAL', playpal), ('S_START', b''), ('POSSA1', patch(4, 4, 2, 4, 1)),
                    ('S_END', b'')])
    path = tmp_path / 'bad.wad'
    path.write_bytes(wad)
    with pytest.raises(ValueError, match='lacks rotations'):
        atlas.build(path, {'families': {'POSS': 'enemy'}}, INFO, tmp_path / 'out')


def test_world_flash_region():
    blob = b'FCWA' + bytes(60)
    uf2 = whx2uf2.build(b'IWHX', None, world=blob)
    blocks = whx2uf2.read_blocks(uf2)
    assert b''.join(p for _, a, _, p in blocks if 0x10280000 <= a < 0x10300000)[:len(blob)] == blob
    for bad in (b'', b'XXXX', b'FCWA' + bytes(0x80000)):
        with pytest.raises(ValueError, match='world atlas'):
            whx2uf2.build(b'IWHX', None, world=bad)
    with pytest.raises(ValueError, match='audio'):
        whx2uf2.build(b'IWHX', None, audio=bytes(0x80001))


def test_real_shareware_atlas_deterministic(tmp_path):
    wad = os.environ.get('FCPICO_AUDIO_WAD')
    if not wad:
        pytest.skip('set FCPICO_AUDIO_WAD to verify local shareware assets')
    import json
    scope = json.loads((ROOT / 'assets/world-sprite-scope.json').read_text())
    first = atlas.build(Path(wad), scope, INFO, tmp_path / 'a')
    second = atlas.build(Path(wad), scope, INFO, tmp_path / 'b')
    assert first == second and first['bytes'] <= 0x80000
    assert (tmp_path / 'a/world_atlas.bin').read_bytes() == (tmp_path / 'b/world_atlas.bin').read_bytes()
