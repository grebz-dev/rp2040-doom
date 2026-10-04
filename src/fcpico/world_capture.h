/* SPDX-License-Identifier: GPL-2.0-or-later */
#ifndef FCPICO_WORLD_CAPTURE_H
#define FCPICO_WORLD_CAPTURE_H
#include <stdint.h>
#ifdef __cplusplus
extern "C" {
#endif
#define FCPICO_WORLD_MAX 128
#define FCPICO_WORLD_WEAPON 255
extern uint8_t fcpico_world_owner;
int fcpico_world_configure(const char *directory, const char *admission);
void fcpico_world_begin(void);
uint8_t fcpico_world_actor(int sprite, int frame, int patch, int flip,
                         int flags, int scale, int light, int x1, int x2,
                         int world_x, int world_y, int world_z);
int fcpico_world_suppressed(uint8_t owner);
void fcpico_world_projected(uint8_t owner, int x, int yl, int yh);
void fcpico_world_visible(uint8_t owner, int x, int yl, int yh);
void fcpico_world_pixels(uint8_t owner, int x, int yl, int count,
                         const uint8_t *pixels, int frac, int step);
void fcpico_world_end(unsigned frame, int video_type, int palette);
#ifdef __cplusplus
}
#endif
#endif
