# SPDX-License-Identifier: BSD-3-Clause
"""Synthetic fixtures for the audio conversion pipeline."""

from __future__ import annotations

import gzip
import os
import struct
import subprocess
import sys
from fractions import Fraction
from pathlib import Path

import pytest

import numpy as np

from audio import apus, mus2apus, sfx2apu, vgm2apus, wadextract

TOOLS = Path(__file__).resolve().parents[2] / "tools" / "audio"


def make_wad(entries, magic=b"IWAD"):
    data = bytearray(12)
    directory = bytearray()
    for name, payload in entries:
        directory.extend(struct.pack("<ii8s", len(data), len(payload), name.encode("ascii")))
        data.extend(payload)
    struct.pack_into("<4sii", data, 0, magic, len(entries), len(data))
    return bytes(data + directory)


def make_vgm(commands, rate=60, loop=None):
    header = bytearray(0x100)
    header[:4] = b"Vgm "
    for offset, value in {
        4: len(header) + len(commands) - 4,
        8: 0x171,
        0x24: rate,
        0x34: len(header) - 0x34,
        0x84: 1789772,
        0x1C: len(header) + loop - 0x1C if loop is not None else 0,
    }.items():
        struct.pack_into("<I", header, offset, value)
    return bytes(header) + commands


def writes(*pairs):
    return b"".join(bytes((0xB4, reg, value)) for reg, value in pairs)


@pytest.mark.parametrize("magic", [b"IWAD", b"PWAD"])
def test_wad_extract_patterns_duplicates_and_empty_markers(tmp_path, magic):
    data = make_wad([
        ("D_E1M1", b"MUS\x1a"), ("DSPISTOL", b"old"),
        ("DSPISTOL", b"new"), ("DSOOF", b"oof"), ("F_START", b""),
    ], magic)
    expected = {"D_E1M1.mus": b"MUS\x1a", "DSOOF.lmp": b"oof", "DSPISTOL.lmp": b"new"}
    for directory in (tmp_path / "first", tmp_path / "second"):
        paths = wadextract.extract_lumps(data, ["d_e1m1", "ds*", "DSOOF"], directory)
        assert {p.name: p.read_bytes() for p in paths} == expected
        assert [p.name for p in paths] == sorted(expected)
    assert wadextract.read_wad(data)["F_START"] == b""


def test_wad_missing_pattern_writes_nothing(tmp_path):
    output = tmp_path / "output"
    with pytest.raises(ValueError, match="no WAD lumps match"):
        wadextract.extract_lumps(make_wad([("DSOOF", b"x")]), ["DSOOF", "D_MISSING"], output)
    assert not output.exists()


@pytest.mark.parametrize("data, message", [
    (b"IWAD", "header"),
    (struct.pack("<4sii", b"NOPE", 0, 12), "magic"),
    (struct.pack("<4sii", b"IWAD", -1, 12), "directory"),
    (struct.pack("<4sii", b"IWAD", 1, 12), "directory"),
    (make_wad([("../BAD", b"x")]), "unsafe name"),
])
def test_wad_rejects_invalid_headers_and_names(data, message):
    with pytest.raises(ValueError, match=message):
        wadextract.read_wad(data)


@pytest.mark.parametrize("offset,size", [(-1, 1), (12, -1), (9999, 1), (12, 9999)])
def test_wad_rejects_invalid_lump_ranges(offset, size):
    data = bytearray(make_wad([("DSOOF", b"x")]))
    directory = struct.unpack_from("<i", data, 8)[0]
    struct.pack_into("<ii", data, directory, offset, size)
    with pytest.raises(ValueError, match="lump 0 is outside"):
        wadextract.read_wad(data)


def test_vgm_merges_and_thins_using_shared_retrigger_rule():
    commands = (
        writes((0, 0xB0), (0, 0xBF), (2, 0x10), (3, 8)) + b"\x62"
        + writes((0, 0xBF), (2, 0x10), (3, 8)) + b"\x62\x62"
        + writes((3, 8)) + b"\x62\x66"
    )
    frames, loop, flags = apus.read_apus(vgm2apus.convert_vgm(make_vgm(commands)))
    assert frames == [[(0, 0xBF), (2, 0x10), (3, 8)], [], [], [(3, 8)]]
    assert loop is None and flags == 0


def test_vgm_loop_retains_writes_suppressed_against_intro():
    intro = writes((0, 0xBF)) + b"\x62"
    body = writes((0, 0xBF)) + b"\x62" + writes((0, 0xB0)) + b"\x62\x66"
    frames, loop, _ = apus.read_apus(vgm2apus.convert_vgm(make_vgm(intro + body, loop=len(intro))))
    assert frames == [[(0, 0xBF)], [(0, 0xBF)], [(0, 0xB0)]]
    assert loop == 1


def test_vgm_loop_inside_silence():
    frames, loop, _ = apus.read_apus(vgm2apus.convert_vgm(make_vgm(b"\x62\x62\x62\x66", loop=1)))
    assert frames == [[], [], []] and loop == 1


@pytest.mark.parametrize("rate,wait,flags", [(60, b"\x62", 0), (50, b"\x63", apus.FLAG_PAL), (0, b"\x62", 0)])
def test_vgm_timing_and_gzip_are_deterministic(rate, wait, flags):
    source = make_vgm(writes((0, 1)) + wait + wait + b"\x66", rate=rate)
    result = vgm2apus.convert_vgm(source)
    assert vgm2apus.convert_vgm(source) == result
    assert vgm2apus.convert_vgm(gzip.compress(source, mtime=0)) == result
    assert apus.read_apus(result) == ([[(0, 1)], []], None, flags)


def test_vgm_short_and_explicit_waits_accumulate_without_rounding_each_wait():
    # 16 + 719 samples = exactly one NTSC frame.
    commands = writes((0, 1)) + b"\x7f\x61\xcf\x02" + writes((0, 2)) + b"\x62\x66"
    assert apus.read_apus(vgm2apus.convert_vgm(make_vgm(commands)))[0] == [[(0, 1)], [(0, 2)]]


def test_vgm_wait_override_and_terminal_writes():
    commands = b"\x64\x62\xdf\x02\x62" + writes((0, 1)) + b"\x66"
    assert apus.read_apus(vgm2apus.convert_vgm(make_vgm(commands)))[0] == [[], [(0, 1)]]


def test_vgm_music_cap_checks_after_thinning():
    frame = writes(*[(reg, reg) for reg in range(12)])
    frames, _, _ = apus.read_apus(vgm2apus.convert_vgm(make_vgm(frame + b"\x62" + frame + b"\x62\x66")))
    assert len(frames[0]) == 12 and len(frames[1]) == 0
    with pytest.raises(ValueError, match="frame 1: 13 APU pairs"):
        vgm2apus.convert_vgm(make_vgm(b"\x62" + writes(*[(reg, reg) for reg in range(13)]) + b"\x62\x66"))


@pytest.mark.parametrize("commands,message", [
    (b"\xb4\x00", "truncated"),
    (b"\x61\x01", "truncated"),
    (b"\x62", "no end"),
    (b"\x50\x00\x66", "unsupported VGM command"),
    (b"\x67\x66", "unsupported VGM command"),
    (writes((0x16, 1)) + b"\x66", "unsupported NES"),
    (writes((0x20, 1)) + b"\x66", "unsupported NES"),
    (writes((0x80, 1)) + b"\x66", "unsupported NES"),
    (writes((0x12, 1)) + b"\x66", "DPCM"),
    (writes((0x15, 0x1F)) + b"\x66", "DPCM"),
    (b"\x64\x61\x01\x00\x66", "override"),
])
def test_vgm_rejects_unsupported_or_truncated_commands(commands, message):
    with pytest.raises(ValueError, match=message):
        vgm2apus.convert_vgm(make_vgm(commands))


@pytest.mark.parametrize("field,value,message", [
    (4, 0xFFFFFFFF, "offset"), (0x34, 0xFFFFFFFF, "offset"),
    (8, 0x150, "version"), (0x24, 59, "frame rate"),
    (0x84, 0, "one NES"), (0x84, 0x801B4F4C, "one NES"),
    (0x84, 0x401B4F4C, "one NES"), (0x1C, 1, "loop offset"),
])
def test_vgm_rejects_invalid_header_fields(field, value, message):
    data = bytearray(make_vgm(b"\x62\x66"))
    struct.pack_into("<I", data, field, value)
    with pytest.raises(ValueError, match=message):
        vgm2apus.convert_vgm(data)


@pytest.mark.parametrize("commands,loop,message", [
    (writes((0, 1)) + b"\x62\x66", 1, "command boundary"),
    (b"\x70\x62\x66", 1, "frame boundary"),
    (b"\x62\x66", 1, "no frames"),
    (writes((0, 1)) + b"\x62\x66", 3, "intro writes"),
])
def test_vgm_rejects_unrepresentable_loops(commands, loop, message):
    with pytest.raises(ValueError, match=message):
        vgm2apus.convert_vgm(make_vgm(commands, loop=loop))


@pytest.mark.parametrize("data", [b"Vgm ", b"\x1f\x8b", b"\x1f\x8b\x08\x00" + b"\x00" * 20])
def test_vgm_rejects_invalid_raw_or_gzip_header(data):
    with pytest.raises(ValueError):
        vgm2apus.convert_vgm(data)


def test_converter_clis_from_repository_root(tmp_path):
    wad = tmp_path / "synthetic.wad"
    wad.write_bytes(make_wad([("D_E1M1", b"MUS\x1a")]))
    subprocess.run([sys.executable, str(TOOLS / "wadextract.py"), str(wad), "D_*", "-o", str(tmp_path / "lumps")], check=True)
    assert (tmp_path / "lumps" / "D_E1M1.mus").read_bytes() == b"MUS\x1a"
    vgm = tmp_path / "song.vgm"
    vgm.write_bytes(make_vgm(writes((0, 1)) + b"\x62\x66"))
    outputs = []
    for index in range(2):
        output = tmp_path / f"song-{index}.apus"
        subprocess.run([sys.executable, str(TOOLS / "vgm2apus.py"), str(vgm), "-o", str(output)], check=True)
        outputs.append(output.read_bytes())
    assert outputs[0] == outputs[1]
    assert apus.read_apus(outputs[0])[0] == [[(0, 1)]]
    invalid = tmp_path / "bad.vgm"
    invalid.write_bytes(make_vgm(b"\x50\x00\x66"))
    output = tmp_path / "bad.apus"
    result = subprocess.run([sys.executable, str(TOOLS / "vgm2apus.py"), str(invalid), "-o", str(output)], capture_output=True, text=True)
    assert result.returncode == 1 and "unsupported VGM command" in result.stderr
    assert not output.exists()


def make_mus(score):
    return struct.pack("<4s6H", b"MUS\x1a", len(score), 16, 1, 0, 0, 0) + score


def vlq(value):
    result = [value & 127]
    while value >> 7:
        value >>= 7
        result.insert(0, 128 | (value & 127))
    return bytes(result)


def make_midi(*tracks, division=70, kind=None):
    header = b"MThd" + struct.pack(">I3H", 6, (len(tracks) > 1) if kind is None else kind, len(tracks), division)
    return header + b"".join(b"MTrk" + struct.pack(">I", len(track)) + track for track in tracks)


def test_mus_velocity_cache_channel_mapping_and_timing():
    score = b"\x90\xbc\x50" + vlq(140) + b"\x90\x40\x01\x0f\x24\x60"
    events, duration = mus2apus.read_mus(make_mus(score))
    assert duration == Fraction(141, 140)
    assert [(e.time, e.status, e.channel, e.first, e.second) for e in events] == [
        (0, 0x90, 0, 60, 80), (1, 0x90, 0, 64, 80),
        (Fraction(141, 140), 0x80, 9, 36, 0),
    ]


def test_mus_and_equivalent_midi_produce_identical_bytes():
    mus = make_mus(b"\x90\xbc\x7f" + vlq(140) + b"\x80\x3c\x00\x60")
    midi = make_midi(b"\x00\x90\x3c\x7f" + vlq(140) + b"\x80\x3c\x00\x00\xff\x2f\x00")
    result = mus2apus.convert_music(mus)
    assert result == mus2apus.convert_music(midi)
    frames, loop, flags = apus.read_apus(result)
    assert len(frames) == 64 and loop == 0 and flags == 0
    assert (0x15, 15) in frames[0] and (1, 8) in frames[0]
    assert frames[1] == []
    assert dict(frames[2])[0] == 0xBF
    assert any(reg == 3 for reg, _ in frames[2])
    assert all(not frame for frame in frames[3:62])
    assert dict(frames[62]) == {0: 0x30, 8: 0x80}
    assert dict(frames[-1])[0x15] == 0
    assert apus.read_apus(mus2apus.convert_music(mus, looping=False))[1] is None


def test_midi_multitrack_tempo_map_and_running_status():
    tempo = b"\x00\xff\x51\x03\x07\xa1\x20" + vlq(70) + b"\xff\x51\x03\x0f\x42\x40" + vlq(70) + b"\xff\x2f\x00"
    notes = b"\x00\x90\x3c\x7f" + vlq(70) + b"\x40\x60" + vlq(70) + b"\x3c\x00\x00\xff\x2f\x00"
    events, duration = mus2apus.read_midi(make_midi(tempo, notes))
    assert [e.time for e in events] == [Fraction(0), Fraction(1, 2), Fraction(3, 2)]
    assert [e.first for e in events] == [60, 64, 60]
    assert duration == Fraction(3, 2)


def test_music_assigns_high_notes_bass_and_percussion_under_cap():
    events = [mus2apus.Event(Fraction(0), 0x90, 0, note, 127) for note in (36, 60, 72)]
    events.append(mus2apus.Event(Fraction(0), 0x90, 9, 38, 100))
    frames, _, _ = apus.read_apus(mus2apus.arrange(events, Fraction(1)))
    first = dict(frames[2])
    assert first[2] | ((first[3] & 7) << 8) == mus2apus.note_period(72)
    assert first[6] | ((first[7] & 7) << 8) == mus2apus.note_period(60)
    assert first[10] | ((first[11] & 7) << 8) == mus2apus.note_period(36, triangle=True)
    assert first[14] == 7 and first[12] & 15 > 0
    assert max(map(len, frames)) <= 12


def test_music_volume_change_does_not_reload_timer():
    events = [mus2apus.Event(Fraction(0), 0x90, 0, 60, 127),
              mus2apus.Event(Fraction(1, 2), 0xB0, 0, 7, 64)]
    frames, _, _ = apus.read_apus(mus2apus.arrange(events, Fraction(1)))
    assert dict(frames[32]) == {0: 0xB8}


def test_music_sustain_release_and_all_sound_off():
    def event(frame, status, first, second=0):
        return mus2apus.Event(Fraction(frame, 60), status, 0, first, second)
    events = [event(0, 0x90, 60, 127), event(1, 0xB0, 64, 127),
              event(2, 0x80, 60), event(3, 0xB0, 64, 0),
              event(4, 0x90, 64, 127), event(5, 0xB0, 120)]
    frames, _, _ = apus.read_apus(mus2apus.arrange(events, Fraction(6, 60)))
    assert frames[3] == frames[4] == []
    assert dict(frames[5]) == {0: 0x30, 8: 0x80}
    assert dict(frames[6])[0] == 0xBF
    assert dict(frames[7]) == {0: 0x30, 8: 0x80}


def test_mus_controller_events_are_consumed_and_mapped():
    events, _ = mus2apus.read_mus(make_mus(b"\x40\x03\x40\x40\x08\x7f\x30\x0e\x20\x80\x60"))
    assert [(e.status, e.first, e.second) for e in events] == [(0xB0, 7, 64), (0xB0, 64, 127), (0xB0, 121, 0), (0xE0, 0, 64)]


@pytest.mark.parametrize("score,message", [
    (b"\x10\xbc", "truncated"), (b"\x90\x3c", "truncated"),
    (b"\x10\x3c", "no end"), (b"\x30\x09", "system controller"),
    (b"\x40\x0a\x00", "controller"), (b"\x10\xbc\xff", "velocity"),
    (b"\x50\x00", "unsupported MUS"),
    (b"\x90\x3c\x80\x80\x80\x80", "four bytes"),
])
def test_mus_rejects_invalid_scores(score, message):
    with pytest.raises(ValueError, match=message):
        mus2apus.read_mus(make_mus(score))


@pytest.mark.parametrize("field,value,message", [(6, 65535, "outside"), (12, 65535, "outside"), (8, 16, "channel count")])
def test_mus_rejects_invalid_header(field, value, message):
    data = bytearray(make_mus(b"\x60"))
    struct.pack_into("<H", data, field, value)
    with pytest.raises(ValueError, match=message):
        mus2apus.read_mus(data)


@pytest.mark.parametrize("track,message", [
    (b"\x00\x3c\x7f", "running status"),
    (b"\x00\x90\x3c", "truncated"),
    (b"\x00\x90\x3c\x7f", "no end-of-track"),
    (b"\x00\xff\x51\x02\x01\x02", "tempo"),
    (b"\x00\xff\x51\x03\x00\x00\x00", "tempo"),
    (b"\x00\xff\x2f\x01\x00", "end-of-track"),
    (b"\x00\xf1\x00", "unsupported MIDI"),
    (b"\x00\x90\x80\x7f", "status byte"),
    (b"\x00\x90\x3c\x7f\x00\xff\x01\x00\x00\x3c\x00", "running status"),
])
def test_midi_rejects_invalid_tracks(track, message):
    with pytest.raises(ValueError, match=message):
        mus2apus.read_midi(make_midi(track))


@pytest.mark.parametrize("division,kind,message", [(0, 0, "PPQN"), (0xE728, 0, "SMPTE"), (70, 2, "type 0 or 1")])
def test_midi_rejects_unsupported_header(division, kind, message):
    with pytest.raises(ValueError, match=message):
        mus2apus.read_midi(make_midi(b"\x00\xff\x2f\x00", division=division, kind=kind))


def test_midi_consumes_program_pressure_pitch_and_sysex():
    track = b"\x00\xc0\x05\x00\xd0\x7f\x00\xa0\x3c\x7f\x00\xe0\x00\x40\x00\xf0\x02\x01\xf7\x00\xff\x2f\x00"
    events, duration = mus2apus.read_midi(make_midi(track))
    assert [e.status for e in events] == [0xC0, 0xD0, 0xA0, 0xE0]
    assert duration == 0


@pytest.mark.parametrize("kind", ["pulse", "noise"])
def test_synthesized_sfx_tracks_envelope_is_deterministic_and_stops(kind):
    rate = 11025
    samples = 128 + 70 * np.sin(2 * np.pi * 440 * np.arange(rate // 5) / rate)
    samples[len(samples) // 2 :] = 128 + (samples[len(samples) // 2 :] - 128) / 2
    result = sfx2apu.synthesize(samples, rate, kind)
    assert result == sfx2apu.synthesize(samples, rate, kind)
    frames, loop, flags = apus.read_apus(result)
    assert loop is None and flags == 0
    assert max(map(len, frames)) <= 4
    assert {reg for frame in frames for reg, _ in frame} <= sfx2apu._REGISTERS[kind]
    control = 4 if kind == "pulse" else 12
    assert dict(frames[0])[control] & 15 == 15
    assert dict(frames[-1])[control] & 15 == 0
    assert any((value & 15) in (7, 8) for frame in frames for reg, value in frame if reg == control)
    if kind == "pulse":
        first = dict(frames[0])
        period = first[6] | ((first[7] & 7) << 8)
        assert abs(sfx2apu._CPU_NTSC / (16 * (period + 1)) - 440) < 15
        assert first[5] == 8


@pytest.mark.parametrize("kind,text,expected", [
    ("pulse", "# item\n04=bf 05=08 06=fd 07=08\n-\n04=30 # mute", [[(4, 0xBF), (5, 8), (6, 0xFD), (7, 8)], [], [(4, 0x30)], [(4, 0x30)]]),
    ("noise", "0c=3f 0e=07 0f=08\n-", [[(12, 0x3F), (14, 7), (15, 8)], [], [(12, 0x30)]]),
])
def test_hand_authored_sfx_frames_are_exact(kind, text, expected):
    result = sfx2apu.script_from_text(text, kind)
    assert apus.read_apus(result) == (expected, None, 0)
    assert sfx2apu.script_from_text(text, kind) == result


@pytest.mark.parametrize("text,message", [("", "no frames"), ("04", "reg=value"), ("04=gg", "reg=value"), ("15=0f", "outside pulse"), ("04=100", "outside pulse"), ("04=bf 04=30", "duplicate")])
def test_hand_authored_sfx_validation(text, message):
    with pytest.raises(ValueError, match=message):
        sfx2apu.script_from_text(text, "pulse")


@pytest.mark.parametrize("samples,rate", [([], 11025), ([128], 0), ([[128]], 11025), ([float("nan")], 11025), ([256], 11025)])
def test_sfx_rejects_invalid_pcm(samples, rate):
    with pytest.raises(ValueError):
        sfx2apu.synthesize(samples, rate, "pulse")


def test_pulse_pitch_handles_nonperiodic_and_silent_windows():
    for window in (np.zeros(300), np.linspace(-128, 127, 300),
                   np.concatenate((np.ones(15), -np.ones(15))),
                   np.random.default_rng(0).normal(size=300)):
        assert 0 < sfx2apu._pitch(window, 11025) < 3000
    # All local peaks are negative: scaling the best peak used to leave
    # no candidate above the threshold and raise StopIteration.
    assert sfx2apu._pitch(np.random.default_rng(8).normal(size=10), 11025) == 220


@pytest.mark.parametrize("kind", ["pulse", "noise"])
def test_silent_sfx_leaves_the_voice_muted(kind):
    frames, _, _ = apus.read_apus(sfx2apu.synthesize(np.full(300, 128), 11025, kind))
    image = {}
    for frame in frames:
        image.update(frame)
    assert image[4 if kind == "pulse" else 12] & 15 == 0


def test_auto_music_and_sfx_clis_are_deterministic(tmp_path):
    mus = make_mus(b"\x90\xbc\x7f" + vlq(140) + b"\x80\x3c\x00\x60")
    input_music = tmp_path / "music.mus"
    input_music.write_bytes(mus)
    wad = tmp_path / "music.wad"
    wad.write_bytes(make_wad([(name, mus) for name in mus2apus.SHAREWARE_MUSIC]))
    rate = 11025
    samples = bytes(16) + (128 + 70 * np.sin(2 * np.pi * 440 * np.arange(1800) / rate)).astype(np.uint8).tobytes() + bytes(16)
    lump = tmp_path / "sound.lmp"
    lump.write_bytes(struct.pack("<HHI", 3, rate, len(samples)) + samples)
    for name, source, arguments in [("mus2apus", input_music, ["--auto"]), ("sfx2apu", lump, ["--auto", "--kind", "pulse"])]:
        results = []
        for index in range(2):
            output = tmp_path / f"{name}-{index}.apus"
            subprocess.run([sys.executable, str(TOOLS / (name + ".py")), str(source), *arguments, "-o", str(output)], check=True)
            results.append(output.read_bytes())
        assert results[0] == results[1]
        apus.read_apus(results[0])
    for index in range(2):
        output = tmp_path / f"bank-{index}"
        subprocess.run([sys.executable, str(TOOLS / "mus2apus.py"), str(wad), "--auto", "-o", str(output)], check=True)
        assert {p.stem for p in output.iterdir()} == set(mus2apus.SHAREWARE_MUSIC)
    assert [(p.name, p.read_bytes()) for p in sorted((tmp_path / "bank-0").iterdir())] == [(p.name, p.read_bytes()) for p in sorted((tmp_path / "bank-1").iterdir())]


def test_shareware_wad_music_and_effects_when_available():
    path = os.environ.get("FCPICO_AUDIO_WAD")
    if not path:
        pytest.skip("set FCPICO_AUDIO_WAD to a local shareware WAD for asset acceptance")
    lumps = wadextract.read_wad(Path(path).read_bytes())
    assert len(mus2apus.SHAREWARE_MUSIC) == 13
    for name in mus2apus.SHAREWARE_MUSIC:
        stream = mus2apus.convert_music(lumps[name])
        assert stream == mus2apus.convert_music(lumps[name]), name
        frames, loop, flags = apus.read_apus(stream)
        assert frames and loop == 0 and flags == 0
        assert max(map(len, frames)) <= 12
        assert dict(frames[-1]).get(0x15) == 0
    sound_count = 0
    for name, lump in lumps.items():
        if not name.startswith("DS"):
            continue
        fmt, rate, length = struct.unpack_from("<HHI", lump)
        assert fmt == 3 and len(lump) >= 8 + length and length > 32, name
        pcm = list(lump[24 : 8 + length - 16])
        for kind in ("pulse", "noise"):
            stream = sfx2apu.synthesize(pcm, rate, kind)
            assert stream == sfx2apu.synthesize(pcm, rate, kind), (name, kind)
            frames, loop, flags = apus.read_apus(stream)
            assert loop is None and flags == 0 and max(map(len, frames)) <= 4
            image = {}
            for frame in frames:
                image.update(frame)
            assert image[4 if kind == "pulse" else 12] & 15 == 0
        sound_count += 1
    assert sound_count > 0
