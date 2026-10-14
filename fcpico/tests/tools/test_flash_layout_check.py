# SPDX-License-Identifier: BSD-3-Clause
"""Tests for tools/flash_layout_check.py.

Builds minimal synthetic ELF32 files in memory with ``struct`` (an ELF
header with e_phoff/e_phentsize=32/e_phnum, followed by that many PT_LOAD
program headers with chosen p_vaddr/p_paddr/p_filesz) and checks the tool's
pass/fail behaviour and reported addresses against the flash addresses
actually defined in port/flash_layout.h.
"""

import importlib.util
import re
import struct
import subprocess
import sys
from pathlib import Path

import pytest

TOOLS_DIR = Path(__file__).resolve().parents[2] / "tools"
SCRIPT_PATH = TOOLS_DIR / "flash_layout_check.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("flash_layout_check", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


flc = _load_module()

DEFINES = flc.parse_flash_layout(flc.FLASH_LAYOUT_H)
FLASH_XIP_BASE = DEFINES["FLASH_XIP_BASE"]
FLASH_TOTAL_BYTES = DEFINES["FLASH_TOTAL_BYTES"]
FLASH_FIRMWARE_ADDR = DEFINES["FLASH_FIRMWARE_ADDR"]
FLASH_WHX_ADDR = DEFINES["FLASH_WHX_ADDR"]
FLASH_ASSETS_ADDR = DEFINES["FLASH_ASSETS_ADDR"]
CAP = FLASH_WHX_ADDR - FLASH_FIRMWARE_ADDR


def build_elf32(segments) -> bytes:
    """Build a minimal ELF32 LE image: an Ehdr plus one Phdr per segment.

    segments: iterable of (p_vaddr, p_paddr, p_filesz); every header is
    PT_LOAD, which is all flash_layout_check.py looks at.
    """
    segments = list(segments)
    e_ident = b"\x7fELF" + bytes([1, 1, 1, 0]) + b"\x00" * 8
    assert len(e_ident) == 16
    ehdr_rest = struct.pack(
        "<HHIIIIIHHHHHH",
        2,  # e_type: ET_EXEC
        40,  # e_machine: EM_ARM (informational only, unchecked)
        1,  # e_version
        0,  # e_entry
        52,  # e_phoff: right after the 52-byte Ehdr
        0,  # e_shoff
        0,  # e_flags
        52,  # e_ehsize
        32,  # e_phentsize -- Elf32_Phdr is exactly 32 bytes
        len(segments),  # e_phnum
        0,  # e_shentsize
        0,  # e_shnum
        0,  # e_shstrndx
    )
    ehdr = e_ident + ehdr_rest
    assert len(ehdr) == 52
    phdrs = b"".join(
        struct.pack(
            "<IIIIIIII",
            1,  # p_type: PT_LOAD
            0,  # p_offset (unused by the tool)
            p_vaddr,
            p_paddr,
            p_filesz,
            p_filesz,  # p_memsz
            5,  # p_flags: R+X
            0x1000,  # p_align
        )
        for (p_vaddr, p_paddr, p_filesz) in segments
    )
    return ehdr + phdrs


def firmware_end_of(stdout: str) -> int:
    m = re.search(r"Firmware end:\s+0x([0-9a-fA-F]+)", stdout)
    assert m, stdout
    return int(m.group(1), 16)


def write_elf(tmp_path: Path, segments, name: str = "firmware.elf") -> Path:
    path = tmp_path / name
    path.write_bytes(build_elf32(segments))
    return path


# ---------------------------------------------------------------------------
# parse_flash_layout against the real project file
# ---------------------------------------------------------------------------


def test_parse_flash_layout_reads_the_real_header():
    defines = flc.parse_flash_layout(flc.FLASH_LAYOUT_H)
    assert defines["FLASH_XIP_BASE"] == 0x10000000
    assert defines["FLASH_TOTAL_BYTES"] == 0x00400000
    assert defines["FLASH_FIRMWARE_ADDR"] == 0x10000000
    assert defines["FLASH_WHX_ADDR"] == 0x10080000
    assert defines["FLASH_ASSETS_ADDR"] == 0x10300000
    # The "u" suffix must not leak into the parsed integer.
    for value in defines.values():
        assert isinstance(value, int)


# ---------------------------------------------------------------------------
# (a) a segment ending below the cap -> pass
# ---------------------------------------------------------------------------


def test_segment_below_cap_passes(tmp_path, capsys):
    filesz = CAP // 4
    elf_path = write_elf(tmp_path, [(FLASH_FIRMWARE_ADDR, FLASH_FIRMWARE_ADDR, filesz)])

    rc = flc.main([str(elf_path)])
    out = capsys.readouterr().out

    assert rc == 0
    assert firmware_end_of(out) == FLASH_FIRMWARE_ADDR + filesz
    assert "ERROR" not in out


# ---------------------------------------------------------------------------
# (b) a segment ending above the cap -> fail
# ---------------------------------------------------------------------------


def test_segment_above_cap_fails(tmp_path, capsys):
    filesz = CAP + 0x1000
    elf_path = write_elf(tmp_path, [(FLASH_FIRMWARE_ADDR, FLASH_FIRMWARE_ADDR, filesz)])

    rc = flc.main([str(elf_path)])
    out = capsys.readouterr().out

    assert rc == 1
    expected_end = FLASH_FIRMWARE_ADDR + filesz
    assert firmware_end_of(out) == expected_end
    assert expected_end > FLASH_WHX_ADDR
    assert "ERROR" in out
    assert f"0x{FLASH_WHX_ADDR:08x}" in out


# ---------------------------------------------------------------------------
# (c) a RAM-resident segment (p_vaddr in RAM, p_paddr in flash) is counted
#     by its p_paddr, not ignored or double-counted by p_vaddr.
# ---------------------------------------------------------------------------


def test_ram_resident_segment_counted_by_p_paddr(tmp_path, capsys):
    filesz = 0x100
    ram_vaddr = 0x20000000
    elf_path = write_elf(tmp_path, [(ram_vaddr, FLASH_FIRMWARE_ADDR, filesz)])

    rc = flc.main([str(elf_path)])
    out = capsys.readouterr().out

    assert rc == 0
    assert firmware_end_of(out) == FLASH_FIRMWARE_ADDR + filesz
    # The segment must actually appear in the "contributing segments" list,
    # printed by its (flash) p_paddr, with a note about the RAM run address.
    assert f"p_paddr=0x{FLASH_FIRMWARE_ADDR:08x}" in out
    assert f"p_vaddr=0x{ram_vaddr:08x}" in out


def test_parse_elf32_and_flash_load_segments_directly(tmp_path):
    """Same scenario as above, exercised through the importable functions
    directly rather than main(), to pin down p_paddr-vs-p_vaddr behaviour."""
    ram_vaddr = 0x20000000
    data = build_elf32([(ram_vaddr, FLASH_FIRMWARE_ADDR, 0x100)])
    headers = flc.parse_elf32(data)
    assert len(headers) == 1
    assert headers[0].p_vaddr == ram_vaddr
    assert headers[0].p_paddr == FLASH_FIRMWARE_ADDR

    flash_hi = FLASH_XIP_BASE + FLASH_TOTAL_BYTES
    segments = flc.flash_load_segments(headers, FLASH_XIP_BASE, flash_hi)
    assert len(segments) == 1
    assert segments[0].p_paddr == FLASH_FIRMWARE_ADDR


# ---------------------------------------------------------------------------
# (d) a segment outside the flash range is ignored
# ---------------------------------------------------------------------------


def test_segment_outside_flash_is_ignored(tmp_path, capsys):
    real_filesz = 0x100
    # A segment with a huge p_filesz that lands nowhere near flash: if it
    # were ever counted it would either dominate the "end" address or (with
    # its p_paddr below FLASH_XIP_BASE) even make the arithmetic nonsensical.
    out_of_flash = (0x00000000, 0x00000000, 0x10000000)
    in_flash = (FLASH_FIRMWARE_ADDR, FLASH_FIRMWARE_ADDR, real_filesz)
    elf_path = write_elf(tmp_path, [out_of_flash, in_flash])

    rc = flc.main([str(elf_path)])
    out = capsys.readouterr().out

    assert rc == 0
    assert firmware_end_of(out) == FLASH_FIRMWARE_ADDR + real_filesz
    # The out-of-flash segment must never show up in the contributing list.
    assert "0x00000000" not in out


def test_only_out_of_flash_segments_yields_zero_bytes_used(tmp_path, capsys):
    elf_path = write_elf(tmp_path, [(0x00000000, 0x00000000, 0x10000000)])

    rc = flc.main([str(elf_path)])
    out = capsys.readouterr().out

    assert rc == 0
    assert firmware_end_of(out) == FLASH_FIRMWARE_ADDR
    assert "Bytes used:     0x0 (0 bytes)" in out


# ---------------------------------------------------------------------------
# --whx
# ---------------------------------------------------------------------------


def test_whx_within_cap_passes(tmp_path, capsys):
    elf_path = write_elf(tmp_path, [(FLASH_FIRMWARE_ADDR, FLASH_FIRMWARE_ADDR, 0x100)])
    whx_cap = FLASH_ASSETS_ADDR - FLASH_WHX_ADDR
    whx_path = tmp_path / "doom1.whx"
    whx_path.write_bytes(b"\x00" * (whx_cap // 2))

    rc = flc.main([str(elf_path), "--whx", str(whx_path)])
    out = capsys.readouterr().out

    assert rc == 0
    assert "ERROR" not in out
    assert f"WHX file:       {whx_path}" in out


def test_whx_over_cap_fails(tmp_path, capsys):
    elf_path = write_elf(tmp_path, [(FLASH_FIRMWARE_ADDR, FLASH_FIRMWARE_ADDR, 0x100)])
    # The WHX reservation ends where the world sprite atlas begins.
    layout = flc.parse_flash_layout(flc.FLASH_LAYOUT_H)
    whx_cap = layout["FLASH_WHX_MAX"]
    assert FLASH_WHX_ADDR + whx_cap == layout["FLASH_WORLD_ADDR"]
    whx_path = tmp_path / "doom1.whx"
    whx_path.write_bytes(b"\x00" * (whx_cap + 0x1000))

    rc = flc.main([str(elf_path), "--whx", str(whx_path)])
    out = capsys.readouterr().out

    assert rc == 1
    assert "ERROR" in out
    assert f"0x{FLASH_WHX_ADDR + whx_cap:08x}" in out


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


def test_bad_magic_is_a_clean_error(tmp_path, capsys):
    bad_path = tmp_path / "not_an_elf.elf"
    bad_path.write_bytes(b"not an elf file at all")

    rc = flc.main([str(bad_path)])
    captured = capsys.readouterr()

    assert rc == 2
    assert "error" in captured.err.lower()


# ---------------------------------------------------------------------------
# One end-to-end subprocess invocation, to prove the CLI itself works.
# ---------------------------------------------------------------------------


def test_cli_subprocess_smoke(tmp_path):
    elf_path = write_elf(tmp_path, [(FLASH_FIRMWARE_ADDR, FLASH_FIRMWARE_ADDR, 0x100)])
    result = subprocess.run(
        [sys.executable, str(SCRIPT_PATH), str(elf_path)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert firmware_end_of(result.stdout) == FLASH_FIRMWARE_ADDR + 0x100
