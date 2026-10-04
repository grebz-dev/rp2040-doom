/* SPDX-License-Identifier: GPL-2.0-or-later */
/* Native NES sprites for admitted enemies and pickups (FC PICO plan 14). */
#ifndef FCPICO_WORLD_SPRITES_H
#define FCPICO_WORLD_SPRITES_H

#include <stdbool.h>
#include <stdint.h>

#include "fcworld.h"

#ifdef __cplusplus
extern "C" {
#endif

struct vissprite_s;

typedef struct {
    uint32_t frames, enabled_frames, candidates, admitted;
    uint32_t streamed_budget, streamed_effect, streamed_lookup, streamed_overflow;
    uint32_t streamed_mismatch;  /**< atlas entry disagrees with engine sprite metadata */
    uint32_t build_failures;
    /* Device-only microsecond timings (zero on host): masks/raster in
     * pd_end_frame, and palette planning plus tile building at frame end. */
    uint32_t resolve_us_max, build_us_max;
    uint64_t resolve_us_total, build_us_total;
    uint32_t timed_frames;
} fcpico_world_stats_t;

/** Validate the generated atlas; world sprites stay off when this fails. */
bool fcpico_world_init(const uint8_t *atlas, uint32_t size, uint32_t identity);
/** The build's generated atlas: flash on device, embedded bytes on host. */
bool fcpico_world_init_default(void);
/** The console speaks protocol v5 (set by the video glue). */
void fcpico_world_set_link(bool ready);
/** pd_begin_frame: decide whether this frame may use native actors. */
void fcpico_world_frame_begin(void);
/** R_ProjectSprite: true when the actor is held for admission instead of drawn. */
bool fcpico_world_defer(const struct vissprite_s *vis, int sprite, int frame, int rotation,
                        int lump, bool flip, uint32_t flags);
/** After BSP traversal: admit within budgets, draw everything else as usual. */
void fcpico_world_admit(void);
/** pd_end_frame, before visplanes consume the column lists: rasterize the
 *  admitted actors against resolved PD visibility. */
void fcpico_world_resolve(void);
/** Composed frame end: the native actors suppressed from this picture. Returns
 *  false when the frame did not use native actors (all actors streamed). */
bool fcpico_world_take(fcworld_frame_t *frame);
/** Like fcpico_world_take(), but always fills `frame`: frames without native
 *  actors become an empty generation with the palettes the console needs. */
bool fcpico_world_prepare(fcworld_frame_t *frame, uint8_t shown_gen);
/** After submission: the frame becomes the palette state the next plan must
 *  respect (its palettes stay on screen until the next generation shows). */
void fcpico_world_note_submitted(const fcworld_frame_t *frame);
/** Console restarted: menu palettes, no world sprites. */
void fcpico_world_reset_console(void);
const fcworld_atlas_t *fcpico_world_atlas(void);
const fcpico_world_stats_t *fcpico_world_stats(void);

/* pd_render.cpp */
uint32_t pd_world_front_depth(int x, int y);
uint32_t pd_world_sprite_depth(uint32_t scale);

#ifdef __cplusplus
}
#endif

#endif
