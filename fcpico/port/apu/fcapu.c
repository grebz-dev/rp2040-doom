/* SPDX-License-Identifier: BSD-3-Clause */
#include "fcapu.h"

#include "fcbus_protocol.h"

#include <string.h>

enum { APUS_HEADER = 16, APUS_NO_LOOP = 0xffff, APUS_FRAME_PAIRS = 15 };
enum { ORDER_STEAL = 1, ORDER_SFX = 2, ORDER_NOTE = 3, ORDER_TIMBRE = 4 };

typedef struct { uint8_t reg, value; } pair_t;
typedef struct { pair_t pairs[APUS_FRAME_PAIRS]; uint8_t count; } frame_t;
typedef struct {
    fcapu_stream_t source;
    size_t pos, end, loop_pos;
    uint32_t frame, count;
    uint16_t loop_frame;
    uint8_t silence, loop_silence;
    bool loop;
} reader_t;
typedef struct { uint8_t reg, value, order; } queued_t;
typedef struct {
    reader_t reader;
    int handle, id;
    uint32_t elapsed, duration;
    uint8_t volume;
    uint8_t envelope;
    bool envelope_valid, volume_dirty;
    bool active;
} voice_t;

static struct {
    const fcapu_bank_t *bank;
    fcapu_write_t write;
    void *user;
    reader_t music;
    voice_t voices[3]; /* pulse 2, noise, DPCM */
    queued_t queue[FCAPU_DEFERRED_CAP];
    uint8_t queue_count;
    uint8_t image[24], image_valid[24];
    uint8_t planned[24], planned_valid[24];
    uint8_t previous_channels;
    uint8_t music_control;
    uint8_t dpcm_pending; /* 1 = trigger, 2 = stop */
    int next_handle;
    int volume;
    bool music_on, paused, control_dirty, volume_dirty;
    bool have_frame;
    uint32_t last_frame;
    fcapu_stats_t stats;
} a;

static uint16_t le16(const uint8_t *p) {
    return (uint16_t)(p[0] | ((uint16_t)p[1] << 8));
}

static uint32_t le32(const uint8_t *p) {
    return (uint32_t)p[0] | ((uint32_t)p[1] << 8) |
           ((uint32_t)p[2] << 16) | ((uint32_t)p[3] << 24);
}

/* Validate once, including the exact body boundary. A loop may start inside
 * a run of silent frames; loop_silence captures that partial run. */
static bool reader_open(reader_t *r, fcapu_stream_t source, bool loop) {
    if (!source.data || source.size < APUS_HEADER ||
        memcmp(source.data, "APUS", 4) || source.data[4] != 1) return false;
    uint32_t body_len = le32(source.data + 12);
    if (body_len > source.size - APUS_HEADER ||
        source.size != APUS_HEADER + (size_t)body_len) return false;
    uint32_t count = le32(source.data + 8);
    uint16_t loop_frame = le16(source.data + 6);
    if (!count || (loop_frame != APUS_NO_LOOP && loop_frame >= count)) return false;

    size_t pos = APUS_HEADER, loop_pos = APUS_HEADER;
    uint32_t frame = 0;
    uint8_t loop_silence = 0;
    while (frame < count) {
        if (pos >= source.size) return false;
        size_t token_pos = pos;
        uint8_t token = source.data[pos++];
        if (token & 0x80) {
            uint8_t run = token & 0x7f;
            if (!run || run > count - frame) return false;
            if (loop_frame != APUS_NO_LOOP && frame <= loop_frame && loop_frame < frame + run) {
                loop_pos = token_pos;
                loop_silence = (uint8_t)(frame + run - loop_frame);
            }
            frame += run;
        } else {
            if (token > APUS_FRAME_PAIRS || source.size - pos < (size_t)token * 2) return false;
            if (frame == loop_frame) loop_pos = token_pos;
            for (uint8_t i = 0; i < token; i++) {
                if (source.data[pos + i * 2] > 0x17) return false;
            }
            pos += (size_t)token * 2;
            frame++;
        }
    }
    if (pos != source.size) return false;
    *r = (reader_t){.source = source, .pos = APUS_HEADER, .end = pos,
                    .loop_pos = loop_pos, .count = count, .loop_frame = loop_frame,
                    .loop_silence = loop_silence,
                    .loop = loop && loop_frame != APUS_NO_LOOP};
    return true;
}

static bool reader_frame(reader_t *r, frame_t *out) {
    out->count = 0;
    if (r->frame == r->count) {
        if (!r->loop) return false;
        r->frame = r->loop_frame;
        r->pos = r->loop_pos + (r->loop_silence ? 1u : 0u);
        r->silence = r->loop_silence;
    }
    if (r->silence) {
        r->silence--;
        r->frame++;
        return true;
    }
    uint8_t token = r->source.data[r->pos++];
    if (token & 0x80) {
        r->silence = (uint8_t)((token & 0x7f) - 1);
        r->frame++;
        return true;
    }
    out->count = token;
    for (uint8_t i = 0; i < token; i++) {
        out->pairs[i] = (pair_t){r->source.data[r->pos], r->source.data[r->pos + 1]};
        r->pos += 2;
    }
    r->frame++;
    return true;
}

static bool channel_owned(uint8_t reg) {
    return ((reg >= 4 && reg <= 7) && a.voices[0].active) ||
           ((reg >= 12 && reg <= 15) && a.voices[1].active);
}

static void queue_remove_channel(int channel) {
    uint8_t first = channel == 0 ? 4 : 12;
    for (uint8_t i = 0; i < a.queue_count;) {
        if (a.queue[i].reg >= first && a.queue[i].reg < first + 4) {
            memmove(a.queue + i, a.queue + i + 1,
                    (size_t)(--a.queue_count - i) * sizeof(a.queue[0]));
        } else i++;
    }
}

static void queue_music_clear(void) {
    for (uint8_t i = 0; i < a.queue_count;) {
        if (a.queue[i].order >= ORDER_NOTE) {
            memmove(a.queue + i, a.queue + i + 1,
                    (size_t)(--a.queue_count - i) * sizeof(a.queue[0]));
        } else i++;
    }
}

static void enqueue(uint8_t reg, uint8_t value, uint8_t order) {
    if (a.queue_count == FCAPU_DEFERRED_CAP) {
        a.stats.dropped++;
        return;
    }
    a.queue[a.queue_count++] = (queued_t){reg, value, order};
}

static uint8_t scaled_volume(uint8_t reg, uint8_t value) {
    if (reg != 0 && reg != 4 && reg != 12) return value;
    int level = a.volume == 0 ? 0 : a.volume <= 5 ? 1 : a.volume <= 10 ? 2 : 3;
    return (uint8_t)((value & 0xf0) | (((value & 0x0f) * level + 1) / 3));
}

static void restore_channel(int channel) {
    uint8_t base = channel == 0 ? 4 : 12;
    if (!a.music_on || !a.image_valid[base]) {
        enqueue(base, 0, ORDER_STEAL);
        if (!a.music_on) return;
    }
    for (uint8_t reg = base; reg < base + 4; reg++) {
        if (a.music_on && a.image_valid[reg]) {
            enqueue(reg, scaled_volume(reg, a.image[reg]),
                    (reg == base + 2 || reg == base + 3) ? ORDER_NOTE : ORDER_TIMBRE);
        }
    }
}

static uint8_t desired_control(void) {
    uint8_t value = (a.music_on && !a.paused) ? a.music_control : 0;
    if (!a.paused && a.voices[0].active) value |= 0x02;
    if (!a.paused && a.voices[1].active) value |= 0x08;
    if (!a.paused && a.voices[2].active) value |= 0x10;
    return value;
}

void fcapu_init(const fcapu_bank_t *bank, fcapu_write_t write, void *user) {
    memset(&a, 0, sizeof(a));
    a.bank = bank;
    a.write = write;
    a.user = user;
    a.music_control = 0x0f;
    a.volume = 15;
    a.next_handle = 1;
}

bool fcapu_music_play(int mus_id, bool loop) {
    reader_t reader;
    if (!a.bank || mus_id < 0 || mus_id >= FCAPU_MUSIC_COUNT ||
        !reader_open(&reader, a.bank->music[mus_id], loop)) return false;
    queue_music_clear();
    memset(a.image_valid, 0, sizeof(a.image_valid));
    memset(a.planned_valid, 0, sizeof(a.planned_valid));
    a.previous_channels = 0;
    a.music = reader;
    a.music_on = true;
    a.paused = false;
    a.music_control = 0x0f;
    a.control_dirty = true;
    return true;
}

void fcapu_music_stop(void) {
    queue_music_clear();
    a.music_on = false;
    a.previous_channels = 0;
    a.control_dirty = true;
    enqueue(0, 0, ORDER_STEAL);
    if (!a.voices[0].active) enqueue(4, 0, ORDER_STEAL);
    if (!a.voices[1].active) enqueue(12, 0, ORDER_STEAL);
}

void fcapu_music_pause(bool paused) {
    if (a.paused == paused) return;
    a.paused = paused;
    a.control_dirty = true;
    a.queue_count = 0;
    if (paused) {
        enqueue(0, 0, ORDER_STEAL);
        enqueue(4, 0, ORDER_STEAL);
        enqueue(12, 0, ORDER_STEAL);
    } else {
        if (a.voices[2].active) a.dpcm_pending = 1;
        for (uint8_t reg = 0; reg < 16; reg++) {
            if (a.music_on && a.image_valid[reg] && !channel_owned(reg)) {
                enqueue(reg, scaled_volume(reg, a.image[reg]),
                        (reg & 3) >= 2 ? ORDER_NOTE : ORDER_TIMBRE);
            }
        }
    }
}

void fcapu_music_volume(int vol_0_15) {
    if (vol_0_15 < 0) vol_0_15 = 0;
    if (vol_0_15 > 15) vol_0_15 = 15;
    if (a.volume != vol_0_15) { a.volume = vol_0_15; a.volume_dirty = true; }
}

static int voice_index(fcapu_sfx_kind_t kind) {
    if (kind == FCAPU_SFX_PULSE2) return 0;
    if (kind == FCAPU_SFX_NOISE) return 1;
    if (kind == FCAPU_SFX_DPCM) return 2;
    return -1;
}

bool fcapu_music_playing(void) {
    return a.music_on;
}

void fcapu_sfx_volume(int handle, int vol_0_127) {
    if (vol_0_127 < 0) vol_0_127 = 0;
    if (vol_0_127 > 127) vol_0_127 = 127;
    for (int i = 0; i < 3; i++) {
        voice_t *voice = &a.voices[i];
        if (!voice->active || voice->handle != handle) continue;
        if (voice->volume != vol_0_127) {
            voice->volume = (uint8_t)vol_0_127;
            voice->volume_dirty = true;
        }
        return;
    }
}

int fcapu_sfx_start(int sfx_id, int vol_0_127) {
    if (!a.bank || !a.bank->sfx || sfx_id < 0 || (size_t)sfx_id >= a.bank->sfx_count) return -1;
    const fcapu_sfx_t *sfx = &a.bank->sfx[sfx_id];
    int index = voice_index(sfx->kind);
    if (index < 0) return -1;
    voice_t *voice = &a.voices[index];
    if (voice->active) {
        const fcapu_sfx_t *current = &a.bank->sfx[voice->id];
        if (sfx->priority > current->priority ||
            (sfx->priority == current->priority && voice->elapsed <= voice->duration / 2))
            return -1;
    }
    reader_t reader = {0};
    if (index == 2) {
        if (!sfx->duration_frames) return -1;
    } else if (!reader_open(&reader, sfx->script, false)) return -1;

    if (index != 2) {
        queue_remove_channel(index);
        enqueue(index == 0 ? 4 : 12, 0, ORDER_STEAL);
    }
    if (vol_0_127 < 0) vol_0_127 = 0;
    if (vol_0_127 > 127) vol_0_127 = 127;
    *voice = (voice_t){.reader = reader, .id = sfx_id, .active = true,
                       .duration = index == 2 ? sfx->duration_frames : reader.count,
                       .volume = (uint8_t)vol_0_127, .handle = a.next_handle++};
    if (a.next_handle <= 0) a.next_handle = 1;
    if (index == 2) a.dpcm_pending = 1;
    a.control_dirty = true;
    return voice->handle;
}

void fcapu_sfx_stop(int handle) {
    for (int i = 0; i < 3; i++) {
        if (!a.voices[i].active || a.voices[i].handle != handle) continue;
        a.voices[i].active = false;
        if (i == 2) a.dpcm_pending = 2;
        else { queue_remove_channel(i); restore_channel(i); }
        a.control_dirty = true;
        return;
    }
}

bool fcapu_sfx_playing(int handle) {
    for (int i = 0; i < 3; i++) {
        if (a.voices[i].active && a.voices[i].handle == handle) return true;
    }
    return false;
}

static void music_frame(void) {
    frame_t frame;
    if (!reader_frame(&a.music, &frame)) {
        fcapu_music_stop();
        return;
    }
    uint8_t before[24], valid_before[24], channels = 0;
    memcpy(before, a.planned, sizeof(before));
    memcpy(valid_before, a.planned_valid, sizeof(valid_before));
    for (uint8_t i = 0; i < frame.count; i++) {
        uint8_t reg = frame.pairs[i].reg;
        if (reg < 16) channels |= (uint8_t)(1u << (reg / 4));
    }
    for (uint8_t i = 0; i < frame.count; i++) {
        uint8_t reg = frame.pairs[i].reg, value = frame.pairs[i].value;
        a.image[reg] = value;
        a.image_valid[reg] = 1;
        if (reg == 0x15) { a.music_control = value & 0x0f; a.control_dirty = true; continue; }
        if (channel_owned(reg)) continue;

        bool emit = !valid_before[reg] || before[reg] != value;
        if (reg == 3 || reg == 7 || reg == 11 || reg == 15) {
            uint8_t channel = reg / 4, low = reg - 1;
            bool changed_low = false;
            for (uint8_t j = 0; j < frame.count; j++) {
                if (frame.pairs[j].reg == low &&
                    (!valid_before[low] || frame.pairs[j].value != before[low]))
                    changed_low = true;
            }
            /* Match tools/audio/apus.py thin_register_writes: a gap in the
             * source channel or a changed period retriggers the note. */
            emit = !(a.previous_channels & (1u << channel)) || changed_low ||
                   !valid_before[reg] || ((before[reg] ^ value) & 0x07);
        }
        if (!emit) continue;
        uint8_t order = (reg < 16 && (reg & 3) >= 2) ? ORDER_NOTE : ORDER_TIMBRE;
        enqueue(reg, scaled_volume(reg, value), order);
        a.planned[reg] = value;
        a.planned_valid[reg] = 1;
    }
    a.previous_channels = channels;
}

static void sfx_frames(void) {
    for (int i = 0; i < 3; i++) {
        voice_t *voice = &a.voices[i];
        if (!voice->active) continue;
        if (voice->elapsed == voice->duration) {
            fcapu_sfx_stop(voice->handle);
            continue;
        }
        if (i != 2) {
            frame_t frame;
            (void)reader_frame(&voice->reader, &frame);
            uint8_t first = i == 0 ? 4 : 12;
            for (uint8_t j = 0; j < frame.count; j++) {
                uint8_t reg = frame.pairs[j].reg;
                if (reg < first || reg >= first + 4) { a.stats.dropped++; continue; }
                uint8_t value = frame.pairs[j].value;
                if (reg == first) {
                    voice->envelope = value;
                    voice->envelope_valid = true;
                    voice->volume_dirty = false;
                    value = (uint8_t)((value & 0xf0) |
                                      (((value & 0x0f) * voice->volume + 63) / 127));
                }
                enqueue(reg, value, ORDER_SFX);
            }
            if (voice->volume_dirty && voice->envelope_valid) {
                uint8_t value = (uint8_t)((voice->envelope & 0xf0) |
                    (((voice->envelope & 0x0f) * voice->volume + 63) / 127));
                enqueue(first, value, ORDER_SFX);
                voice->volume_dirty = false;
            }
        }
        voice->elapsed++;
    }
}

static bool write_now(uint8_t reg, uint8_t value, uint8_t *pairs) {
    if (*pairs >= APU_PAIRS_MAX_V2 || !a.write || !a.write(a.user, reg, value)) return false;
    (*pairs)++;
    return true;
}

void fcapu_pump(uint32_t frame_no) {
    if (a.have_frame && a.last_frame == frame_no) return;
    a.have_frame = true;
    a.last_frame = frame_no;
    a.stats.frames++;
    uint8_t pairs = 0;

    /* Expiry is known before this frame's arbitration; a DPCM stop must
     * precede every deferred synth/music write. */
    if (a.voices[2].active && a.voices[2].elapsed == a.voices[2].duration)
        fcapu_sfx_stop(a.voices[2].handle);

    if (a.dpcm_pending && (!a.paused || a.dpcm_pending == 2)) {
        uint8_t control = desired_control();
        if (a.dpcm_pending == 1 && a.voices[2].active) {
            const fcapu_sfx_t *sfx = &a.bank->sfx[a.voices[2].id];
            /* DPCM trigger/stop is immediate and precedes all queued writes. */
            if (!write_now(0x10, sfx->dpcm_rate, &pairs) ||
                !write_now(0x12, sfx->dpcm_address, &pairs) ||
                !write_now(0x13, sfx->dpcm_length, &pairs) ||
                !write_now(0x15, control & (uint8_t)~0x10, &pairs) ||
                !write_now(0x15, control | 0x10, &pairs)) a.stats.dropped++;
        } else if (!write_now(0x15, control, &pairs)) a.stats.dropped++;
        a.dpcm_pending = 0;
        a.control_dirty = false;
    }
    if (!a.paused) {
        sfx_frames();
        if (a.music_on) music_frame();
    }
    if (a.control_dirty) {
        enqueue(0x15, desired_control(), ORDER_STEAL);
        a.control_dirty = false;
    }
    if (a.volume_dirty && !a.paused) {
        for (uint8_t reg = 0; reg <= 12; reg += 4) {
            if (a.music_on && a.image_valid[reg] && !channel_owned(reg))
                enqueue(reg, scaled_volume(reg, a.image[reg]), ORDER_TIMBRE);
        }
        a.volume_dirty = false;
    }

    for (uint8_t order = ORDER_STEAL; order <= ORDER_TIMBRE; order++) {
        for (uint8_t i = 0; i < a.queue_count && pairs < APU_PAIRS_MAX_V2;) {
            if (a.queue[i].order != order) { i++; continue; }
            if (!write_now(a.queue[i].reg, a.queue[i].value, &pairs)) goto done;
            memmove(a.queue + i, a.queue + i + 1,
                    (size_t)(--a.queue_count - i) * sizeof(a.queue[0]));
        }
    }
done:
    a.stats.pairs_last_frame = pairs;
    if (pairs > a.stats.pairs_per_frame_max) a.stats.pairs_per_frame_max = pairs;
    a.stats.queue_depth = a.queue_count;
    a.stats.deferred += a.queue_count;
}

const fcapu_stats_t *fcapu_stats(void) { return &a.stats; }
