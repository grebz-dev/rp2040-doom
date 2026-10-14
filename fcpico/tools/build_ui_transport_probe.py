#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-3-Clause
"""Build an isolated v3 mailbox receive probe without changing production ROM."""

from __future__ import annotations

import argparse
from pathlib import Path
import subprocess

from build_sprite_probe import ROOT, SOURCE

STAMP = '        .include "../version.inc"'
TAIL_READ = '        READ8 MBX_ZP_HI+$38\n'
HELLO = '        lda #FCBUS_PROTOCOL_V2\n'
TABLES = '.valid:\n        lda <MBX_ZP_LO+MBX_FLAGS\n'
SETUP = '        sta <KEY_NEW\n'
LOOP = '        jsr KEY_RTN\n        jmp UR_MAIN_LOOP\n'
TABLE_ANCHOR = '        .bank 3\n        org $ED00'


def probe_source(hud: bool = False) -> str:
    source = SOURCE.read_text()
    if (source.count(STAMP) != 1 or source.count(TAIL_READ) != 1 or
            source.count(HELLO) != 1 or source.count(TABLES) != 1 or
            source.count(SETUP) != 1 or source.count(LOOP) != 1 or
            source.count(TABLE_ANCHOR) != 1):
        raise ValueError("Doom v2 source changed; inspect v3 insertion points")
    extra = ''.join(f'        lda $2007\n        sta $03{n:02X}\n'
                    for n in range(16))
    commit = '''.valid:
        ; The main loop publishes a complete $0200 OAM shadow by setting
        ; $0310 last. A commit frame defers BG tables until the next NMI.
        lda $0310
        beq .ui_tables
        lda #0
        sta $2003
        lda #2
        sta $4014
        lda #0
        sta $0310
        jmp .no_pal
.ui_tables:
        lda <MBX_ZP_LO+MBX_FLAGS
'''
    source = (source.replace(HELLO, '        lda #FCBUS_PROTOCOL_V3\n')
            .replace(SETUP, SETUP + '        sta $0310\n')
            .replace(TAIL_READ, TAIL_READ + extra)
            .replace(TABLES, commit)
            .replace(STAMP, '        db "20DOOM-03-9005",0' if hud else
                     '        db "20DOOM-03-9001",0'))
    if hud:
        source = add_hud_demo(source)
    return source


def add_hud_demo(source: str) -> str:
    setup = '''        ; Park the CPU OAM shadow and make the local font's colour 3 white.
        ldx #0
        lda #$EF
.shadow_park:
        sta $0200,x
        inx
        inx
        inx
        inx
        bne .shadow_park
        lda #$FF
        sta $0311
        lda #$3F
        sta $2006
        lda #$13
        sta $2006
        lda #$30
        sta $2007
'''
    anchor = '        lda #$08\n        sta $2006\n        lda #0\n        sta $2006\n        lda #FP_COM_INI'
    if source.count(anchor) != 1:
        raise ValueError("no unique setup insert point")
    source = source.replace(anchor, setup + anchor)
    source = source.replace(LOOP, '        jsr KEY_RTN\n        jsr UI_APPLY\n        jmp UR_MAIN_LOOP\n')
    routine = '''
; A minimal dynamic HUD slice: H and three native font digits for health.
; Generation and XOR are checked before touching the complete OAM shadow.
UI_APPLY:
        lda <MBX_ZP_LO+MBX_FLAGS
        and #MBX_FLAG_UI_VALID
        bne .packet
        rts
.packet:
        lda $0300
        cmp $0311
        bne .new
        rts
.new:
        lda #$A5
        ldx #0
.checksum:
        eor $0300,x
        inx
        cpx #16
        bne .checksum
        cmp #0
        beq .checked
        rts
.checked:
        lda #0
        sta $0310
        lda $0300
        sta $0311
        lda $0301
        and #1
        bne .show
        lda #$EF
        sta $0200
        sta $0204
        sta $0208
        sta $020C
        lda #1
        sta $0310
        rts
.show:
        lda #191
        sta $0200
        sta $0204
        sta $0208
        sta $020C
        lda #$48
        sta $0201
        lda #0
        sta $0202
        sta $0206
        sta $020A
        sta $020E
        lda #104
        sta $0203
        lda #112
        sta $0207
        lda #120
        sta $020B
        lda #128
        sta $020F
        lda $0306
        sta $12
        lda $0307
        sta $13
        ldx #0
.hundreds:
        lda $13
        bne .subtract100
        lda $12
        cmp #100
        bcc .tens
.subtract100:
        sec
        lda $12
        sbc #100
        sta $12
        lda $13
        sbc #0
        sta $13
        inx
        cpx #10
        bne .hundreds
.tens:
        txa
        clc
        adc #$30
        sta $0205
        ldx #0
.tenloop:
        lda $12
        cmp #10
        bcc .ones
        sec
        sbc #10
        sta $12
        inx
        bne .tenloop
.ones:
        txa
        clc
        adc #$30
        sta $0209
        lda $12
        clc
        adc #$30
        sta $020D
        lda #1
        sta $0310
        rts

'''
    return source.replace(TABLE_ANCHOR, routine + TABLE_ANCHOR)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--nesasm", type=Path, default=ROOT / ".build/tools/nesasm/nesasm")
    parser.add_argument("--hud", action="store_true",
                        help="decode checked health snapshot to four native font sprites")
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    temporary = SOURCE.parent / "PG_ui_transport_tmp.asm"
    try:
        temporary.write_text(probe_source(args.hud))
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
