#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-3-Clause
"""Build an isolated setup-time sprite probe from the Doom v2 console bank.

The production ROM source is not changed. This probe tests writes to both ends
of the system-font-free $1800-$1FFF sprite region, palette and visible OAM.
It does not establish the physical board's CHR wiring or a runtime UI schedule.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "fcpico/bootrom/src/PG_main.asm"
ANCHOR = "        lda #$08\n        sta $2006\n        lda #0\n        sta $2006\n        lda #FP_COM_INI"
TABLE_ANCHOR = "        .bank 3\n        org $ED00"
STAMP_INCLUDE = '        .include "../version.inc"'
PROBE_STAMP = '        db "20DOOM-02-9001",0'
DIAGNOSTIC_STAMP = '        db "20DOOM-02-9002",0'
LIMIT_STAMP = '        db "20DOOM-02-9004",0'


def tile(*rows: str) -> bytes:
    if len(rows) != 8 or any(len(row) != 8 or set(row) - {".", "#"} for row in rows):
        raise ValueError("probe art must be eight rows of eight pixels")
    return bytes(int(row.replace("#", "1").replace(".", "0"), 2) for row in rows) + bytes(8)


def table(name: str, data: bytes) -> str:
    lines = [name + ":"]
    for offset in range(0, len(data), 16):
        lines.append("        db " + ",".join(f"${value:02X}" for value in data[offset:offset + 16]))
    return "\n".join(lines) + "\n"


def probe_source(diagnostic: bool = False, limit: bool = False) -> tuple[str, bytes, bytes]:
    if diagnostic and limit:
        raise ValueError("select only one probe variant")
    art = b"".join((
        tile("..##....", ".#..#...", "#....#..", "######..", "#....#..", "#....#..", "........", "........"),
        tile("...##...", "..####..", ".######.", "########", ".######.", "..####..", "...##...", "........"),
        tile(".######.", "#......#", "#.#..#.#", "#......#", "#......#", "#......#", "#......#", "#......#"),
        tile(".######.", "#......#", "#.#..#.#", "#......#", "#......#", "#......#", "#......#", "#......#"),
        tile("#......#", "#......#", "#..##..#", "#......#", "#......#", "#......#", "#......#", ".######."),
        tile("#......#", "#......#", "#..##..#", "#......#", "#......#", "#......#", "#......#", ".######."),
    ))
    last = tile("#.#.#.#.", ".#.#.#.#", "#.#.#.#.", ".#.#.#.#", "#.#.#.#.", ".#.#.#.#", "#.#.#.#.", ".#.#.#.#")
    oam = bytes((31, 128, 0, 24, 31, 129, 0, 40,
                 31, 130, 0, 72, 31, 131, 0, 80,
                 39, 132, 0, 72, 39, 133, 0, 80,
                 31, 255, 0, 120))
    if diagnostic:
        # The first A/diamond pair is unchanged. At y=64, draw the same two
        # pattern indices from later OAM slots, then copied art at new pattern
        # indices. This separates missing OAM entries from missing CHR tiles.
        oam += bytes((63, 128, 0, 24, 63, 129, 0, 40,
                      63, 134, 0, 72, 63, 135, 0, 88))
        art += art[:32]
    if limit:
        # Nine sprites overlap scanlines 32..39; the last diamond must be
        # dropped by the real NES sprite evaluator with limit removal off.
        oam = oam[:16] + bytes((31, 132, 0, 96, 31, 133, 0, 104)) + oam[24:]
        oam += bytes((31, 128, 0, 136, 31, 129, 0, 160))
    setup = f"""        ; Setup-only sprite probe, with rendering still disabled.
        lda #$18
        sta $2006
        lda #0
        sta $2006
        ldx #0
.probe_chr:
        lda probe_tiles,x
        sta $2007
        inx
        cpx #{len(art)}
        bne .probe_chr
        lda #$1F
        sta $2006
        lda #$F0
        sta $2006
        ldx #0
.probe_last:
        lda probe_last_tile,x
        sta $2007
        inx
        cpx #16
        bne .probe_last
        lda #$3F
        sta $2006
        lda #$11
        sta $2006
        lda #$30
        sta $2007
        lda #0
        sta $2003
        ldx #0
.probe_oam:
        lda probe_oam_data,x
        sta $2004
        inx
        cpx #{len(oam)}
        bne .probe_oam

"""
    source = SOURCE.read_text()
    if source.count(ANCHOR) != 1 or source.count(TABLE_ANCHOR) != 1 or \
            source.count(STAMP_INCLUDE) != 1:
        raise ValueError("Doom ROM source no longer has unique probe insertion points")
    source = source.replace(ANCHOR, setup + ANCHOR)
    source = source.replace(TABLE_ANCHOR,
                            table("probe_tiles", art) + table("probe_last_tile", last)
                            + table("probe_oam_data", oam) + "\n" + TABLE_ANCHOR)
    source = source.replace(STAMP_INCLUDE,
                            DIAGNOSTIC_STAMP if diagnostic else
                            LIMIT_STAMP if limit else PROBE_STAMP)
    return source, bytes(2048) + art + bytes(2048 - len(art) - 16) + last, oam


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--nesasm", type=Path, default=ROOT / ".build/tools/nesasm/nesasm")
    parser.add_argument("--diagnostic", action="store_true",
                        help="duplicate A/diamond by OAM slot and CHR address")
    parser.add_argument("--limit", action="store_true",
                        help="place a ninth sprite on one scanline for Mesen limit proof")
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    source, chr_data, oam = probe_source(args.diagnostic, args.limit)
    temporary = SOURCE.parent / "PG_sprite_probe_tmp.asm"
    try:
        temporary.write_text(source)
        subprocess.run([str(args.nesasm.resolve()), "-s", "-f", "-o", str(output / "probe.raw.nes"),
                        temporary.name], cwd=SOURCE.parent, check=True)
    finally:
        temporary.unlink(missing_ok=True)
    subprocess.run(["python3", str(ROOT / "fcpico/tools/nes/normalize_ines.py"),
                    str(output / "probe.raw.nes"), str(output / "probe.nes")], check=True)
    subprocess.run(["python3", str(ROOT / "fcpico/tools/nes/check_fixbank.py"),
                    str(output / "probe.nes"),
                    str(ROOT / "fcpico/external/fc-pico/tutorial_project/BOOTROM/bootrom_fixr.bin")], check=True)
    (output / "sprite_chr.bin").write_bytes(chr_data)
    (output / "oam.bin").write_bytes(oam + bytes((0xEF, 0, 0, 0)) * (64 - len(oam) // 4))
    (output / "sprite_pal.bin").write_bytes(bytes((0x0F, 0x30)) + bytes((0x0F,)) * 14)
    print(f"wrote {output / 'probe.nes'} and expected sprite dumps")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
