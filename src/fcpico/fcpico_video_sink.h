/* SPDX-License-Identifier: GPL-2.0-or-later */
#ifndef FCPICO_VIDEO_SINK_H
#define FCPICO_VIDEO_SINK_H

#include <stdint.h>

/* A complete 320x200 indexed frame arrives in ascending scanline order. */
void fcvideo_line_sink(int y, const uint8_t *line320);
void fcvideo_frame_end(int palette_num, int video_type);

#endif
