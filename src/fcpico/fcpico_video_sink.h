/* SPDX-License-Identifier: GPL-2.0-or-later */
#ifndef FCPICO_VIDEO_SINK_H
#define FCPICO_VIDEO_SINK_H

#include <stdint.h>

/* Start the cartridge bus before entering the Doom main loop. */
void fcpico_video_device_init(void);
void fcpico_video_device_diag_tick(void);
/* Poll USB serial for the BOOTSEL command on the main core. */
void fcpico_video_device_poll_bootsel(void);

/* A complete 320x200 indexed frame arrives in ascending scanline order. */
void fcvideo_line_sink(int y, const uint8_t *line320);
void fcvideo_frame_end(int palette_num, int video_type);

#endif
