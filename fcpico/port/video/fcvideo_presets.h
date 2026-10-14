/* SPDX-License-Identifier: BSD-3-Clause */
#ifndef FCVIDEO_PRESETS_H
#define FCVIDEO_PRESETS_H

#include <stdint.h>

typedef struct {
    uint8_t backdrop;
    uint8_t subpalettes[4][3];
    uint8_t shadow_lift; /* lift PLAYPAL channels only while building err/lut */
} fcvideo_preset_t;

enum {
    FCVIDEO_PRESET_HUE = 0,
    FCVIDEO_PRESET_SHARED_WHITE = 1,
    FCVIDEO_PRESET_PIPU = 2,
    FCVIDEO_PRESET_SHADOW_DETAIL = 3,
    /* Shadow detail with its dark-red subpalette 2 replaced by the native HUD
     * palette (00 30 16, HUD index order), so the HUD needs no palette of its
     * own and the world keeps four. */
    FCVIDEO_PRESET_SHARED_HUD = 4,
    FCVIDEO_PRESET_COUNT = 5,
};

/* Values are 2C02 palette indices. The PiPU set was measured on a TV;
 * shadow-detail contrast was confirmed on FC PICO hardware on 2026-09-25. */
extern const fcvideo_preset_t fcvideo_presets[FCVIDEO_PRESET_COUNT];

#ifndef FCVIDEO_DEFAULT_PRESET
#define FCVIDEO_DEFAULT_PRESET FCVIDEO_PRESET_SHARED_HUD
#endif

#endif
