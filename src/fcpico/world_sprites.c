/* SPDX-License-Identifier: GPL-2.0-or-later */
/*
 * Native NES sprites for admitted enemies and pickups (FC PICO plan 14).
 *
 * R_ProjectSprite hands eligible actors here instead of inserting them into the
 * PD column lists. After BSP traversal they are ranked (live monsters, then
 * pickups, then corpses; nearest first) and admitted whole while the frame's
 * tile, OAM and scanline budgets allow. Rejected actors are drawn exactly as
 * before. Admitted actors never clip geometry, so the background behind them
 * stays intact; pd_end_frame later rasterizes them against the resolved PD
 * columns, hiding every pixel where nearer geometry, a nearer streamed actor
 * or the held weapon is in front.
 */
#include "world_sprites.h"

#include <stdlib.h>
#include <string.h>

#include "doom/doomstat.h"
#include "doom/f_wipe.h"
#include "doom/p_mobj.h"
#include "doom/r_local.h"
#include "doom/r_things.h"

#if PICO_ON_DEVICE
#include "pico/time.h"
#define WORLD_NOW() time_us_32()
#else
#define WORLD_NOW() 0u
#endif

#define MAX_CANDIDATES 32

extern uint8_t inhelpscreens;  /* m_menu.c */

typedef struct {
    vissprite_t vis;
    fcworld_projection_t projection;
    fcworld_box_t box;
    uint32_t depth;
    uint8_t rank;
} candidate_t;

static fcworld_atlas_t atlas;
static bool atlas_ok, link_ready, frame_enabled, raster_ready;
static candidate_t candidates[MAX_CANDIDATES];
static uint8_t candidate_count;
static uint8_t admitted[FCWORLD_MAX_ACTORS];
static uint8_t admitted_count;
static fcworld_raster_t raster;
static fcpico_world_stats_t stats;
/* The console's sprite palettes 0/2 after the last submitted generation and
 * whether that generation uses them; a new plan may only replace unused ones. */
static fcworld_palette_state_t shown_state = {FCWORLD_MENU_PALETTES_INIT, 0};

bool fcpico_world_init(const uint8_t *data, uint32_t size, uint32_t identity) {
    atlas_ok = fcworld_atlas_open(&atlas, data, size) && atlas.identity == identity &&
               atlas.sprite_count == NUMSPRITES;
    return atlas_ok;
}

void fcpico_world_set_link(bool ready) { link_ready = ready; }
const fcworld_atlas_t *fcpico_world_atlas(void) { return atlas_ok ? &atlas : NULL; }
const fcpico_world_stats_t *fcpico_world_stats(void) { return &stats; }

void fcpico_world_frame_begin(void) {
    stats.frames++;
    candidate_count = 0;
    admitted_count = 0;
    raster_ready = false;
    fcworld_raster_reset(&raster);
    frame_enabled = atlas_ok && link_ready && gamestate == GS_LEVEL &&
        gamestate == wipegamestate && !wipestate && !menuactive && !automapactive &&
        !inhelpscreens;
    if (frame_enabled) stats.enabled_frames++;
}

bool fcpico_world_defer(const vissprite_t *vis, int sprite, int frame, int rotation,
                        int lump, bool flip, uint32_t flags) {
    if (!frame_enabled) return false;
    if (flags & (MF_SHADOW | MF_TRANSLATION)) {
        stats.streamed_effect++;
        return false;
    }
    fcworld_projection_t p;
    bool atlas_flip;
    if (!fcworld_atlas_lookup(&atlas, sprite, frame & FF_FRAMEMASK, rotation, &atlas_flip,
                              &p.patch)) {
        stats.streamed_lookup++;  /* family outside the scoped atlas */
        return false;
    }
    if (atlas_flip != flip || (sprite_width(lump) >> FRACBITS) != p.patch.width ||
        (sprite_offset(lump) >> FRACBITS) != p.patch.left ||
        (sprite_topoffset(lump) >> FRACBITS) != p.patch.top) {
        stats.streamed_mismatch++;
        return false;
    }
    p.x1 = (int16_t)vis->x1;
    p.x2 = (int16_t)vis->x2;
    p.startfrac = vis->startfrac;
    p.xiscale = vis->xiscale;
    p.texturemid = vis->texturemid;
    p.iscale = (uint32_t)abs(vis->xiscale) >> detailshift;
    p.centery = (int16_t)centery;
    p.view_x = (int16_t)viewwindowx;
    p.view_y = (int16_t)viewwindowy;
    p.view_width = (int16_t)viewwidth;
    p.view_height = (int16_t)viewheight;
    fcworld_box_t box;
    if (!fcworld_bounds(&p, &box) || fcworld_box_tiles(&box) > FCWORLD_MAX_TILES ||
        candidate_count == MAX_CANDIDATES) {
        stats.streamed_budget++;
        return false;
    }
    candidate_t *c = &candidates[candidate_count++];
    c->vis = *vis;
    c->projection = p;
    c->box = box;
    c->depth = pd_world_sprite_depth((uint32_t)vis->scale);
    c->rank = (flags & MF_CORPSE) ? 2 : (flags & MF_COUNTKILL) ? 0 : (flags & MF_SPECIAL) ? 1 : 2;
    stats.candidates++;
    return true;
}

static int compare_candidates(const void *a, const void *b) {
    const candidate_t *x = a, *y = b;
    if (x->rank != y->rank) return x->rank - y->rank;
    if (x->depth != y->depth) return x->depth < y->depth ? -1 : 1;
    return x->box.x - y->box.x;
}

void fcpico_world_admit(void) {
    if (candidate_count == 0) return;
    qsort(candidates, candidate_count, sizeof candidates[0], compare_candidates);
    uint8_t lines[240];
    memset(lines, 0, sizeof lines);
    unsigned tiles = 0;
    for (unsigned i = 0; i < candidate_count; ++i) {
        const fcworld_box_t *box = &candidates[i].box;
        unsigned columns = (box->w + 7u) / 8u, rows = (box->h + 7u) / 8u;
        bool fits = admitted_count < FCWORLD_MAX_ACTORS &&
            tiles + columns * rows <= FCWORLD_MAX_TILES &&
            tiles + columns * rows <= WORLD_OAM_ENTRIES;
        for (int line = box->y; fits && line < box->y + (int)rows * 8 && line < 240; ++line) {
            fits = lines[line] + columns <= FCWORLD_MAX_LINE_SPRITES;
        }
        if (!fits) {
            stats.streamed_budget++;
            R_DrawSpriteEarly(&candidates[i].vis);
            continue;
        }
        for (int line = box->y; line < box->y + (int)rows * 8 && line < 240; ++line) {
            lines[line] = (uint8_t)(lines[line] + columns);
        }
        tiles += columns * rows;
        admitted[admitted_count++] = (uint8_t)i;
        stats.admitted++;
    }
}

static bool hidden(void *context, int x, int y) {
    const candidate_t *c = context;
    return pd_world_front_depth(x, y) < c->depth;
}

void fcpico_world_resolve(void) {
    if (!frame_enabled) return;
    uint32_t start = WORLD_NOW();
    /* Nearer actors take lower OAM indices so they win sprite priority. */
    for (unsigned i = 1; i < admitted_count; ++i) {
        uint8_t v = admitted[i];
        unsigned j = i;
        while (j > 0 && candidates[admitted[j - 1]].depth > candidates[v].depth) {
            admitted[j] = admitted[j - 1];
            --j;
        }
        admitted[j] = v;
    }
    for (unsigned i = 0; i < admitted_count; ++i) {
        candidate_t *c = &candidates[admitted[i]];
        if (!fcworld_raster_actor(&raster, &c->projection, &c->box, hidden, c)) {
            stats.streamed_overflow++;
        }
    }
    raster_ready = true;
    uint32_t elapsed = WORLD_NOW() - start;
    stats.resolve_us_total += elapsed;
    if (elapsed > stats.resolve_us_max) stats.resolve_us_max = elapsed;
}

bool fcpico_world_take(fcworld_frame_t *frame) {
    bool used = frame_enabled && raster_ready;
    raster_ready = false;
    if (!used) return false;
    uint32_t start = WORLD_NOW();
    bool built = fcworld_frame_build(frame, &raster, &atlas, &shown_state);
    uint32_t elapsed = WORLD_NOW() - start;
    stats.build_us_total += elapsed;
    if (elapsed > stats.build_us_max) stats.build_us_max = elapsed;
    stats.timed_frames++;
    if (!built) stats.build_failures++;  /* admitted actors stay native-empty, never doubled */
    return true;
}

#include "world_atlas_identity.h"
#if PICO_ON_DEVICE
#include "flash_layout.h"
#define ATLAS_DATA ((const uint8_t *)FLASH_WORLD_ADDR)
#else
extern const unsigned char fcpico_world_atlas_bytes[];
#define ATLAS_DATA ((const uint8_t *)fcpico_world_atlas_bytes)
#endif

bool fcpico_world_prepare(fcworld_frame_t *frame, uint8_t shown_gen) {
    if (fcpico_world_take(frame)) return true;
    /* Leaving gameplay clears world sprites with their own palettes first;
     * menu palettes return only once no world sprite can show them. */
    static const uint8_t menu[MBX_WORLD_PAL_LEN] = FCWORLD_MENU_PALETTES_INIT;
    fcworld_frame_clear(frame, shown_gen != 0 ? shown_state.palettes : menu);
    return false;
}

void fcpico_world_note_submitted(const fcworld_frame_t *frame) {
    fcworld_frame_state(frame, &shown_state);
}

void fcpico_world_reset_console(void) {
    static const uint8_t menu[MBX_WORLD_PAL_LEN] = FCWORLD_MENU_PALETTES_INIT;
    memcpy(shown_state.palettes, menu, sizeof menu);
    shown_state.busy = 0;
}

bool fcpico_world_init_default(void) {
    return fcpico_world_init(ATLAS_DATA, FCWORLD_ATLAS_BYTES, FCWORLD_ATLAS_IDENTITY);
}
