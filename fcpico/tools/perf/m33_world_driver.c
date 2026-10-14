/* SPDX-License-Identifier: BSD-3-Clause */
/* Identical workload on the host and Cortex-M33, using a generated v2 atlas. */
#include "fcworld.h"
#include <string.h>
#ifndef WORLD_BARE_METAL
#include <stdio.h>
#endif

uint8_t atlas_data[1024 * 1024];
uint32_t atlas_size;
uint8_t results[256 * 1024];
uint32_t result_size, cases, failed;
static fcworld_raster_t raster;
static fcworld_frame_t frame, previous;
static fcworld_t world;
static void save(const void *data, unsigned size);

/* The last sprite-sized actor can require every tile to change. The cache
 * also contains the previous displayed generation, which must stay intact. */
static void submission_workload(void) {
    fcworld_reset(&world);
    uint32_t random = 0x1432350u;
    for (unsigned i = 0; i < FCWORLD_POOL_TILES; ++i) {
        world.slots[i].valid = true;
        world.slots[i].shown = i < FCWORLD_MAX_TILES;
        for (unsigned b = 0; b < 16; ++b) {
            random = random * 1664525u + 1013904223u;
            world.slots[i].data[b] = (uint8_t)(random >> 24);
        }
    }
    fcworld_frame_clear(&frame, world.shown_pal);
    frame.tile_count = frame.oam_count = FCWORLD_MAX_TILES;
    for (unsigned t = 0; t < frame.tile_count; ++t) {
        for (unsigned b = 0; b < 16; ++b) {
            random = random * 1664525u + 1013904223u;
            frame.tiles[t][b] = (uint8_t)(random >> 24);
        }
        frame.oam[t][0] = (uint8_t)(39 + (t / 4) * 8);
        frame.oam[t][1] = (uint8_t)t;
        frame.oam[t][3] = (uint8_t)(64 + (t % 4) * 8);
    }
#ifdef FCWORLD_HAS_RESERVATION
    if (!fcworld_reserve(&world)) { failed = 1; return; }
#endif
    uint8_t generation = fcworld_submit_held(&world, &frame);
    if (generation == 0xFF || world.upload_count != FCWORLD_MAX_TILES) failed = 1;
    save(&generation, 1);
    save(world.oam, sizeof world.oam);
    save(&world.upload_count, 1);
    for (unsigned t = 0; t < world.upload_count; ++t)
        save(world.slots[world.upload[t]].data, 16);
}

static void save(const void *data, unsigned size) {
    if (result_size + size > sizeof results) { failed = 1; return; }
    memcpy(results + result_size, data, size);
    result_size += size;
}

static void save_frame(void) {
    save(&frame.tile_count, 1);
    save(&frame.oam_count, 1);
    save(frame.tiles, frame.tile_count * 16);
    save(frame.oam, frame.oam_count * 4);
    save(frame.palettes, sizeof frame.palettes);
    save(&frame.peak_line, 1);
    save(&frame.actor_count, 1);
    for (unsigned a = 0; a < frame.actor_count; ++a) {
        save(&frame.actor[a].id, 4);
        save(&frame.actor[a].ax, 2);
        save(&frame.actor[a].ay, 2);
        save(&frame.actor[a].occluded, 1);
        save(&frame.actor[a].first, 1);
        save(&frame.actor[a].count, 1);
        save(&frame.actor[a].stale, 1);
    }
}

static bool hidden(void *context, int x, int y) {
    (void)context;
    return (x + y * 3) % 17 < 3;
}

static void workload(void) {
    fcworld_atlas_t atlas;
    if (!fcworld_atlas_open(&atlas, atlas_data, atlas_size)) { failed = 1; return; }
    unsigned families = 0;
    for (unsigned sprite = 0; sprite < atlas.sprite_count && families < 12; ++sprite) {
        fcworld_patch_t patch;
        bool flip;
        if (!fcworld_atlas_lookup(&atlas, sprite, 0, 0, &flip, &patch)) continue;
        families++;
        for (unsigned variant = 0; variant < 6; ++variant) {
            fcworld_projection_t p = {0};
            p.patch = patch;
            p.x1 = 80; p.x2 = 240;
            p.iscale = 131072 + (variant % 3) * 65536;
            p.xiscale = variant & 1 ? -(int32_t)p.iscale : (int32_t)p.iscale;
            p.startfrac = variant & 1 ? (patch.width - 1) * 65536 : 0;
            p.texturemid = patch.top * 65536;
            p.centery = 100; p.view_width = 320; p.view_height = 168;
            fcworld_box_t box;
            if (!fcworld_bounds(&p, &box) || fcworld_box_tiles(&box) > FCWORLD_MAX_TILES)
                continue;
            fcworld_raster_reset(&raster);
            if (!fcworld_raster_actor(&raster, &p, &box, variant == 5 ? hidden : NULL, NULL)) {
                failed = 1; return;
            }
            raster.actor[0].id = sprite + 1;
            /* Crowded views commonly contain several actors of one family. */
            p.x1 += 90; p.centery += 55;
            fcworld_box_t second;
            if (fcworld_bounds(&p, &second) &&
                fcworld_box_tiles(&box) + fcworld_box_tiles(&second) <= FCWORLD_MAX_TILES &&
                box.y + box.h + 7 < second.y) {
                if (!fcworld_raster_actor(&raster, &p, &second, NULL, NULL)) { failed = 1; return; }
                raster.actor[1].id = sprite + 100;
            }
            save(&raster.used, 2);
            save(raster.pixels, raster.used);
            fcworld_palette_state_t shown;
            fcworld_frame_state(&previous, &shown);
            if (!fcworld_frame_build(&frame, &raster, &atlas, &shown)) { failed = 1; return; }
            save_frame();
            unsigned held = fcworld_frame_limit(&frame, &previous, 8);
            save(&held, 4);
            save_frame();
            previous = frame;
            cases++;
        }
    }
    if (cases == 0) failed = 1;
    submission_workload();
}

#ifdef WORLD_BARE_METAL
void __attribute__((noinline, noreturn)) finished(void) {
    for (;;) __asm__ volatile("bkpt #0");
}
void __attribute__((noreturn, used)) entry(void) { workload(); finished(); }
#else
int main(int argc, char **argv) {
    if (argc != 2) return 2;
    FILE *input = fopen(argv[1], "rb");
    if (!input) return 2;
    atlas_size = fread(atlas_data, 1, sizeof atlas_data, input);
    if (ferror(input) || !feof(input)) { fclose(input); return 2; }
    fclose(input);
    workload();
    if (failed) return 1;
    return fwrite(results, 1, result_size, stdout) == result_size ? 0 : 1;
}
#endif
