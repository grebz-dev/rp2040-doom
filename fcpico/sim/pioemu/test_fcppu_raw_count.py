# SPDX-License-Identifier: BSD-3-Clause
"""The diagnostic counter must see /RD even when CS1 rejects the access."""

import pytest
from pioemu import State, clock_cycles_reached, emulate

from conftest import load_program, pin_defines, timeline_source


@pytest.mark.parametrize("selected", [[], [True], [False], [True, False, False, True]])
def test_raw_and_qualified_counts_distinguish_bus_selection(selected):
    pins = pin_defines()
    idle = (1 << pins["PI_RD_BIT"]) | (1 << pins["PI_CS1_BIT"])
    timeline = [idle] * 8
    for is_selected in selected:
        levels = 0 if is_selected else 1 << pins["PI_CS1_BIT"]
        # At 150 MHz, 160 ns is 24 clocks. A long low pulse counts once.
        timeline += [levels] * 24
        timeline += [levels | (1 << pins["PI_RD_BIT"])] * 24
    timeline += [idle] * 8

    for name, count in [("fcppu_raw_count", len(selected)),
                        ("fcppu_rna", sum(selected))]:
        program = load_program(name)
        states = emulate(program.opcodes, initial_state=State(),
                         stop_when=clock_cycles_reached(len(timeline)),
                         input_source=timeline_source(timeline),
                         jmp_pin=pins["PI_CS1_BIT"],
                         wrap_target=program.wrap_target, wrap_top=program.wrap_top)
        final = list(states)[-1][1]
        assert final.x_register == 0xFFFFFFFF - count
        assert final.input_shift_register.contents == max(0, count - 1)
        assert not final.receive_fifo  # only the ARM's explicit PUSH samples it
