#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-3-Clause
"""Place doom1.whx in a UF2 at its fixed flash address, optionally merged with firmware.

The RP2350 bootrom writes every UF2 block to the address it carries, so one
drag-and-drop file can hold both the firmware (from 0x10000000) and the WHX
(TINY_WAD_ADDR, 0x10080000). Blocks are renumbered so blockNo/numBlocks agree
across the merged file; the family ID is taken from the firmware UF2.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import struct
import sys

from flash_layout_check import FLASH_LAYOUT_H, parse_flash_layout

UF2_MAGIC0 = 0x0A324655
UF2_MAGIC1 = 0x9E5D5157
UF2_MAGIC_END = 0x0AB16F30
UF2_FLAG_FAMILY = 0x00002000
PAYLOAD = 256
WHX_ADDR = 0x10080000
FLASH_BASE = 0x10000000
FLASH_BYTES = 4 * 1024 * 1024
RP2350_ARM_S = 0xE48BFF59
_HEADER = struct.Struct("<8I")


def read_blocks(data: bytes) -> list[tuple[int, int, int, bytes]]:
    """Returns (flags, address, family, payload) per block; validates framing."""
    if len(data) % 512:
        raise ValueError("UF2 length is not a multiple of 512")
    blocks = []
    for offset in range(0, len(data), 512):
        block = data[offset:offset + 512]
        magic0, magic1, flags, address, size, _, _, family = _HEADER.unpack_from(block)
        if (magic0, magic1) != (UF2_MAGIC0, UF2_MAGIC1) or \
                struct.unpack_from("<I", block, 508)[0] != UF2_MAGIC_END:
            raise ValueError(f"bad UF2 magic in block at byte {offset}")
        if size > 476:
            raise ValueError(f"bad payload size {size} at byte {offset}")
        blocks.append((flags, address, family, block[32:32 + size]))
    return blocks


def binary_blocks(data: bytes, address: int, family: int) -> list[tuple[int, int, int, bytes]]:
    return [(UF2_FLAG_FAMILY, address + off, family, data[off:off + PAYLOAD])
            for off in range(0, len(data), PAYLOAD)]


def write_blocks(blocks: list[tuple[int, int, int, bytes]]) -> bytes:
    out = bytearray()
    for number, (flags, address, family, payload) in enumerate(blocks):
        block = _HEADER.pack(UF2_MAGIC0, UF2_MAGIC1, flags, address, len(payload),
                             number, len(blocks), family)
        block += payload + bytes(476 - len(payload)) + struct.pack("<I", UF2_MAGIC_END)
        out += block
    return bytes(out)


def build(whx: bytes, firmware: bytes | None, address: int = WHX_ADDR,
          audio: bytes | None = None, world: bytes | None = None) -> bytes:
    if address % 4096 or not FLASH_BASE <= address < FLASH_BASE + FLASH_BYTES:
        raise ValueError("WHX address must be sector-aligned inside the 4 MiB flash")
    if address + len(whx) > FLASH_BASE + FLASH_BYTES:
        raise ValueError("WHX does not fit in flash")
    blocks = read_blocks(firmware) if firmware is not None else []
    families = {family for flags, _, family, _ in blocks if flags & UF2_FLAG_FAMILY}
    if len(families) > 1:
        raise ValueError("firmware UF2 mixes family IDs")
    family = families.pop() if families else RP2350_ARM_S
    for _, block_addr, _, payload in blocks:
        if block_addr + len(payload) > address:
            raise ValueError(f"firmware reaches 0x{block_addr + len(payload):08x}, "
                             f"overlapping the WHX at 0x{address:08x}")
    merged = blocks + binary_blocks(whx, address, family)
    if audio is not None:
        layout = parse_flash_layout(FLASH_LAYOUT_H)
        assets = layout['FLASH_ASSETS_ADDR']
        cap = min(layout['FLASH_ASSETS_MAX'], layout['FLASH_AUDIO_MAX'],
                  layout['FLASH_SAVES_ADDR'] - assets)
        if not audio or len(audio) > cap:
            raise ValueError("audio archive is empty or exceeds its reserved flash region")
        if address + len(whx) > assets:
            raise ValueError("WHX overlaps audio assets")
        merged += binary_blocks(audio, assets, family)
    if world is not None:
        layout = parse_flash_layout(FLASH_LAYOUT_H)
        start = layout['FLASH_WORLD_ADDR']
        cap = min(layout['FLASH_WORLD_MAX'], layout['FLASH_ASSETS_ADDR'] - start)
        if not world or len(world) > cap or world[:4] != b'FCWA':
            raise ValueError("world atlas is empty, invalid or exceeds its reserved flash region")
        if address + len(whx) > start:
            raise ValueError("WHX overlaps the world atlas")
        merged += binary_blocks(world, start, family)
    return write_blocks(merged)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("whx", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--firmware", type=Path, help="firmware UF2 to merge in front")
    parser.add_argument("--audio", type=Path, help="generated audio.bin at FLASH_ASSETS_ADDR")
    parser.add_argument("--world", type=Path, help="generated world_atlas.bin at FLASH_WORLD_ADDR")
    parser.add_argument("--address", type=lambda v: int(v, 0), default=WHX_ADDR)
    args = parser.parse_args(argv)
    firmware = args.firmware.read_bytes() if args.firmware else None
    try:
        data = build(args.whx.read_bytes(), firmware, args.address,
                     args.audio.read_bytes() if args.audio else None,
                     args.world.read_bytes() if args.world else None)
    except ValueError as error:
        parser.error(str(error))
    args.output.write_bytes(data)
    print(f"{args.output}: {len(data) // 512} blocks, WHX at 0x{args.address:08x}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
