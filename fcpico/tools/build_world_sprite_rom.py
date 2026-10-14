#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-3-Clause
"""Build the v5 native HUD ROM with acknowledged world sprite packets.

The 9016 HUD source gains three console-side pieces:

* NMI: one world packet per heartbeat. CHR packets write four tiles from the
  attribute bytes; OAM packets stage 16 entries at $0400 after the critical
  section. A committing OAM packet writes sprite palettes 0 and 2 in vblank.
  The third heartbeat byte reports the generation in OAM (high nibble) and the
  last applied packet sequence (low nibble).
* Main loop: WORLD_APPLY copies a committed generation into world slots 35..63
  and requests the next OAM DMA. Generation 0 hides those slots.
* A new world packet outranks a pending OAM DMA. The RP sends nothing while it
  waits for a commit to show, so HUD and world DMAs run in that gap instead of
  costing a packet heartbeat each.
* Menu/hide paths leave world slots alone while a world generation owns them.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import subprocess

from build_native_status_probe import MENU_LAYOUTS, native_status_source
from build_sprite_probe import ROOT, SOURCE
from native_status_art import (key_icon_art, menu_logo_art, resident_art,
                               resident_face_map, selected_plate_art)

STAMP = "20DOOM-05-9031"
W_SHOWN_HI, W_SLOTS, W_SLOTS_HI, W_ACK = 0x0312, 0x0313, 0x0314, 0x0315
W_POST, W_COMMIT, W_COMMIT_GEN = 0x0316, 0x0317, 0x0318
W_STAGE = 0x0400
WORLD_SLOT_BASE = 0x0200 + 35 * 4
WORLD_SLOT_BYTES = 29 * 4
# Sprite palettes 0 ($3F11) and 2 ($3F19) as left by the 9016 setup: menu glyphs
# and the title logo. The RP restores them before releasing a menu.
MENU_SPRITE_PALETTES = bytes((0x16, 0x0F, 0x16, 0x12, 0x22, 0x38))
FACE_TILES = 119


def world_tile_pool() -> tuple[int, ...]:
    """Sprite-table tiles that no gameplay, menu or logo OAM references."""
    face_tiles, _ = resident_face_map()
    assert max(face_tiles) < 0x80 + FACE_TILES
    used = set(range(0x13, 0x13 + 21))      # small pause logo, 7x3
    used |= set(range(0x2E, 0x2E + len(key_icon_art()) // 16))
    used |= set(range(0x32, 0x32 + len(selected_plate_art()) // 16))
    used |= {ord(">")}
    used |= {ord(char) for labels, *_ in MENU_LAYOUTS for label in labels
             for char in label if char != " "}
    used |= set(range(0x60, 0x60 + len(menu_logo_art()) // 16))
    used |= set(range(0x80, 0x80 + FACE_TILES))
    assert len(resident_art()) == 128 * 16
    return tuple(tile for tile in range(0x100) if tile not in used)


def _replace_once(source: str, old: str, new: str) -> str:
    if source.count(old) != 1:
        raise ValueError(f"no unique v5 insertion point: {old[:60]!r}")
    return source.replace(old, new)


def _chr_packet() -> str:
    lines = [".world_chr:"]
    for tile in range(4):
        lines += [f"        lda <MBX_ZP_LO+MBX_WORLD_ARG+{tile * 2}", "        sta $2006",
                  f"        lda <MBX_ZP_LO+MBX_WORLD_ARG+{tile * 2 + 1}", "        sta $2006",
                  f"        WRITE8 MBX_ZP_HI+${tile * 16:02X}",
                  f"        WRITE8 MBX_ZP_HI+${tile * 16 + 8:02X}"]
    lines += ["        lda <MBX_ZP_LO+MBX_WORLD_SEQ", f"        sta ${W_ACK:04X}",
              "        jmp .no_pal"]
    return "\n".join(lines) + "\n"


def _palette_writes() -> str:
    lines = []
    for group, address in enumerate((0x3F11, 0x3F19)):
        lines += ["        lda #$3F", "        sta $2006", f"        lda #${address & 0xFF:02X}",
                  "        sta $2006"]
        for i in range(3):
            lines += [f"        lda <MBX_ZP_LO+MBX_WORLD_PAL+{group * 3 + i}", "        sta $2007"]
    return "\n".join(lines) + "\n"


def _stage_copy(label: str, base: int) -> str:
    lines = [f"{label}:"]
    for i in range(64):
        lines += [f"        lda <MBX_ZP_HI+${i:02X}", f"        sta ${base + i:04X}"]
    lines.append("        jmp .post_commit")
    return "\n".join(lines) + "\n"


def world_nmi_packet() -> str:
    return f"""        lda #0
        sta $0310
        lda ${W_SLOTS_HI:04X}
        sta ${W_SHOWN_HI:04X}
        jmp .no_pal
.ui_tables:
        lda <MBX_ZP_LO+MBX_FLAGS
        and #MBX_FLAG_WORLD
        bne .world_packet
        jmp .world_none
.world_packet:
        ; A duplicate is not reapplied; the RP resends until its seq is echoed.
        lda <MBX_ZP_LO+MBX_WORLD_SEQ
        cmp ${W_ACK:04X}
        bne .world_new
        jmp .no_pal
.world_new:
        lda <MBX_ZP_LO+MBX_WORLD_OP
        cmp #WORLD_OP_CHR
        bne .world_not_chr
        jmp .world_chr
.world_not_chr:
        cmp #WORLD_OP_OAM
        beq .world_oam
        jmp .no_pal
.world_oam:
        ; Staging and commit touch CPU RAM only; finish them after scroll.
        lda <MBX_ZP_LO+MBX_WORLD_SEQ
        sta ${W_ACK:04X}
        lda #1
        sta ${W_POST:04X}
        lda <MBX_ZP_LO+MBX_WORLD_ARG
        bmi .world_palettes
        jmp .no_pal
.world_palettes:
{_palette_writes()}        jmp .no_pal
{_chr_packet()}.world_none:
        lda <MBX_ZP_LO+MBX_FLAGS
"""


def world_post() -> str:
    return f"""        sta $2005
        sta $2005

        ; World OAM staging is not PPU work and may run past vblank.
        lda ${W_POST:04X}
        bne .post_world
        jmp .post_done
.post_world:
        lda #0
        sta ${W_POST:04X}
        lda <MBX_ZP_LO+MBX_WORLD_ARG
        and #$7F
        cmp #WORLD_OAM_NONE
        bne .post_payload
        jmp .post_commit
.post_payload:
        cmp #WORLD_OAM_PACKET
        bne .post_low
        jmp .post_high
{_stage_copy('.post_low', W_STAGE)}{_stage_copy('.post_high', W_STAGE + 64)}.post_commit:
        lda <MBX_ZP_LO+MBX_WORLD_ARG
        bmi .post_publish
        jmp .post_done
.post_publish:
        lda <MBX_ZP_LO+MBX_WORLD_GEN
        sta ${W_COMMIT_GEN:04X}
        lda #1
        sta ${W_COMMIT:04X}
.post_done:

        lda <MBX_ZP_LO+MBX_FLAGS
        and #MBX_FLAG_APU_VALID
"""


def world_apply() -> str:
    return f"""
; Publish a committed world generation into OAM slots 35..63. Clearing $0310
; first prevents a pending UI DMA from sending half-copied entries.
WORLD_APPLY:
        lda ${W_COMMIT:04X}
        bne .commit
        rts
.commit:
        lda #0
        sta $0310
        sta ${W_COMMIT:04X}
        ldx #0
        lda ${W_COMMIT_GEN:04X}
        beq .clear
.copy:
        lda ${W_STAGE:04X},x
        sta ${WORLD_SLOT_BASE:04X},x
        inx
        cpx #{WORLD_SLOT_BYTES}
        bne .copy
        jmp .publish
.clear:
        lda #$EF
.hide:
        sta ${WORLD_SLOT_BASE:04X},x
        inx
        inx
        inx
        inx
        cpx #{WORLD_SLOT_BYTES}
        bne .hide
.publish:
        lda ${W_COMMIT_GEN:04X}
        sta ${W_SLOTS:04X}
        asl a
        asl a
        asl a
        asl a
        sta ${W_SLOTS_HI:04X}
        lda #1
        sta $0310
        rts

"""


def native_world_source() -> str:
    source = native_status_source()
    source = _replace_once(source, "20DOOM-04-9016", STAMP)
    source = _replace_once(source, "        lda #FCBUS_PROTOCOL_V4\n",
                           "        lda #FCBUS_PROTOCOL_V5\n")
    init = "".join(f"        sta ${address:04X}\n" for address in
                   (W_SHOWN_HI, W_SLOTS, W_SLOTS_HI, W_ACK, W_POST, W_COMMIT, W_COMMIT_GEN))
    source = _replace_once(source, "        lda #$FF\n        sta $0311\n",
                           "        lda #0\n" + init + "        lda #$FF\n        sta $0311\n")
    dma_tail = ("        lda #2\n        sta $4014\n        lda #0\n        sta $0310\n"
                "        jmp .no_pal\n.ui_tables:\n        lda <MBX_ZP_LO+MBX_FLAGS\n")
    source = _replace_once(source, dma_tail, "        lda #2\n        sta $4014\n" +
                           world_nmi_packet())
    heartbeat = ("        lda #FP_COM_KEY\n        sta $2007\n        lda <KEY_NEW\n"
                 "        sta $2007\n        lda #0\n        sta $2007\n")
    source = _replace_once(source, heartbeat,
        "        lda #FP_COM_KEY\n        sta $2007\n        lda <KEY_NEW\n"
        "        sta $2007\n"
        "        ; pad2 = shown generation << 4 | last applied packet sequence.\n"
        f"        lda ${W_SHOWN_HI:04X}\n        ora ${W_ACK:04X}\n        sta $2007\n")
    source = _replace_once(source,
        "        sta $2005\n        sta $2005\n\n        lda <MBX_ZP_LO+MBX_FLAGS\n"
        "        and #MBX_FLAG_APU_VALID\n", world_post())
    source = _replace_once(source, "        jsr UI_APPLY\n        jmp UR_MAIN_LOOP\n",
                           "        jsr UI_APPLY\n        jsr WORLD_APPLY\n"
                           "        jmp UR_MAIN_LOOP\n")
    # The status-hidden path clears only status slots; MENU_APPLY owns 35..63
    # and leaves them to an active world generation.
    hidden = "".join(f"        sta $02{n * 4:02X}\n" for n in range(35, 64))
    source = _replace_once(source, hidden + "        jsr MENU_APPLY\n",
                           "        jsr MENU_APPLY\n")
    source = _replace_once(source, "MENU_APPLY:\n        lda #$EF\n",
                           f"MENU_APPLY:\n        lda ${W_SLOTS:04X}\n"
                           "        beq .menu_owned\n        rts\n.menu_owned:\n"
                           "        lda #$EF\n")
    source = _replace_once(source, "UI_APPLY:\n", world_apply() + "UI_APPLY:\n")
    source = _replace_once(source, "        lda $0310\n        beq .ui_tables\n",
        "        ; A new world packet outranks a pending DMA, which keeps $0310.\n"
        "        lda <MBX_ZP_LO+MBX_FLAGS\n        and #MBX_FLAG_WORLD\n"
        "        beq .dma_pending\n"
        "        lda <MBX_ZP_LO+MBX_WORLD_SEQ\n"
        f"        cmp ${W_ACK:04X}\n        bne .world_new\n"
        ".dma_pending:\n        lda $0310\n        beq .ui_tables\n")
    return source


def build(output: Path, nesasm: Path) -> Path:
    output.mkdir(parents=True, exist_ok=True)
    temporary = SOURCE.parent / "PG_world_rom_tmp.asm"
    try:
        temporary.write_text(native_world_source())
        subprocess.run([str(nesasm.resolve()), "-s", "-f", "-o",
                        str(output / "probe.raw.nes"), temporary.name],
                       cwd=SOURCE.parent, check=True, stdout=subprocess.DEVNULL)
    finally:
        temporary.unlink(missing_ok=True)
    subprocess.run(["python3", str(ROOT / "fcpico/tools/nes/normalize_ines.py"),
                    str(output / "probe.raw.nes"), str(output / "probe.nes")], check=True)
    subprocess.run(["python3", str(ROOT / "fcpico/tools/nes/check_fixbank.py"),
                    str(output / "probe.nes"),
                    str(ROOT / "fcpico/external/fc-pico/tutorial_project/BOOTROM/bootrom_fixr.bin")], check=True)
    return output / "probe.nes"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--nesasm", type=Path, default=ROOT / ".build/tools/nesasm/nesasm")
    args = parser.parse_args()
    build(args.output.resolve(), args.nesasm)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
