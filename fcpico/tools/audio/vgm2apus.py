#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-3-Clause
"""Convert a single-2A03 VGM/VGZ register log to a capped APUS music stream.

    python3 fcpico/tools/audio/vgm2apus.py song.vgm -o song.apus

Supports NES writes and standard sample waits. Writes within each output frame
are merged (last value wins), then thinned by the shared APUS retrigger rule.
DPCM data, expansion chips and other commands fail explicitly. Arrange music
without DPCM; sampled effects use the separately packed boot-ROM DPCM bank.
Format source: https://github.com/vgmrips/vgmplay-legacy/blob/master/VGMPlay/vgmspec171.txt
"""

from __future__ import annotations

import argparse
import gzip
import struct
import zlib
from pathlib import Path

if __package__ in (None, ""):
    from apus import thin_register_writes, write_apus
else:
    from .apus import thin_register_writes, write_apus

MUSIC_PAIRS_MAX = 12
_MUSIC_REGISTERS = frozenset(range(0x10)) | {0x15, 0x17}


def convert_vgm(data: bytes) -> bytes:
    """Quantize 44.1 kHz timestamps into 60 Hz NTSC or 50 Hz PAL frames.

    A loop must start on both a command and a frame boundary. Thinning starts
    afresh at the loop point so writes needed on later iterations survive.
    """
    if data.startswith(b"\x1f\x8b"):
        try:
            data = gzip.decompress(data)
        except (OSError, EOFError, zlib.error) as exc:
            raise ValueError("invalid gzip VGM") from exc
    if len(data) < 0x40 or data[:4] != b"Vgm ":
        raise ValueError("expected a complete VGM header")

    def word(offset):
        return struct.unpack_from("<I", data, offset)[0]

    version = word(8)
    if not 0x161 <= version <= 0x171:
        raise ValueError(f"unsupported VGM version 0x{version:x} (need 1.61..1.71)")
    end = word(4) + 4
    start = word(0x34) + 0x34 if word(0x34) else 0x40
    if not 0x40 <= start < end <= len(data):
        raise ValueError("VGM data/EOF offset is outside the file")
    clock = word(0x84) if start >= 0x88 else 0
    if clock == 0 or clock & 0xC0000000:
        raise ValueError("need one NES APU without FDS expansion")
    rate = word(0x24) or 60
    if rate not in (50, 60):
        raise ValueError(f"unsupported VGM frame rate {rate}")
    samples_per_frame = 44100 // rate
    loop_offset = word(0x1C)
    loop_position = loop_offset + 0x1C if loop_offset else None
    if loop_position is not None and not start <= loop_position < end:
        raise ValueError("VGM loop offset is outside the command stream")
    images: dict[int, dict[int, int]] = {}
    sample = 0
    position = start
    loop_frame = None
    waits = {0x62: 735, 0x63: 882}
    ended = False

    def take(count):
        nonlocal position
        if position + count > end:
            raise ValueError(f"truncated VGM command at 0x{position - 1:x}")
        payload = data[position : position + count]
        position += count
        return payload

    while position < end:
        command_position = position
        if position == loop_position:
            if sample % samples_per_frame:
                raise ValueError("VGM loop is not on a frame boundary")
            loop_frame = sample // samples_per_frame
            if loop_frame in images:
                raise ValueError("VGM loop shares a frame with intro writes")
        command = take(1)[0]
        if command == 0x66:
            ended = True
            break
        if command == 0xB4:
            reg, value = take(2)
            if reg not in _MUSIC_REGISTERS or (reg == 0x15 and value & 0x10):
                raise ValueError(f"unsupported NES register/DPCM write at 0x{command_position:x}")
            images.setdefault(sample // samples_per_frame, {})[reg] = value
        elif command == 0x61:
            sample += int.from_bytes(take(2), "little")
        elif command in waits:
            sample += waits[command]
        elif 0x70 <= command <= 0x7F:
            sample += (command & 15) + 1
        elif command == 0x64:
            target, low, high = take(3)
            if target not in waits:
                raise ValueError("VGM wait override must target 0x62 or 0x63")
            waits[target] = low | (high << 8)
        else:
            raise ValueError(f"unsupported VGM command 0x{command:02x} at 0x{command_position:x}")
    if not ended:
        raise ValueError("VGM has no end command")
    if loop_position is not None and loop_frame is None:
        raise ValueError("VGM loop offset is not a command boundary")
    frame_count = max(
        (sample + samples_per_frame - 1) // samples_per_frame,
        max(images, default=-1) + 1,
    )
    if loop_frame is not None and loop_frame >= frame_count:
        raise ValueError("VGM loop has no frames")
    source = [images.get(index, {}) for index in range(frame_count)]
    if loop_frame is None:
        frames = thin_register_writes(source)
    else:
        frames = thin_register_writes(source[:loop_frame]) + thin_register_writes(source[loop_frame:])
    for index, frame in enumerate(frames):
        if len(frame) > MUSIC_PAIRS_MAX:
            raise ValueError(f"frame {index}: {len(frame)} APU pairs exceeds music cap {MUSIC_PAIRS_MAX}")
    return write_apus(frames, loop_frame=loop_frame, pal=rate == 50)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("-o", "--output", type=Path)
    args = parser.parse_args(argv)
    try:
        result = convert_vgm(args.input.read_bytes())
        output = args.output or args.input.with_suffix(".apus")
        output.write_bytes(result)
    except (OSError, ValueError) as exc:
        parser.exit(1, f"vgm2apus: {exc}\n")
    print(f"{output}: {len(result)} bytes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
