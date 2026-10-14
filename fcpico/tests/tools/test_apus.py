# SPDX-License-Identifier: BSD-3-Clause
"""Tests for tools/audio/apus.py: the ``.apus`` writer/reader and the
note-retrigger thinning rule.
"""

from __future__ import annotations

import random
import struct

import pytest

from audio import apus

HEADER_STRUCT = struct.Struct("<4sBBHII")


# ---------------------------------------------------------------------------
# Header layout: magic, version, flags, loop_frame, frame_count, body_len
# ---------------------------------------------------------------------------


def test_header_is_16_bytes_with_documented_layout():
    data = apus.write_apus([[(0x00, 1)]], loop_frame=None, pal=False)
    assert apus.HEADER_SIZE == 16
    magic, version, flags, loop_word, frame_count, body_len = HEADER_STRUCT.unpack_from(
        data, 0
    )
    assert magic == b"APUS"
    assert version == 1
    assert flags == 0
    assert loop_word == 0xFFFF
    assert frame_count == 1
    assert body_len == len(data) - apus.HEADER_SIZE


def test_pal_flag_set_in_header():
    data = apus.write_apus([[]], loop_frame=None, pal=True)
    _, _, flags, _, _, _ = HEADER_STRUCT.unpack_from(data, 0)
    assert flags & apus.FLAG_PAL

    data_ntsc = apus.write_apus([[]], loop_frame=None, pal=False)
    _, _, flags_ntsc, _, _, _ = HEADER_STRUCT.unpack_from(data_ntsc, 0)
    assert not (flags_ntsc & apus.FLAG_PAL)


def test_loop_frame_encoded_at_documented_offset():
    data = apus.write_apus([[], [], []], loop_frame=1, pal=False)
    (loop_word,) = struct.unpack_from("<H", data, 6)
    assert loop_word == 1


def test_no_loop_uses_0xffff_sentinel():
    data = apus.write_apus([[]], loop_frame=None)
    (loop_word,) = struct.unpack_from("<H", data, 6)
    assert loop_word == 0xFFFF


# ---------------------------------------------------------------------------
# write_apus / read_apus round trips
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("loop_frame", [None, 0, 4])
def test_round_trip_mixed_frames(loop_frame):
    frames = [
        [],
        [(0x00, 0xBF), (0x01, 0x00), (0x02, 0x10), (0x03, 0x05)],
        [],
        [],
        [(0x15, 0x0F)],
    ]
    data = apus.write_apus(frames, loop_frame=loop_frame, pal=False)
    out_frames, out_loop, flags = apus.read_apus(data)
    assert out_frames == frames
    assert out_loop == loop_frame
    assert flags == 0


def test_round_trip_pal_flag():
    data = apus.write_apus([[(0x00, 1)]], loop_frame=None, pal=True)
    _, _, flags = apus.read_apus(data)
    assert flags & apus.FLAG_PAL


def test_round_trip_empty_stream():
    data = apus.write_apus([], loop_frame=None)
    out_frames, loop_frame, _ = apus.read_apus(data)
    assert out_frames == []
    assert loop_frame is None


def test_round_trip_long_silence_run_splits_across_127_frame_tokens():
    frames = [[] for _ in range(300)]
    data = apus.write_apus(frames, loop_frame=None)
    body = data[apus.HEADER_SIZE :]
    # ceil(300 / 127) == 3 run-length tokens: 127 + 127 + 46
    assert len(body) == 3
    assert body[0] == 0x80 | 127
    assert body[1] == 0x80 | 127
    assert body[2] == 0x80 | (300 - 254)
    out_frames, _, _ = apus.read_apus(data)
    assert out_frames == frames


def test_round_trip_max_pairs_per_frame():
    frame = [(reg, (reg * 7) & 0xFF) for reg in range(apus.MAX_PAIRS_PER_FRAME)]
    data = apus.write_apus([frame], loop_frame=None)
    out_frames, _, _ = apus.read_apus(data)
    assert out_frames == [frame]


def test_round_trip_register_and_value_edges():
    frame = [(apus.MIN_REG, 0), (apus.MAX_REG, 0xFF)]
    data = apus.write_apus([frame], loop_frame=None)
    out_frames, _, _ = apus.read_apus(data)
    assert out_frames == [frame]


def test_write_apus_body_is_consumed_exactly_with_no_leftover_bytes():
    frames = [[(0, 1)], [], [], [(2, 3), (4, 5)], [], [], []]
    data = apus.write_apus(frames, loop_frame=None)
    magic, version, flags, loop_word, frame_count, body_len = HEADER_STRUCT.unpack_from(
        data, 0
    )
    body = data[apus.HEADER_SIZE :]
    assert len(body) == body_len

    # Manually re-walk the body the way read_apus does, and confirm the
    # cursor lands exactly on len(body) once frame_count frames have been
    # decoded -- i.e. write_apus never emits trailing padding read_apus
    # would silently ignore.
    pos = 0
    decoded = 0
    while decoded < frame_count:
        token = body[pos]
        pos += 1
        if token & 0x80:
            decoded += token & 0x7F
        else:
            decoded += 1
            pos += 2 * token
    assert pos == len(body)


# ---------------------------------------------------------------------------
# write_apus error handling
# ---------------------------------------------------------------------------


def test_write_apus_rejects_too_many_pairs_and_names_the_frame():
    frame = [(0, 0)] * (apus.MAX_PAIRS_PER_FRAME + 1)
    with pytest.raises(ValueError, match="frame 2"):
        apus.write_apus([[], [], frame], loop_frame=None)


def test_write_apus_accepts_exactly_the_pair_cap():
    frame = [(0, 0)] * apus.MAX_PAIRS_PER_FRAME
    # must not raise
    apus.write_apus([frame], loop_frame=None)


def test_write_apus_rejects_register_out_of_range():
    with pytest.raises(ValueError):
        apus.write_apus([[(apus.MAX_REG + 1, 0)]], loop_frame=None)
    with pytest.raises(ValueError):
        apus.write_apus([[(-1, 0)]], loop_frame=None)


def test_write_apus_rejects_value_out_of_range():
    with pytest.raises(ValueError):
        apus.write_apus([[(0x00, 256)]], loop_frame=None)
    with pytest.raises(ValueError):
        apus.write_apus([[(0x00, -1)]], loop_frame=None)


def test_write_apus_rejects_loop_sentinel_collision():
    with pytest.raises(ValueError):
        apus.write_apus([[]], loop_frame=0xFFFF)


def test_write_apus_rejects_loop_out_of_16_bit_range():
    with pytest.raises(ValueError):
        apus.write_apus([[]], loop_frame=-1)
    with pytest.raises(ValueError):
        apus.write_apus([[]], loop_frame=0x10000)


# ---------------------------------------------------------------------------
# read_apus error handling
# ---------------------------------------------------------------------------


def test_read_apus_rejects_short_header():
    with pytest.raises(ValueError):
        apus.read_apus(b"APUS")


def test_read_apus_rejects_bad_magic():
    data = bytearray(apus.write_apus([[]], loop_frame=None))
    data[0:4] = b"XXXX"
    with pytest.raises(ValueError):
        apus.read_apus(bytes(data))


def test_read_apus_rejects_unsupported_version():
    data = bytearray(apus.write_apus([[]], loop_frame=None))
    data[4] = 2
    with pytest.raises(ValueError):
        apus.read_apus(bytes(data))


def test_read_apus_rejects_body_shorter_than_declared():
    data = apus.write_apus([[(0, 1), (2, 3)]], loop_frame=None)
    with pytest.raises(ValueError):
        apus.read_apus(data[:-1])


def test_read_apus_rejects_truncated_register_pair():
    data = apus.write_apus([[(0, 1), (2, 3)]], loop_frame=None)
    # Chop the body down but leave body_len claiming the full length so the
    # length pre-check passes and decoding runs out of bytes mid-pair.
    truncated = bytearray(data[:-1])
    struct.pack_into("<I", truncated, 12, len(data) - apus.HEADER_SIZE - 1)
    with pytest.raises(ValueError):
        apus.read_apus(bytes(truncated))


def test_read_apus_rejects_zero_length_silence_token():
    data = bytearray(apus.write_apus([[]], loop_frame=None))
    assert data[apus.HEADER_SIZE] == 0x81
    data[apus.HEADER_SIZE] = 0x80  # k == 0
    with pytest.raises(ValueError):
        apus.read_apus(bytes(data))


def test_read_apus_rejects_frame_count_overshoot():
    data = bytearray(apus.write_apus([[], []], loop_frame=None))
    struct.pack_into("<I", data, 8, 1)  # claim 1 frame; body still encodes 2
    with pytest.raises(ValueError):
        apus.read_apus(bytes(data))


def test_read_apus_rejects_running_out_of_body_before_frame_count():
    data = bytearray(apus.write_apus([[]], loop_frame=None))
    struct.pack_into("<I", data, 8, 5)  # claim 5 frames; body only encodes 1
    with pytest.raises(ValueError):
        apus.read_apus(bytes(data))


# ---------------------------------------------------------------------------
# thin_register_writes: general (non-high-period) registers
# ---------------------------------------------------------------------------


def test_general_register_suppressed_when_unchanged():
    images = [
        {0x00: 0x9F},
        {0x00: 0x9F},
        {0x00: 0xAF},
    ]
    out = apus.thin_register_writes(images)
    assert out == [[(0x00, 0x9F)], [], [(0x00, 0xAF)]]


def test_general_register_first_write_always_emitted():
    out = apus.thin_register_writes([{0x01: 0x00}])
    assert out == [[(0x01, 0x00)]]


def test_general_register_tracked_across_gap_frames():
    images = [
        {0x00: 0x50},
        {},  # gap frame: register 0x00 not mentioned at all
        {0x00: 0x50},  # same value as last *emitted* -- still redundant
    ]
    out = apus.thin_register_writes(images)
    assert out == [[(0x00, 0x50)], [], []]


def test_output_pairs_are_in_ascending_register_order():
    out = apus.thin_register_writes([{0x05: 1, 0x00: 2, 0x01: 3}])
    regs = [reg for reg, _ in out[0]]
    assert regs == sorted(regs)


# ---------------------------------------------------------------------------
# thin_register_writes: high-period ($4003/$4007/$400B/$400F) retrigger rule
# ---------------------------------------------------------------------------


def test_high_period_emitted_on_new_note_even_if_byte_value_unchanged():
    images = [
        {0x00: 0x80, 0x01: 0x00, 0x02: 0x10, 0x03: 0x05},
        {},  # channel silent for a frame in the source
        {0x03: 0x05},  # rewritten with the SAME byte value: still a new note
    ]
    out = apus.thin_register_writes(images)
    assert out[0] == [(0x00, 0x80), (0x01, 0x00), (0x02, 0x10), (0x03, 0x05)]
    assert out[1] == []
    assert out[2] == [(0x03, 0x05)]


def test_high_period_suppressed_when_channel_continues_and_period_unchanged():
    images = [
        {0x00: 0x80, 0x01: 0x00, 0x02: 0x10, 0x03: 0x05},
        {0x00: 0x90, 0x03: 0x05},  # channel still active; high reg redundant
    ]
    out = apus.thin_register_writes(images)
    assert out[1] == [(0x00, 0x90)]


def test_high_period_emitted_when_low_period_register_changes_in_the_same_frame():
    """The same-frame-ordering scenario: within one frame's image, both the
    low-period register (0x02, processed first in ascending order) and the
    high-period register (0x03) are rewritten. The high register's "period
    changed" check must compare against the register state as of the START
    of the frame -- a naive implementation that instead compares against a
    live dict already mutated by 0x02's own emission (earlier in the very
    same ascending-order loop) would wrongly conclude nothing changed and
    drop 0x03, since by the time 0x03 is examined the dict already agrees
    with the new value.
    """
    images = [
        {0x00: 0x80, 0x01: 0x00, 0x02: 0x10, 0x03: 0x05},  # frame 0: new note
        {0x02: 0x11, 0x03: 0x05},  # frame 1: low period changes; high byte's
        # own bits are unchanged (still 0x05)
    ]
    out = apus.thin_register_writes(images)
    assert (0x02, 0x11) in out[1]
    assert (0x03, 0x05) in out[1]


def test_high_period_suppressed_when_low_reg_rewritten_to_same_value():
    images = [
        {0x00: 0x80, 0x01: 0x00, 0x02: 0x10, 0x03: 0x05},
        {0x00: 0x81, 0x02: 0x10, 0x03: 0x05},  # 0x02 present but unchanged
    ]
    out = apus.thin_register_writes(images)
    assert (0x03, 0x05) not in out[1]


def test_high_period_emitted_when_period_bits_change_alone():
    images = [
        {0x00: 0x80, 0x01: 0x00, 0x02: 0x10, 0x03: 0x02},
        {0x00: 0x80, 0x03: 0x03},  # low reg absent; bits0-2 of high reg change
    ]
    out = apus.thin_register_writes(images)
    assert (0x03, 0x03) in out[1]


def test_high_period_length_counter_bits_alone_do_not_retrigger():
    # Bits 3-7 of the high register are the length-counter load; changing
    # only those (with no period change and no new note) must not retrigger.
    images = [
        {0x00: 0x80, 0x01: 0x00, 0x02: 0x10, 0x03: 0x02},
        {0x00: 0x80, 0x03: 0xFA},  # bits0-2 still 0b010; bits3-7 differ
    ]
    out = apus.thin_register_writes(images)
    assert (0x03, 0xFA) not in out[1]


def test_high_period_new_note_beats_a_same_value_general_register_check():
    # A high-period register's value can equal what's already loaded and
    # still need emitting purely because it is a new note (rule (a)) -- the
    # general "only if value differs" rule must NOT apply to it.
    images = [
        {0x00: 0x80, 0x01: 0x00, 0x02: 0x10, 0x03: 0x05},
        {},  # gap: channel goes idle
        {0x00: 0x80, 0x01: 0x00, 0x02: 0x10, 0x03: 0x05},  # identical retrigger
    ]
    out = apus.thin_register_writes(images)
    assert (0x03, 0x05) in out[2]


@pytest.mark.parametrize(
    "base,high", [(0x00, 0x03), (0x04, 0x07), (0x08, 0x0B), (0x0C, 0x0F)]
)
def test_high_period_channel_mapping_new_note(base, high):
    images = [
        {},
        {base: 0x80, base + 1: 0x00, base + 2: 0x10, high: 0x05},
    ]
    out = apus.thin_register_writes(images)
    assert (high, 0x05) in out[1]


def test_frame_zero_has_no_previous_frame_so_new_note_is_always_true():
    images = [{0x0C: 0x30, 0x0D: 0x00, 0x0E: 0x08, 0x0F: 0x40}]
    out = apus.thin_register_writes(images)
    assert (0x0F, 0x40) in out[0]


def test_thinned_output_round_trips_through_write_and_read_apus():
    images = [
        {0x00: 0x80, 0x01: 0x00, 0x02: 0x10, 0x03: 0x05},
        {},
        {0x00: 0x80, 0x03: 0x05},
        {0x02: 0x20, 0x03: 0x05},
        {},
        {},
    ]
    frames = apus.thin_register_writes(images)
    data = apus.write_apus(frames, loop_frame=0)
    out_frames, loop_frame, _ = apus.read_apus(data)
    assert out_frames == frames
    assert loop_frame == 0


# ---------------------------------------------------------------------------
# Randomised cross-check of the note-retrigger rule's stated invariants
# ---------------------------------------------------------------------------


def _independent_rule_check(images, out_frames):
    """Independently re-derive, from the plain-English note-retrigger
    rule, what each frame's thinned output should contain, and compare.

    Written directly against the spec text rather than by reading
    thin_register_writes' source, as a second opinion alongside the
    example-based tests above (which pin down the tricky same-frame
    ordering case precisely).
    """
    high_regs = {0x03: 0x00, 0x07: 0x04, 0x0B: 0x08, 0x0F: 0x0C}
    low_of = {0x03: 0x02, 0x07: 0x06, 0x0B: 0x0A, 0x0F: 0x0E}

    last_emitted: dict[int, int] = {}
    prev_image: dict[int, int] = {}

    for image, out in zip(images, out_frames):
        out_map = dict(out)
        snapshot = dict(last_emitted)
        expected_regs = set()

        for reg in sorted(image):
            value = image[reg]
            if reg in high_regs:
                base = high_regs[reg]
                channel_written_prev = any((base + k) in prev_image for k in range(4))
                new_note = not channel_written_prev
                low_reg = low_of[reg]
                period_changed = low_reg in image and image[low_reg] != snapshot.get(
                    low_reg
                )
                prev_high = snapshot.get(reg)
                if prev_high is None or (value & 0x07) != (prev_high & 0x07):
                    period_changed = True
                emit = new_note or period_changed
            else:
                emit = value != snapshot.get(reg)

            if emit:
                expected_regs.add(reg)
                assert out_map.get(reg) == value
                last_emitted[reg] = value

        assert set(out_map) == expected_regs
        prev_image = image


def test_thin_register_writes_matches_independent_reading_of_the_rule():
    rng = random.Random(20260910)
    regs = list(range(0x00, 0x10))
    for _ in range(100):
        n_frames = rng.randint(1, 25)
        images = []
        for _ in range(n_frames):
            if rng.random() < 0.25:
                images.append({})
                continue
            chosen = rng.sample(regs, rng.randint(1, 8))
            images.append({r: rng.randint(0, 255) for r in chosen})
        out = apus.thin_register_writes(images)
        _independent_rule_check(images, out)
