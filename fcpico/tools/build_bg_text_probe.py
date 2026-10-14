#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-3-Clause
"""Build isolated v4 ROM with a 28-tile native background text row."""

from __future__ import annotations

import argparse
from pathlib import Path
import subprocess

from build_sprite_probe import ROOT, SOURCE
from build_ui_transport_probe import probe_source

MESSAGE = b"NATIVE BACKGROUND TEXT WORKS"
assert len(MESSAGE) == 28


def text_source() -> str:
    source = probe_source(hud=True)
    old_hello = '        lda #FCBUS_PROTOCOL_V3\n'
    old_stamp = '        db "20DOOM-03-9005",0'
    anchor = '        lda #$08\n        sta $2006\n        lda #0\n        sta $2006\n        lda #FP_COM_INI'
    table_anchor = '        .bank 3\n        org $ED00'
    for token in (old_hello, old_stamp, anchor, table_anchor):
        if source.count(token) != 1:
            raise ValueError(f"no unique text probe insertion point: {token!r}")
    setup = '''        ; Row 29, columns 2..29 use the locally stored BG font.
        lda #$23
        sta $2006
        lda #$A2
        sta $2006
        ldx #0
.native_text:
        lda native_text_data,x
        sta $2007
        inx
        cpx #NATIVE_TEXT_TILES
        bne .native_text
'''
    table = 'native_text_data:\n        db ' + ','.join(f'${value:02X}' for value in MESSAGE) + '\n\n'
    return (source.replace(old_hello, '        lda #FCBUS_PROTOCOL_V4\n')
            .replace(old_stamp, '        db "20DOOM-04-9001",0')
            .replace(anchor, setup + anchor)
            .replace(table_anchor, table + table_anchor))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--nesasm", type=Path, default=ROOT / ".build/tools/nesasm/nesasm")
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    temporary = SOURCE.parent / "PG_bg_text_tmp.asm"
    try:
        temporary.write_text(text_source())
        subprocess.run([str(args.nesasm.resolve()), "-s", "-f", "-o",
                        str(output / "probe.raw.nes"), temporary.name],
                       cwd=SOURCE.parent, check=True)
    finally:
        temporary.unlink(missing_ok=True)
    subprocess.run(["python3", str(ROOT / "fcpico/tools/nes/normalize_ines.py"),
                    str(output / "probe.raw.nes"), str(output / "probe.nes")], check=True)
    subprocess.run(["python3", str(ROOT / "fcpico/tools/nes/check_fixbank.py"),
                    str(output / "probe.nes"),
                    str(ROOT / "fcpico/external/fc-pico/tutorial_project/BOOTROM/bootrom_fixr.bin")], check=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
