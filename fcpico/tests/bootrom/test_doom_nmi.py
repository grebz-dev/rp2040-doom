# SPDX-License-Identifier: BSD-3-Clause
"""Execute the assembled Doom v2 NMI, including worst-case APU replay."""

from __future__ import annotations

from pathlib import Path
import sys

import pytest

from nmi_harness import NmiHarness, SENTINEL_RETURN, build_mailbox, load_ines

ROOT = Path(__file__).resolve().parents[3]
ROM = ROOT / "fcpico/bootrom/out/doom.nes"
sys.path.insert(0, str(ROOT / "fcpico/tools"))
from fcpico import protocol  # noqa: E402


@pytest.fixture(scope="module")
def harness() -> NmiHarness:
    if not ROM.exists():
        pytest.skip("build fcpico/bootrom/out/doom.nes first")
    return NmiHarness(load_ines(ROM))


def mailbox(apu_pairs: int = 0) -> bytes:
    data = bytearray(build_mailbox(
        [(i, (i * 3 + 1) & 0xFF) for i in range(apu_pairs)],
        size=protocol.FC_COM_BUF_SIZE_V2,
    ))
    data[protocol.MBX_FLAGS] = (protocol.MBX_FLAG_V2 | protocol.MBX_FLAG_ATTR_VALID |
                                protocol.MBX_FLAG_PAL_VALID | protocol.MBX_FLAG_APU_VALID)
    data[protocol.MBX_PAL:protocol.MBX_PAL + 16] = bytes(range(16))
    data[protocol.MBX_ATTR:protocol.MBX_ATTR + 64] = bytes(range(64))
    return bytes(data)


def test_worst_case_nmi_budget_and_mailbox_copy(harness: NmiHarness) -> None:
    payload = mailbox(15)
    result = harness.run_nmi(payload)
    print(f"Doom v2 NMI: critical={result.critical_section_cycles}, "
          f"total={result.total_cycles}, reads={result.reads_2007}")
    assert result.critical_section_cycles is not None
    assert result.critical_section_cycles <= protocol.NMI_CRITICAL_CYCLES_MAX
    assert result.total_cycles <= protocol.NMI_TOTAL_CYCLES_MAX
    assert result.reads_2007 == 1 + protocol.FC_COM_BUF_SIZE_V2
    assert result.final_pc == SENTINEL_RETURN
    assert result.memory[0x20:0x60] == payload[:64]
    assert result.memory[0xC0:0x100] == payload[64:]
    assert result.apu_writes == [(0x4000 + i, (i * 3 + 1) & 0xFF)
                                 for i in range(15)]


def test_ppu_writes_and_heartbeat_order(harness: NmiHarness) -> None:
    result = harness.run_nmi(mailbox(), {0x85: 0xA5})
    writes = [(address, value) for _cycle, address, value in result.writes]
    ppu_data = [value for address, value in writes if address == 0x2007]
    assert ppu_data == list(range(64)) + list(range(16)) + [protocol.FP_COM_KEY, 0xA5, 0]
    addresses = [value for address, value in writes if address == 0x2006]
    assert addresses == [0x08, 0x00, 0x23, 0xC0, 0x3F, 0x00, 0x08, 0x00,
                         0x08, 0x01]
    last_packet = max(i for i, pair in enumerate(writes)
                      if pair == (0x2007, protocol.FP_COM_KEY))
    assert writes[last_packet:last_packet + 3] == [
        (0x2007, protocol.FP_COM_KEY), (0x2007, 0xA5), (0x2007, 0),
    ]
    assert all(address != 0x2007 for address, _ in writes[last_packet + 3:])
    assert writes[last_packet + 3:last_packet + 9] == [
        (0x2006, 0x08), (0x2006, 0x01),
        (0x2000, 0x88), (0x2001, 0x1E), (0x2005, 0), (0x2005, 0),
    ]


def test_bad_magic_cannot_apply_tables(harness: NmiHarness) -> None:
    payload = bytearray(mailbox())
    payload[protocol.MBX_MAGIC] = 0
    payload[protocol.MBX_CMD] = protocol.PF_COM_DMOD
    result = harness.run_nmi(bytes(payload))
    assert result.memory[0x20] == 0
    assert result.memory[0x22] == 0
    assert [value for _cycle, address, value in result.writes if address == 0x2007] == [
        protocol.FP_COM_KEY, 0, 0,
    ]


def test_stamp_passes_fix_bank_cartridge_check() -> None:
    """The fix bank reflashes on a stamp mismatch only if the reply starts "20"."""
    if not ROM.exists():
        pytest.skip("build fcpico/bootrom/out/doom.nes first")
    prg = ROM.read_bytes()[16:]
    stamp = prg[protocol.FCBUS_ROM_STAMP_OFF:protocol.FCBUS_ROM_STAMP_OFF + 14]
    assert stamp.startswith(b"20"), stamp
    assert stamp.isascii() and len(stamp) == 14
