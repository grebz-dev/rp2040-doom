/* SPDX-License-Identifier: BSD-3-Clause */
/* Cortex-M33 driver for tools/perf/m33_convert_cost.py: one conversion. */
#include "fcvideo.h"
#include <string.h>
fcvideo_t video;
uint8_t err[FCVIDEO_ERR_BYTES], lut[FCVIDEO_LUT_BYTES];
uint8_t stream[VRAM_BUF_BYTES_V2], attr[MBX_ATTR_LEN];
volatile int native_flag;
uint8_t frame_in[FCVIDEO_FRAME_BYTES + 16], prev_in[MBX_ATTR_LEN];
void __attribute__((noinline, noreturn)) finished(void) {
    for (;;) __asm__ volatile("bkpt #0");
}
void __attribute__((noreturn, used)) entry(void) {
    fcvideo_tables_t t = {err, lut, {0}};
    fcvideo_init(&video, &t);
    memcpy(video.frame, frame_in, sizeof frame_in);
    memcpy(video.previous_attr, prev_in, sizeof prev_in);
    video.native_status = native_flag;
    video.have_previous = 1;
    fcvideo_convert_staged(&video, stream, attr, 0);
    finished();
}
void *memset(void *d, int c, unsigned n) { unsigned char *p = d; while (n--) *p++ = (unsigned char)c; return d; }
void *memcpy(void *d, const void *s, unsigned n) { unsigned char *p = d; const unsigned char *q = s; while (n--) *p++ = *q++; return d; }
