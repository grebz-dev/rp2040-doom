; SPDX-License-Identifier: BSD-3-Clause
; Minimal Doom v2 console bank. The permanent $F000 bank remains byte-identical.

        .inesprg 2
        .ineschr 0
        .inesmir 1
        .inesmap 0

        .include "../gen/protocol.inc"

NMI_SVA         EQU $1C
NMI_SVX         EQU $1D
NMI_SVY         EQU $1E
KEY_NEW         EQU $85
FLG_2000        EQU $B0
FLG_2001        EQU $B1
NMI_FLG         EQU $B3
SYS_TIMER       EQU $B5
KEY_RTN         EQU $F006

READ8 MACRO
        lda $2007
        sta <\1+0
        lda $2007
        sta <\1+1
        lda $2007
        sta <\1+2
        lda $2007
        sta <\1+3
        lda $2007
        sta <\1+4
        lda $2007
        sta <\1+5
        lda $2007
        sta <\1+6
        lda $2007
        sta <\1+7
        ENDM

WRITE8 MACRO
        lda <\1+0
        sta $2007
        lda <\1+1
        sta $2007
        lda <\1+2
        sta $2007
        lda <\1+3
        sta $2007
        lda <\1+4
        sta $2007
        lda <\1+5
        sta $2007
        lda <\1+6
        sta $2007
        lda <\1+7
        sta $2007
        ENDM

        .code
        .bank 0
        org $8000

; Called by the fix bank after RAM/ROM checks. Tile $80 selects the cartridge
; stream at $0800; tile $00 addresses the local font instead. The black
; palette hides the picture until the first valid v2 mailbox arrives.
UR_MAIN_SETUP:
        sei
        lda #0
        sta $2000
        sta $2001
        sta <FLG_2000
        sta <FLG_2001
        sta <NMI_FLG
        sta <SYS_TIMER
        sta <KEY_NEW
        bit $2002
        lda #$20
        sta $2006
        lda #0
        sta $2006
        lda #$80
        ldx #4
        ldy #0
.clear_nt:
        sta $2007
        iny
        bne .clear_nt
        dex
        bne .clear_nt
        ; Match the tutorial's PLY_STG_0: zero attributes, and tile $00 in
        ; the adjacent nametable. Fetches crossing into it must NOT consume
        ; the cartridge stream (this produces the measured 66/64 cadence).
        lda #$23
        sta $2006
        lda #$C0
        sta $2006
        lda #0
        ldx #64
.clear_attr:
        sta $2007
        dex
        bne .clear_attr
        ldx #4
.clear_nt1:
        sta $2007
        iny
        bne .clear_nt1
        dex
        bne .clear_nt1
        lda #$3F
        sta $2006
        lda #0
        sta $2006
        lda #$0F
        ldx #32
.clear_pal:
        sta $2007
        dex
        bne .clear_pal

        ; Rendering uses sprite fetches for the measured read count, but no
        ; visible sprites. Put all 64 OAM entries below the visible area.
        lda #0
        sta $2003
        ldx #64
.park_oam:
        lda #$EF
        sta $2004
        lda #0
        sta $2004
        sta $2004
        sta $2004
        dex
        bne .park_oam

        lda #$08
        sta $2006
        lda #0
        sta $2006
        lda #FP_COM_INI
        sta $2007
        lda #0
        sta $2007
        lda #FP_COM_HELLO
        sta $2007
        lda #FCBUS_PROTOCOL_V2
        sta $2007

        lda #$88
        sta <FLG_2000
        sta $2000
        lda #$1E
        sta <FLG_2001
        sta $2001
        lda #0
        sta $2005
        sta $2005
        rts

UR_MAIN_LOOP:
        lda <SYS_TIMER
.wait:
        cmp <SYS_TIMER
        beq .wait
        jsr KEY_RTN
        jmp UR_MAIN_LOOP

; Fixed-size v2 NMI: 128 mailbox bytes, then attribute and palette writes,
; then the three-byte controller heartbeat. Restore the tutorial's final
; PPU address before restoring scroll. APU replay is last.
NMI:
        bit $2002
        sta <NMI_SVA
        inc <SYS_TIMER
        lda <NMI_FLG
        beq .ready
        lda <NMI_SVA
        rti
.ready:
        inc <NMI_FLG
        stx <NMI_SVX
        sty <NMI_SVY

        lda #$08
        sta $2006
        lda #0
        sta $2006
        lda $2007
        READ8 MBX_ZP_LO+$00
        READ8 MBX_ZP_LO+$08
        READ8 MBX_ZP_LO+$10
        READ8 MBX_ZP_LO+$18
        READ8 MBX_ZP_LO+$20
        READ8 MBX_ZP_LO+$28
        READ8 MBX_ZP_LO+$30
        READ8 MBX_ZP_LO+$38
        READ8 MBX_ZP_HI+$00
        READ8 MBX_ZP_HI+$08
        READ8 MBX_ZP_HI+$10
        READ8 MBX_ZP_HI+$18
        READ8 MBX_ZP_HI+$20
        READ8 MBX_ZP_HI+$28
        READ8 MBX_ZP_HI+$30
        READ8 MBX_ZP_HI+$38

        lda <MBX_ZP_LO+MBX_MAGIC
        cmp #PF_MAGIC_CODE
        beq .valid
        lda #0
        sta <MBX_ZP_LO+MBX_FLAGS
        sta <MBX_ZP_LO+MBX_CMD
.valid:
        lda <MBX_ZP_LO+MBX_FLAGS
        and #MBX_FLAG_ATTR_VALID
        bne .attr
        jmp .no_attr
.attr:
        lda #$23
        sta $2006
        lda #$C0
        sta $2006
        WRITE8 MBX_ZP_HI+$00
        WRITE8 MBX_ZP_HI+$08
        WRITE8 MBX_ZP_HI+$10
        WRITE8 MBX_ZP_HI+$18
        WRITE8 MBX_ZP_HI+$20
        WRITE8 MBX_ZP_HI+$28
        WRITE8 MBX_ZP_HI+$30
        WRITE8 MBX_ZP_HI+$38
.no_attr:
        lda <MBX_ZP_LO+MBX_FLAGS
        and #MBX_FLAG_PAL_VALID
        beq .no_pal
        lda #$3F
        sta $2006
        lda #0
        sta $2006
        WRITE8 MBX_ZP_LO+MBX_PAL
        WRITE8 MBX_ZP_LO+MBX_PAL+8
.no_pal:
        lda #$08
        sta $2006
        lda #0
        sta $2006
        lda #FP_COM_KEY
        sta $2007
        lda <KEY_NEW
        sta $2007
        lda #0
        sta $2007

        ; The tutorial ends its one-byte heartbeat at $0801. Our three
        ; writes leave v=$0803; $2005 only updates t, so the first pre-render
        ; fetches would skip two more tiles (four stream reads). Restore v
        ; without a $2007 access, preserving the calibrated 66/64 cadence.
        lda #$08
        sta $2006
        lda #$01
        sta $2006
        lda <FLG_2000
        sta $2000
        lda <FLG_2001
        sta $2001
        lda #0
        sta $2005
        sta $2005

        lda <MBX_ZP_LO+MBX_FLAGS
        and #MBX_FLAG_APU_VALID
        beq .apu_end
        ldx #0
.apu_loop:
        ldy <MBX_ZP_LO+MBX_APU,x
        bmi .apu_end
        inx
        lda <MBX_ZP_LO+MBX_APU,x
        inx
        sta $4000,y
        cpx #MBX_APU_LEN
        bne .apu_loop
.apu_end:
        ldy <NMI_SVY
        ldx <NMI_SVX
        lda #0
        sta <NMI_FLG
        lda <NMI_SVA
NMI_RTI:
        rti

        .bank 3
        org $ED00
        jmp NMI
        org $EE80
        rti
        org $EF00
        jmp UR_MAIN_SETUP
        jmp UR_MAIN_LOOP
        org $EFF0
        .include "../version.inc"
        org $EFFF
        db 0
        org $F000
        .incbin "../../external/fc-pico/tutorial_project/BOOTROM/bootrom_fixr.bin"
