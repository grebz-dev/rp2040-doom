#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-3-Clause
"""Deterministic four-voice fallback arrangement of MUS or MIDI music.

    python3 fcpico/tools/audio/mus2apus.py song.mus --auto -o song.apus
    python3 fcpico/tools/audio/mus2apus.py doom1.wad --auto -o /tmp/music

MUS uses 140 ticks/second. MIDI type 0/1 uses PPQN and its tempo map.
Highest two active notes go to pulses; the lowest goes to octave-folded
triangle; channel 10 percussion uses noise. Program, pan and pitch bend are
parsed but ignored in this fixed-timbre fallback. NTSC output only.
MIDI format: https://midi.org/standard-midi-files-specification
APU timers: https://www.nesdev.org/wiki/APU_period_table
"""

from __future__ import annotations

import argparse
import struct
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path

if __package__ in (None, ""):
    from apus import thin_register_writes, write_apus
    from wadextract import read_wad
else:
    from .apus import thin_register_writes, write_apus
    from .wadextract import read_wad

SHAREWARE_MUSIC = tuple(f"D_E1M{i}" for i in range(1, 10)) + (
    "D_INTER", "D_INTRO", "D_INTROA", "D_VICTOR",
)
_MUS_CONTROLLERS = (0, 32, 1, 7, 10, 11, 91, 93, 64, 67, 120, 123, 126, 127, 121)
CPU_NTSC = 1789773


class Reader:
    """Bound every parser read to its declared score/track chunk."""

    def __init__(self, data: bytes):
        self.data = data
        self.position = 0

    def take(self, count: int) -> bytes:
        if count < 0 or self.position + count > len(self.data):
            raise ValueError(f"truncated music data at byte {self.position}")
        result = self.data[self.position : self.position + count]
        self.position += count
        return result

    def byte(self) -> int:
        return self.take(1)[0]

    def vlq(self) -> int:
        value = 0
        for _ in range(4):
            byte = self.byte()
            value = (value << 7) | (byte & 127)
            if byte < 128:
                return value
        raise ValueError("music variable-length quantity exceeds four bytes")


@dataclass(frozen=True)
class Event:
    time: Fraction
    status: int
    channel: int
    first: int
    second: int = 0


def read_mus(data: bytes) -> tuple[list[Event], Fraction]:
    if len(data) < 16 or data[:4] != b"MUS\x1a":
        raise ValueError("expected a complete MUS header")
    length, start, primary, secondary, instruments = struct.unpack_from("<5H", data, 4)
    if primary > 15 or secondary > 15:
        raise ValueError("invalid MUS channel count")
    if start < 16 + 2 * instruments or start > len(data) - length:
        raise ValueError("MUS score/instrument table is outside the file")
    reader = Reader(data[start : start + length])
    velocities = [127] * 16
    events = []
    tick = 0
    while reader.position < len(reader.data):
        descriptor = reader.byte()
        kind = (descriptor >> 4) & 7
        source_channel = descriptor & 15
        channel = 9 if source_channel == 15 else source_channel + (source_channel >= 9)
        time = Fraction(tick, 140)
        if kind == 6:
            return events, time
        first = reader.byte()
        if kind == 0:
            events.append(Event(time, 0x80, channel, first & 127))
        elif kind == 1:
            if first & 128:
                velocity = reader.byte()
                if velocity > 127:
                    raise ValueError("invalid MUS velocity")
                velocities[source_channel] = velocity
            events.append(Event(time, 0x90, channel, first & 127, velocities[source_channel]))
        elif kind == 2:
            events.append(Event(time, 0xE0, channel, (first * 64) & 127, (first * 64) >> 7))
        elif kind == 3:
            if not 10 <= first <= 14:
                raise ValueError("invalid MUS system controller")
            events.append(Event(time, 0xB0, channel, _MUS_CONTROLLERS[first]))
        elif kind == 4:
            second = reader.byte()
            if first > 9 or second > 127:
                raise ValueError("invalid MUS controller")
            events.append(Event(time, 0xC0 if first == 0 else 0xB0,
                                channel, second if first == 0 else _MUS_CONTROLLERS[first],
                                0 if first == 0 else second))
        else:
            raise ValueError(f"unsupported MUS event {kind}")
        if descriptor & 128:
            tick += reader.vlq()
    raise ValueError("MUS score has no end event")


def read_midi(data: bytes) -> tuple[list[Event], Fraction]:
    reader = Reader(data)
    if reader.take(4) != b"MThd":
        raise ValueError("expected MIDI header")
    header = reader.take(int.from_bytes(reader.take(4), "big"))
    if len(header) < 6:
        raise ValueError("truncated MIDI header")
    kind, tracks, division = struct.unpack_from(">3H", header)
    if kind not in (0, 1) or not tracks or (kind == 0 and tracks != 1):
        raise ValueError("need MIDI type 0 or 1 with valid track count")
    if not division or division & 0x8000:
        raise ValueError("MIDI SMPTE timing is unsupported; need PPQN")
    raw = []
    for track_index in range(tracks):
        if reader.take(4) != b"MTrk":
            raise ValueError("expected MIDI track chunk")
        track = Reader(reader.take(int.from_bytes(reader.take(4), "big")))
        tick = 0
        running = None
        ended = False
        order = 0
        while track.position < len(track.data):
            tick += track.vlq()
            first = track.byte()
            if first < 128:
                if running is None:
                    raise ValueError("MIDI running status has no channel status")
                track.position -= 1
                status = running
            else:
                status = first
            if status == 0xFF:
                running = None
                meta = track.byte()
                payload = track.take(track.vlq())
                if meta == 0x51:
                    if len(payload) != 3 or int.from_bytes(payload, "big") == 0:
                        raise ValueError("invalid MIDI tempo event")
                    raw.append((tick, track_index, order, "tempo", int.from_bytes(payload, "big")))
                elif meta == 0x2F:
                    if payload or track.position != len(track.data):
                        raise ValueError("invalid MIDI end-of-track event")
                    raw.append((tick, track_index, order, "end", None))
                    ended = True
                    break
            elif status in (0xF0, 0xF7):
                running = None
                track.take(track.vlq())
            elif 0x80 <= status <= 0xEF:
                running = status
                size = 1 if (status & 0xF0) in (0xC0, 0xD0) else 2
                payload = track.take(size)
                if any(value > 127 for value in payload):
                    raise ValueError("MIDI channel data contains a status byte")
                raw.append((tick, track_index, order, "event", (status, payload)))
            else:
                raise ValueError(f"unsupported MIDI status 0x{status:02x}")
            order += 1
        if not ended:
            raise ValueError("MIDI track has no end-of-track event")
    if reader.position != len(data):
        raise ValueError("data follows the declared MIDI tracks")
    events = []
    previous_tick = 0
    elapsed = Fraction(0)
    tempo = 500000
    for tick, _, _, category, payload in sorted(raw):
        elapsed += Fraction((tick - previous_tick) * tempo, division * 1000000)
        previous_tick = tick
        if category == "tempo":
            tempo = payload
        elif category == "event":
            status, values = payload
            events.append(Event(elapsed, status & 0xF0, status & 15,
                                values[0], values[1] if len(values) == 2 else 0))
    return events, elapsed


def note_period(note: int, triangle: bool = False) -> int:
    """Octave-fold pitches into an audible, representable timer range."""
    if triangle:
        while note < 36:
            note += 12
        while note > 59:
            note -= 12
    divisor = 32 if triangle else 16
    while True:
        frequency = 440 * 2 ** ((note - 69) / 12)
        period = int(CPU_NTSC / (divisor * frequency) + 0.5) - 1
        if period > 2047:
            note += 12
        elif period < 8:
            note -= 12
        else:
            return period


def arrange(events: list[Event], duration: Fraction, looping: bool = True) -> bytes:
    """Render at 60 Hz; setup and a retrigger gap precede the score."""
    frame_count = int(duration * 60) + 1
    if frame_count > 60 * 60 * 30:
        raise ValueError("music exceeds the 30-minute offline conversion limit")
    grouped = {}
    for event in events:
        grouped.setdefault(int(event.time * 60), []).append(event)
    # Enable channels before length loads, inhibit frame IRQs, disable sweep.
    images = [{0x15: 15, 0x17: 0x40, 0: 0x30, 1: 8,
               4: 0x30, 5: 8, 8: 0x80, 12: 0x30}, {}]
    # fcapu applies its own retrigger rule to the thinned stream. The gap
    # ensures the first note reloads length counters on every loop, even
    # when its pitch equals the preceding iteration's last note.
    active = {}  # (channel, key) -> (velocity, onset serial, released)
    volume = [127] * 16
    expression = [127] * 16
    sustain = [False] * 16
    previous = [None] * 4
    serial = 0
    for frame in range(frame_count):
        for event in grouped.get(frame, []):
            channel, key, value = event.channel, event.first, event.second
            identity = (channel, key)
            if event.status == 0x90 and value:
                serial += 1
                active[identity] = (value, serial, False)
            elif event.status in (0x80, 0x90):
                if identity in active and sustain[channel]:
                    velocity, onset, _ = active[identity]
                    active[identity] = (velocity, onset, True)
                else:
                    active.pop(identity, None)
            elif event.status == 0xB0:
                if key == 7:
                    volume[channel] = value
                elif key == 11:
                    expression[channel] = value
                elif key == 64:
                    sustain[channel] = value >= 64
                elif key == 121:
                    expression[channel] = 127
                    sustain[channel] = False
                elif key in (120, 123, 126, 127):
                    active = {k: v for k, v in active.items() if k[0] != channel}
                if not sustain[channel]:
                    active = {k: v for k, v in active.items()
                              if k[0] != channel or not v[2]}
        notes = []
        drums = []
        for (channel, key), (velocity, onset, _) in active.items():
            level = (velocity * volume[channel] * expression[channel] * 15 + 127**3 // 2) // 127**3
            if level:
                (drums if channel == 9 else notes).append((key, onset, channel, level))
        notes.sort(reverse=True)
        selected = [notes[0] if notes else None,
                    notes[1] if len(notes) > 1 else None,
                    min(notes) if notes else None,
                    max(drums, key=lambda n: n[1]) if drums else None]
        image = {}
        for voice, note in enumerate(selected):
            old = previous[voice]
            if note == old:
                continue
            base = voice * 4
            if note is None:
                if old is not None:
                    image[base] = 0x80 if voice == 2 else 0x30
                continue
            key, onset, channel, level = note
            if old is not None and note[:3] == old[:3]:
                # Envelope changes must not reload the length/timer register.
                if voice != 2:
                    image[base] = (0x30 if voice == 3 else 0xB0) | level
                continue
            if voice == 3:
                # Kick/tom = low noise, snare = mid, cymbal/hat = bright.
                period = 12 if key in (35, 36, 41, 43, 45, 47, 48, 50) else 3 if key in (42, 44, 46, 49, 51, 52, 55, 57, 59) else 7
                image.update({12: 0x30 | level, 14: period, 15: 8})
            else:
                period = note_period(key, triangle=voice == 2)
                image.update({base: 0xFF if voice == 2 else 0xB0 | level,
                              base + 2: period & 255, base + 3: 8 | (period >> 8)})
        images.append(image)
        previous = selected
    # A finite stream must leave no sounding notes. The loop also starts clean.
    images.append({0: 0x30, 4: 0x30, 8: 0x80, 12: 0x30, 0x15: 0})
    return write_apus(thin_register_writes(images), loop_frame=0 if looping else None)


def convert_music(data: bytes, looping: bool = True) -> bytes:
    parser = read_mus if data.startswith(b"MUS\x1a") else read_midi
    return arrange(*parser(data), looping=looping)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("--auto", required=True, action="store_true", help="use the fallback arrangement")
    parser.add_argument("--no-loop", action="store_true")
    parser.add_argument("-o", "--output", type=Path)
    args = parser.parse_args(argv)
    try:
        data = args.input.read_bytes()
        if data[:4] in (b"IWAD", b"PWAD"):
            if args.output is None:
                raise ValueError("WAD conversion needs an output directory (-o)")
            lumps = read_wad(data)
            missing = [name for name in SHAREWARE_MUSIC if name not in lumps]
            if missing:
                raise ValueError(f"WAD is missing shareware music: {', '.join(missing)}")
            # Finish validation/conversion before writing any output files.
            results = [(name, convert_music(lumps[name], not args.no_loop))
                       for name in SHAREWARE_MUSIC]
            args.output.mkdir(parents=True, exist_ok=True)
            for name, result in results:
                path = args.output / (name + ".apus")
                path.write_bytes(result)
                print(f"{path}: {len(result)} bytes")
        else:
            result = convert_music(data, not args.no_loop)
            path = args.output or args.input.with_suffix(".apus")
            path.write_bytes(result)
            print(f"{path}: {len(result)} bytes")
    except (OSError, ValueError) as exc:
        parser.exit(1, f"mus2apus: {exc}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
