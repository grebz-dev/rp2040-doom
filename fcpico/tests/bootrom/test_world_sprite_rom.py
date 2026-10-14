# SPDX-License-Identifier: BSD-3-Clause
"""v5 world packets: exact CHR/OAM effects, acknowledgements and vblank budgets."""

from __future__ import annotations

from pathlib import Path
import re
import shutil
import subprocess
import sys

import pytest
from py65.devices.mpu6502 import MPU
from py65.memory import ObservableMemory

from nmi_harness import NmiHarness, build_mailbox, load_ines

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "fcpico/tools"))
from fcpico import protocol as p  # noqa: E402
import build_world_sprite_rom as w  # noqa: E402

NESASM = ROOT / ".build/tools/nesasm/nesasm"
PAIRS = [(reg, (index + 1) & 255) for index, reg in enumerate(
    (0, 1, 2, 3, 4, 6, 7, 8, 10, 11, 12, 14, 15, 0x15, 0x17))]


@pytest.fixture(scope="module")
def rom(tmp_path_factory: pytest.TempPathFactory) -> tuple[bytes, dict[str, int]]:
    if not NESASM.exists() and shutil.which("nesasm") is None:
        pytest.skip("pinned NESASM is not built")
    out = tmp_path_factory.mktemp("world_rom")
    w.build(out, NESASM if NESASM.exists() else Path(shutil.which("nesasm")))
    labels = {}
    for line in (out / "probe.raw.nes.0.nl").read_text().splitlines():
        match = re.match(r"\$([0-9A-F]{4})#([A-Z_]+)#", line)
        if match:
            labels[match.group(2)] = int(match.group(1), 16)
    return load_ines(out / "probe.nes"), labels


def mailbox(flags: int = 0) -> bytearray:
    data = bytearray(build_mailbox(PAIRS, size=p.FC_COM_BUF_SIZE_V5))
    data[p.MBX_FLAGS] = (p.MBX_FLAG_V2 | p.MBX_FLAG_V3 | p.MBX_FLAG_V4 |
                         p.MBX_FLAG_APU_VALID | flags)
    return data


def world_packet(op: int, seq: int, gen: int, args: bytes, payload: bytes) -> bytearray:
    data = mailbox(p.MBX_FLAG_WORLD)
    data[p.MBX_WORLD_OP] = op
    data[p.MBX_WORLD_SEQ] = seq
    data[p.MBX_WORLD_GEN] = gen
    data[p.MBX_WORLD_ARG:p.MBX_WORLD_ARG + len(args)] = args
    data[p.MBX_WORLD_PAYLOAD:p.MBX_WORLD_PAYLOAD + len(payload)] = payload
    return data


def heartbeat(result) -> list[int]:
    values = [value for _, address, value in result.writes if address == 0x2007]
    return values[-3:]


def ppu_writes(result) -> list[tuple[int, int]]:
    """Decode $2006/$2007 into (vram address, value) data writes."""
    address, high, decoded = 0, True, []
    for _, register, value in result.writes:
        if register == 0x2006:
            address = (value << 8 | address & 0xFF) if high else (address & 0xFF00 | value)
            high = not high
        elif register == 0x2007:
            decoded.append((address & 0x3FFF, value))
            address += 1
    return decoded


BUDGETS: list[tuple[int, int, int]] = []


def check_budget(result) -> None:
    assert result.critical_section_cycles is not None
    BUDGETS.append((result.critical_section_cycles, result.extra_cycles, result.total_cycles))
    assert result.critical_section_cycles + result.extra_cycles + 1 <= p.NMI_CRITICAL_CYCLES_MAX
    assert result.total_cycles + 1 <= p.NMI_TOTAL_CYCLES_MAX
    assert [(a, v) for a, v in result.apu_writes] == [(0x4000 + r, v) for r, v in PAIRS
                                                      if r <= 0x13]


def test_chr_packet_writes_four_tiles_and_acknowledges(rom) -> None:
    prg, _ = rom
    tiles = [0x00, 0x31, 0x5F, 0xFF]
    args = b"".join(bytes(((0x1000 + t * 16) >> 8, (t * 16) & 0xFF)) for t in tiles)
    payload = bytes(range(64))
    result = NmiHarness(prg).run_nmi(bytes(world_packet(p.WORLD_OP_CHR, 7, 3, args, payload)),
                                     {w.W_SHOWN_HI: 2 << 4, w.W_ACK: 6})
    data = [(a, v) for a, v in ppu_writes(result) if a >= 0x1000 and a < 0x2000]
    expected = [(0x1000 + t * 16 + i, payload[n * 16 + i]) for n, t in enumerate(tiles)
                for i in range(16)]
    assert data == expected
    assert result.memory[w.W_ACK] == 7
    assert heartbeat(result) == [p.FP_COM_KEY, 0, (2 << 4) | 7]
    assert (0x4014, 2) not in [(a, v) for _, a, v in result.writes]
    assert result.reads_2007 == 1 + p.FC_COM_BUF_SIZE_V5
    check_budget(result)


def test_duplicate_packet_is_not_reapplied(rom) -> None:
    prg, _ = rom
    args = bytes((0x10, 0x00) * 4)
    result = NmiHarness(prg).run_nmi(bytes(world_packet(p.WORLD_OP_CHR, 7, 3, args, bytes(64))),
                                     {w.W_ACK: 7})
    assert not [a for a, _ in ppu_writes(result) if 0x1000 <= a < 0x2000]
    assert heartbeat(result)[-1] == 7


def test_oam_packets_stage_and_commit_after_scroll(rom) -> None:
    prg, _ = rom
    low = bytes((n * 3) & 255 for n in range(64))
    result = NmiHarness(prg).run_nmi(bytes(world_packet(p.WORLD_OP_OAM, 1, 4, bytes((0,)), low)))
    assert result.memory[w.W_STAGE:w.W_STAGE + 64] == low
    assert result.memory[w.W_COMMIT] == 0
    assert result.memory[w.W_ACK] == 1
    check_budget(result)
    scroll = [cycle for cycle, address, _ in result.writes if address == 0x2005][-1]
    assert all(cycle < scroll for cycle, address, _ in result.writes if address in (0x2006, 0x2007))

    high = bytes((255 - n) for n in range(64))
    pals = bytes((0x08, 0x00, 0x26, 0x09, 0x0F, 0x2A))
    args = bytes((p.WORLD_OAM_PACKET | p.WORLD_OAM_COMMIT,)) + pals
    result = NmiHarness(prg).run_nmi(bytes(world_packet(p.WORLD_OP_OAM, 2, 4, args, high)),
                                     {w.W_ACK: 1})
    assert result.memory[w.W_STAGE + 64:w.W_STAGE + 128] == high
    assert result.memory[w.W_COMMIT] == 1
    assert result.memory[w.W_COMMIT_GEN] == 4
    assert [(a, v) for a, v in ppu_writes(result) if 0x3F00 <= a < 0x3F20] == \
        [(0x3F11 + i, pals[i]) for i in range(3)] + [(0x3F19 + i, pals[3 + i]) for i in range(3)]
    check_budget(result)

    clear = bytes((p.WORLD_OAM_NONE | p.WORLD_OAM_COMMIT,)) + w.MENU_SPRITE_PALETTES
    result = NmiHarness(prg).run_nmi(bytes(world_packet(p.WORLD_OP_OAM, 3, 0, clear, bytes(64))),
                                     {w.W_ACK: 2, w.W_STAGE: 0x55})
    assert result.memory[w.W_STAGE] == 0x55
    assert result.memory[w.W_COMMIT] == 1 and result.memory[w.W_COMMIT_GEN] == 0
    check_budget(result)


def test_new_packet_outranks_pending_dma(rom) -> None:
    prg, _ = rom
    preset = {0x0310: 1, w.W_SLOTS_HI: 5 << 4, w.W_SHOWN_HI: 4 << 4, w.W_ACK: 3}
    result = NmiHarness(prg).run_nmi(
        bytes(world_packet(p.WORLD_OP_CHR, 4, 5, bytes((0x10, 0) * 4), bytes(64))), preset)
    writes = [(a, v) for _, a, v in result.writes]
    assert (0x4014, 2) not in writes
    assert result.memory[0x0310] == 1, "the DMA stays pending for the next NMI"
    assert result.memory[w.W_ACK] == 4
    assert result.memory[w.W_SHOWN_HI] == 4 << 4
    assert [a for a, _ in ppu_writes(result) if 0x1000 <= a < 0x2000]
    assert heartbeat(result) == [p.FP_COM_KEY, 0, (4 << 4) | 4]
    check_budget(result)


def test_pending_dma_runs_when_no_new_packet(rom) -> None:
    prg, _ = rom
    preset = {0x0310: 1, w.W_SLOTS_HI: 5 << 4, w.W_SHOWN_HI: 4 << 4, w.W_ACK: 4}
    for data in (world_packet(p.WORLD_OP_CHR, 4, 5, bytes((0x10, 0) * 4), bytes(64)),
                 mailbox()):
        result = NmiHarness(prg).run_nmi(bytes(data), preset)
        writes = [(a, v) for _, a, v in result.writes]
        assert (0x4014, 2) in writes
        assert result.memory[0x0310] == 0
        assert result.memory[w.W_SHOWN_HI] == 5 << 4
        assert not [a for a, _ in ppu_writes(result) if 0x1000 <= a < 0x2000]
        assert heartbeat(result) == [p.FP_COM_KEY, 0, (5 << 4) | 4]
        check_budget(result)


def test_text_and_tables_keep_v4_behaviour(rom) -> None:
    prg, _ = rom
    data = mailbox(p.MBX_FLAG_ATTR_VALID | p.MBX_FLAG_PAL_VALID)
    data[p.MBX_ATTR:p.MBX_ATTR + 64] = bytes(range(64))
    result = NmiHarness(prg).run_nmi(bytes(data))
    assert [(a, v) for a, v in ppu_writes(result) if 0x23C0 <= a < 0x2400] == \
        [(0x23C0 + i, i) for i in range(64)]
    assert heartbeat(result) == [p.FP_COM_KEY, 0, 0]
    check_budget(result)


def run_routine(prg: bytes, address: int, preset: dict[int, int]) -> bytes:
    memory = ObservableMemory(subject=[0] * 0x10000)
    memory[0x8000:0x10000] = prg
    for key, value in preset.items():
        memory[key] = value
    mpu = MPU(memory=memory, pc=address)
    mpu.sp = 0xFF
    mpu.stPushWord(0xFFFD)  # RTS returns to $FFFE
    for _ in range(20000):
        if mpu.pc == 0xFFFE:
            return bytes(memory[0:0x800])
        mpu.step()
    raise AssertionError("routine did not return")


def test_world_apply_publishes_committed_slots_atomically(rom) -> None:
    prg, labels = rom
    stage = {w.W_STAGE + i: (i * 7) & 255 for i in range(128)}
    ram = run_routine(prg, labels["WORLD_APPLY"],
                      {w.W_COMMIT: 1, w.W_COMMIT_GEN: 6, 0x0310: 0, **stage})
    assert ram[w.WORLD_SLOT_BASE:w.WORLD_SLOT_BASE + w.WORLD_SLOT_BYTES] == \
        bytes((i * 7) & 255 for i in range(w.WORLD_SLOT_BYTES))
    assert ram[0x0200:w.WORLD_SLOT_BASE] == bytes(w.WORLD_SLOT_BASE - 0x200)
    assert (ram[w.W_SLOTS], ram[w.W_SLOTS_HI], ram[0x0310], ram[w.W_COMMIT]) == \
        (6, 6 << 4, 1, 0)

    ram = run_routine(prg, labels["WORLD_APPLY"],
                      {w.W_COMMIT: 1, w.W_COMMIT_GEN: 0, w.W_SLOTS: 6,
                       **{w.WORLD_SLOT_BASE + i: 0x44 for i in range(w.WORLD_SLOT_BYTES)}})
    assert all(ram[w.WORLD_SLOT_BASE + 4 * n] == 0xEF for n in range(29))
    assert ram[w.W_SLOTS] == 0

    untouched = run_routine(prg, labels["WORLD_APPLY"], {w.W_COMMIT: 0, 0x0310: 0})
    assert untouched[0x0310] == 0


def test_menu_routine_yields_world_slots(rom) -> None:
    prg, labels = rom
    slots = {w.WORLD_SLOT_BASE + i: 0x44 for i in range(w.WORLD_SLOT_BYTES)}
    ram = run_routine(prg, labels["MENU_APPLY"], {w.W_SLOTS: 3, **slots})
    assert ram[w.WORLD_SLOT_BASE:w.WORLD_SLOT_BASE + w.WORLD_SLOT_BYTES] == \
        bytes((0x44,)) * w.WORLD_SLOT_BYTES
    ram = run_routine(prg, labels["MENU_APPLY"], {w.W_SLOTS: 0, **slots})
    assert ram[w.WORLD_SLOT_BASE] == 0xEF


def test_tile_pool_avoids_every_resident_tile() -> None:
    pool = w.world_tile_pool()
    assert len(pool) == 53
    header = (ROOT / "fcpico/port/video/fcworld.h").read_text()
    listed = [int(v, 16) for v in re.findall(r"0x([0-9A-Fa-f]{2})",
              header.split("FCWORLD_TILE_POOL_INIT", 1)[1].split("}", 1)[0])]
    assert tuple(listed) == pool
    palettes = header.split("FCWORLD_MENU_PALETTES_INIT", 1)[1].split("}", 1)[0]
    assert bytes(int(v, 16) for v in re.findall(r"0x([0-9A-Fa-f]{2})", palettes)) == \
        w.MENU_SPRITE_PALETTES
