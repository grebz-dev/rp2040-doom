# SPDX-License-Identifier: BSD-3-Clause
"""Native row commands must fit vblank and defer a complete OAM commit."""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from nmi_harness import NmiHarness, build_mailbox, load_ines

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "fcpico/tools"))
from fcpico import protocol  # noqa: E402


def _nesasm() -> Path:
    pinned = ROOT / ".build/tools/nesasm/nesasm"
    if pinned.exists():
        return pinned
    found = os.environ.get("NESASM_BIN") or shutil.which("nesasm")
    if not found:
        pytest.skip("pinned NESASM is not built")
    return Path(found)


def test_native_text_commands_defer_oam_and_fit_vblank(tmp_path: Path) -> None:
    subprocess.run([sys.executable, str(ROOT / "fcpico/tools/build_native_status_probe.py"),
                    "--output", str(tmp_path), "--nesasm", str(_nesasm())], check=True)
    rom = load_ines(tmp_path / "probe.nes")
    pairs = [(reg, (index + 1) & 255) for index, reg in enumerate(
        (0, 1, 2, 3, 4, 6, 7, 8, 10, 11, 12, 14, 15, 0x15, 0x17))]
    pairs[-2:] = [(0x15, 15), (0x17, 0x40)]
    payload = bytearray(build_mailbox(pairs,
                                      size=protocol.FC_COM_BUF_SIZE_V4))
    payload[protocol.MBX_FLAGS] = (protocol.MBX_FLAG_V2 | protocol.MBX_FLAG_V3 |
                                   protocol.MBX_FLAG_V4 | protocol.MBX_FLAG_UI_VALID |
                                   protocol.MBX_FLAG_ATTR_VALID |
                                   protocol.MBX_FLAG_PAL_VALID |
                                   protocol.MBX_FLAG_APU_VALID)
    payload[protocol.MBX_CMD:protocol.MBX_CMD + 6] = bytes(
        (0xE3, 0xA2, ord('B'), 0xE3, 0xA3, ord('0')))
    text_frame = NmiHarness(rom).run_nmi(bytes(payload), {0x0310: 1})
    writes = [(address, value) for _, address, value in text_frame.writes]
    assert text_frame.reads_2007 == 1 + protocol.FC_COM_BUF_SIZE_V4
    assert [value for address, value in writes if address == 0x2007] == [
        ord('B'), ord('0'), protocol.FP_COM_KEY, 0, 0]
    assert (0x4014, 2) not in writes
    assert [(address, value) for address, value in writes
            if 0x4000 <= address <= 0x4017 and address not in (0x4014, 0x4016)] == \
        [(0x4000 + reg, value) for reg, value in pairs]
    assert text_frame.memory[0x0310] == 1
    assert text_frame.critical_section_cycles is not None
    assert text_frame.critical_section_cycles <= protocol.NMI_CRITICAL_CYCLES_MAX
    assert text_frame.total_cycles <= protocol.NMI_TOTAL_CYCLES_MAX

    payload[protocol.MBX_CMD:protocol.MBX_CMD + 6] = bytes(6)
    commit = NmiHarness(rom).run_nmi(bytes(payload), {0x0310: 1})
    commit_writes = [(address, value) for _, address, value in commit.writes]
    assert (0x4014, 2) in commit_writes
    assert [(address, value) for address, value in commit_writes
            if 0x4000 <= address <= 0x4017 and address not in (0x4014, 0x4016)] == \
        [(0x4000 + reg, value) for reg, value in pairs]
    assert commit.memory[0x0310] == 0
    assert commit.critical_section_cycles is not None
    assert commit.critical_section_cycles + commit.extra_cycles + 1 <= \
        protocol.NMI_CRITICAL_CYCLES_MAX
    assert commit.total_cycles + 1 <= protocol.NMI_TOTAL_CYCLES_MAX
