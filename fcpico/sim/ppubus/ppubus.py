# SPDX-License-Identifier: BSD-3-Clause
"""ppubus.py -- host model of the console side of the fcbus PPU bus (L3).

Models qualifying `$2007` read strobes and NMI-time `$2007` write traffic at
the level of *counts and ordering*, without emulating the 6502 or the PPU's
own video timing.

This model is calibrated against the NES-001 trace in
``tests/fixtures/hw_trace_ntsc/trace_6.hex``. The physical board selects 66
reads on the pre-render line and 64 on every visible line. The transmitter
and counter use the same CS1 and /RD tests, so every selected read both emits
a byte and decrements the counter.

``reads_per_line`` (default 64 = 32 tiles, the picture's visible width)
    Strobes the PIO counter ``fcppu_rna`` attributes to a line. This is
    what ``PPU_COUNT_VAL`` is compared against, so it alone determines
    :meth:`PpuBus.frame_read_count`.
``prerender_reads`` defaults to the measured 66. The NMI issues one dummy
read and ``mailbox_len`` data reads, giving 15,491 physical qualifying reads
for v1. ``fcppu_rna`` publishes ``!X`` before its ``jmp x--`` decrement, so
its value is a zero-based last-read index: 15,490. :meth:`frame_read_count`
models that reported value, while :meth:`frame_qualifying_read_count` and
:meth:`frame_byte_count` model the 15,491 physical strobes.

Buffer layout note: because of this, :meth:`PpuBus.reconstruct` does **not**
assume the same absolute byte offsets as ``fcpico.stream`` (whose
``VRAM_MAILBOX_OFF`` = 15,426 is a real, unambiguous C-pointer offset into
the firmware's buffer). Instead ``reconstruct()`` walks the byte stream using
:meth:`PpuBus.frame_events`'s own structure (pre-render filler,
``reads_per_line`` bytes per visible line, the dummy, the mailbox) and packs
each line's bytes into pixels with ``fcpico.stream.unpack_run`` -- the same
bit-level convention (MSB-first pixel, low byte plane 0 / high byte plane 1)
``convVram()`` and ``fcpico.stream`` use, just not the same byte *offsets*.
:meth:`PpuBus.reference_buffer` builds a buffer in this module's own layout
(the exact inverse of ``reconstruct()``) for tests and for
:class:`SimpleCart`; it is deliberately not byte-identical to
``fcpico.stream.encode_frame()``'s output.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

TOOLS_DIR = Path(__file__).resolve().parent.parent.parent / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from fcpico import protocol, stream  # noqa: E402

__all__ = ["PpuBus", "SimpleCart"]


class PpuBus:
    """Parameterised model of one frame's qualifying-read/write traffic.

    Parameters:
    ``reads_per_line``, ``prerender_reads``, ``mailbox_len`` (64 for
    protocol v1, 128 for v2 -- selects both the mailbox length and the
    v1-raw-byte-vs-v2-KEY-packet heartbeat format), and ``cs1_mask`` (the pattern-space
    address decode used by the Mesen mapper; stored for parity
    with that model but not otherwise used here, since this model works at
    the byte-count level and never sees PPU addresses).
    """

    def __init__(
        self,
        reads_per_line: int = 64,
        prerender_reads: int = 66,
        mailbox_len: int = protocol.FC_COM_BUF_SIZE_V1,
        counter_report_bias: int = protocol.PPU_COUNTER_REPORT_BIAS,
        cs1_mask: int = 0xF800,
        bytes_per_line: int | None = None,
    ):
        if mailbox_len not in (protocol.FC_COM_BUF_SIZE_V1, protocol.FC_COM_BUF_SIZE_V2,
                               protocol.FC_COM_BUF_SIZE_V3):
            raise ValueError(
                f"mailbox_len must be {protocol.FC_COM_BUF_SIZE_V1} (v1) or "
                f"{protocol.FC_COM_BUF_SIZE_V2} (v2), or "
                f"{protocol.FC_COM_BUF_SIZE_V3} (v3), got {mailbox_len}"
            )
        if reads_per_line <= 0 or reads_per_line % 2:
            raise ValueError(f"reads_per_line must be a positive even number, got {reads_per_line}")
        if bytes_per_line is None:
            bytes_per_line = reads_per_line
        if bytes_per_line <= 0 or bytes_per_line % 2:
            raise ValueError(f"bytes_per_line must be a positive even number, got {bytes_per_line}")
        if bytes_per_line != reads_per_line:
            raise ValueError(
                f"bytes_per_line ({bytes_per_line}) must equal reads_per_line "
                f"({reads_per_line}): the hardware transmitter and counter use the same "
                "CS1-qualified /RD edge"
            )
        if counter_report_bias < 0:
            raise ValueError("counter_report_bias must not be negative")
        self.reads_per_line = reads_per_line
        self.bytes_per_line = bytes_per_line
        self.visible_bytes_per_line = reads_per_line
        self.prerender_reads = prerender_reads
        self.mailbox_len = mailbox_len
        self.osr_prelude_bytes = 0
        self.counter_report_bias = counter_report_bias
        self.cs1_mask = cs1_mask
        self.lines = protocol.VRAM_LINES  # 240; not a constructor parameter, the console geometry is fixed

    def frame_read_count(self) -> int:
        """Value reported by the PIO counter and compared with ``PPU_COUNT_VAL``."""
        return self.frame_qualifying_read_count() - self.counter_report_bias

    def frame_qualifying_read_count(self) -> int:
        """Physical CS1-qualified /RD falling edges in one complete frame."""
        return (
            self.prerender_reads
            + self.lines * self.reads_per_line
            + 1  # the NMI's mandatory dummy read
            + self.mailbox_len
        )

    def frame_byte_count(self) -> int:
        """Total ``cart.ppu_read()`` calls one :meth:`run_frame` makes (no faults injected).

        The transmit state machine answers every qualifying strobe.
        """
        return self.frame_qualifying_read_count()

    def uncounted_bytes_per_frame(self) -> int:
        """Physical reads beyond the counter's zero-based reported value."""
        return self.frame_byte_count() - self.frame_read_count()

    def frame_events(self):
        """Yield this frame's read events in bus order.

        - ``('read', -1, i)`` for ``i`` in ``0..prerender_reads``: the
          pre-render scanline's reads (NES scanline -1).
        - ``('read', line, tile)`` for ``line`` in ``0..239``, ``tile`` in
          ``0..bytes_per_line``: one visible scanline's served bytes. The
          first ``visible_bytes_per_line`` of them carry the displayed
          picture; any remainder is served and never displayed.
        - ``('nmi_read', 0)``: the mandatory dummy read of the NMI's
          `$2007` read sequence (discarded, never picture or mailbox data).
        - ``('nmi_read', i)`` for ``i`` in ``1..1+mailbox_len``: the mailbox
          bytes themselves.
        """
        for i in range(self.prerender_reads):
            yield ("read", -1, i)
        for line in range(self.lines):
            for tile in range(self.bytes_per_line):
                yield ("read", line, tile)
        yield ("nmi_read", 0)
        for i in range(1, 1 + self.mailbox_len):
            yield ("nmi_read", i)

    def run_frame(
        self,
        cart,
        heartbeat: int = 0,
        drop: int = 0,
        duplicate: int = 0,
        hold_heartbeat: bool = False,
    ) -> bytes:
        """Pull one frame's bytes from ``cart`` and deliver the heartbeat.

        Calls ``cart.ppu_read() -> int`` once per :meth:`frame_events` entry
        (in order) and returns the bytes collected. ``heartbeat`` is the
        16-bit controller state; unless ``hold_heartbeat`` is set, it is
        then delivered through ``cart.ppu_write(byte)`` -- a single raw byte
        for a v1 (``mailbox_len == 64``) bus, or the 3-byte v2
        ``FP_COM_KEY, pad1, pad2`` packet for v2.

        Fault injection:
        ``drop`` removes that many events off the end of this frame's read
        sequence before pulling bytes (so the returned buffer is ``drop``
        bytes short -- a simulated missed strobe); ``duplicate`` instead
        repeats the last event that many extra times (a simulated
        double-counted strobe). ``hold_heartbeat=True`` pulls the frame's
        bytes as normal but skips the ``ppu_write()`` call, simulating a
        stalled heartbeat.
        """
        events = list(self.frame_events())
        if drop:
            if drop > len(events):
                raise ValueError(f"drop={drop} exceeds this frame's {len(events)} events")
            events = events[: len(events) - drop]
        if duplicate:
            events = events + [events[-1]] * duplicate

        received = bytearray(cart.ppu_read() & 0xFF for _ in events)

        if not hold_heartbeat:
            self._write_heartbeat(cart, heartbeat)
        return bytes(received)

    def run_frames(self, cart, n_frames: int, heartbeat: int = 0, faults: dict | None = None):
        """Run ``n_frames`` consecutive frames; ``faults[k]`` (if given) are extra kwargs for frame ``k``."""
        faults = faults or {}
        return [self.run_frame(cart, heartbeat=heartbeat, **faults.get(k, {})) for k in range(n_frames)]

    def _write_heartbeat(self, cart, heartbeat: int) -> None:
        if self.mailbox_len >= protocol.FC_COM_BUF_SIZE_V2:
            cart.ppu_write(protocol.FP_COM_KEY)
            cart.ppu_write((heartbeat >> 8) & 0xFF)
            cart.ppu_write(heartbeat & 0xFF)
        else:
            cart.ppu_write(heartbeat & 0xFF)

    def reconstruct(self, bytes_read: bytes):
        """Inverse of a fault-free :meth:`run_frame`: bytes -> ``(pix, mailbox)``.

        ``bytes_read`` must be exactly :meth:`frame_byte_count` bytes (what
        a fault-free ``run_frame`` returns). Skips the pre-render line's
        reads (no picture data), then for each visible
        line unpacks the first ``visible_bytes_per_line`` of its
        ``bytes_per_line`` served bytes into pixels with
        ``fcpico.stream.unpack_run`` (so the default 64 gives the usual
        256-pixel-wide picture) and steps over the rest, discards the dummy
        read, and returns the trailing ``mailbox_len`` bytes verbatim as the
        mailbox.

        Returns ``(pix, mailbox)``: ``pix`` is a
        ``(240, visible_bytes_per_line * 4)`` ``uint8`` array of 2-bit
        colours; ``mailbox`` is ``bytes``.
        """
        data = bytes(bytes_read)
        expected = self.frame_byte_count()
        if len(data) != expected:
            raise ValueError(f"expected exactly {expected} bytes (a fault-free frame), got {len(data)}")

        pos = self.prerender_reads  # skip all physical pre-render reads, including OSR zeros
        words_per_line = self.visible_bytes_per_line // 2
        width = words_per_line * 8
        pix = np.zeros((self.lines, width), dtype=np.uint8)
        for line in range(self.lines):
            line_bytes = data[pos : pos + self.visible_bytes_per_line]
            pos += self.bytes_per_line
            for t in range(words_per_line):
                word = line_bytes[2 * t] | (line_bytes[2 * t + 1] << 8)
                pix[line, t * 8 : t * 8 + 8] = stream.unpack_run(word)

        pos += 1  # the NMI's dummy read
        mailbox = data[pos : pos + self.mailbox_len]
        return pix, mailbox

    def reference_buffer(self, pix, mailbox: bytes) -> bytes:
        """Build a buffer in *this module's* layout (the exact inverse of :meth:`reconstruct`).

        Not the same layout as ``fcpico.stream.encode_frame()`` (see the
        module docstring) -- this is what :class:`SimpleCart` should serve
        for :meth:`run_frame` + :meth:`reconstruct` to round-trip ``pix``
        and ``mailbox`` exactly. ``pix`` is
        ``(240, visible_bytes_per_line * 4)``, values 0..3; ``mailbox`` must
        be exactly ``mailbox_len`` bytes. Each line is followed by
        ``bytes_per_line - visible_bytes_per_line`` zero bytes, which the
        model serves and :meth:`reconstruct` steps over.
        """
        pix = np.asarray(pix, dtype=np.uint8) & 0x03
        words_per_line = self.visible_bytes_per_line // 2
        width = words_per_line * 8
        if pix.shape != (self.lines, width):
            raise ValueError(f"pix must have shape ({self.lines}, {width}), got {pix.shape}")
        mailbox = bytes(mailbox)
        if len(mailbox) != self.mailbox_len:
            raise ValueError(f"mailbox must be {self.mailbox_len} bytes, got {len(mailbox)}")

        tail = self.bytes_per_line - self.visible_bytes_per_line  # served, never displayed
        out = bytearray(self.prerender_reads)
        for line in range(self.lines):
            for t in range(words_per_line):
                word = stream.pack_run(pix[line, t * 8 : t * 8 + 8].tolist())
                out.append(word & 0xFF)
                out.append((word >> 8) & 0xFF)
            out += bytes(tail)
        out.append(0)  # consumed by the dummy read
        out += mailbox
        return bytes(out)


class SimpleCart:
    """The simplest possible cartridge: a fixed buffer served linearly.

    ``ppu_read()`` returns ``buf[0], buf[1], ...`` forever, wrapping if more
    bytes are requested than ``buf`` holds. Tests may request an artificial
    leading-zero prefix with ``osr_prelude_bytes``; calibrated hardware uses zero.
    ``ppu_write()`` (any heartbeat write, in this model) restarts the
    sequence from the prelude, matching ``pio_sm_restart()`` being called on
    every DMA re-arm in ``rp_system::ppu_dma()``. It has no protocol logic
    at all -- pair it with :meth:`PpuBus.reference_buffer`, not a raw
    ``fcpico.stream.encode_frame()`` buffer (again, see the module
    docstring for why those are different layouts).
    """

    def __init__(self, buf: bytes, osr_prelude_bytes: int = 0):
        if not buf:
            raise ValueError("buf must not be empty")
        self._buf = bytes(buf)
        self._prelude = osr_prelude_bytes
        self._pos = 0

    def ppu_read(self) -> int:
        if self._pos < self._prelude:
            value = 0
        else:
            value = self._buf[(self._pos - self._prelude) % len(self._buf)]
        self._pos += 1
        return value

    def ppu_write(self, value: int) -> None:
        self._pos = 0
