# SPDX-License-Identifier: BSD-3-Clause
"""Tests for the L3 PPU-bus model (fcpico/sim/ppubus/ppubus.py).

These tests pin the model to the NES-001 hardware trace and
keep the PIO counter's zero-based report distinct from physical read strobes.

Two things these tests deliberately do NOT do:

- They never retype a wire constant. Everything comes from `fcpico.protocol`, which is
  generated from `fcpico/fcbus/fcbus_protocol.h` by `fcpico/tools/gen_protocol.py`. A test
  that hardcodes 15490 would keep passing after someone changed the header, which is the
  opposite of useful.
- The hardware fixture is the independent oracle for pre-render and visible-line counts;
  encode/decode round trips then check the calibrated model's internal consistency.

conftest.py puts fcpico/tools on sys.path; fcpico/sim is added here because the model lives
outside both the tools tree and the tests tree, and `tests/conftest.py` is shared with
other suites and owned by nobody in particular.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

_SIM_DIR = Path(__file__).resolve().parents[2] / "sim"
if str(_SIM_DIR) not in sys.path:
    sys.path.insert(0, str(_SIM_DIR))

from fcpico import protocol, stream  # noqa: E402
from ppubus import PpuBus, SimpleCart  # noqa: E402
from trace_decode import decode_dump  # noqa: E402


V1 = protocol.FC_COM_BUF_SIZE_V1  # 64
V2 = protocol.FC_COM_BUF_SIZE_V2  # 128
HW_TRACE = Path(__file__).parents[1] / "fixtures" / "hw_trace_ntsc" / "trace_6.hex"


# ---------------------------------------------------------------------------
# The totals: what the whole module is calibrated to reproduce
# ---------------------------------------------------------------------------


def test_default_frame_read_count_is_the_firmware_v1_total():
    assert PpuBus().frame_read_count() == protocol.PPU_COUNT_VAL_V1


def test_defaults_are_calibrated_to_the_ntsc_hardware_trace():
    report = decode_dump(HW_TRACE.read_text(encoding="ascii"))
    bus = PpuBus()

    assert report.prerender_reads == bus.prerender_reads == 66
    assert set(report.line_counts) == {bus.reads_per_line} == {64}
    assert bus.frame_qualifying_read_count() == protocol.PPU_COUNT_VAL_V1 + 1
    assert bus.counter_report_bias == 1
    assert bus.frame_read_count() == bus.frame_qualifying_read_count() - bus.counter_report_bias
    assert bus.frame_byte_count() == bus.frame_qualifying_read_count()


def test_v2_mailbox_gives_the_firmware_v2_total():
    assert PpuBus(mailbox_len=V2).frame_read_count() == protocol.PPU_COUNT_VAL_V2


def test_the_two_totals_differ_by_exactly_the_mailbox_growth():
    """The only thing v2 changes about the frame is a longer mailbox."""
    assert protocol.PPU_COUNT_VAL_V2 - protocol.PPU_COUNT_VAL_V1 == V2 - V1
    assert PpuBus(mailbox_len=V2).frame_read_count() - PpuBus().frame_read_count() == V2 - V1


def test_frame_read_count_is_the_sum_of_its_documented_parts():
    """Restates the module docstring's arithmetic so a changed default is caught here."""
    bus = PpuBus()
    assert bus.frame_read_count() == (
        bus.prerender_reads
        + bus.lines * bus.reads_per_line
        + 1  # the NMI's mandatory dummy read
        + bus.mailbox_len
        - bus.counter_report_bias
    )
    assert bus.lines == protocol.VRAM_LINES


def test_mailbox_len_must_be_a_real_protocol_length():
    for bad in (0, 16, 63, 65, 127, 256):
        with pytest.raises(ValueError, match="mailbox_len"):
            PpuBus(mailbox_len=bad)


@pytest.mark.parametrize("bad", [0, -2, 1, 63, 65])
def test_reads_per_line_must_be_positive_and_even(bad):
    """Odd is refused because two bytes make one 16-bit stream word."""
    with pytest.raises(ValueError, match="reads_per_line"):
        PpuBus(reads_per_line=bad)


# ---------------------------------------------------------------------------
# The hardware-resolved count/consumption relationship
# ---------------------------------------------------------------------------


def test_picture_count_is_the_measured_prerender_plus_visible_lines():
    bus = PpuBus()
    assert bus.prerender_reads + bus.lines * bus.reads_per_line == protocol.PPU_PICTURE_COUNT


def test_transmitter_answers_every_qualifying_read():
    bus = PpuBus()
    assert bus.bytes_per_line == bus.reads_per_line
    assert bus.frame_byte_count() == bus.frame_qualifying_read_count()
    assert bus.uncounted_bytes_per_frame() == bus.counter_report_bias == 1


@pytest.mark.parametrize("bytes_per_line", [62, 68])
def test_bytes_per_line_must_equal_reads_per_line(bytes_per_line):
    """Both PIO state machines use the same CS1-qualified /RD edge."""
    with pytest.raises(ValueError, match="bytes_per_line"):
        PpuBus(reads_per_line=64, bytes_per_line=bytes_per_line)


@pytest.mark.parametrize("bad", [0, -4, 67])
def test_bytes_per_line_must_be_positive_and_even(bad):
    with pytest.raises(ValueError, match="bytes_per_line"):
        PpuBus(bytes_per_line=bad)


# ---------------------------------------------------------------------------
# frame_events: order and quantities
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("mailbox_len", [V1, V2])
def test_frame_events_order_and_quantities(mailbox_len):
    bus = PpuBus(mailbox_len=mailbox_len)
    events = list(bus.frame_events())
    assert len(events) == bus.frame_byte_count()

    reads = [e for e in events if e[0] == "read"]
    nmi = [e for e in events if e[0] == "nmi_read"]
    assert len(reads) + len(nmi) == len(events)

    # OSR zeros are values emitted by the first reads, not extra read events.
    kinds = [e[0] for e in events]
    assert kinds == ["read"] * len(reads) + ["nmi_read"] * len(nmi)

    assert len(nmi) == 1 + mailbox_len
    assert [e[1] for e in nmi] == list(range(1 + mailbox_len))

    prerender = [e for e in reads if e[1] == -1]
    assert len(prerender) == bus.prerender_reads
    visible = [e for e in reads if e[1] != -1]
    assert len(visible) == protocol.VRAM_LINES * bus.bytes_per_line
    # The pre-render line comes before every visible line, and lines ascend.
    assert [e[1] for e in reads] == [-1] * bus.prerender_reads + [
        line for line in range(protocol.VRAM_LINES) for _ in range(bus.bytes_per_line)
    ]
    assert [e[2] for e in visible[: bus.bytes_per_line]] == list(range(bus.bytes_per_line))


# ---------------------------------------------------------------------------
# Frame re-arm starts at the DMA buffer
# ---------------------------------------------------------------------------


def test_frame_rearm_has_no_extra_zero_byte_prelude():
    bus = PpuBus()
    assert bus.osr_prelude_bytes == 0
    cart = SimpleCart(bytes(range(1, 256)) * 80)
    got = bus.run_frame(cart)
    assert got[0] == 1
    assert bus.frame_read_count() == protocol.PPU_COUNT_VAL_V1


def test_a_write_restarts_the_stream():
    """Every DMA re-arm resumes from buffer byte zero."""
    bus = PpuBus()
    cart = SimpleCart(bytes(range(1, 256)) * 80)
    first = bus.run_frame(cart)
    second = bus.run_frame(cart)
    assert second[0] == 1
    assert second == first  # the same buffer, re-served from the top


# ---------------------------------------------------------------------------
# Round trip: reference_buffer -> SimpleCart -> run_frame -> reconstruct
# ---------------------------------------------------------------------------


def _demo_pix(bus, seed=1234):
    rng = np.random.default_rng(seed)
    width = bus.visible_bytes_per_line * 4
    return rng.integers(0, 4, size=(protocol.VRAM_LINES, width), dtype=np.uint8)


def _demo_mailbox(bus):
    return bytes((i * 7 + 3) & 0xFF for i in range(bus.mailbox_len))


@pytest.mark.parametrize("mailbox_len", [V1, V2])
def test_round_trip_recovers_pixels_and_mailbox(mailbox_len):
    bus = PpuBus(mailbox_len=mailbox_len)
    pix = _demo_pix(bus)
    mailbox = _demo_mailbox(bus)
    cart = SimpleCart(bus.reference_buffer(pix, mailbox))
    got = bus.run_frame(cart)
    assert len(got) == bus.frame_byte_count()
    out_pix, out_mailbox = bus.reconstruct(got)
    assert np.array_equal(out_pix, pix)
    assert out_mailbox == mailbox


def test_reconstruct_uses_the_same_bit_convention_as_the_firmware():
    """Pixel 0 is bit 7; the low byte is bitplane 0 and the high byte bitplane 1.
    Asserted against `fcpico.stream`, which is tested separately against `convVram()`."""
    bus = PpuBus()
    pix = np.zeros((protocol.VRAM_LINES, 256), dtype=np.uint8)
    pix[0, 0] = 1  # plane 0 only
    pix[0, 1] = 2  # plane 1 only
    pix[0, 2] = 3  # both
    cart = SimpleCart(bus.reference_buffer(pix, _demo_mailbox(bus)))
    got = bus.run_frame(cart)
    first = bus.prerender_reads
    lo, hi = got[first], got[first + 1]
    assert lo == 0b1010_0000  # pixels 0 and 2 set in plane 0
    assert hi == 0b0110_0000  # pixels 1 and 2 set in plane 1
    assert stream.unpack_run(lo | (hi << 8))[:3] == [1, 2, 3]


def test_reconstruct_refuses_a_frame_of_the_wrong_length():
    bus = PpuBus()
    full = bus.reference_buffer(_demo_pix(bus), _demo_mailbox(bus))
    served = bus.run_frame(SimpleCart(full))
    with pytest.raises(ValueError, match="fault-free frame"):
        bus.reconstruct(served[:-1])
    with pytest.raises(ValueError, match="fault-free frame"):
        bus.reconstruct(served + b"\x00")


def test_reference_buffer_validates_its_arguments():
    bus = PpuBus()
    good_pix, good_mbx = _demo_pix(bus), _demo_mailbox(bus)
    with pytest.raises(ValueError, match="pix must have shape"):
        bus.reference_buffer(good_pix[:, :128], good_mbx)
    with pytest.raises(ValueError, match="mailbox must be"):
        bus.reference_buffer(good_pix, good_mbx[:-1])


def test_reference_buffer_is_not_the_firmware_buffer_layout():
    """The module docstring is explicit that this is a different layout from
    `fcpico.stream.encode_frame()`, whose offsets are real C pointer offsets. If they
    ever do coincide, that is a change worth noticing rather than assuming."""
    bus = PpuBus()
    pix = _demo_pix(bus)
    mailbox = _demo_mailbox(bus)
    mine = bus.reference_buffer(pix, mailbox)
    theirs = bytes(stream.encode_frame(pix, mailbox))
    assert mine != theirs
    # The firmware's mailbox offset is unambiguous and unrelated to this model's counts.
    assert theirs[protocol.VRAM_MAILBOX_OFF_V1 : protocol.VRAM_MAILBOX_OFF_V1 + len(mailbox)] == mailbox


# ---------------------------------------------------------------------------
# The heartbeat
# ---------------------------------------------------------------------------


def test_v1_heartbeat_is_one_raw_byte():
    bus = PpuBus()
    writes = []
    cart = _RecordingCart(bus, writes)
    bus.run_frame(cart, heartbeat=0x1234)
    assert writes == [0x34]


def test_v2_heartbeat_is_a_three_byte_key_packet():
    bus = PpuBus(mailbox_len=V2)
    writes = []
    cart = _RecordingCart(bus, writes)
    bus.run_frame(cart, heartbeat=0x1234)
    assert writes == [protocol.FP_COM_KEY, 0x12, 0x34]


def test_hold_heartbeat_skips_the_write_but_not_the_reads():
    bus = PpuBus()
    writes = []
    cart = _RecordingCart(bus, writes)
    got = bus.run_frame(cart, heartbeat=0xFF, hold_heartbeat=True)
    assert writes == []
    assert len(got) == bus.frame_byte_count()


class _RecordingCart:
    """A cart that serves zeros and records every heartbeat byte written to it."""

    def __init__(self, bus, writes):
        self._writes = writes

    def ppu_read(self) -> int:
        return 0

    def ppu_write(self, value: int) -> None:
        self._writes.append(value)


# ---------------------------------------------------------------------------
# Fault injection: a slip must change the count by exactly the slip
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("n", [1, 2, 3, 64])
def test_a_dropped_read_shortens_the_frame_by_exactly_that_much(n):
    bus = PpuBus()
    cart = SimpleCart(bus.reference_buffer(_demo_pix(bus), _demo_mailbox(bus)))
    got = bus.run_frame(cart, drop=n)
    assert len(got) == bus.frame_byte_count() - n


@pytest.mark.parametrize("n", [1, 2, 3, 64])
def test_a_duplicated_read_lengthens_the_frame_by_exactly_that_much(n):
    bus = PpuBus()
    cart = SimpleCart(bus.reference_buffer(_demo_pix(bus), _demo_mailbox(bus)))
    got = bus.run_frame(cart, duplicate=n)
    assert len(got) == bus.frame_byte_count() + n


def test_a_slip_changes_the_count_and_nothing_else():
    """A dropped strobe must leave every byte before the slip untouched: the model
    removes events off the end, so the picture is intact and only the mailbox tail is
    short. That is what lets a test tell "the link slipped" from "the picture is wrong"."""
    bus = PpuBus()
    buf = bus.reference_buffer(_demo_pix(bus), _demo_mailbox(bus))
    clean = bus.run_frame(SimpleCart(buf))
    short = bus.run_frame(SimpleCart(buf), drop=3)
    assert short == clean[:-3]
    long = bus.run_frame(SimpleCart(buf), duplicate=3)
    assert long[: len(clean)] == clean


def test_dropping_more_than_a_frame_is_refused():
    bus = PpuBus()
    cart = SimpleCart(b"\x00" * 16)
    with pytest.raises(ValueError, match="exceeds this frame"):
        bus.run_frame(cart, drop=bus.frame_byte_count() + 1)


def test_a_slipped_frame_is_followed_by_a_clean_one():
    """Recovery: the heartbeat write restarts the cart, so the frame after a slip is
    byte-identical to a frame with no slip at all. In the firmware this is `ppu_dma()`
    calling `pio_sm_restart()`; here it is `SimpleCart.ppu_write()` rewinding."""
    bus = PpuBus()
    cart = SimpleCart(bus.reference_buffer(_demo_pix(bus), _demo_mailbox(bus)))
    frames = bus.run_frames(cart, 3, faults={1: {"drop": 5}})
    assert len(frames[0]) == bus.frame_byte_count()
    assert len(frames[1]) == bus.frame_byte_count() - 5
    assert len(frames[2]) == bus.frame_byte_count()
    assert frames[2] == frames[0]
    bus.reconstruct(frames[2])  # and it decodes, which frames[1] would not


def test_run_frames_applies_faults_only_to_the_named_frame():
    bus = PpuBus()
    cart = SimpleCart(b"\x5A" * 1024)
    frames = bus.run_frames(cart, 4, faults={0: {"duplicate": 2}, 3: {"drop": 1}})
    sizes = [len(f) for f in frames]
    n = bus.frame_byte_count()
    assert sizes == [n + 2, n, n, n - 1]


# ---------------------------------------------------------------------------
# SimpleCart
# ---------------------------------------------------------------------------


def test_simple_cart_wraps_when_asked_for_more_than_it_holds():
    cart = SimpleCart(b"\x01\x02\x03", osr_prelude_bytes=2)
    assert [cart.ppu_read() for _ in range(8)] == [0, 0, 1, 2, 3, 1, 2, 3]


def test_simple_cart_refuses_an_empty_buffer():
    with pytest.raises(ValueError, match="must not be empty"):
        SimpleCart(b"")
