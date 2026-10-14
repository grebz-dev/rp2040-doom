# SPDX-License-Identifier: BSD-3-Clause
"""
L4 test for `fcppu_w` (fcpico/fcbus/fcppu.pio), the receive path that captures one byte per
qualifying Famicom PPU write to $2007.

Firmware configuration this mirrors (tutorial_project/tuto1_hw/sys/rp_system.cpp,
`rp_system::init()`, SM_RECV): `in`-pins base GPIO6 (unused by pioemu -- see below), `jmp` pin
GPIO17 (CS1), RX FIFO joined, `in` shift right with autopush at 32 bits.

pioemu quirk this test works around: `emulate()` has no `in_base`/`in_count` parameter, so
`in pins, 32` always reads the raw, unshifted 32-bit GPIO0-31 word (see conftest.py's module
docstring for the source-level evidence). On real hardware the captured byte would already sit
in bits 0-7 of the pushed word because `sm_config_set_in_pins(&cw, PI_D0_BIT)` rebases the `in`
group to GPIO6; here it instead sits at bits PI_D0_BIT..PI_D0_BIT+7 (6..13) of the raw word, so
`_byte_of()` below shifts it back down before comparing.
"""
import pioemu
from pioemu import State, clock_cycles_reached

from conftest import load_program, pin_defines, timeline_source

PINS = pin_defines()
WR = PINS["PI_WR_BIT"]
CS1 = PINS["PI_CS1_BIT"]
D0 = PINS["PI_D0_BIT"]

# CS1 is active-low: 0 means "this cartridge is selected".
SELECTED = 0
NOT_SELECTED = 1


def _word(wr: int, cs1: int, data: int = 0) -> int:
    return (wr << WR) | (cs1 << CS1) | ((data & 0xFF) << D0)


def _hold(value: int, cycles: int) -> list[int]:
    return [value] * cycles


IDLE = _word(wr=1, cs1=NOT_SELECTED)


def _byte_of(word: int) -> int:
    """Undo pioemu's lack of an `in`-pin base: shift the captured byte back down from
    GPIO PI_D0_BIT..+7 to bits 0..7."""
    return (word >> D0) & 0xFF


def _run(timeline: list[int]):
    program = load_program("fcppu_w")
    results = list(
        pioemu.emulate(
            program.opcodes,
            initial_state=State(),
            stop_when=clock_cycles_reached(len(timeline)),
            input_source=timeline_source(timeline),
            jmp_pin=CS1,
            shift_isr_right=True,
            auto_push=True,
            push_threshold=32,
            wrap_target=program.wrap_target,
            wrap_top=program.wrap_top,
        )
    )
    return results


def _push_events(results) -> list[tuple[int, int]]:
    """[(cycle_index, pushed_word), ...] -- one entry per cycle where receive_fifo grew by one."""
    events = []
    for i, (prev, cur) in enumerate(results):
        if len(cur.receive_fifo) > len(prev.receive_fifo):
            events.append((i, cur.receive_fifo[-1]))
    return events


def _selected_pulse(data: int, low: int = 6, high: int = 6) -> list[int]:
    return _hold(_word(wr=0, cs1=SELECTED, data=data), low) + _hold(
        _word(wr=1, cs1=SELECTED, data=data), high
    )


def _unselected_pulse(data: int, low: int = 6, high: int = 6) -> list[int]:
    return _hold(_word(wr=0, cs1=NOT_SELECTED, data=data), low) + _hold(
        _word(wr=1, cs1=NOT_SELECTED, data=data), high
    )


def test_selected_write_is_captured():
    """A single selected /WR pulse pushes exactly one word to the RX FIFO; its data byte
    matches what was driven on D0..D7."""
    timeline = _hold(IDLE, 3) + _selected_pulse(0x5A) + _hold(IDLE, 3)

    events = _push_events(_run(timeline))

    assert len(events) == 1
    _, word = events[0]
    assert _byte_of(word) == 0x5A


def test_unselected_write_is_not_captured():
    """CS1 high (not selected) throughout the pulse: no word is ever pushed."""
    timeline = _hold(IDLE, 3) + _unselected_pulse(0xFF) + _hold(IDLE, 3)

    results = _run(timeline)

    assert _push_events(results) == []
    assert list(results[-1][1].receive_fifo) == []


def test_several_selected_writes_interleaved_with_unselected():
    """Several selected writes, interleaved with unselected ones: exactly one word per selected
    write, in order, and none for the unselected ones."""
    timeline = (
        _hold(IDLE, 3)
        + _selected_pulse(0x11)
        + _hold(IDLE, 2)
        + _unselected_pulse(0xDE)
        + _hold(IDLE, 2)
        + _selected_pulse(0x22)
        + _hold(IDLE, 2)
        + _unselected_pulse(0xAD)
        + _hold(IDLE, 2)
        + _selected_pulse(0x33)
        + _hold(IDLE, 3)
    )

    events = _push_events(_run(timeline))

    assert [_byte_of(word) for _, word in events] == [0x11, 0x22, 0x33]


def test_value_at_rising_edge_wins():
    """The data byte changes mid-pulse, while /WR is still low; the byte actually captured is
    the one present around the rising edge, not the earlier value.

    Timing note: each pioemu cycle executes exactly one instruction, including a *satisfied*
    `wait` (it advances the program counter that same cycle, but the instruction landed on --
    here, `in pins, 32` -- still needs its own following cycle to run). So `in` samples the bus
    one cycle *after* `wait 1 gpio PI_WR_BIT` first sees WR high, not on that cycle itself; the
    final value must therefore already be present by the sampling cycle and held at least one
    cycle past it, which the timeline below (final value present for several cycles before *and*
    after the rising edge) satisfies with room to spare.
    """
    decoy, final = 0xAA, 0x33
    timeline = (
        _hold(IDLE, 3)
        + _hold(_word(wr=0, cs1=SELECTED, data=decoy), 5)  # decoy for most of the low phase
        + _hold(_word(wr=0, cs1=SELECTED, data=final), 3)  # switches to `final` before WR rises
        + _hold(_word(wr=1, cs1=SELECTED, data=final), 6)  # rising edge and settle: still `final`
        + _hold(IDLE, 3)
    )

    results = _run(timeline)
    events = _push_events(results)

    assert len(events) == 1
    cycle, word = events[0]
    assert _byte_of(word) == final
    # The pins at the exact cycle of the push show `final`, confirming `decoy` never reached the ISR.
    assert _byte_of(results[cycle][1].pin_values) == final
