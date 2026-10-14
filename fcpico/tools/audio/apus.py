# SPDX-License-Identifier: BSD-3-Clause
"""Writer/reader for the ``.apus`` register-write stream format.

Format::

    offset  size  field
    0       4     magic "APUS"
    4       1     version = 1
    5       1     flags: bit0 PAL timing (period table), bit1 reserved
    6       2     loop_frame (little endian; 0xFFFF = no loop)
    8       4     frame_count
    12      4     body_len
    16      ...   body

    body:   frame := count(u8: 0..15) then count x (reg u8 in 0..0x17, value u8)
            | silence(u8: 0x80 | k, k in 1..127 frames with no writes)

``count <= 12`` is enforced here (the tool cap from the same section) so
that 3-4 pairs per frame remain for SFX under the v2 cap of 15 pairs
(``APU_PAIRS_MAX_V2``); the wire format itself allows up to 15. ``reg`` is
checked against 0..0x17 -- the full raw APU register file $4000-$4017
(mixer/DMC control $4015 and the frame-counter register $4017 included),
which is the range the sequencer is allowed to replay even though the
conventional music register range is only 0..0x13.
"""

from __future__ import annotations

import struct

MAGIC = b"APUS"
VERSION = 1

FLAG_PAL = 0x01
_FLAG_RESERVED_MASK = 0x02

NO_LOOP = 0xFFFF

MIN_REG = 0x00
MAX_REG = 0x17

MAX_PAIRS_PER_FRAME = 12
MAX_SILENCE_RUN = 0x7F  # k in 1..127

_HEADER_STRUCT = struct.Struct("<4sBBHII")
HEADER_SIZE = _HEADER_STRUCT.size  # 16

# reg -> (channel base register, low-period register) for the four
# high-period/length-counter registers ($4003/$4007/$400B/$400F).
_HIGH_PERIOD_CHANNEL_BASE = {0x03: 0x00, 0x07: 0x04, 0x0B: 0x08, 0x0F: 0x0C}
_HIGH_PERIOD_LOW_REG = {0x03: 0x02, 0x07: 0x06, 0x0B: 0x0A, 0x0F: 0x0E}


def write_apus(frames, loop_frame: int | None, pal: bool = False) -> bytes:
    """Encode ``frames`` (a list of per-frame ``[(reg, value), ...]`` lists,
    already thinned -- see :func:`thin_register_writes`) as an ``.apus``
    byte stream.

    Consecutive empty frames (``[]``) are run-length-encoded as one or more
    ``0x80 | k`` bytes with ``k`` in 1..127, splitting longer runs across
    several such bytes. A non-empty frame is written as a count byte
    followed by ``count`` ``(reg, value)`` byte pairs.

    Raises ``ValueError`` naming the offending frame's index if a frame has
    more than :data:`MAX_PAIRS_PER_FRAME` pairs, if any register is outside
    0..0x17, or if any value is outside 0..0xFF. Raises ``ValueError`` if
    ``loop_frame`` does not fit the 16-bit field or collides with the
    ``0xFFFF`` "no loop" sentinel.
    """
    if loop_frame is not None and loop_frame == NO_LOOP:
        raise ValueError(
            f"loop_frame {NO_LOOP:#x} collides with the 'no loop' sentinel"
        )
    if loop_frame is None:
        loop_word = NO_LOOP
    else:
        if not (0 <= loop_frame < NO_LOOP):
            raise ValueError(f"loop_frame {loop_frame} does not fit a 16-bit field")
        loop_word = loop_frame

    body = bytearray()
    n = len(frames)
    i = 0
    while i < n:
        frame = frames[i]
        if len(frame) == 0:
            run = 0
            while i < n and len(frames[i]) == 0 and run < MAX_SILENCE_RUN:
                run += 1
                i += 1
            body.append(0x80 | run)
            continue

        if len(frame) > MAX_PAIRS_PER_FRAME:
            raise ValueError(
                f"frame {i}: {len(frame)} (reg, value) pairs exceeds the "
                f"{MAX_PAIRS_PER_FRAME}-pair-per-frame tool cap that leaves "
                f"room for SFX"
            )
        body.append(len(frame))
        for reg, value in frame:
            if not (MIN_REG <= reg <= MAX_REG):
                raise ValueError(
                    f"frame {i}: register {reg:#04x} out of range "
                    f"{MIN_REG:#04x}..{MAX_REG:#04x}"
                )
            if not (0 <= value <= 0xFF):
                raise ValueError(
                    f"frame {i}: value {value} for register {reg:#04x} "
                    f"out of range 0..0xff"
                )
            body.append(reg)
            body.append(value)
        i += 1

    flags = FLAG_PAL if pal else 0
    header = _HEADER_STRUCT.pack(MAGIC, VERSION, flags, loop_word, n, len(body))
    return header + bytes(body)


def read_apus(data: bytes):
    """Decode an ``.apus`` byte stream, the inverse of :func:`write_apus`.

    Returns ``(frames, loop_frame, flags)``: ``frames`` is a list of
    ``[(reg, value), ...]`` lists (one per frame, silence runs expanded back
    into individual empty-list frames), ``loop_frame`` is ``None`` when the
    header's loop field is the ``0xFFFF`` sentinel or else the frame index,
    and ``flags`` is the raw flags byte (``flags & FLAG_PAL`` tells PAL vs
    NTSC timing).

    Round-trips exactly with :func:`write_apus`: for any ``frames`` list
    accepted by ``write_apus`` (each frame with 0..12 pairs, registers in
    0..0x17), ``read_apus(write_apus(frames, loop, pal))`` reproduces
    ``frames``, ``loop`` and ``pal`` (via ``flags & FLAG_PAL``).
    """
    data = bytes(data)
    if len(data) < HEADER_SIZE:
        raise ValueError(
            f"apus data is {len(data)} bytes, shorter than the "
            f"{HEADER_SIZE}-byte header"
        )
    magic, version, flags, loop_word, frame_count, body_len = _HEADER_STRUCT.unpack_from(
        data, 0
    )
    if magic != MAGIC:
        raise ValueError(f"bad magic {magic!r}, expected {MAGIC!r}")
    if version != VERSION:
        raise ValueError(f"unsupported .apus version {version}, expected {VERSION}")

    body = data[HEADER_SIZE : HEADER_SIZE + body_len]
    if len(body) != body_len:
        raise ValueError(
            f"apus body is {len(body)} bytes, shorter than body_len={body_len} "
            "in the header"
        )

    frames: list[list[tuple[int, int]]] = []
    pos = 0
    while len(frames) < frame_count:
        if pos >= len(body):
            raise ValueError(
                f"apus body ended at offset {pos} after {len(frames)} of "
                f"{frame_count} frames"
            )
        token = body[pos]
        pos += 1
        if token & 0x80:
            run = token & 0x7F
            if run == 0:
                raise ValueError(
                    f"silence token 0x{token:02x} at body offset {pos - 1} "
                    "has run length 0"
                )
            frames.extend([] for _ in range(run))
        else:
            count = token
            frame: list[tuple[int, int]] = []
            for _ in range(count):
                if pos + 2 > len(body):
                    raise ValueError(
                        f"apus body truncated inside frame {len(frames)}'s "
                        "register pairs"
                    )
                frame.append((body[pos], body[pos + 1]))
                pos += 2
            frames.append(frame)

        if len(frames) > frame_count:
            raise ValueError(
                f"apus body decoded {len(frames)} frames, more than the "
                f"header's frame_count={frame_count}"
            )

    loop_frame = None if loop_word == NO_LOOP else loop_word
    return frames, loop_frame, flags


def thin_register_writes(frames_of_full_register_images):
    """Apply the note-retrigger rule to a per-frame register-image log.

    ``frames_of_full_register_images`` is a list with one ``dict[int, int]``
    per frame, mapping ``reg -> value`` for exactly the registers that frame
    of the *source* (a VGM log or a naive full-state tracker dump) says to
    write -- a sparse per-tick write set, not a complete snapshot of every
    APU register. Returns the thinned ``frames`` list in the shape
    :func:`write_apus` expects: ``[(reg, value), ...]`` per frame, in
    ascending register order.

    Rule (FC PICO GB's note-retrigger rule): rewriting one of the four high-period/length-counter registers
    ``$4003``/``$4007``/``$400B``/``$400F`` restarts that channel's envelope
    and length counter, which clicks audibly if done every frame merely
    because the source log redundantly repeats the channel's state. So:

    * For every register *except* those four: emit ``(reg, value)`` only
      when ``value`` differs from the value last *emitted* for that
      register (never-yet-emitted counts as "differs"). This is plain
      redundant-write suppression, tracked across the whole stream, not
      just against the previous frame.
    * For each of ``$4003``/``$4007``/``$400B``/``$400F`` (present in this
      frame's image): emit it only when

      (a) its channel had no write at all in the *previous* frame's source
          image -- "channel written" means any of that channel's 4
          registers (e.g. $4000-$4003 for pulse 1) is a key in that
          previous image; no write at all is a gap, so this frame starts a
          new note; frame 0 has no previous frame, so this is always true
          for it; or
      (b) the period changed: either the channel's low-period register
          (``$4002``/``$4006``/``$400A``/``$400E``) is present in *this*
          frame's image with a value that differs from what was last
          emitted for it, or bits 0-2 of this high register's value differ
          from bits 0-2 of what was last emitted for it.

      Unlike the general rule, this is not a plain "value changed" check:
      if (a) or (b) holds the register is emitted even when its byte value
      happens to equal what is already loaded (a genuinely new note or a
      period change legitimately retriggers the envelope); if neither
      holds it is *not* emitted even though the source image mentions it.

    Volume/duty (``$4000``/``$4004``/``$400C``) and sweep
    (``$4001``/``$4005``) go through the general rule and are written
    whenever they change.
    """
    last_emitted: dict[int, int] = {}
    prev_image: dict[int, int] = {}
    out_frames: list[list[tuple[int, int]]] = []

    for image in frames_of_full_register_images:
        # All of this frame's decisions compare against the hardware state
        # as of *before* this frame -- a snapshot taken up front, rather
        # than the live `last_emitted` dict -- so that (for example) the
        # low-period register's own update earlier in this same frame's
        # (ascending-register-order) loop can't hide a period change from
        # the high-period register's check that follows it.
        snapshot = dict(last_emitted)
        out: list[tuple[int, int]] = []
        for reg in sorted(image):
            value = image[reg]
            if reg in _HIGH_PERIOD_CHANNEL_BASE:
                base = _HIGH_PERIOD_CHANNEL_BASE[reg]
                channel_written_prev = any(
                    (base + k) in prev_image for k in range(4)
                )
                new_note = not channel_written_prev

                period_changed = False
                low_reg = _HIGH_PERIOD_LOW_REG[reg]
                if low_reg in image and image[low_reg] != snapshot.get(low_reg):
                    period_changed = True
                prev_high = snapshot.get(reg)
                if prev_high is None or (value & 0x07) != (prev_high & 0x07):
                    period_changed = True

                if new_note or period_changed:
                    out.append((reg, value))
                    last_emitted[reg] = value
            else:
                if value != snapshot.get(reg):
                    out.append((reg, value))
                    last_emitted[reg] = value
        out_frames.append(out)
        prev_image = image

    return out_frames
