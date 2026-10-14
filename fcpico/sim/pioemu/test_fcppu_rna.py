# SPDX-License-Identifier: BSD-3-Clause
"""
L4 test for `fcppu_rna` (fcpico/fcbus/fcppu.pio), the read counter that tracks how far through the
frame the PPU has got.

X is preset to `~null` (0xFFFFFFFF) and `jmp x--` decrements it once per qualifying `/RD`; on
each such read `mov isr, !x` first loads the *pre-decrement* X, inverted, into the ISR. So after
N qualifying reads: `x_register == (0xFFFFFFFF - N) & 0xFFFFFFFF`, and the ISR holds the
complement of X as it stood going into the Nth (most recent) qualifying read's `mov`, i.e.
`(N - 1) & 0xFFFFFFFF` (0 if N == 0, since no `mov isr, ...` has ever run). Both formulas were
checked empirically against pioemu for N in 0..4 while building this test (see fcpico/README.md).

pioemu quirk this test relies on: `mov isr, !x` does **not** trigger autopush. Autopush only
fires from an `in` instruction's own shift-count bookkeeping reaching `push_threshold`; a plain
`mov` to ISR resets that counter to 0 (pioemu's `write_to_isr()` defaults `count=0`, and only the
`out ISR, n` destination is special-cased to set it -- see conftest.py's docstring and
`pioemu/instruction_decoder.py`'s comment: "no other command where the ISR is written to has a
similar effect"). fcppu_rna has no `push` either, so `receive_fifo` never receives anything from
this program's own execution regardless of the firmware's autopush configuration -- which is by
design: the firmware reads the running count with an explicit, firmware-issued `push noblock`
(`pio_sm_exec(pio0, SM_TRCNT, 0x8000)` in `rp_system::ppu_dma()`), not autopush. This test
therefore reads `state.input_shift_register.contents` directly, standing in for that `push`.
"""
import pioemu
from pioemu import State, clock_cycles_reached

from conftest import load_program, pin_defines, timeline_source

PINS = pin_defines()
RD = PINS["PI_RD_BIT"]
CS1 = PINS["PI_CS1_BIT"]
WR = PINS["PI_WR_BIT"]

# CS1 is active-low: 0 means "this cartridge is selected".
SELECTED = 0
NOT_SELECTED = 1

MASK32 = 0xFFFFFFFF


def _word(rd: int, cs1: int, wr: int = 1) -> int:
    return (rd << RD) | (cs1 << CS1) | (wr << WR)


def _hold(value: int, cycles: int) -> list[int]:
    return [value] * cycles


IDLE = _word(rd=1, cs1=NOT_SELECTED)


def _selected_pulse(low: int = 4, high: int = 4) -> list[int]:
    return _hold(_word(rd=0, cs1=SELECTED), low) + _hold(_word(rd=1, cs1=SELECTED), high)


def _unselected_pulse(low: int = 4, high: int = 4) -> list[int]:
    return _hold(_word(rd=0, cs1=NOT_SELECTED), low) + _hold(_word(rd=1, cs1=NOT_SELECTED), high)


def _run(timeline: list[int]):
    program = load_program("fcppu_rna")
    return list(
        pioemu.emulate(
            program.opcodes,
            initial_state=State(),
            stop_when=clock_cycles_reached(len(timeline)),
            input_source=timeline_source(timeline),
            jmp_pin=CS1,
            wrap_target=program.wrap_target,
            wrap_top=program.wrap_top,
        )
    )


def _expected(n_qualifying: int) -> tuple[int, int]:
    x = (MASK32 - n_qualifying) & MASK32
    isr = (n_qualifying - 1) & MASK32 if n_qualifying >= 1 else 0
    return x, isr


def test_no_reads_leaves_initial_state():
    timeline = _hold(IDLE, 6)

    final = _run(timeline)[-1][1]

    assert final.x_register == MASK32
    assert final.input_shift_register.contents == 0


def test_n_qualifying_reads_decrement_x_and_load_isr():
    for n in (1, 2, 3, 4):
        timeline = _hold(IDLE, 3)
        for _ in range(n):
            timeline += _selected_pulse()
            timeline += _hold(IDLE, 2)

        final = _run(timeline)[-1][1]

        exp_x, exp_isr = _expected(n)
        assert final.x_register == exp_x, f"N={n}: x_register"
        assert final.input_shift_register.contents == exp_isr, f"N={n}: isr"


def test_unselected_reads_do_not_count():
    timeline = _hold(IDLE, 3)
    for _ in range(3):
        timeline += _unselected_pulse()
        timeline += _hold(IDLE, 2)

    final = _run(timeline)[-1][1]

    assert final.x_register == MASK32
    assert final.input_shift_register.contents == 0


def test_qualifying_and_non_qualifying_reads_interleaved():
    """N=3 qualifying and M=2 non-qualifying /RD pulses, interleaved; only the qualifying ones
    move the count."""
    pattern = [True, False, True, False, True]  # 3 selected, 2 unselected
    n_qualifying = pattern.count(True)

    timeline = _hold(IDLE, 3)
    for selected in pattern:
        timeline += _selected_pulse() if selected else _unselected_pulse()
        timeline += _hold(IDLE, 2)

    final = _run(timeline)[-1][1]

    exp_x, exp_isr = _expected(n_qualifying)
    assert final.x_register == exp_x
    assert final.input_shift_register.contents == exp_isr


def test_write_activity_does_not_affect_the_count():
    """fcppu_rna never reads /WR (GPIO21) at all -- wiggling it between reads must not perturb
    the count."""
    n_qualifying = 2
    timeline = _hold(IDLE, 3)
    for _ in range(n_qualifying):
        timeline += _selected_pulse()
        timeline += _hold(_word(rd=1, cs1=NOT_SELECTED, wr=0), 2)
        timeline += _hold(_word(rd=1, cs1=NOT_SELECTED, wr=1), 2)

    final = _run(timeline)[-1][1]

    exp_x, exp_isr = _expected(n_qualifying)
    assert final.x_register == exp_x
    assert final.input_shift_register.contents == exp_isr
