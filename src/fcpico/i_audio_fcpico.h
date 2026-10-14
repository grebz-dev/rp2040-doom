/* SPDX-License-Identifier: GPL-2.0-or-later */
#ifndef I_AUDIO_FCPICO_H
#define I_AUDIO_FCPICO_H

#include "fcapu.h"

/* Tables and APUS bytes remain owned by the caller for the engine lifetime.
 * Music names are full WAD names (D_E1M1); effect names omit DS (pistol).
 * Asset installation is explicit: an unconfigured build remains silent. */
typedef struct {
    const fcapu_bank_t *streams;
    const char *music_names[FCAPU_MUSIC_COUNT];
    const char *const *sfx_names;
} fcpico_audio_bank_t;

bool I_FCPicoAudioSetBank(const fcpico_audio_bank_t *bank);
void *I_FCPicoRegisterSongLump(int lumpnum);
void I_FCPicoAudioPump(uint32_t heartbeat);
bool I_FCPicoAudioHasBank(void);

/* Core 0 owns engine/sequencer state. Device bus IRQ calls Pump after DMA
 * re-arm; Pump and all engine sequencer operations exclude local interrupts.
 * Additional platform polls at the same heartbeat are harmless. */
void fcpico_audio_update(void);
bool fcpico_audio_write(void *user, uint8_t reg, uint8_t value);

#endif
