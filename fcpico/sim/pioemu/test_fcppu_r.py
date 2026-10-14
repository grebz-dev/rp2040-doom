# SPDX-License-Identifier: BSD-3-Clause
"""
L4 test for `fcppu_r` (fcpico/fcbus/fcppu.pio), the transmit path that presents one byte per
qualifying Famicom PPU read.

**Coverage note**: `fcppu_r` raises `irq set IRQ_DIR` to hand bus-direction control to
`fcppu_dir` (see test_fcppu_dir.py). pioemu 0.88 does not decode `irq` instructions at all (see
conftest.py's module docstring): hitting one ends `emulate()`'s generator immediately, with no
exception and no further cycles. The single `irq set IRQ_DIR` in this program is therefore
patched to `conftest.NOP_OPCODE` (`mov y, y`, confirmed inert) before emulating, and **this test
does not cover the IRQ handshake to fcppu_dir at all** -- that integration is covered by L5
(full-chip simulation) and L7 (hardware).

**Cold-start note**: `fcppu_r` begins with a one-time `mov osr, null` (contents=0, shift counter
reset to 0) before `.wrap_target`. Autopull only refills the OSR once *its own* out-driven shift
count reaches the 32-bit threshold -- a plain `mov` into the OSR does not itself count toward
that threshold (pioemu's `write_to_osr()` always resets the counter to 0; only the `out ->ISR`
destination is special-cased to set a nonzero counter, per an explicit comment in
`pioemu/instruction_decoder.py` -- see conftest.py). Consequently the first four qualifying
reads after a fresh start shift out that `null`-loaded zero content, byte by byte, before the
first real TX FIFO word is ever pulled in; `test_cold_start_shifts_out_zero_before_first_autopull`
below pins this down explicitly rather than leaving it as a surprise. Real firmware sidesteps it
by manually priming the FIFO/OSR before the PPU's first read of the frame
(`rp_system.cpp`'s `pio_sm_put_blocking()` calls, and the `pio_sm_exec(pio0, SM_TRAN, 0x6008)`
-- "out pins, 8" -- priming in `ppu_dma()`). The other tests below start from an
already-primed OSR (program counter placed at `wrap_target`, i.e. *past* the one-time
`mov osr, null`) to exercise the steady-state byte order in isolation from this transient,
mirroring what the firmware's own priming achieves.
"""
from collections import deque

import pioemu
from pioemu import ShiftRegister, State, clock_cycles_reached

from conftest import NOP_OPCODE, load_program, patch_opcode, pin_defines

PINS = pin_defines()
RD = PINS["PI_RD_BIT"]
CS1 = PINS["PI_CS1_BIT"]
D0 = PINS["PI_D0_BIT"]
DATA_MASK = 0xFF << D0

# CS1 is active-low: 0 means "this cartridge is selected".
SELECTED = 0
NOT_SELECTED = 1

IRQ_INSTRUCTION_INDEX = 3  # `irq set IRQ_DIR`, see the module docstring
OUT_INSTRUCTION_INDEX = 4  # `out pins, 8`


def _control_word(rd: int, cs1: int) -> int:
    return (rd << RD) | (cs1 << CS1)


def _hold(value: int, cycles: int) -> list[int]:
    return [value] * cycles


IDLE = _control_word(rd=1, cs1=NOT_SELECTED)


def _selected_pulse(low: int = 4, high: int = 4) -> list[int]:
    return _hold(_control_word(0, SELECTED), low) + _hold(_control_word(1, SELECTED), high)


def _unselected_pulse(low: int = 4, high: int = 4) -> list[int]:
    return _hold(_control_word(0, NOT_SELECTED), low) + _hold(_control_word(1, NOT_SELECTED), high)


def _holding_data_source(control_timeline: list[int]):
    """Like `conftest.timeline_source`, but only drives RD/CS1 -- the data pins (GPIO
    PI_D0_BIT..+7) pass through whatever `out pins, 8` last wrote instead of being reset to 0
    every cycle.

    This is needed because pioemu drives *every* input-direction pin from `input_source` on
    *every* cycle (see conftest.py's docstring), and this program never touches `pindirs` (that
    is fcppu_dir's job, deliberately not modelled here -- see the coverage note above): without
    the pass-through, a byte written by `out pins, 8` would be overwritten back to 0 on the very
    next emulated cycle instead of staying visible for the rest of the /RD-low window.
    """

    def _source(state: State) -> int:
        index = state.clock if state.clock < len(control_timeline) else len(control_timeline) - 1
        control = control_timeline[index]
        return (state.pin_values & DATA_MASK) | (control & ~DATA_MASK)

    return _source


def _load_patched_program():
    program = load_program("fcppu_r")
    assert (program.opcodes[IRQ_INSTRUCTION_INDEX] >> 13) & 7 == 6, "expected `irq` at this index"
    opcodes = patch_opcode(program.opcodes, IRQ_INSTRUCTION_INDEX, NOP_OPCODE)
    return program, opcodes


def _run(timeline: list[int], *, initial_state: State):
    program, opcodes = _load_patched_program()
    return list(
        pioemu.emulate(
            opcodes,
            initial_state=initial_state,
            stop_when=clock_cycles_reached(len(timeline)),
            input_source=_holding_data_source(timeline),
            jmp_pin=CS1,
            out_base=D0,
            out_count=8,
            shift_osr_right=True,
            auto_pull=True,
            pull_threshold=32,
            wrap_target=program.wrap_target,
            wrap_top=program.wrap_top,
        )
    )


def _primed_state(first_word: int, *remaining_words: int) -> State:
    """An initial State standing in for a state machine that has already been running a while:
    program counter placed at `wrap_target` (*past* the one-time `mov osr, null`, which would
    otherwise immediately clobber the priming below) with the OSR and TX FIFO pre-loaded as if
    autopull had already pulled `first_word` and the firmware had queued `remaining_words`
    behind it."""
    program = load_program("fcppu_r")
    return State(
        program_counter=program.wrap_target,
        output_shift_register=ShiftRegister(first_word, 0),
        transmit_fifo=deque(remaining_words),
    )


def _bytes_during_selected_windows(results) -> list[int]:
    """One entry per cycle where the *previous* instruction was `out pins, 8`: the data-pin byte
    right after it executed."""
    return [
        (cur.pin_values & DATA_MASK) >> D0
        for prev, cur in results
        if prev.program_counter == OUT_INSTRUCTION_INDEX
    ]


def test_cold_start_shifts_out_zero_before_first_autopull():
    """Documents the transient described in the module docstring: starting fresh (program
    counter 0 runs `mov osr, null` first), the first four qualifying reads (4 x 8 = 32 bits)
    shift out zero before the first TX FIFO word is ever autopulled in."""
    timeline = _hold(IDLE, 3)
    for _ in range(6):
        timeline += _selected_pulse()
        timeline += _hold(IDLE, 2)

    initial_state = State(transmit_fifo=deque([0x44332211, 0x88776655]))
    results = _run(timeline, initial_state=initial_state)

    assert _bytes_during_selected_windows(results) == [0x00, 0x00, 0x00, 0x00, 0x11, 0x22]


def test_bytes_appear_in_order_during_selected_windows():
    """With the OSR already primed (steady state -- see `_primed_state`), qualifying reads
    consume 0x44332211 then 0x88776655 low-byte first (shift right), in FIFO order:
    0x11, 0x22, 0x33, 0x44, 0x55, 0x66."""
    timeline = _hold(IDLE, 3)
    for _ in range(6):
        timeline += _selected_pulse()
        timeline += _hold(IDLE, 2)

    results = _run(timeline, initial_state=_primed_state(0x44332211, 0x88776655))

    assert _bytes_during_selected_windows(results) == [0x11, 0x22, 0x33, 0x44, 0x55, 0x66]


def test_unselected_reads_do_not_consume_a_byte():
    """A /RD pulse while CS1 is high must not advance the OSR/TX FIFO: the byte sequence for the
    surrounding selected reads is unbroken, and the queued FIFO word is untouched."""
    timeline = (
        _hold(IDLE, 3)
        + _selected_pulse()
        + _hold(IDLE, 2)
        + _unselected_pulse()
        + _hold(IDLE, 2)
        + _selected_pulse()
        + _hold(IDLE, 3)
    )

    results = _run(timeline, initial_state=_primed_state(0x44332211, 0x88776655))

    assert _bytes_during_selected_windows(results) == [0x11, 0x22]
    assert list(results[-1][1].transmit_fifo) == [0x88776655]
