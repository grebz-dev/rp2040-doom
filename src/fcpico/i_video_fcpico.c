/* SPDX-License-Identifier: GPL-2.0-or-later */
/* FC PICO 8-bit scanline composition. Bus publication follows. */

#include "config.h"
#include "doomtype.h"
#include "i_video.h"
#include "picodoom.h"
#include "fcpico_video_sink.h"
#include "pico/sem.h"
#include "pico/multicore.h"
#include "v_video.h"
#include "w_wad.h"
#include "doom/r_data.h"
#include "doom/f_wipe.h"

#include <assert.h>
#include <stdint.h>
#include <string.h>

boolean screenvisible = true;
boolean screensaver_mode = false;
isb_int8_t usegamma = 0;
unsigned int joywait = 0;
pixel_t *I_VideoBuffer;
volatile uint8_t interp_in_use;
volatile uint8_t wipe_min;

uint8_t __attribute__((aligned(4))) frame_buffer[2][SCREENWIDTH * MAIN_VIEWHEIGHT];
semaphore_t render_frame_ready, display_frame_freed;
static uint8_t text_screen[80 * 25 * 2];
uint8_t *text_screen_data = text_screen;

uint8_t next_video_type;
uint8_t next_frame_index;
uint8_t next_overlay_index;
uint8_t *next_video_scroll;
int16_t *wipe_yoffsets_raw;
uint8_t *wipe_yoffsets;
uint32_t *wipe_linelookup;

static int palette_num;
static uint8_t line8[SCREENWIDTH];
static uint16_t overlay_offsets[VPATCHLIST_COUNT_OVERLAY];

#if PICO_ON_DEVICE
static void render_core1(void)
{
    for (;;) pd_core1_loop();
}
#endif

/* The host runner supplies these; the device converter will replace the weak sink. */
__attribute__((weak)) void fcvideo_line_sink(int y, const uint8_t *line320)
{
    (void)y;
    (void)line320;
}
__attribute__((weak)) void fcvideo_frame_end(int palette, int video_type)
{
    (void)palette;
    (void)video_type;
}

static const uint8_t *base_row(int y, int video_type)
{
    if (video_type == VIDEO_TYPE_DOUBLE) {
        return y < MAIN_VIEWHEIGHT ? frame_buffer[next_frame_index] + y * SCREENWIDTH : NULL;
    }
    if (video_type == VIDEO_TYPE_SINGLE || video_type == VIDEO_TYPE_SAVING ||
        video_type == VIDEO_TYPE_WIPE) {
        if (y < MAIN_VIEWHEIGHT) {
            return frame_buffer[next_frame_index] + y * SCREENWIDTH;
        }
        return frame_buffer[next_frame_index ^ 1] + (y - 32) * SCREENWIDTH;
    }
    return NULL;
}

static void compose_base_row(int y, int video_type)
{
    const uint8_t *src = base_row(y, video_type);
    if (src == NULL) {
        memset(line8, 0, sizeof(line8));
        return;
    }
    memcpy(line8, src, sizeof(line8));
    if (video_type == VIDEO_TYPE_WIPE && wipe_yoffsets != NULL && wipe_linelookup != NULL) {
        for (int x = 0; x < SCREENWIDTH; ++x) {
            int rel = y - wipe_yoffsets[x];
            if (rel >= 0 && rel < SCREENHEIGHT) {
#if PICO_ON_DEVICE
                uintptr_t old_addr = wipe_linelookup[rel];
#else
                uintptr_t old_addr = (uintptr_t)&frame_buffer[0][0] + wipe_linelookup[rel];
#endif
                uintptr_t start = (uintptr_t)&frame_buffer[0][0];
                if (old_addr >= start && old_addr <= start + sizeof(frame_buffer) - 1 - x) {
                    line8[x] = ((const uint8_t *)old_addr)[x];
                }
            }
        }
    }
#if !DEMO1_ONLY
    if (next_video_scroll != NULL && video_type == VIDEO_TYPE_SINGLE) {
        memmove(line8 + 1, line8, SCREENWIDTH - 1);
        line8[0] = next_video_scroll[y];
    }
#endif
}

/* Consume one packed vpatch row, retaining the byte offset for the next row. */
static uint16_t draw_patch_row(uint8_t *dest, const patch_t *patch,
                               const vpatchlist_t *entry, uint16_t offset)
{
    const uint8_t *data0 = vpatch_data(patch);
    const uint8_t *data = data0 + offset;
    const uint8_t *pal = vpatch_palette(patch);
    unsigned x = entry->entry.x;
    unsigned width = vpatch_width(patch);
    unsigned repeat = entry->entry.repeat;
    unsigned pos = 0;
    unsigned type = vpatch_type(patch);
    if (vpatch_has_shared_palette(patch)) {
        unsigned shared = vpatch_shared_palette(patch);
        assert(shared < NUM_SHARED_PALETTES);
        pal = vpatch_palette(resolve_vpatch_handle(vpatch_for_shared_palette[shared]));
    }
    if (width == 0 || x >= SCREENWIDTH || width * (repeat + 1) > SCREENWIDTH - x) {
        return offset;
    }
    switch (type) {
    case vp4_solid:
    case vp4_alpha:
        while (pos < width) {
            uint8_t packed = *data++;
            uint8_t low = packed & 15u;
            uint8_t high = packed >> 4;
            if (type == vp4_solid || low != 0) dest[x + pos] = pal[low];
            ++pos;
            if (pos < width) {
                if (type == vp4_solid || high != 0) dest[x + pos] = pal[high];
                ++pos;
            }
        }
        break;
    case vp4_runs:
    case vp6_runs:
    case vp8_runs:
        while (pos < width) {
            uint8_t gap = *data++;
            unsigned len;
            if (gap == 0xff) break;
            pos += gap;
            len = *data++;
            if (type == vp4_runs) {
                for (unsigned i = 0; i < len; i += 2) {
                    uint8_t packed = *data++;
                    if (pos < width) dest[x + pos++] = pal[packed & 15u];
                    if (i + 1 < len && pos < width) dest[x + pos++] = pal[packed >> 4];
                }
            } else if (type == vp6_runs) {
                uint32_t packed = 0;
                unsigned available = 0;
                for (unsigned i = 0; i < len; ++i) {
                    while (available < 6) {
                        packed |= (uint32_t)*data++ << available;
                        available += 8;
                    }
                    if (pos < width) dest[x + pos++] = pal[packed & 63u];
                    packed >>= 6;
                    available -= 6;
                }
            } else {
                for (unsigned i = 0; i < len && pos < width; ++i) {
                    dest[x + pos++] = pal[*data++];
                }
            }
        }
        break;
    case vp_border:
        dest[x] = *data++;
        if (width > 1) memset(dest + x + 1, *data, width - 2);
        ++data;
        dest[x + width - 1] = *data++;
        break;
    default:
        assert(false);
        break;
    }
    if (repeat != 0) {
        if (entry->entry.patch_handle == VPATCH_M_THERMM) --width;
        for (unsigned i = 0; i < repeat * width; ++i) {
            dest[x + width + i] = dest[x + i];
        }
    }
    return (uint16_t)(data - data0);
}

static void advance_wipe(void)
{
    int minimum = SCREENHEIGHT;
    int regular = next_overlay_index;
    if (next_video_type != VIDEO_TYPE_WIPE || wipe_yoffsets == NULL ||
        wipe_yoffsets_raw == NULL || wipe_min > SCREENHEIGHT) return;
    for (int x = 0; x < SCREENWIDTH; ++x) {
        int value = wipe_yoffsets_raw[x];
        if (value < 0) {
            if (regular) wipe_yoffsets_raw[x] = ++value;
            value = 0;
        } else {
            int step = value < 16 ? (1 + value + regular) / 2 : 4;
            value += step;
            if (value > SCREENHEIGHT) value = SCREENHEIGHT;
            wipe_yoffsets_raw[x] = value;
        }
        wipe_yoffsets[x] = (uint8_t)value;
        if (value < minimum) minimum = value;
    }
    wipe_min = (uint8_t)minimum;
}

static void compose_frame(void)
{
    int video_type = next_video_type;
    const vpatchlist_t *overlays = vpatchlists->overlays[next_overlay_index];
    memset(overlay_offsets, 0, sizeof(overlay_offsets));
    advance_wipe();
    for (int y = 0; y < SCREENHEIGHT; ++y) {
        compose_base_row(y, video_type);
        if (video_type >= FIRST_VIDEO_TYPE_WITH_OVERLAYS) {
            for (unsigned i = 1; i < overlays->header.size; ++i) {
                const patch_t *patch;
                int patch_y = overlays[i].entry.y;
                if (y < patch_y) continue;
                patch = resolve_vpatch_handle(overlays[i].entry.patch_handle);
                if (y - patch_y >= vpatch_height(patch)) continue;
                overlay_offsets[i] = draw_patch_row(line8, patch, &overlays[i],
                                                    overlay_offsets[i]);
            }
        }
        fcvideo_line_sink(y, line8);
    }
    fcvideo_frame_end(palette_num, video_type);
}

void I_InitGraphics(void)
{
    sem_init(&render_frame_ready, 0, 2);
    sem_init(&display_frame_freed, 1, 2);
    pd_init();
#if PICO_ON_DEVICE
    multicore_launch_core1(render_core1);
#endif
}

void I_ShutdownGraphics(void) {}
void I_SetPaletteNum(int num) { palette_num = num; }
int I_GetPaletteIndex(int r, int g, int b)
{
    (void)r; (void)g; (void)b;
    return palette_num;
}
void I_UpdateNoBlit(void) {}
void I_FinishUpdate(void)
{
    if (sem_available(&render_frame_ready)) {
        sem_acquire_blocking(&render_frame_ready);
        compose_frame();
        sem_release(&display_frame_freed);
    }
#if PICO_ON_DEVICE && FCPICO_DIAGNOSTIC_ENGINE_DELAY
    fcpico_video_device_diag_tick();
#endif
}
void I_ReadScreen(pixel_t *scr)
{
    if (scr != NULL) {
        memset(scr, 0, SCREENWIDTH * SCREENHEIGHT * sizeof(pixel_t));
    }
}
void I_BeginRead(void) {}
void I_SetWindowTitle(const char *title) { (void)title; }
void I_CheckIsScreensaver(void) {}
void I_SetGrabMouseCallback(grabmouse_callback_t callback) { (void)callback; }
void I_DisplayFPSDots(boolean dots_on) { (void)dots_on; }
void I_BindVideoVariables(void) {}
void I_InitWindowTitle(void) {}
void I_InitWindowIcon(void) {}
void I_GraphicsCheckCommandLine(void) {}
void I_StartFrame(void) {}
void I_StartTic(void) {}
void I_EnableLoadingDisk(int xoffs, int yoffs) { (void)xoffs; (void)yoffs; }
void I_GetWindowPosition(int *x, int *y, int w, int h)
{
    (void)w; (void)h;
    if (x != NULL) { *x = 0; }
    if (y != NULL) { *y = 0; }
}
