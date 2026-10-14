/* SPDX-License-Identifier: BSD-3-Clause */
#ifndef FCVIDEO_H
#define FCVIDEO_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include "fcbus_protocol.h"
#include "fcvideo_presets.h"
#include "fcui.h"

#define FCVIDEO_SRC_WIDTH 320
#define FCVIDEO_SRC_HEIGHT 200
#define FCVIDEO_WIDTH (VRAM_TILE_COLS * 8)
#define FCVIDEO_SCALED_HEIGHT 224
#define FCVIDEO_TOP_MARGIN 8
#define FCVIDEO_STATUS_START 192
#define FCVIDEO_FRAME_BYTES (VRAM_LINES * FCVIDEO_WIDTH)
#define FCVIDEO_ERR_BYTES (4 * 256)
#define FCVIDEO_LUT_BYTES (4 * 256 * 16)
#define FCVIDEO_PALETTE_SET_COUNT 14
/* Native HUD cells use this subpalette with pixel values 0..3 as backdrop,
 * grey, white, red; FCVIDEO_PRESET_SHARED_HUD shares it with the world. */
#define FCVIDEO_HUD_PALETTE 2

/* err[p][idx] and lut[p][idx][bayer] are generated from PLAYPAL and the
 * selected NES preset by the host reference. They remain immutable for a
 * game palette; console palette flashes replace only the 16 mailbox bytes. */
typedef struct {
    const uint8_t *err; /* 4 * 256 bytes */
    const uint8_t *lut; /* 4 * 256 * 16 bytes */
    uint8_t palette[MBX_PAL_LEN];
} fcvideo_tables_t;

typedef struct {
    fcvideo_tables_t tables;   /* err/lut are read only by fcvideo_init */
    uint8_t frame[FCVIDEO_FRAME_BYTES + 16]; /* prefetch past line 239 is zero */
    /* Derived by fcvideo_init. planes[p][y & 3][index]: for x & 3 == k, bit
     * 3 - k is the low-plane bit and bit 7 - k the high-plane bit, so four
     * pixels combine with masks only. err_pair[i][index] = err[2i] | err[2i+1] << 16. */
    uint8_t planes[4 * 4 * 256];
    uint32_t err_pair[2][256];
    uint8_t previous_attr[MBX_ATTR_LEN];
    bool have_previous;
    bool native_status;
    fcui_status_t status;
} fcvideo_t;

/* Reads tables->err and tables->lut once; lut may alias video->frame
 * (FCVIDEO_LUT_BYTES of scratch). */
void fcvideo_init(fcvideo_t *video, const fcvideo_tables_t *tables);
size_t fcvideo_sizeof(void);
/* Build immutable palette-0 tables once, outside the frame/ISR path. RGB is
 * the first 768 bytes of PLAYPAL; output buffers are caller-owned. */
void fcvideo_build_tables(const uint8_t playpal_rgb[256 * 3],
                          const fcvideo_preset_t *preset,
                          uint8_t err[FCVIDEO_ERR_BYTES],
                          uint8_t lut[FCVIDEO_LUT_BYTES],
                          uint8_t palette[MBX_PAL_LEN]);
/* Generate the engine's synthetic red/yellow/green flash sets by tinting the
 * preset's NES RGB anchors. Set 0 is the exact untinted preset; callers can
 * select another set with fcvideo_set_palette without rebuilding err/lut. */
void fcvideo_build_palette_sets(const fcvideo_preset_t *preset,
                                uint8_t sets[FCVIDEO_PALETTE_SET_COUNT][MBX_PAL_LEN]);
void fcvideo_set_palette(fcvideo_t *video, const uint8_t palette[MBX_PAL_LEN]);
void fcvideo_set_native_status(fcvideo_t *video, bool enabled);
void fcvideo_set_status_snapshot(fcvideo_t *video, const fcui_status_t *status);
/* Scanline staging avoids a second 320x200 frame buffer on the RP2350.
 * Call begin, then push each source line once, then convert_staged. */
void fcvideo_frame_begin(fcvideo_t *video);
void fcvideo_push_line(fcvideo_t *video, int y,
                       const uint8_t line[FCVIDEO_SRC_WIDTH]);
/* Replace the converted legacy status area with a concrete backing and native
 * pixel text when the v4 status screen is active. Call after push_line. */
void fcvideo_blank_status(fcvideo_t *video);
void fcvideo_convert_staged(fcvideo_t *video,
                            uint8_t stream[VRAM_BUF_BYTES_V2],
                            uint8_t attr[MBX_ATTR_LEN], bool reset_hysteresis);
/* Remove the selected pattern reads for native BG text row 28. Call only after
 * convert_staged when the console negotiated v4. The v2 mailbox is discarded;
 * the bus inserts the current mailbox at VRAM_MAILBOX_OFF_V4 on heartbeat. */
void fcvideo_compact_native_text(uint8_t stream[VRAM_BUF_BYTES_V4]);
/* Replace converted episode names with three centered rows of the permanent
 * NES menu font. Call after v4 compaction, only while episode menu 2 is open. */
void fcvideo_overlay_episode_menu(uint8_t stream[VRAM_BUF_BYTES_V4],
                                  uint8_t attr[MBX_ATTR_LEN],
                                  uint8_t palette[MBX_PAL_LEN]);
/* Writes a full v2 buffer and returns the selected attribute table in attr.
 * Pass reset_hysteresis=true for the first frame after a palette preset change. */
void fcvideo_convert(fcvideo_t *video,
                     const uint8_t source[FCVIDEO_SRC_HEIGHT * FCVIDEO_SRC_WIDTH],
                     uint8_t stream[VRAM_BUF_BYTES_V2],
                     uint8_t attr[MBX_ATTR_LEN], bool reset_hysteresis);

#endif
