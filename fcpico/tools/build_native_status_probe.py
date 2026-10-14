#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-3-Clause
"""Build a v4 ROM with a 4x4 Doomguy face and compact status sprites."""

from __future__ import annotations

import argparse
from pathlib import Path
import subprocess

from build_bg_text_probe import MESSAGE, text_source
from build_sprite_probe import ROOT, SOURCE, table
from native_status_art import (bg_status_tiles,
                               face_resident_index, key_icon_art,
                               menu_logo_art, resident_art,
                               resident_face_map, resident_face_overflow,
                               selected_plate_art)

STAMP = "20DOOM-04-9016"
STATUS_OAM_COUNT = 35
MENU_GLYPHS = 28
OAM_COUNT = 64
MENU_LAYOUTS = (
    (("NEW", "OPTIONS", "LOAD", "SAVE", "READ ME", "QUIT"), 87, 16, 92),
    (("", "", ""), 87, 32, 92),
    (("EASY", "NORMAL", "HARD", "ULTRA", "NIGHT"), 87, 16, 92),
    (("END GAME", "MESSAGE", "SOUND"), 58, 16, 80),
    (("EFFECTS", "", "MUSIC"), 87, 16, 80),
)


def menu_oam(layout: tuple[tuple[str, ...], int, int, int]) -> bytes:
    labels, y0, step, x0 = layout
    data = bytearray()
    for row, label in enumerate(labels):
        assert sum(char != " " for char in label) <= 7
        for col, char in enumerate(label):
            if char != " ":
                data.extend((y0 + row * step, ord(char), 0, x0 + col * 8))
    assert len(data) <= MENU_GLYPHS * 4
    data.extend((0xEF, 0x20, 0, 0) * (MENU_GLYPHS - len(data) // 4))
    return bytes(data)


def episode_menu_oam() -> bytes:
    # The three full names are composited into the background stream by
    # fcvideo_overlay_episode_menu(). Only the native cursor uses OAM here.
    return bytes((0xEF, 0, 0, 0)) * MENU_GLYPHS


def main_menu_oam() -> bytes:
    return menu_oam(MENU_LAYOUTS[0])


def logo_oam() -> bytes:
    return bytes(value for row in range(4) for column in range(8)
                 for value in (15 + row * 8, 0x60 + row * 8 + column,
                               2, 96 + column * 8))


def small_logo_oam() -> bytes:
    return bytes(value for row in range(3) for column in range(7)
                 for value in (39 + row * 8, 0x13 + row * 7 + column,
                               3, 100 + column * 8))


def oam_template() -> bytes:
    data = bytearray()
    for row in range(4):
        for col in range(4):
            data.extend((0xEF, 0x80, 1, 112 + col * 8))
    for index in range(6):
        data.extend((0xEF, 0x32 + index, 3, 78 + index % 3 * 10))
    for _ in range(3):
        data.extend((0xEF, 0x2E, 3, 184))
    data.extend((0xEF, 0, 0, 0) * (STATUS_OAM_COUNT - len(data) // 4))
    data.extend((0xEF, 0x20, 0, 0) * MENU_GLYPHS)
    data.extend((0xEF, ord(">"), 0, 76))
    data.extend((0xEF, 0, 0, 0) * (OAM_COUNT - STATUS_OAM_COUNT - MENU_GLYPHS - 1))
    assert len(data) == OAM_COUNT * 4
    return bytes(data)


def status_routine() -> str:
    lines = [
        "; Publish a complete, checked UI snapshot to OAM shadow.",
        "UI_APPLY:",
        "        lda <MBX_ZP_LO+MBX_FLAGS", "        and #MBX_FLAG_UI_VALID",
        "        bne .packet", "        rts", ".packet:",
        "        lda $0300", "        cmp $0311", "        bne .new", "        rts", ".new:",
        "        lda #$A5", "        ldx #0", ".checksum:",
        "        eor $0300,x", "        inx", "        cpx #16", "        bne .checksum",
        "        cmp #0", "        beq .checked", "        rts", ".checked:",
        "        lda #0", "        sta $0310", "        lda $0300", "        sta $0311",
        "        lda $0301", "        and #$01", "        beq .hide",
        "        jmp .show", ".hide:", "        lda #$EF",
    ]
    lines += [f"        sta $02{n * 4:02X}" for n in range(OAM_COUNT)]
    lines += ["        jsr MENU_APPLY", "        lda #1", "        sta $0310",
              "        rts", ".show:",
              "        ; Menu logo sprites replace status slots 0..31.",
              "        ; Restore Y, tile, attributes and X before face updates.",
              "        ldx #0", ".restore_status:",
              "        lda native_oam_data,x", "        sta $0200,x", "        inx",
              "        cpx #140", "        bne .restore_status"]
    for row in range(4):
        lines.append(f"        lda #{199 + row * 8}")
        lines += [f"        sta $02{n * 4:02X}"
                  for n in range(row * 4, row * 4 + 4)]
    lines += [
        "        ldx $0302", "        cpx #42", "        bcc .face_valid",
        "        ldx #0", ".face_valid:",
        "        lda face_group_data,x", "        asl a", "        asl a",
        "        asl a", "        asl a", "        tax",
    ]
    for index in range(16):
        lines += [f"        lda face_tile_data+{index},x",
                  f"        sta $02{index * 4 + 1:02X}",
                  f"        lda face_flip_data+{index},x", "        ora #1",
                  f"        sta $02{index * 4 + 2:02X}"]
    for i in range(6):
        offset = (16 + i) * 4
        mask = 1 << i  # ARMS 2..7 map to owned weapon bits 0..5.
        y = 200 if i < 3 else 212
        lines += ["        lda $0304", f"        and #${mask:02X}",
                  f"        beq .weapon_hide_{i}", f"        lda #{y}",
                  f"        sta $02{offset:02X}",
                  "        lda $0305", "        and #$0F", f"        cmp #{i + 1}",
                  f"        beq .weapon_done_{i}",
                  f".weapon_hide_{i}:", "        lda #$EF",
                  f"        sta $02{offset:02X}", f".weapon_done_{i}:"]
    for i in range(3):
        offset = (22 + i) * 4
        mask = (1 << i) | (1 << (i + 3))
        lines += ["        lda $0303", f"        and #${mask:02X}",
                  f"        beq .key_hide_{i}",
                  f"        lda #{200 + i * 12}",
                  f"        sta $02{offset:02X}",
                  f"        lda #${0x2E + i:02X}", f"        sta $02{offset + 1:02X}",
                  f"        jmp .key_done_{i}", f".key_hide_{i}:",
                  "        lda #$EF", f"        sta $02{offset:02X}",
                  f".key_done_{i}:"]
    lines += [
        "        jsr MENU_APPLY", "        lda #1", "        sta $0310",
        "        rts", "",
        "MENU_APPLY:",
        "        lda #$EF", "        ldx #0", ".hide_menu:",
        "        sta $028C,x", "        inx", "        inx", "        inx", "        inx",
        "        cpx #116", "        bne .hide_menu",
        "        lda $0301", "        and #$04", "        beq .menu_done",
        "        lda $0305", "        lsr a", "        lsr a", "        lsr a", "        lsr a",
        "        beq .menu_done", "        cmp #6", "        bcs .menu_done",
        "        cmp #3", "        beq .menu_done",
        "        sec", "        sbc #1", "        tax",
        "        lda menu_ptr_lo,x", "        sta $16",
        "        lda menu_ptr_hi,x", "        sta $17",
        "        lda menu_first_y,x", "        sta $18",
        "        lda menu_cursor_x,x", "        sta $19",
        "        ldy #0", ".copy_menu:",
        "        lda [$16],y", "        sta $028C,y", "        iny",
        "        cpy #112", "        bne .copy_menu",
        "        lda $0301", "        lsr a", "        lsr a", "        lsr a", "        lsr a",
        "        and #7", "        asl a", "        asl a", "        asl a", "        asl a",
        "        sta $1A", "        lda menu_cursor_step,x", "        cmp #32",
        "        bne .cursor_base", "        asl $1A", ".cursor_base:",
        "        lda $1A", "        clc", "        adc $18", "        sta $02FC",
        "        lda $19", "        sta $02FF",
        ".menu_done:",
        "        lda $0301", "        and #$04", "        bne .logo_active",
        "        rts", ".logo_active:",
        "        lda $0305", "        lsr a", "        lsr a", "        lsr a", "        lsr a",
        "        cmp #1", "        bne .no_logo",
        "        lda $0301", "        and #1", "        bne .small_logo",
        "        ldx #0",
        ".copy_logo:", "        lda logo_oam_data,x", "        sta $0200,x",
        "        inx", "        cpx #128", "        bne .copy_logo",
        "        jmp .no_logo",
        ".small_logo:", "        lda #$EF", "        ldx #0",
        ".small_hide:", "        sta $0200,x", "        inx", "        inx",
        "        inx", "        inx", "        cpx #140", "        bne .small_hide",
        "        ldx #0", ".pause_logo_copy:",
        "        lda logo_oam_data,x", "        sta $0200,x",
        "        inx", "        cpx #128", "        bne .pause_logo_copy",
        ".no_logo:", "        rts", "",
    ]
    return "\n".join(lines) + "\n"


def native_status_source() -> str:
    source = text_source()
    old_message = "native_text_data:\n        db " + ",".join(f"${v:02X}" for v in MESSAGE)
    if source.count(old_message) != 1 or source.count("20DOOM-04-9001") != 1:
        raise ValueError("v4 text source changed")
    source = source.replace("20DOOM-04-9001", STAMP)
    backing_row = bytearray(b" " * len(MESSAGE))
    source = source.replace(old_message,
                            "native_text_data:\n        db " + ",".join(
                                f"${value:02X}" for value in backing_row))
    start = source.index("; A minimal dynamic HUD slice:")
    end = source.index("native_text_data:\n", start)
    source = source[:start] + status_routine() + source[end:]
    setup_anchor = "        lda #$FF\n        sta $0311\n"
    if source.count(setup_anchor) != 1:
        raise ValueError("no unique HUD setup point")
    setup = [
        "        ; Deduplicated and flipped 4x4 face states.",
        "        lda #$18", "        sta $2006", "        lda #0", "        sta $2006",
    ]
    art = resident_art()
    for page in range((len(art) + 255) // 256):
        size = min(256, len(art) - page * 256)
        setup += ["        ldx #0", f".chr_page_{page}:",
                  f"        lda native_chr_data+${page * 256:04X},x",
                  "        sta $2007", "        inx"]
        if size == 256:
            setup += [f"        bne .chr_page_{page}"]
        else:
            setup += [f"        cpx #{size}", f"        bne .chr_page_{page}"]
    overflow = resident_face_overflow()
    for label, data, address in (
        ("face_overflow", overflow, 0x1280),
        ("key", key_icon_art(), 0x12E0),
        ("plate", selected_plate_art(), 0x1320),
    ):
        if not data:
            continue
        setup += [f"        lda #${address >> 8:02X}", "        sta $2006",
                  f"        lda #${address & 0xFF:02X}", "        sta $2006",
                  "        ldx #0", f".{label}_art:",
                  f"        lda native_{label}_chr_data,x", "        sta $2007",
                  "        inx", f"        cpx #{len(data)}", f"        bne .{label}_art"]
    for label, data, address in (("logo", menu_logo_art(), 0x1600),):
        setup += [f"        lda #${address >> 8:02X}", "        sta $2006",
                  f"        lda #${address & 0xFF:02X}", "        sta $2006"]
        for page in range((len(data) + 255) // 256):
            size = min(256, len(data) - page * 256)
            setup += ["        ldx #0", f".{label}_page_{page}:",
                      f"        lda native_{label}_chr_data+${page * 256:04X},x",
                      "        sta $2007", "        inx"]
            setup += ([f"        bne .{label}_page_{page}"] if size == 256 else
                      [f"        cpx #{size}", f"        bne .{label}_page_{page}"])
    bg_tiles = bg_status_tiles()
    for index, (char, _) in enumerate(bg_tiles):
        address = char * 16
        setup += [f"        lda #${address >> 8:02X}", "        sta $2006",
                  f"        lda #${address & 0xFF:02X}", "        sta $2006",
                  "        ldx #0", f".bg_tile_{index}:",
                  f"        lda native_bg_text_data+${index * 16:04X},x",
                  "        sta $2007", "        inx", "        cpx #16",
                  f"        bne .bg_tile_{index}"]
    setup += [
        "        ldx #0", ".native_oam:", "        lda native_oam_data,x",
        "        sta $0200,x", "        inx", "        bne .native_oam",
        "        lda #$3F", "        sta $2006",
        "        lda #$11", "        sta $2006", "        lda #$16", "        sta $2007",
        "        lda #$3F", "        sta $2006",
        "        lda #$12", "        sta $2006", "        lda #$0F", "        sta $2007",
        "        lda #$3F", "        sta $2006", "        lda #$15", "        sta $2006",
        "        lda #$07", "        sta $2007", "        lda #$18", "        sta $2007",
        "        lda #$26", "        sta $2007",
        "        ; Dedicated logo palette: dark blue, bright blue, yellow.",
        "        lda #$3F", "        sta $2006", "        lda #$19", "        sta $2006",
        "        lda #$12", "        sta $2007",
        "        lda #$22", "        sta $2007",
        "        lda #$38", "        sta $2007",
        "        lda #$3F", "        sta $2006",
        "        lda #$1D", "        sta $2006",
        "        lda #$12", "        sta $2007",
        "        lda #$38", "        sta $2007",
        "        lda #$16", "        sta $2007",
    ]
    source = source.replace(setup_anchor, setup_anchor + "\n".join(setup) + "\n")
    font_palette = ("        lda #$13\n        sta $2006\n"
                    "        lda #$30\n        sta $2007")
    if source.count(font_palette) != 1:
        raise ValueError("no unique inherited sprite font palette")
    source = source.replace(font_palette, font_palette.replace("#$30", "#$16"))
    command_anchor = ".valid:\n"
    if source.count(command_anchor) != 1:
        raise ValueError("no unique v4 NMI command point")
    commands = '''.valid:
        ; Two native row pokes per frame. Defer OAM and BG tables on this NMI;
        ; the complete shadow remains ready for the next NMI.
        lda <MBX_ZP_LO+MBX_CMD
        cmp #$C0
        bcc .ui_no_text
        and #$3F
        sta $2006
        lda <MBX_ZP_LO+MBX_CMD+1
        sta $2006
        lda <MBX_ZP_LO+MBX_CMD+2
        sta $2007
        lda <MBX_ZP_LO+MBX_CMD+3
        cmp #$C0
        bcs .second_text
        jmp .no_pal
.second_text:
        and #$3F
        sta $2006
        lda <MBX_ZP_LO+MBX_CMD+4
        sta $2006
        lda <MBX_ZP_LO+MBX_CMD+5
        sta $2007
        jmp .no_pal
.ui_no_text:
'''
    source = source.replace(command_anchor, commands)
    anchor = "native_text_data:\n"
    if source.count(anchor) != 1:
        raise ValueError("no unique data point")
    face_groups = bytes(face_resident_index(index) for index in range(42))
    face_tiles, face_flips = resident_face_map()
    menu_data = "".join(table(f"menu_oam_{index}",
                              main_menu_oam() if index == 0 else
                              episode_menu_oam() if index == 1 else menu_oam(layout))
                        for index, layout in enumerate(MENU_LAYOUTS))
    menu_data += table("menu_ptr_lo", bytes((0,)) * len(MENU_LAYOUTS)).replace(
        "        db " + ",".join("$00" for _ in MENU_LAYOUTS),
        "        db " + ",".join(f"LOW(menu_oam_{i})" for i in range(len(MENU_LAYOUTS))))
    menu_data += table("menu_ptr_hi", bytes((0,)) * len(MENU_LAYOUTS)).replace(
        "        db " + ",".join("$00" for _ in MENU_LAYOUTS),
        "        db " + ",".join(f"HIGH(menu_oam_{i})" for i in range(len(MENU_LAYOUTS))))
    menu_data += table("menu_first_y", bytes(layout[1] for layout in MENU_LAYOUTS))
    menu_data += table("menu_cursor_step", bytes(layout[2] for layout in MENU_LAYOUTS))
    menu_data += table("menu_cursor_x", bytes((76, 32, 76, 60, 60)))
    source = source.replace(anchor,
        "        .bank 1\n        org $A000\n" +
        table("native_chr_data", art) +
        table("native_face_overflow_chr_data", overflow) +
        table("native_key_chr_data", key_icon_art()) +
        table("native_plate_chr_data", selected_plate_art()) +
        table("native_oam_data", oam_template()) +
        table("native_logo_chr_data", menu_logo_art()) +
        table("logo_oam_data", logo_oam()) +
        table("small_logo_oam_data", small_logo_oam()) +
        table("native_bg_text_data", b"".join(tile for _, tile in bg_tiles)) +
        table("face_group_data", face_groups) +
        table("face_tile_data", face_tiles) +
        table("face_flip_data", face_flips) + menu_data + "\n" + anchor)
    return source


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--nesasm", type=Path, default=ROOT / ".build/tools/nesasm/nesasm")
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    temporary = SOURCE.parent / "PG_native_status_tmp.asm"
    try:
        temporary.write_text(native_status_source())
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
    (output / "sprite_chr.bin").write_bytes(resident_art())
    (output / "oam_template.bin").write_bytes(oam_template())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
