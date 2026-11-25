# SPDX-License-Identifier: BSD-3-Clause
"""World sprite atlas: engine-keyed views, flips, palettes and flash packaging."""
import os
import json
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


def test_scenery_scope_animation_and_roles(tmp_path):
    """Build the shipped scope: static and animated props need scenery roles,
    while map corpses using enemy families must remain distinguishable."""
    scope = json.loads((ROOT / 'assets/world-sprite-scope.json').read_text())
    scenery = {f for f, role in scope['families'].items() if role == 'scenery'}
    assert {'BAR1', 'FCAN', 'COLU', 'CAND', 'TRE1', 'SMIT',
            'TBLU', 'TGRN', 'TRED', 'TLMP', 'TLP2', 'HDB1'} <= scenery
    assert not {'BEXP', 'POL5', 'POB1', 'POB2', 'BRS1'} & scenery
    playpal = bytearray(768)
    playpal[3:6] = bytes((0, 160, 0))
    lumps = [('PLAYPAL', bytes(playpal)), ('S_START', b'')]
    # Include every scenery family, plus an enemy and pickup to catch role
    # collisions. Model barrel/torch/lamp animations alongside static props.
    frames = {f: {'BAR1': 2, 'TLMP': 4, 'TBLU': 4}.get(f, 1) for f in sorted(scenery)}
    frames.update(POSS=1, STIM=1)
    for family, count in frames.items():
        for frame in range(count):
            lumps.append((family + chr(65 + frame) + '0', patch(6, 5, 3, 5, 1)))
    lumps.append(('S_END', b''))
    wad = tmp_path / 'scenery.wad'
    wad.write_bytes(make_wad(lumps))
    manifest = atlas.build(wad, scope, INFO, tmp_path / 'out')
    blob = (tmp_path / 'out/world_atlas.bin').read_bytes()
    enum = atlas.sprite_enum(INFO)
    family_off = struct.unpack_from('<I', blob, 36)[0]
    for family, count in frames.items():
        for frame in range(count):
            for rotation in range(8):
                record, flip = lookup(blob, enum.index(family), frame, rotation)
                assert record[:4] == (6, 5, 3, 5) and not flip
                flags = blob[family_off + atlas.FAMILY_RECORD * record[4] + 6]
                assert flags & 5 == (4 if family in scenery else 1 if family == 'POSS' else 0)
        assert lookup(blob, enum.index(family), count, 0) is None
    assert manifest['bytes'] <= 0x80000
    assert lookup(blob, enum.index('BEXP'), 0, 0) is None
    assert 'BEXP' not in scope['families']
    # A WAD without these props still builds normally and reports them missing.
    minimal = atlas.build(_wad(tmp_path), scope, INFO, tmp_path / 'minimal')
    assert scenery <= set(minimal['missing_families'])


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


def test_band_errors_match_runtime_arithmetic():
    import numpy as np
    # Same case as test_fcworld.c test_light_bands_use_lit_colours.
    centre = np.array([[4096, 819, 0, 819]])
    nes = np.zeros((64, 4), np.int64)
    nes[0x06] = (6554, 1966, 492, 2027)
    assert atlas.band_errors(centre, nes)[0, 0x06] == 51
    # A perfect match is zero; distances saturate at 255.
    assert atlas.band_errors(nes[[0x06]], nes)[0, 0x06] == 0
    far = np.array([[-16384, 8000, -8000, 11314]])
    assert atlas.band_errors(far, nes).max() == 255


def test_light_bands_follow_colormap(tmp_path):
    """Lit colours come from the WAD's COLORMAP; each band's palettes keep
    three distinct colours and the family record carries them."""
    import numpy as np
    playpal = bytearray(768)
    playpal[3:6] = bytes((0, 160, 0))         # green
    playpal[6:9] = bytes((200, 30, 30))       # red
    playpal[9:12] = bytes((0, 40, 0))         # dark green
    playpal[12:15] = bytes((50, 8, 8))        # dark red
    colormap = bytearray(34 * 256)
    for level in range(34):
        for i in range(256):
            colormap[level * 256 + i] = i if level < 8 else {1: 3, 2: 4}.get(i, 0)
    lumps = [('PLAYPAL', bytes(playpal)), ('COLORMAP', bytes(colormap)), ('S_START', b'')]
    lumps += [(f'POSSA{r}', patch(6, 5, 3, 5, 1 + r % 2)) for r in range(1, 9)]
    lumps += [('S_END', b'')]
    path = tmp_path / 'lit.wad'
    path.write_bytes(make_wad(lumps))
    manifest = atlas.build(path, {'families': {'POSS': 'enemy'}}, INFO, tmp_path / 'out')
    blob = (tmp_path / 'out/world_atlas.bin').read_bytes()
    assert struct.unpack_from('<H', blob, 4)[0] == 3
    family_off, nes_off = struct.unpack_from('<II', blob, 36)
    record = blob[family_off:family_off + atlas.FAMILY_RECORD]
    centres = np.frombuffer(record[16 + 2 * 15 * 64 + atlas.BAND_CANDIDATES:], '<i2'
                            ).reshape(atlas.LIGHT_BANDS, 15, 4)
    count = record[7]
    # Bands 0 and 1 (levels 0 and 6) see the bright colours, bands 2+ the dark ones.
    assert (centres[1, :count, 0] == centres[0, :count, 0]).all() or \
        abs(int(centres[1, :count, 0].sum()) - int(centres[0, :count, 0].sum())) < 64
    assert (centres[2, :count, 0] < centres[0, :count, 0]).all()
    for band in manifest['families']['POSS']['light_candidates']:
        for palette in band:
            assert len(set(palette)) == 3
    nes = np.frombuffer(blob[nes_off:nes_off + 64 * 8], '<i2').reshape(64, 4)
    assert nes[0x30, 0] > nes[0x0F, 0]


INFO_C = ROOT.parent / 'src/doom/info.c'


def test_auto_scope_roles_from_engine_tables():
    import world_scope
    things = struct.pack('<hhhhh', 0, 0, 0, 3001, 7) * 5 + struct.pack('<hhhhh', 0, 0, 0, 2011, 7)
    wad = make_wad([('E1M1', b''), ('THINGS', things), ('E1M2', b''),
                    ('THINGS', struct.pack('<hhhhh', 0, 0, 0, 3004, 7) * 2)])
    scope = world_scope.auto_scope(wad, INFO_C, reviewed={
        'palette_overrides': {'POSS': [['0F', '1A', '27']], 'NOPE': [['0F', '0F', '0F']]}})
    roles = scope['families']
    assert roles['TROO'] == roles['POSS'] == roles['SKUL'] == 'enemy'    # SKUL: shootable attacker
    assert roles['STIM'] == roles['SHOT'] == 'pickup'
    assert 'PLAY' not in roles and 'PUFF' not in roles and 'BAL1' not in roles
    assert 'BAR1' not in roles                                        # scenery only on request
    # Both map lumps count: imps placed five times outrank zombiemen placed twice.
    assert scope['placed']['TROO'] == 5 and scope['placed']['POSS'] == 2
    assert scope['priority'].index('TROO') < scope['priority'].index('POSS')
    assert scope['priority'].index('POSS') < scope['priority'].index('STIM')   # enemies first
    assert scope['palette_overrides'] == {'POSS': [['0F', '1A', '27']]}
    scenery = world_scope.auto_scope(wad, INFO_C, include_scenery=True)['families']
    assert scenery['BAR1'] == 'scenery' and 'BEXP' not in scenery      # spawn sprites only


def test_auto_scope_matches_reviewed_shareware_scope():
    wad = os.environ.get('FCPICO_AUDIO_WAD')
    if not wad:
        pytest.skip('set FCPICO_AUDIO_WAD to verify local shareware assets')
    import json
    import world_scope
    reviewed = json.loads((ROOT / 'assets/world-sprite-scope.json').read_text())
    # The reviewed scope includes scenery, so compare with scenery derived too.
    auto = world_scope.auto_scope(Path(wad).read_bytes(), INFO_C, include_scenery=True)['families']
    for family, role in reviewed['families'].items():
        assert auto.get(family) == role, family


def test_atlas_trims_lowest_priority_families(tmp_path):
    scope = {'families': {'POSS': 'enemy', 'STIM': 'pickup'}, 'priority': ['POSS', 'STIM']}
    full = atlas.build(_wad(tmp_path), scope, INFO, tmp_path / 'full')
    assert full['trimmed_families'] == []
    trimmed = atlas.build(_wad(tmp_path), scope, INFO, tmp_path / 'trim',
                          max_bytes=full['bytes'] - 1)
    assert trimmed['trimmed_families'] == ['STIM'] and list(trimmed['families']) == ['POSS']
    assert trimmed['bytes'] < full['bytes']
    # The size estimate is exact up to alignment, so the full atlas fits its own size.
    again = atlas.build(_wad(tmp_path), scope, INFO, tmp_path / 'exact', max_bytes=full['bytes'])
    assert again['trimmed_families'] == []
