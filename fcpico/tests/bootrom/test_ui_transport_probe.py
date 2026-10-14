# SPDX-License-Identifier: BSD-3-Clause
"""Cycle and byte checks for the isolated v3 UI mailbox receiver."""

from __future__ import annotations

from pathlib import Path
import subprocess
import sys

from nmi_harness import NmiHarness, build_mailbox, load_ines

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "fcpico/tools"))
from fcpico import protocol  # noqa: E402


def test_v3_receiver_copies_snapshot_with_max_audio(tmp_path: Path) -> None:
    subprocess.run([sys.executable, str(ROOT / "fcpico/tools/build_ui_transport_probe.py"),
                    "--output", str(tmp_path)], check=True)
    payload = bytearray(build_mailbox([(i, i + 1) for i in range(15)],
                                      size=protocol.FC_COM_BUF_SIZE_V3))
    payload[protocol.MBX_FLAGS] = (protocol.MBX_FLAG_V2 | protocol.MBX_FLAG_V3 |
                                   protocol.MBX_FLAG_UI_VALID |
                                   protocol.MBX_FLAG_ATTR_VALID |
                                   protocol.MBX_FLAG_PAL_VALID |
                                   protocol.MBX_FLAG_APU_VALID)
    payload[protocol.MBX_PAL:protocol.MBX_PAL + 16] = bytes(range(16))
    payload[protocol.MBX_ATTR:protocol.MBX_ATTR + 64] = bytes(range(64))
    payload[protocol.MBX_UI:protocol.MBX_UI + 16] = bytes(range(0x80, 0x90))
    result = NmiHarness(load_ines(tmp_path / "probe.nes")).run_nmi(bytes(payload))
    assert result.reads_2007 == 1 + protocol.FC_COM_BUF_SIZE_V3
    assert result.memory[0x20:0x60] == payload[:64]
    assert result.memory[0xC0:0x100] == payload[64:128]
    assert result.memory[0x300:0x310] == payload[128:144]
    assert result.critical_section_cycles is not None
    assert result.critical_section_cycles <= protocol.NMI_CRITICAL_CYCLES_MAX
    assert result.total_cycles <= protocol.NMI_TOTAL_CYCLES_MAX
    assert len(result.apu_writes) == 15

    commit = NmiHarness(load_ines(tmp_path / "probe.nes")).run_nmi(
        bytes(payload), {0x0310: 1})
    writes = [(address, value) for _, address, value in commit.writes]
    assert (0x2003, 0) in writes
    assert (0x4014, 2) in writes
    assert commit.memory[0x0310] == 0
    assert [value for address, value in writes if address == 0x2007] == [
        protocol.FP_COM_KEY, 0, 0]
    # NmiHarness models the 513-cycle DMA stall separately. Include it in the
    # critical section, and allow one extra cycle for the other DMA parity.
    assert commit.critical_section_cycles is not None
    assert commit.critical_section_cycles + commit.extra_cycles + 1 <= \
        protocol.NMI_CRITICAL_CYCLES_MAX
    assert commit.total_cycles + 1 <= protocol.NMI_TOTAL_CYCLES_MAX
