/* SPDX-License-Identifier: BSD-3-Clause */
/* Host reference state for tools/perf/m33_convert_cost.py. */
#include "fcvideo.h"
#include <stdio.h>
static uint8_t err[FCVIDEO_ERR_BYTES], lut[FCVIDEO_LUT_BYTES];
static fcvideo_t video; static uint8_t stream[VRAM_BUF_BYTES_V2], attr[MBX_ATTR_LEN];
int main(int c, char **v) {
    FILE *f = fopen(v[1], "rb"); uint8_t pp[768]; if (fread(pp, 1, 768, f) != 768) return 1; fclose(f);
    fcvideo_tables_t t = {err, lut, {0}};
    fcvideo_build_tables(pp, &fcvideo_presets[FCVIDEO_DEFAULT_PRESET], err, lut, t.palette);
    fcvideo_init(&video, &t);
    static uint8_t raw[64000]; f = fopen(v[2], "rb"); if (fread(raw, 1, 64000, f) != 64000) return 1; fclose(f);
    fcui_status_t st = {.flags = FCUI_FLAG_STATUS_VISIBLE, .health = 87, .ready_weapon = 2, .ammo = {50}};
    fcvideo_set_native_status(&video, 1); fcvideo_set_status_snapshot(&video, &st);
    fcvideo_frame_begin(&video);
    for (int y = 0; y < 200; ++y) fcvideo_push_line(&video, y, raw + y * 320);
    fcvideo_blank_status(&video);
    fcvideo_convert_staged(&video, stream, attr, 1);  /* fills previous_attr */
    f = fopen(v[3], "wb");
    fwrite(err, 1, sizeof err, f); fwrite(lut, 1, sizeof lut, f);
    fwrite(video.frame, 1, sizeof video.frame, f); fwrite(video.previous_attr, 1, MBX_ATTR_LEN, f);
    fwrite(stream, 1, sizeof stream, f); fwrite(attr, 1, sizeof attr, f);
    fclose(f); return 0;
}
