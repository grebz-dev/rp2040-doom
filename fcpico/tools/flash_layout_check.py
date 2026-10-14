#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-3-Clause
"""flash_layout_check.py -- check a firmware ELF's flash footprint.

Parses ``../port/flash_layout.h`` (relative to this script) for the
``#define NAME 0x...u`` addresses that make up the single source of truth
for the cartridge's QSPI flash layout, then parses the firmware ELF (32-bit,
little-endian; hand-rolled with ``struct`` -- no external ELF library is a
build dependency) to find where its PT_LOAD segments actually land in flash.

IMPORTANT: a pico-sdk ELF's RAM-resident ``.data`` segment is PT_LOAD with
its *file* image living in flash and its *run* image living in RAM: p_paddr
is the flash load address (where the bytes are copied from at boot) and
p_vaddr is the RAM address they are copied to. This script always sums
flash usage from p_paddr, never p_vaddr -- using p_vaddr would silently
drop .data's real flash cost (or, for a segment that is pure SRAM with no
flash image at all, make it look like it lived in flash).

Usage:
    flash_layout_check.py <elf> [--whx <file>]

Exit 1 (after printing the report) if the firmware's flash footprint runs
past FLASH_WHX_ADDR, or (with --whx) if FLASH_WHX_ADDR + the WHX file size
runs past FLASH_ASSETS_ADDR. Exit 2 on a usage or parsing error. Exit 0
otherwise.
"""

import argparse
import re
import struct
import sys
from dataclasses import dataclass
from pathlib import Path

FLASH_LAYOUT_H = Path(__file__).resolve().parent.parent / "port" / "flash_layout.h"

DEFINE_RE = re.compile(r"^\s*#define\s+(\S+)\s+(0[xX][0-9A-Fa-f]+)u?\b")

ELF_MAGIC = b"\x7fELF"
ELFCLASS32 = 1
ELFDATA2LSB = 1
PT_LOAD = 1

# Elf32_Ehdr, minus the 16-byte e_ident that precedes it.
EHDR_FMT = "<HHIIIIIHHHHHH"
EHDR_SIZE = 16 + struct.calcsize(EHDR_FMT)  # 52
# Elf32_Phdr.
PHDR_FMT = "<IIIIIIII"
PHDR_SIZE = struct.calcsize(PHDR_FMT)  # 32


class FlashLayoutError(Exception):
    """Raised for a malformed flash_layout.h or ELF file."""


@dataclass(frozen=True)
class ProgramHeader:
    p_type: int
    p_offset: int
    p_vaddr: int
    p_paddr: int
    p_filesz: int
    p_memsz: int
    p_flags: int
    p_align: int


def parse_flash_layout(path: Path) -> dict:
    """Return {NAME: value} for every ``#define NAME 0x...u`` line."""
    defines = {}
    text = path.read_text(encoding="utf-8")
    for line in text.splitlines():
        m = DEFINE_RE.match(line)
        if m:
            defines[m.group(1)] = int(m.group(2), 16)
    return defines


def require_defines(defines: dict, names) -> None:
    missing = [n for n in names if n not in defines]
    if missing:
        raise FlashLayoutError(
            f"{FLASH_LAYOUT_H} is missing #define(s): {', '.join(missing)}"
        )


def parse_elf32(data: bytes) -> list:
    """Parse a 32-bit little-endian ELF's program headers from raw bytes."""
    if len(data) < EHDR_SIZE or data[:4] != ELF_MAGIC:
        raise FlashLayoutError("not an ELF file (bad magic)")
    ei_class = data[4]
    ei_data = data[5]
    if ei_class != ELFCLASS32:
        raise FlashLayoutError(f"expected a 32-bit ELF (EI_CLASS=1), got {ei_class}")
    if ei_data != ELFDATA2LSB:
        raise FlashLayoutError(f"expected a little-endian ELF (EI_DATA=1), got {ei_data}")

    (
        _e_type,
        _e_machine,
        _e_version,
        _e_entry,
        e_phoff,
        _e_shoff,
        _e_flags,
        _e_ehsize,
        e_phentsize,
        e_phnum,
        _e_shentsize,
        _e_shnum,
        _e_shstrndx,
    ) = struct.unpack_from(EHDR_FMT, data, 16)

    if e_phnum and e_phentsize != PHDR_SIZE:
        raise FlashLayoutError(
            f"unexpected e_phentsize {e_phentsize} (expected {PHDR_SIZE})"
        )

    headers = []
    for i in range(e_phnum):
        offset = e_phoff + i * e_phentsize
        if offset + PHDR_SIZE > len(data):
            raise FlashLayoutError(f"program header {i} runs past end of file")
        fields = struct.unpack_from(PHDR_FMT, data, offset)
        headers.append(ProgramHeader(*fields))
    return headers


def flash_load_segments(headers, flash_lo: int, flash_hi: int):
    """PT_LOAD segments whose *flash load address* (p_paddr) falls in flash.

    See the module docstring: p_paddr, never p_vaddr, is the address that
    matters here -- it is the only one that says where the segment's bytes
    actually sit in the flash image.
    """
    return [h for h in headers if h.p_type == PT_LOAD and flash_lo <= h.p_paddr < flash_hi]


def format_report(elf_path: Path, defines: dict, segments, args_whx: str):
    """Build the report lines and return (lines, ok, whx_lines, whx_ok)."""
    xip_base = defines["FLASH_XIP_BASE"]
    total_bytes = defines["FLASH_TOTAL_BYTES"]
    firmware_addr = defines["FLASH_FIRMWARE_ADDR"]
    whx_addr = defines["FLASH_WHX_ADDR"]

    lines = []
    lines.append(f"flash_layout_check: {elf_path}")
    lines.append(f"  FLASH_XIP_BASE      = 0x{xip_base:08x}")
    lines.append(f"  FLASH_TOTAL_BYTES   = 0x{total_bytes:08x} ({total_bytes} bytes)")
    lines.append(f"  FLASH_FIRMWARE_ADDR = 0x{firmware_addr:08x}")
    lines.append(f"  FLASH_WHX_ADDR      = 0x{whx_addr:08x}")
    lines.append("")

    lines.append(
        "Contributing PT_LOAD segments (p_paddr in flash range; a RAM-resident "
        ".data's p_vaddr is its RAM run address and is not used for this sum):"
    )
    if segments:
        for h in sorted(segments, key=lambda h: h.p_paddr):
            end = h.p_paddr + h.p_filesz
            ram_note = ""
            if h.p_vaddr != h.p_paddr:
                ram_note = f"  (p_vaddr=0x{h.p_vaddr:08x}, RAM run address)"
            lines.append(
                f"    p_paddr=0x{h.p_paddr:08x} p_filesz=0x{h.p_filesz:08x} "
                f"end=0x{end:08x}{ram_note}"
            )
        firmware_start = min(h.p_paddr for h in segments)
        firmware_end = max(h.p_paddr + h.p_filesz for h in segments)
    else:
        lines.append("    (none)")
        firmware_start = firmware_end = firmware_addr
    lines.append("")

    bytes_used = firmware_end - firmware_addr
    cap = whx_addr - firmware_addr
    bytes_free = cap - bytes_used
    percent_free = (bytes_free / cap * 100.0) if cap else 0.0

    lines.append(f"Firmware start: 0x{firmware_start:08x}")
    lines.append(f"Firmware end:   0x{firmware_end:08x}")
    lines.append(f"Bytes used:     0x{bytes_used:x} ({bytes_used} bytes)")
    lines.append(
        f"Cap:            0x{cap:x} ({cap} bytes)  [FLASH_WHX_ADDR - FLASH_FIRMWARE_ADDR]"
    )
    lines.append(f"Bytes free:     0x{bytes_free:x} ({bytes_free} bytes)")
    lines.append(f"Percent free:   {percent_free:.1f}%")

    ok = firmware_end <= whx_addr
    if not ok:
        lines.append("")
        lines.append(
            f"ERROR: firmware flash footprint ends at 0x{firmware_end:08x}, which is "
            f"past FLASH_WHX_ADDR (0x{whx_addr:08x}) by 0x{firmware_end - whx_addr:x} bytes"
        )

    whx_lines = []
    whx_ok = True
    if args_whx is not None:
        assets_addr = defines["FLASH_ASSETS_ADDR"]
        whx_path = Path(args_whx)
        whx_size = whx_path.stat().st_size
        whx_end = whx_addr + whx_size
        whx_cap = min(assets_addr, whx_addr + defines.get("FLASH_WHX_MAX", assets_addr - whx_addr)) - whx_addr
        whx_free = whx_cap - whx_size

        whx_lines.append(f"WHX file:       {whx_path} ({whx_size} bytes)")
        whx_lines.append(f"WHX end:        0x{whx_end:08x}  [FLASH_WHX_ADDR + size]")
        whx_lines.append(
            f"WHX cap:        0x{whx_cap:x} ({whx_cap} bytes)  "
            "[FLASH_WHX_MAX, before FLASH_WORLD_ADDR / FLASH_ASSETS_ADDR]"
        )
        whx_lines.append(f"WHX bytes free: 0x{whx_free:x} ({whx_free} bytes)")

        whx_ok = whx_size <= whx_cap
        if not whx_ok:
            whx_lines.append(
                f"ERROR: FLASH_WHX_ADDR + WHX size ends at 0x{whx_end:08x}, which is "
                f"past its reserved region (0x{whx_addr + whx_cap:08x}) by "
                f"0x{whx_end - whx_addr - whx_cap:x} bytes"
            )

    return lines, ok, whx_lines, whx_ok


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Check a firmware ELF's flash footprint against port/flash_layout.h."
    )
    parser.add_argument("elf", help="path to the firmware .elf")
    parser.add_argument(
        "--whx", metavar="FILE", help="also check FLASH_WHX_ADDR + size(FILE) <= FLASH_ASSETS_ADDR"
    )
    args = parser.parse_args(argv)

    required = (
        "FLASH_XIP_BASE",
        "FLASH_TOTAL_BYTES",
        "FLASH_FIRMWARE_ADDR",
        "FLASH_WHX_ADDR",
        "FLASH_ASSETS_ADDR",
    )
    try:
        defines = parse_flash_layout(FLASH_LAYOUT_H)
        require_defines(defines, required)

        elf_path = Path(args.elf)
        data = elf_path.read_bytes()
        headers = parse_elf32(data)
    except (OSError, FlashLayoutError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    flash_lo = defines["FLASH_XIP_BASE"]
    flash_hi = flash_lo + defines["FLASH_TOTAL_BYTES"]
    segments = flash_load_segments(headers, flash_lo, flash_hi)

    report_lines, ok, whx_lines, whx_ok = format_report(elf_path, defines, segments, args.whx)
    for line in report_lines:
        print(line)
    if whx_lines:
        print()
        for line in whx_lines:
            print(line)

    return 0 if (ok and whx_ok) else 1


if __name__ == "__main__":
    sys.exit(main())
