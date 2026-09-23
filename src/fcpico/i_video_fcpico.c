/* SPDX-License-Identifier: GPL-2.0-or-later */
/* FC PICO video skeleton. The 8-bit composition and bus publication follow. */

#include "config.h"
#include "doomtype.h"
#include "i_video.h"
#include "picodoom.h"
#include "pico/sem.h"

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

void I_InitGraphics(void)
{
    sem_init(&render_frame_ready, 0, 2);
    sem_init(&display_frame_freed, 1, 2);
    pd_init();
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
        sem_release(&display_frame_freed);
    }
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
