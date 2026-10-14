/*
 * fcbus_protocol.h -- FC PICO <-> Famicom wire protocol constants.
 *
 * SPDX-License-Identifier: BSD-3-Clause
 *
 * THE SINGLE SOURCE OF TRUTH.  tools/gen_protocol.py renders
 * this file into bootrom/gen/protocol.inc (nesasm) and tools/fcpico/protocol.py.
 * Every line of the form
 *     #define NAME  <integer literal or expression of earlier NAMEs>   // comment
 * is exported.  Keep expressions to + - * / ( ) << >> and decimal/hex literals.
 * Names listed in the ASM_ALIAS table of the generator get a second nesasm name
 * (the tutorial's boot ROM spells some constants differently from its firmware).
 *
 * v1 values are those of tutorial_project/tuto1_hw/sys/rp_system.h and
 * tutorial_project/BOOTROM/SysPico.asm and MUST NEVER CHANGE (tests/protocol
 * asserts equality against those files).  v2 only adds.
 */
#ifndef FCBUS_PROTOCOL_H
#define FCBUS_PROTOCOL_H

/* ------------------------------------------------------------------------ */
/* @section mailbox-v1   cartridge -> console, on the tail of the frame stream */
/* ------------------------------------------------------------------------ */
#define PF_MAGIC_NO          0xFC   // mailbox[1] validity sentinel (asm: PF_MAGIC_CODE)
#define PF_COM_NONE          0x00   // end of command list
#define PF_COM_DMOD          0x01   // blank display, enter bulk data mode
#define PF_COM_FDIN          0x02   // palette fade in
#define PF_COM_FDOT          0x03   // palette fade out
#define PF_COM_SE            0x80   // 0x80|n play sound effect n (disabled in tutorial ROM)
#define PF_COM_BGM           0xA0   // 0xA0|n play music n (disabled in tutorial ROM)
#define PF_COM_VRAM          0xC0   // 0xC0|adrH, adrL, data: poke one VRAM byte

#define PF_DAT_VRAM          0x80   // bulk data mode: VRAM block
#define PF_DAT_RAM           0x81   // bulk data mode: CPU RAM block
#define PF_DAT_STEP          0x82   // bulk data mode: leave and switch console step

#define FC_COM_BUF_SIZE16    16     // bytes of the mailbox the v1 command executor parses (2..15)
#define FC_COM_BUF_SIZE_V1   64     // v1 mailbox length appended to every frame
#define PICO_SNDREG          0x10   // offset of the APU (reg,value) pair area in the mailbox
#define PICO_APU_BUF_SIZE    0x40   // depth of the firmware's APU write ring (v1 firmware)
#define APU_PAIRS_MAX_V1     23     // pairs that fit: (64 - 16 - 2) / 2, terminator 0xFF
#define APU_PAIRS_MAX_NMI_V1 24     // pairs the tutorial NMI replay loop will apply (0x30 bytes)

/* ------------------------------------------------------------------------ */
/* @section opcodes-v1   console -> cartridge, single bytes written to $2007  */
/* ------------------------------------------------------------------------ */
#define FP_COM_ACK           0x0F   // unused on both sides
#define FP_COM_NAK           0x1F   // unused on both sides
#define FP_COM_VER           0x2F   // request the 14-byte boot-ROM build stamp
#define FP_COM_ROM           0x3F   // + page: request 256 bytes of the boot ROM image
#define FP_COM_LOG           0xBF   // + up to 7 bytes: print on the cartridge serial console
#define FP_COM_DRQ           0xCF   // bulk data mode: request next block header
#define FP_COM_DLD           0xDF   // + page: bulk data mode: request one 256-byte page
#define FP_COM_RST           0xEF   // restart the cartridge firmware
#define FP_COM_INI           0xFF   // + stage: initialise the cartridge
/* Any byte not listed above (and, in v2, not FP_COM_KEY/HELLO) is a v1 controller byte. */

/* ------------------------------------------------------------------------ */
/* @section keys          controller byte bit layout (both directions)       */
/* ------------------------------------------------------------------------ */
#define KEY_A                0x80
#define KEY_B                0x40
#define KEY_SEL              0x20
#define KEY_RUN              0x10   // Start
#define KEY_UP               0x08
#define KEY_DOWN             0x04
#define KEY_LEFT             0x02
#define KEY_RIGHT            0x01

/* ------------------------------------------------------------------------ */
/* @section stream        frame stream geometry (empirical, from the tutorial) */
/* ------------------------------------------------------------------------ */
#define PPU_PICTURE_COUNT    15426  // 66 pre-render + 240*64 visible qualifying reads
#define PPU_COUNTER_REPORT_BIAS 1   // fcppu_rna reports the zero-based index of the last read
#define PPU_COUNT_WINDOW     2      // +/- reads tolerated before the DMA is stopped
#define PPU_COUNT_VAL_V1     (PPU_PICTURE_COUNT + FC_COM_BUF_SIZE_V1)   // 15490
#define VRAM_LINE_WORDS      34     // historical convVram() batch size; not the selected-read stride
#define VRAM_HEAD_WORDS      31     // word index of line 0, tile 0
#define VRAM_LINES           240
#define VRAM_TILE_COLS       32     // selected 16-bit words advanced per visible scanline
#define VRAM_BUF_BYTES_V1    (36 * 2 * 240 + FC_COM_BUF_SIZE_V1)        // 17344, DMA length in bytes
#define VRAM_MAILBOX_OFF_V1  (PPU_COUNT_VAL_V1 - FC_COM_BUF_SIZE_V1)    // 15426: mailbox byte offset in the buffer
#define FCBUS_VER_SYNC_W0    0x21212121   // "!!!!" pushed before the version stamp
#define FCBUS_VER_SYNC_W1    0x43462321   // "!#FC" little-endian; the fix bank syncs on 'C'
#define FCBUS_ROM_STAMP_OFF  0x6FF0       // stamp offset within the 32 KB PRG image ($EFF0)
#define FCBUS_ROM_STAMP_LEN  14           // bytes compared by CHK_ROMVER (16 streamed)
#define FCBUS_ROM_PRG_BYTES  0x8000
#define FCBUS_ROM_INES_HDR   16
#define FCBUS_DRQ_MAGIC_BASE 0xFC00       // drq_ret(): word0 = base + com + (adr << 16), word1 = size

/* ------------------------------------------------------------------------ */
/* @section v2            Doom boot ROM protocol (superset)                  */
/* ------------------------------------------------------------------------ */
#define FCBUS_PROTOCOL_V2    2
#define FP_COM_KEY           0x4F   // + pad1, pad2: controller packet = the v2 heartbeat
#define FP_COM_HELLO         0x5F   // + version: sent once after FP_COM_INI by a v2 boot ROM
#define FC_COM_BUF_SIZE_V2   128    // v2 mailbox length
#define PPU_COUNT_VAL_V2     (PPU_PICTURE_COUNT + FC_COM_BUF_SIZE_V2)   // 15554
#define VRAM_BUF_BYTES_V2    (36 * 2 * 240 + FC_COM_BUF_SIZE_V2)        // 17408
#define VRAM_MAILBOX_OFF_V2  (PPU_COUNT_VAL_V2 - FC_COM_BUF_SIZE_V2)    // 15426 (same tail position)

#define MBX_FLAGS            0      // byte 0: flags (v1: unused)
#define MBX_MAGIC            1      // byte 1: PF_MAGIC_NO
#define MBX_CMD              2      // bytes 2..15: command list, PF_COM_NONE terminated
#define MBX_CMD_LEN          14
#define MBX_APU              16     // bytes 16..47: (reg,value) pairs, 0xFF terminated
#define MBX_APU_LEN          32
#define APU_PAIRS_MAX_V2     15     // (32 - 2) / 2 pairs leave room for the terminator
#define MBX_PAL              48     // bytes 48..63: BG palette $3F00-$3F0F
#define MBX_PAL_LEN          16
#define MBX_ATTR             64     // bytes 64..127: attribute table $23C0-$23FF
#define MBX_ATTR_LEN         64

#define MBX_FLAG_ATTR_VALID  0x01
#define MBX_FLAG_PAL_VALID   0x02
#define MBX_FLAG_APU_VALID   0x04
#define MBX_FLAG_V2          0x80

#define MBX_ZP_LO            0x20   // console zero page holding mailbox bytes 0..63
#define MBX_ZP_HI            0xC0   // console zero page holding mailbox bytes 64..127

#define NMI_CRITICAL_CYCLES_MAX 1900 // budget for the vblank-critical part of the v2 NMI
#define NMI_TOTAL_CYCLES_MAX    2200 // budget for the whole v2 NMI (NTSC vblank = 2273)

/* ------------------------------------------------------------------------ */
/* @section v3            Versioned compact native HUD snapshot              */
/* ------------------------------------------------------------------------ */
#define FCBUS_PROTOCOL_V3    3
#define FC_COM_BUF_SIZE_V3   144    // v2 mailbox plus one 16-byte HUD snapshot
#define PPU_COUNT_VAL_V3     (PPU_PICTURE_COUNT + FC_COM_BUF_SIZE_V3)  // 15570
#define VRAM_BUF_BYTES_V3    (36 * 2 * 240 + FC_COM_BUF_SIZE_V3)       // 17424
#define VRAM_MAILBOX_OFF_V3  (PPU_COUNT_VAL_V3 - FC_COM_BUF_SIZE_V3)  // 15426
#define MBX_UI              128    // bytes 128..143: immutable HUD snapshot
#define MBX_UI_LEN          16
#define MBX_FLAG_UI_VALID   0x08   // snapshot has a complete generation
#define MBX_FLAG_V3         0x40   // v3 mailbox signature alongside MBX_FLAG_V2

/* ------------------------------------------------------------------------ */
/* @section v4            Fixed native BG text row, compacted picture stream */
/* ------------------------------------------------------------------------ */
#define FCBUS_PROTOCOL_V4    4
#define NATIVE_TEXT_ROW      29    // BG tile row 29, NES scanlines 232..239
#define NATIVE_TEXT_COL      2     // first native tile, x=16
#define NATIVE_TEXT_TILES    28    // columns 2..29, x=16..239
#define NATIVE_TEXT_BYTES    (NATIVE_TEXT_TILES * 8 * 2) // skipped CS1 reads
#define PPU_PICTURE_COUNT_V4 (PPU_PICTURE_COUNT - NATIVE_TEXT_BYTES) // 14978
#define FC_COM_BUF_SIZE_V4   FC_COM_BUF_SIZE_V3
#define PPU_COUNT_VAL_V4     (PPU_PICTURE_COUNT_V4 + FC_COM_BUF_SIZE_V4) // 15122
#define VRAM_BUF_BYTES_V4    VRAM_BUF_BYTES_V3
#define VRAM_MAILBOX_OFF_V4  PPU_PICTURE_COUNT_V4
#define MBX_FLAG_V4         0x20   // fixed native text geometry

/* ------------------------------------------------------------------------ */
/* @section v5            World sprite packets over the unchanged v4 stream   */
/* ------------------------------------------------------------------------ */
/* One acknowledged packet per heartbeat replaces the command list. CHR packets
 * reuse the attribute bytes as four 16-byte tiles; OAM packets stage entries in
 * console RAM and may commit a generation. Heartbeat pad2 reports the generation
 * in OAM (high nibble) and the last applied packet sequence (low nibble). */
#define FCBUS_PROTOCOL_V5    5
#define FC_COM_BUF_SIZE_V5   FC_COM_BUF_SIZE_V4
#define PPU_COUNT_VAL_V5     PPU_COUNT_VAL_V4  // 15122: no stream geometry change
#define VRAM_MAILBOX_OFF_V5  VRAM_MAILBOX_OFF_V4
#define MBX_FLAG_WORLD      0x10   // command area holds one world packet; excludes ATTR/PAL
#define MBX_WORLD_OP        2      // WORLD_OP_CHR or WORLD_OP_OAM (MBX_CMD)
#define MBX_WORLD_SEQ       3      // 1..15, echoed in heartbeat pad2 once applied
#define MBX_WORLD_GEN       4      // generation the packet belongs to, 0..15
#define MBX_WORLD_ARG       5      // CHR: 4 x (adrH, adrL); OAM: entry offset | commit bit
#define MBX_WORLD_PAL       6      // OAM commit: sprite palettes 0 and 2, colours 1..3
#define MBX_WORLD_PAL_LEN   6
#define MBX_WORLD_PAYLOAD   MBX_ATTR
#define WORLD_OP_CHR        0x57
#define WORLD_OP_OAM        0x58
#define WORLD_CHR_TILES     4      // tiles per CHR packet (MBX_ATTR_LEN / 16)
#define WORLD_OAM_PACKET    16     // OAM entries per packet (MBX_ATTR_LEN / 4)
#define WORLD_OAM_FIRST     35     // first world OAM slot; 0..34 remain native status
#define WORLD_OAM_ENTRIES   29     // slots 35..63, shared with gameplay-hidden menu text
#define WORLD_OAM_COMMIT    0x80   // MBX_WORLD_ARG: publish staged entries with this packet
#define WORLD_OAM_NONE      0x7F   // MBX_WORLD_ARG offset: no payload (generation 0 clears)
#define WORLD_GEN_MASK      0x0F
#define WORLD_SEQ_MASK      0x0F
#define WORLD_HB_GEN_SHIFT  4

#endif /* FCBUS_PROTOCOL_H */
