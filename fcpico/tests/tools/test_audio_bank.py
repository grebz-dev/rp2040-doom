# SPDX-License-Identifier: BSD-3-Clause
"""Bank generation, fixed-region packaging and generated-table replay."""
import hashlib
import os
from pathlib import Path
import shutil
import struct
import subprocess

import pytest

from audio import apus, build_bank, mus2apus
from test_audio_convert import make_wad
import whx2uf2

ROOT = Path(__file__).resolve().parents[2]


def fixture_wad():
    mus = struct.pack('<4s6H', b'MUS\x1a', 1, 16, 0, 0, 0, 0) + b'\x60'
    dmx = struct.pack('<HHI', 3, 11025, 216) + bytes([128]) * 16 + bytes(range(184)) + bytes([128]) * 16
    return make_wad([(name, mus) for name in mus2apus.SHAREWARE_MUSIC] + [('DSPISTOL', dmx), ('DSITEMUP', dmx)])


@pytest.mark.parametrize('real', [False, True])
def test_generated_tables_replay_and_refuse_wrong_archive(tmp_path, real):
    if real:
        wad = os.environ.get('FCPICO_AUDIO_WAD')
        if not wad:
            pytest.skip('set FCPICO_AUDIO_WAD to replay the complete local bank')
        data = Path(wad).read_bytes()
        sounds = (ROOT.parent / 'src/doom/sounds.c').read_text()
    else:
        data = fixture_wad()
        sounds = 'SOUND("pistol", 64), SOUND("itemup", 78)'
    blob, manifest = build_bank.build_bank(data, sounds, 524288)
    assert blob[:32] == hashlib.sha256(blob[32:]).digest()
    assert [r['name'] for r in manifest['music']] == list(mus2apus.SHAREWARE_MUSIC)
    if not real:
        assert [(r['name'], r['kind'], r['priority']) for r in manifest['sfx']] == [('itemup', 'pulse', 78), ('pistol', 'noise', 64)]
    for record in manifest['music'] + manifest['sfx']:
        assert record['offset'] % 4 == 0
        apus.read_apus(blob[record['offset']:record['offset'] + record['size']])
    (tmp_path / 'audio_bank.c').write_text(build_bank.source(blob, manifest))
    (tmp_path / 'flash_layout.h').write_text('#define FLASH_ASSETS_ADDR asset_data\n')
    (tmp_path / 'probe.c').write_text('''
#include <assert.h>
#include <stdint.h>
#include <string.h>
static uint8_t asset_data[%d];
static const uint8_t archive[] = {%s};
#include "audio_bank.c"
static unsigned pairs;
static bool write_pair(void *user, uint8_t reg, uint8_t value) {
    (void)user; (void)value; assert(reg <= 0x17); assert(++pairs <= 15); return true;
}
int main(void) {
    assert(fcpico_audio_default_bank() == NULL);
    memcpy(asset_data, archive, sizeof(archive));
    const fcpico_audio_bank_t *bank = fcpico_audio_default_bank();
    assert(bank != NULL);
    uint32_t heartbeat = 0;
    for (int i = 0; i < 13; i++) {
        fcapu_init(bank->streams, write_pair, NULL);
        assert(fcapu_music_play(i, false));
        do { pairs = 0; fcapu_pump(++heartbeat); } while (fcapu_music_playing());
        assert(fcapu_stats()->dropped == 0);
    }
    for (size_t i = 0; i < bank->streams->sfx_count; i++) {
        fcapu_init(bank->streams, write_pair, NULL);
        int handle = fcapu_sfx_start((int)i, 127); assert(handle > 0);
        do { pairs = 0; fcapu_pump(++heartbeat); } while (fcapu_sfx_playing(handle));
        assert(fcapu_stats()->dropped == 0);
    }
    asset_data[0] ^= 1;
    assert(fcpico_audio_default_bank() == NULL);
    return 0;
}
''' % (len(blob), ','.join(str(b) for b in blob)))
    cc = shutil.which('cc')
    assert cc, 'host C compiler required'
    subprocess.run([cc, '-std=c11', '-Wall', '-Wextra', '-Werror', '-DPICO_ON_DEVICE=1',
                    '-fsanitize=address,undefined', '-g',
                    '-I' + str(ROOT.parent / 'src/fcpico'),
                    '-I' + str(ROOT / 'port/apu'), '-I' + str(ROOT / 'fcbus'), str(tmp_path / 'probe.c'),
                    str(ROOT / 'port/apu/fcapu.c'), '-o', str(tmp_path / 'probe')], check=True)
    subprocess.run([str(tmp_path / 'probe')], env={**os.environ, 'ASAN_OPTIONS': 'detect_leaks=0'}, check=True)


@pytest.mark.parametrize('mutate,match', [
    (lambda lumps: lumps.pop('D_E1M1'), 'missing shareware'),
    (lambda lumps: lumps.update(DSPISTOL=b'x'), 'truncated DMX'),
    (lambda lumps: lumps.update(DSPISTOL=struct.pack('<HHI', 3, 0, 33) + bytes(33)), 'invalid DMX'),
    (lambda lumps: lumps.update(DSBOGUS=lumps['DSPISTOL']), 'no Doom priority'),
])
def test_rejects_incomplete_banks(mutate, match):
    from audio.wadextract import read_wad
    lumps = read_wad(fixture_wad())
    mutate(lumps)
    with pytest.raises(ValueError, match=match):
        build_bank.build_bank(make_wad(lumps.items()), 'SOUND("pistol", 64), SOUND("itemup", 78)', 524288)


def test_rejects_capacity_and_bad_priorities():
    with pytest.raises(ValueError, match='exceeds'):
        build_bank.build_bank(fixture_wad(), 'SOUND("pistol", 64)', 20)
    with pytest.raises(ValueError, match='priorities'):
        build_bank.build_bank(fixture_wad(), 'SOUND("pistol", 999)', 524288)


def test_packaging_regions_and_round_trip():
    blob, _ = build_bank.build_bank(fixture_wad(), 'SOUND("pistol", 64), SOUND("itemup", 78)', 524288)
    uf2 = whx2uf2.build(b'IWHX', None, audio=blob)
    blocks = whx2uf2.read_blocks(uf2)
    assert b''.join(payload for _, addr, _, payload in blocks if addr >= 0x10300000) == blob
    with pytest.raises(ValueError, match='overlaps'):
        whx2uf2.build(bytes(0x280001), None, audio=blob)
    for bad in (b'', bytes(0x80001)):
        with pytest.raises(ValueError, match='archive'):
            whx2uf2.build(b'IWHX', None, audio=bad)


def test_real_shareware_bank_deterministic():
    wad = os.environ.get('FCPICO_AUDIO_WAD')
    if not wad:
        pytest.skip('set FCPICO_AUDIO_WAD to verify local shareware assets')
    args = (Path(wad).read_bytes(), (ROOT.parent / 'src/doom/sounds.c').read_text(), 524288)
    first, manifest = build_bank.build_bank(*args)
    second, repeated = build_bank.build_bank(*args)
    assert first == second and manifest == repeated
    assert len(manifest['music']) == 13 and len(manifest['sfx']) == 55
    assert manifest['sha256'] == hashlib.sha256(first).hexdigest()
    assert len(first) < 524288
