/* SPDX-License-Identifier: GPL-2.0-or-later */
#include "level_palette.h"
#include "doom/doomstat.h"

#if FCPICO_MAP_PALETTES && FCVIDEO_DEFAULT_PRESET == FCVIDEO_PRESET_SHARED_HUD
#include "fcpico_map_palettes.h"
#define MAP_PALETTE_COUNT (sizeof fcpico_map_palettes / sizeof fcpico_map_palettes[0])
#endif

int fcpico_level_preset(fcvideo_preset_t *out)
{
    const fcvideo_preset_t *base = &fcvideo_presets[FCVIDEO_DEFAULT_PRESET];
    *out = *base;
#ifdef MAP_PALETTE_COUNT
    if (gamestate == GS_LEVEL) {
        for (unsigned i = 0; i < MAP_PALETTE_COUNT; i++) {
            const fcvideo_map_palette_t *map = &fcpico_map_palettes[i];
            if (map->episode == gameepisode && map->map == gamemap) {
                fcvideo_map_preset(base, map, out);
                return (int)i + 1;
            }
        }
    }
#endif
    return 0;
}
