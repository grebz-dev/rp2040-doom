/* SPDX-License-Identifier: BSD-3-Clause */
#ifndef FCAPU_H
#define FCAPU_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#define FCAPU_MUSIC_COUNT 16
#define FCAPU_DEFERRED_CAP 64

typedef struct {
    const uint8_t *data;
    size_t size;
} fcapu_stream_t;

typedef enum {
    FCAPU_SFX_PULSE2,
    FCAPU_SFX_NOISE,
    FCAPU_SFX_DPCM
} fcapu_sfx_kind_t;

/* Synthesized effects use the same APUS framing as music, without looping.
 * DPCM effects use the three hardware register values and a frame duration. */
typedef struct {
    fcapu_sfx_kind_t kind;
    uint8_t priority;             /* Lower is more important. */
    fcapu_stream_t script;
    uint8_t dpcm_rate;
    uint8_t dpcm_address;
    uint8_t dpcm_length;
    uint16_t duration_frames;
} fcapu_sfx_t;

typedef struct {
    fcapu_stream_t music[FCAPU_MUSIC_COUNT];
    const fcapu_sfx_t *sfx;
    size_t sfx_count;
} fcapu_bank_t;

/* Return false if the destination cannot accept another pair this frame.
 * The callback owns the mailbox terminator; fcapu never writes it. */
typedef bool (*fcapu_write_t)(void *user, uint8_t reg, uint8_t value);

typedef struct {
    uint32_t frames;
    uint32_t deferred;
    uint32_t dropped;
    uint8_t pairs_last_frame;
    uint8_t pairs_per_frame_max;
    uint8_t queue_depth;
} fcapu_stats_t;

void fcapu_init(const fcapu_bank_t *bank, fcapu_write_t write, void *user);
bool fcapu_music_play(int mus_id, bool loop);
void fcapu_music_stop(void);
void fcapu_music_pause(bool paused);
void fcapu_music_volume(int vol_0_15);
bool fcapu_music_playing(void);
int fcapu_sfx_start(int sfx_id, int vol_0_127);
void fcapu_sfx_stop(int handle);
bool fcapu_sfx_playing(int handle);
/* Synth envelope volume changes apply on the next pump, including silent
 * script frames. DPCM retains its fixed sample amplitude. */
void fcapu_sfx_volume(int handle, int vol_0_127);
/* Call freely; the sequencer advances once for each new heartbeat number. */
void fcapu_pump(uint32_t frame_no);
const fcapu_stats_t *fcapu_stats(void);

#endif
