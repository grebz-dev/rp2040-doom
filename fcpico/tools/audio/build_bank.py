#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-3-Clause
"""Generate a shareware audio archive and matching host/device C tables.

Assets are local build outputs. Device streams live in FLASH_ASSETS_ADDR;
host builds embed the same archive for deterministic register capture.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import struct
from pathlib import Path

import numpy as np

if __package__ in (None, ""):
    from mus2apus import SHAREWARE_MUSIC, convert_music
    from sfx2apu import synthesize
    from wadextract import read_wad
else:
    from .mus2apus import SHAREWARE_MUSIC, convert_music
    from .sfx2apu import synthesize
    from .wadextract import read_wad

# Initial synthesized arrangement, pending listening/hand-authored overrides.
PULSE_EFFECTS = frozenset((
    "firsht", "itemup", "wpnup", "swtchn", "swtchx", "pstop", "stnmov",
    "posit1", "posit2", "posit3", "bgsit1", "bgsit2", "sgtsit", "brssit",
    "posact", "bgact", "dmact", "noway",
))


def build_bank(wad: bytes, sounds: str, capacity: int):
    lumps = read_wad(wad)
    missing = [name for name in SHAREWARE_MUSIC if name not in lumps]
    if missing:
        raise ValueError("missing shareware music: " + ", ".join(missing))
    priorities = {name: int(priority) for name, priority in
                  re.findall(r'SOUND\("([a-z0-9]+)",\s*(\d+)\)', sounds)}
    if not priorities or any(not 0 <= value <= 255 for value in priorities.values()):
        raise ValueError("invalid or missing Doom SOUND priorities")
    archive = bytearray()
    music, effects = [], []

    def append(name, data, **metadata):
        archive.extend(bytes((-len(archive)) % 4))
        record = dict(name=name, offset=len(archive), size=len(data), **metadata)
        archive.extend(data)
        if len(archive) > capacity:
            raise ValueError("audio bank exceeds FLASH_AUDIO_MAX")
        return record

    for name in SHAREWARE_MUSIC:
        music.append(append(name, convert_music(lumps[name])))
    for lump in sorted(name for name in lumps if name.startswith("DS")):
        name = lump[2:].lower()
        if name not in priorities:
            raise ValueError(f"{lump}: no Doom priority")
        data = lumps[lump]
        if len(data) < 8:
            raise ValueError(f"{lump}: truncated DMX header")
        kind, rate, length = struct.unpack_from("<HHI", data)
        if kind != 3 or not rate or length <= 32 or len(data) < 8 + length:
            raise ValueError(f"{lump}: invalid DMX PCM data")
        samples = np.frombuffer(data[24:8 + length - 16], dtype=np.uint8)
        voice = "pulse" if name in PULSE_EFFECTS else "noise"
        effects.append(append(name, synthesize(samples, rate, voice),
                              kind=voice, priority=priorities[name]))
    if not effects:
        raise ValueError("WAD has no sound effects")
    # Pair the firmware tables with this archive, refusing stale/unflashed data.
    signature = hashlib.sha256(archive).digest()
    blob = signature + bytes(archive)
    if len(blob) > capacity:
        raise ValueError("audio bank exceeds FLASH_AUDIO_MAX")
    for record in music + effects:
        record['offset'] += len(signature)
    manifest = dict(version=1, size=len(blob), sha256=hashlib.sha256(blob).hexdigest(),
                    wad_sha256=hashlib.sha256(wad).hexdigest(), music=music, sfx=effects)
    return blob, manifest


def source(blob: bytes, manifest: dict) -> str:
    lines = ['/* Generated locally; do not commit game assets. */',
             '#include "i_audio_fcpico.h"', '#include "flash_layout.h"',
             '#include <string.h>',
             '#if PICO_ON_DEVICE',
             '#define AUDIO_DATA ((const uint8_t *)FLASH_ASSETS_ADDR)', '#else',
             'static const uint8_t audio_data[] = {']
    for offset in range(0, len(blob), 24):
        lines.append('    ' + ','.join(str(byte) for byte in blob[offset:offset + 24]) + ',')
    lines += ['};', '#define AUDIO_DATA audio_data', '#endif',
              'static const fcapu_sfx_t effects[] = {']
    for record in manifest['sfx']:
        kind = 'PULSE2' if record['kind'] == 'pulse' else 'NOISE'
        lines.append('    {.kind = FCAPU_SFX_%s, .priority = %d, .script = {AUDIO_DATA + %d, %d}},' %
                     (kind, record['priority'], record['offset'], record['size']))
    lines += ['};', 'static const fcapu_bank_t streams = { .music = {']
    for record in manifest['music']:
        lines.append('    {AUDIO_DATA + %d, %d},' % (record['offset'], record['size']))
    lines += ['}, .sfx = effects, .sfx_count = sizeof(effects) / sizeof(effects[0]) };',
              'static const char *const names[] = {']
    lines += ['    "%s",' % record['name'] for record in manifest['sfx']]
    lines += ['};', 'static const fcpico_audio_bank_t bank = {',
              '    .streams = &streams, .sfx_names = names, .music_names = {']
    lines += ['    "%s",' % record['name'] for record in manifest['music']]
    lines += ['}};',
              'const fcpico_audio_bank_t *fcpico_audio_default_bank(void) {',
              '    static const uint8_t signature[] = {' + ','.join(str(byte) for byte in blob[:32]) + '};',
              '    return memcmp(AUDIO_DATA, signature, sizeof(signature)) == 0 ? &bank : NULL;',
              '}', '']
    return '\n'.join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('wad', type=Path)
    parser.add_argument('--sounds', required=True, type=Path)
    parser.add_argument('--layout', required=True, type=Path)
    parser.add_argument('-o', '--output', required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        layout = args.layout.read_text()
        capacity = int(re.search(r"#define\s+FLASH_AUDIO_MAX\s+(0x[0-9a-fA-F]+)", layout)[1], 16)
        blob, manifest = build_bank(args.wad.read_bytes(), args.sounds.read_text(), capacity)
        text = source(blob, manifest)
        args.output.mkdir(parents=True, exist_ok=True)
        (args.output / 'audio.bin').write_bytes(blob)
        (args.output / 'audio_bank.c').write_text(text)
        (args.output / 'audio.json').write_text(json.dumps(manifest, indent=2) + '\n')
    except (OSError, ValueError, TypeError) as exc:
        parser.exit(1, f'build_bank: {exc}\n')
    print(f"audio bank: {len(blob)} bytes, {len(manifest['music'])} songs, {len(manifest['sfx'])} effects")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
