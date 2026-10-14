# SPDX-License-Identifier: BSD-3-Clause
"""Execute console setup and check the PPU addresses it will render from."""

from pathlib import Path

import pytest
from py65.devices.mpu6502 import MPU
from py65.memory import ObservableMemory

from nmi_harness import load_ines


ROM = Path(__file__).resolve().parents[2] / "bootrom/out/doom.nes"


def test_background_fetches_target_cartridge_stream() -> None:
    """Preserve the tutorial's tile-$80 stream selection, not its font tiles.

    PLY_STG_0 fills nametable 0 with $80; the fix bank loads its local font
    at $0000. The command/mailbox port uses $0800. A zero tile redirects
    background reads to the font area while mailbox heartbeats still work.
    """
    if not ROM.exists():
        pytest.skip("build fcpico/bootrom/out/doom.nes first")
    memory = ObservableMemory()
    memory[0x8000:0x10000] = load_ines(ROM)
    vram = bytearray([0xFF]) * 0x4000
    registers = [0] * 8
    address = 0
    high_byte = True

    def read_status(_address: int) -> int:
        nonlocal high_byte
        high_byte = True
        return 0x80

    def write_register(register: int, value: int) -> None:
        nonlocal address, high_byte
        registers[register - 0x2000] = value
        if register == 0x2006:
            if high_byte:
                address = (value & 0x3F) << 8
            else:
                address = (address & 0x3F00) | value
            high_byte = not high_byte
        elif register == 0x2007:
            vram[address & 0x3FFF] = value
            address += 32 if registers[0] & 4 else 1

    memory.subscribe_to_read([0x2002], read_status)
    memory.subscribe_to_write(range(0x2000, 0x2008), write_register)
    cpu = MPU(memory=memory, pc=0xEF00)
    cpu.stPushWord(0x7FFF)  # setup's RTS returns to $8000
    for _ in range(20_000):
        cpu.step()
        if cpu.pc == 0x8000 and cpu.sp == 0xFF:
            break
    else:
        pytest.fail("Doom setup did not return")

    assert registers[0] == 0x88  # NMI; BG $0000; sprites $1000
    assert registers[1] == 0x1E  # render background and sprites
    pattern_base = (registers[0] & 0x10) << 8
    addresses = {pattern_base + tile * 16 for tile in vram[0x2000:0x23C0]}
    assert all(0x0800 <= addr < 0x1000 for addr in addresses), (
        f"background fetches miss the $0800 stream port: {sorted(addresses)}"
    )
    assert vram[0x23C0:0x2400] == bytes(64)
    assert vram[0x2400:0x2800] == bytes(1024)
