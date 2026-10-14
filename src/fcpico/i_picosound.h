/* SPDX-License-Identifier: GPL-2.0-or-later */
#ifndef FCPICO_I_PICOSOUND_H
#define FCPICO_I_PICOSOUND_H

#include <stdbool.h>
typedef struct audio_buffer audio_buffer_t;

bool I_PicoSoundIsInitialized(void);
void I_PicoSoundSetMusicGenerator(void (*generator)(audio_buffer_t *buffer));
void I_PicoSoundFade(bool in);
bool I_PicoSoundFading(void);

#endif
