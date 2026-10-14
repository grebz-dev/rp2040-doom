# SPDX-License-Identifier: BSD-3-Clause
"""
L4 test for `fcppu_dir` (fcpico/fcbus/fcppu.pio), the bus-direction control program.

Not exercised: `fcppu_dir` both waits on and clears a PIO IRQ (`wait 1 irq IRQ_DIR`,
`irq clear IRQ_DIR`) to synchronise with `fcppu_r`. pioemu 0.88 does not decode
`irq set`/`wait`/`clear` at all (see conftest.py's module docstring: hitting one ends
`emulate()`'s generator immediately -- no exception, no further cycles), and even the opcode
that *does* assemble for `wait 1 irq N` is emulated as a plain `wait 1 gpio N`: pioemu's
`WaitInstruction.source` field is decoded but never consulted by the instruction decoder
(verified empirically -- see fcpico/README.md). So there is no way to drive this program's IRQ
wait/clear handshake correctly in pioemu, only a way to run it and get a misleading answer.
The direction handshake between fcppu_r and fcppu_dir is covered by L5 (full-chip simulation)
and L7 (hardware) instead.

What *is* checked here: the program still assembles cleanly through the same
`.define`/case-quirk preprocessing as the other three programs (see conftest.py), so a future
edit to fcppu.pio that breaks assembly is still caught even though this program's runtime
behaviour can't be emulated.
"""
import pytest

from conftest import load_program


def test_fcppu_dir_assembles():
    """Not emulated (see module docstring) -- only that the real fcppu.pio's `fcppu_dir` block
    still assembles through the shared preprocessing pipeline, into well-formed 16-bit opcodes."""
    program = load_program("fcppu_dir")

    assert len(program.opcodes) > 0
    assert all(0 <= opcode <= 0xFFFF for opcode in program.opcodes)


@pytest.mark.skip(
    reason=(
        "needs irq wait/clear, unsupported by pioemu 0.88; "
        "covered by full-chip simulation and hardware"
    )
)
def test_bus_turns_around_only_during_a_qualifying_read():
    """Intent, not executable under pioemu 0.88 (see module docstring): once fcppu_r raises
    IRQ_DIR on a qualifying /RD falling edge, fcppu_dir should drive D0..D7 as outputs only until
    /RD rises, then release the bus back to inputs -- and never drive it at all across a
    non-qualifying /RD. It is checked by full-chip simulation
    and on hardware instead."""
    raise NotImplementedError
