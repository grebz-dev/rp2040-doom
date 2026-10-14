"""
SPDX-License-Identifier: BSD-3-Clause

pytest suite measuring the *tutorial* boot ROM's NMI handler with py65.

Run with:

    python3 -m pytest fcpico/tests/bootrom -q -s

(the ``-s`` is what makes ``test_cycle_table_report``'s table visible; see
fcpico/README.md for what the numbers mean and their known
approximations).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import List, Tuple

import pytest

from nmi_harness import (
    KEY_NEW,
    NTSC_VBLANK_CYCLES,
    PALFADE_VAL,
    SENTINEL_RETURN,
    NmiHarness,
    build_mailbox,
    cycle_table,
    load_ines,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
TUTORIAL_ROM = REPO_ROOT / "fcpico/external/fc-pico/tutorial_project" / "BOOTROM" / "rom.NES"
JSON_REPORT_PATH = Path(__file__).resolve().parent / "tutorial_nmi_cycles.json"


def _apu_pairs(n: int) -> List[Tuple[int, int]]:
    """n synthetic (register, value) pairs, reg always < $80 so none of them
    is mistaken for the $FF replay terminator, and always != $14 so none of
    them collides with the $4014 OAM-DMA address the sprite-DMA tests key
    on."""
    return [(i % 0x13, (i * 7 + 3) & 0xFF) for i in range(n)]


@pytest.fixture(scope="module")
def harness() -> NmiHarness:
    assert TUTORIAL_ROM.is_file(), f"tutorial ROM not found at {TUTORIAL_ROM}"
    return NmiHarness(load_ines(TUTORIAL_ROM))


# ---------------------------------------------------------------------------
# Cycle table + JSON report -- kept first so it still runs (and the JSON
# still gets written) even if a later, more specific assertion fails.
# ---------------------------------------------------------------------------


def test_cycle_table_report(harness):
    rows = cycle_table(harness, apu_pair_counts=(0, 8, 16, 24))

    JSON_REPORT_PATH.write_text(
        json.dumps(
            {
                "rom": str(TUTORIAL_ROM.relative_to(REPO_ROOT)),
                "ntsc_vblank_cycles": NTSC_VBLANK_CYCLES,
                "rows": rows,
            },
            indent=2,
        )
        + "\n"
    )

    columns = [
        ("apu_pairs", ">9"),
        ("sprite_dma", ">10"),
        ("raw_cycles", ">10"),
        ("corrected_cycles", ">16"),
        ("total_cycles", ">12"),
        ("critical_section_cycles", ">15"),
        ("reads_2007", ">11"),
        ("num_writes", ">10"),
    ]
    header = " | ".join(f"{name:{width}}" for name, width in columns)
    print()
    print(f"tutorial NMI cycle table (NTSC vblank budget = {NTSC_VBLANK_CYCLES})")
    print(header)
    print("-" * len(header))
    for row in rows:
        cells = []
        for name, width in columns:
            value = row[name]
            cells.append(f"{'n/a' if value is None else value:{width}}")
        print(" | ".join(cells))

    assert len(rows) == 8  # {0,8,16,24} APU pairs x {on,off} sprite DMA
    assert JSON_REPORT_PATH.exists()


# ---------------------------------------------------------------------------
# Structural assertions
# ---------------------------------------------------------------------------


def test_ends_with_rti_and_reads_65_bytes(harness):
    result = harness.run_nmi(build_mailbox())
    assert result.reads_2007 == 65  # 1 dummy read + 64 mailbox bytes
    # RTI popped exactly what run_nmi pushed onto an otherwise-empty stack,
    # i.e. every push the handler itself made (jsr transPALLET, the
    # commented-out user-NMI jsr when NMI_CALL_ADR is null) had a matching
    # pop -- the handler did not run off into the weeds.
    assert result.final_pc == SENTINEL_RETURN


def test_2006_written_twice_before_reads_and_before_reply(harness):
    result = harness.run_nmi(build_mailbox())
    writes_2006 = [(a, v) for _c, a, v in result.writes if a == 0x2006]
    # SET_VRAM_ADD2 #$0800 (macro.h) is `lda #HIGH($0800); sta $2006;
    # lda #LOW($0800); sta $2006` == $08 then $00, twice: once before the
    # mailbox reads, once before the reply.
    assert writes_2006 == [
        (0x2006, 0x08),
        (0x2006, 0x00),
        (0x2006, 0x08),
        (0x2006, 0x00),
    ]

    indices_2006 = [i for i, (_c, a, _v) in enumerate(result.writes) if a == 0x2006]
    indices_2007 = [i for i, (_c, a, _v) in enumerate(result.writes) if a == 0x2007]
    assert indices_2006[0] == 0, "nothing else is written before the first $2006 pair"
    assert indices_2006[-1] < indices_2007[0], "the second $2006 pair precedes the reply"


def test_key_new_written_after_mailbox_reads(harness):
    sentinel = 0x37
    result = harness.run_nmi(build_mailbox(), {KEY_NEW: sentinel})

    writes_2007 = [(i, v) for i, (_c, a, v) in enumerate(result.writes) if a == 0x2007]
    assert [value for _index, value in writes_2007] == [sentinel]

    (key_new_index, _value), = writes_2007
    last_2006_index = max(i for i, (_c, a, _v) in enumerate(result.writes) if a == 0x2006)
    assert key_new_index > last_2006_index


def test_mailbox_is_copied_byte_exactly_to_zero_page(harness):
    """Verify the simulated CPU RAM contains the cartridge mailbox."""
    mailbox = bytearray((index * 37 + 11) & 0xFF for index in range(64))
    mailbox[1] = 0xFC
    mailbox[2] = 0x00
    result = harness.run_nmi(bytes(mailbox))

    assert result.memory[0x20:0x60] == bytes(mailbox)


def test_valid_mailbox_preserves_command_for_main_loop(harness):
    """Verify NMI leaves a valid command queued for non-interrupt dispatch."""
    mailbox = bytearray(build_mailbox())
    mailbox[2] = 0x03

    result = harness.run_nmi(bytes(mailbox))

    assert result.memory[0x22] == 0x03


def test_invalid_mailbox_clears_command_before_main_loop(harness):
    """Verify a torn mailbox cannot dispatch a stale command."""
    mailbox = bytearray(build_mailbox())
    mailbox[1] = 0x00
    mailbox[2] = 0x03

    result = harness.run_nmi(bytes(mailbox))

    assert result.memory[0x21] == 0x00
    assert result.memory[0x22] == 0x00


# ---------------------------------------------------------------------------
# Sprite DMA ($4014)
# ---------------------------------------------------------------------------


def test_4014_written_once_when_sprite_dma_enabled(harness):
    result = harness.run_nmi(build_mailbox(), {PALFADE_VAL: 0x00})
    writes_4014 = [(a, v) for _c, a, v in result.writes if a == 0x4014]
    assert writes_4014 == [(0x4014, 2)]
    assert result.extra_cycles == 513


@pytest.mark.parametrize("palfade_val", [0x01, 0x7F, 0xFF])
def test_4014_not_written_while_fading(harness, palfade_val):
    result = harness.run_nmi(build_mailbox(), {PALFADE_VAL: palfade_val})
    writes_4014 = [(a, v) for _c, a, v in result.writes if a == 0x4014]
    assert writes_4014 == []
    assert result.extra_cycles == 0


# ---------------------------------------------------------------------------
# APU register replay
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("n", [0, 1, 8, 23, 24])
def test_apu_pairs_replayed_in_order(harness, n):
    pairs = _apu_pairs(n)
    result = harness.run_nmi(build_mailbox(pairs))
    expected = [(0x4000 + reg, value) for reg, value in pairs]
    assert result.apu_writes == expected


def test_apu_replay_stops_at_24_pairs_even_if_more_are_supplied(harness):
    pairs = _apu_pairs(30)  # more than build_mailbox()/the NMI loop can hold
    result = harness.run_nmi(build_mailbox(pairs))
    expected = [(0x4000 + reg, value) for reg, value in pairs[:24]]
    assert result.apu_writes == expected
    assert len(result.apu_writes) == 24


# ---------------------------------------------------------------------------
# Cycle budget
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("n", [0, 8, 24])
def test_total_cycles_within_ntsc_vblank(harness, n):
    result = harness.run_nmi(build_mailbox(_apu_pairs(n)))
    assert result.corrected_cycles < NTSC_VBLANK_CYCLES
    assert result.total_cycles < NTSC_VBLANK_CYCLES
