# SPDX-License-Identifier: BSD-3-Clause
"""Protect the real boot ROM when making a private-mapper co-simulation copy."""
from pathlib import Path

import pytest

from nes.set_mapper import set_mapper


def test_tutorial_prg_and_fix_bank_unchanged():
    rom = (Path(__file__).resolve().parents[3] / "fcpico/external/fc-pico/tutorial_project/BOOTROM/rom.NES").read_bytes()
    mapped = set_mapper(rom)
    assert mapped[16:] == rom[16:]
    assert mapped[6] & 0x0f == rom[6] & 0x0f
    assert mapped[7] & 0x0c == 0x08
    assert (mapped[6] >> 4) | (mapped[7] & 0xf0) | ((mapped[8] & 0x0f) << 8) == 4093
    assert mapped[11] == 7


@pytest.mark.parametrize("data", [b"", b"NES\x1a" + bytes(32780), b"bad!" + bytes(32780)])
def test_rejects_invalid_rom(data):
    with pytest.raises(ValueError):
        set_mapper(data)
