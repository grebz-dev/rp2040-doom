/* SPDX-License-Identifier: GPL-2.0-or-later */
#ifndef FCPICO_LEVEL_PALETTE_H
#define FCPICO_LEVEL_PALETTE_H

#include "fcvideo.h"

/* The background preset for the current game state: the map's own world
 * palettes during a level when the build has them, else the default preset.
 * Returns a key that changes exactly when the preset does. */
int fcpico_level_preset(fcvideo_preset_t *out);

#endif
