/* SPDX-License-Identifier: BSD-3-Clause */
#include "fcapu.h"
#include "fcbus_protocol.h"
#include "ctest_lite.h"

#include <string.h>

typedef struct { uint8_t reg, value; } write_t;
typedef struct {
    write_t writes[32];
    int count;
    bool accept;
} sink_t;

static bool capture(void *user, uint8_t reg, uint8_t value) {
    sink_t *sink = user;
    if (!sink->accept) return false;
    CHECK(sink->count < (int)(sizeof(sink->writes) / sizeof(sink->writes[0])));
    if (sink->count < (int)(sizeof(sink->writes) / sizeof(sink->writes[0])))
        sink->writes[sink->count++] = (write_t){reg, value};
    return true;
}

static void header(uint8_t *data, uint16_t loop, uint32_t frames, uint32_t body_size) {
    memcpy(data, "APUS", 4);
    data[4] = 1;
    data[5] = 0;
    data[6] = (uint8_t)loop;
    data[7] = (uint8_t)(loop >> 8);
    for (int i = 0; i < 4; i++) {
        data[8 + i] = (uint8_t)(frames >> (8 * i));
        data[12 + i] = (uint8_t)(body_size >> (8 * i));
    }
}

static void tick(sink_t *sink, uint32_t frame) {
    sink->count = 0;
    fcapu_pump(frame);
    CHECK(sink->count <= APU_PAIRS_MAX_V2);
    CHECK(fcapu_stats()->queue_depth <= FCAPU_DEFERRED_CAP);
}

static bool has(const sink_t *sink, uint8_t reg, uint8_t value) {
    for (int i = 0; i < sink->count; i++)
        if (sink->writes[i].reg == reg && sink->writes[i].value == value) return true;
    return false;
}

static void test_loop_inside_silence_and_retrigger(void) {
    uint8_t song[16 + 1 + 2 + 1 + 1 + 2] = {0};
    const uint8_t body[] = {1, 3, 0x09, 0x83, 1, 3, 0x09};
    header(song, 2, 5, sizeof(body));
    memcpy(song + 16, body, sizeof(body));
    fcapu_bank_t bank = {0};
    bank.music[0] = (fcapu_stream_t){song, sizeof(song)};
    sink_t sink = {.accept = true};
    fcapu_init(&bank, capture, &sink);
    CHECK(fcapu_music_play(0, true));
    tick(&sink, 1);
    CHECK(has(&sink, 3, 0x09));
    tick(&sink, 2); CHECK(!has(&sink, 3, 0x09));
    tick(&sink, 3); CHECK(!has(&sink, 3, 0x09));
    tick(&sink, 4); CHECK(!has(&sink, 3, 0x09));
    tick(&sink, 5); CHECK(has(&sink, 3, 0x09));
    tick(&sink, 6); CHECK(!has(&sink, 3, 0x09));
    tick(&sink, 7); CHECK(!has(&sink, 3, 0x09));
    tick(&sink, 8); CHECK(has(&sink, 3, 0x09));
    int count = sink.count;
    fcapu_pump(8);
    CHECK_EQ(sink.count, count); /* Same heartbeat is a no-op. */
}

static void test_high_period_only_retriggers_on_gap_or_period_change(void) {
    const uint8_t body[] = {
        2, 2, 0x10, 3, 0x08,
        2, 0, 0x9f, 3, 0x08,
        2, 2, 0x11, 3, 0x08,
        1, 3, 0x09,
    };
    uint8_t song[16 + sizeof(body)] = {0};
    header(song, 0xffff, 4, sizeof(body));
    memcpy(song + 16, body, sizeof(body));
    fcapu_bank_t bank = {0};
    bank.music[0] = (fcapu_stream_t){song, sizeof(song)};
    sink_t sink = {.accept = true};
    fcapu_init(&bank, capture, &sink);
    CHECK(fcapu_music_play(0, false));
    tick(&sink, 1); CHECK(has(&sink, 3, 0x08));
    tick(&sink, 2); CHECK(!has(&sink, 3, 0x08));
    tick(&sink, 3); CHECK(has(&sink, 3, 0x08));
    tick(&sink, 4); CHECK(has(&sink, 3, 0x09));
}

static void test_steal_restore_pause_and_volume(void) {
    uint8_t song[16 + 9] = {0};
    const uint8_t body[] = {4, 4, 0x9f, 5, 0x00, 6, 0x34, 7, 0x08};
    header(song, 0, 1, sizeof(body));
    memcpy(song + 16, body, sizeof(body));
    uint8_t effect[16 + 6] = {0};
    const uint8_t effect_body[] = {1, 4, 0x8f, 1, 4, 0x84};
    header(effect, 0xffff, 2, sizeof(effect_body));
    memcpy(effect + 16, effect_body, sizeof(effect_body));
    fcapu_sfx_t sfx = {.kind = FCAPU_SFX_PULSE2, .priority = 10,
                       .script = {effect, sizeof(effect)}};
    fcapu_bank_t bank = {.sfx = &sfx, .sfx_count = 1};
    bank.music[0] = (fcapu_stream_t){song, sizeof(song)};
    sink_t sink = {.accept = true};
    fcapu_init(&bank, capture, &sink);
    CHECK(fcapu_music_play(0, true));
    tick(&sink, 1);
    CHECK(has(&sink, 4, 0x9f));
    int handle = fcapu_sfx_start(0, 127);
    CHECK(handle > 0);
    tick(&sink, 2);
    CHECK(has(&sink, 4, 0x00));
    CHECK(has(&sink, 4, 0x8f));
    CHECK(fcapu_sfx_playing(handle));
    tick(&sink, 3);
    CHECK(has(&sink, 4, 0x84));
    tick(&sink, 4);
    CHECK(!fcapu_sfx_playing(handle));
    CHECK(has(&sink, 4, 0x9f));

    fcapu_music_pause(true);
    tick(&sink, 5);
    CHECK(has(&sink, 0x15, 0));
    CHECK(has(&sink, 4, 0));
    fcapu_music_volume(5);
    fcapu_music_pause(false);
    tick(&sink, 6);
    CHECK(has(&sink, 0x15, 0x0f));
    CHECK(has(&sink, 4, 0x95));
}

static void test_dpcm_first_and_preemption(void) {
    fcapu_sfx_t sfx[2] = {
        {.kind = FCAPU_SFX_DPCM, .priority = 30, .dpcm_rate = 9,
         .dpcm_address = 2, .dpcm_length = 10, .duration_frames = 4},
        {.kind = FCAPU_SFX_DPCM, .priority = 10, .dpcm_rate = 7,
         .dpcm_address = 3, .dpcm_length = 8, .duration_frames = 2},
    };
    fcapu_bank_t bank = {.sfx = sfx, .sfx_count = 2};
    sink_t sink = {.accept = true};
    fcapu_init(&bank, capture, &sink);
    int old = fcapu_sfx_start(0, 127);
    CHECK(old > 0);
    CHECK_EQ(fcapu_sfx_start(0, 127), -1);
    int newer = fcapu_sfx_start(1, 127);
    CHECK(newer > old);
    CHECK(!fcapu_sfx_playing(old));
    tick(&sink, 1);
    CHECK(sink.count >= 5);
    const uint8_t expected[] = {0x10, 0x12, 0x13, 0x15, 0x15};
    for (int i = 0; i < 5; i++) CHECK_EQ(sink.writes[i].reg, expected[i]);
    CHECK_EQ(sink.writes[0].value, 7);
    CHECK_EQ(sink.writes[4].value, 0x10);
    tick(&sink, 2);
    CHECK(fcapu_sfx_playing(newer));
    tick(&sink, 3);
    CHECK(!fcapu_sfx_playing(newer));
    CHECK(has(&sink, 0x15, 0));
    /* Stopping an expired handle has no effect. */
    fcapu_sfx_stop(newer);
    tick(&sink, 4);
    CHECK_EQ(sink.count, 0);
}

static void test_queue_cap_and_drop_counter(void) {
    uint8_t song[16 + 10 * (1 + 12 * 2)] = {0};
    header(song, 0xffff, 10, sizeof(song) - 16);
    for (int frame = 0; frame < 10; frame++) {
        uint8_t *p = song + 16 + frame * 25;
        *p++ = 12;
        for (int reg = 0; reg < 12; reg++) {
            *p++ = (uint8_t)reg;
            *p++ = (uint8_t)(frame + reg + 1);
        }
    }
    fcapu_bank_t bank = {0};
    bank.music[0] = (fcapu_stream_t){song, sizeof(song)};
    sink_t sink = {.accept = false};
    fcapu_init(&bank, capture, &sink);
    CHECK(fcapu_music_play(0, false));
    for (uint32_t frame = 1; frame <= 10; frame++) tick(&sink, frame);
    CHECK_EQ(fcapu_stats()->queue_depth, FCAPU_DEFERRED_CAP);
    CHECK(fcapu_stats()->dropped > 0);
    sink.accept = true;
    for (uint32_t frame = 11; frame <= 20; frame++) tick(&sink, frame);
    CHECK_EQ(fcapu_stats()->queue_depth, 0);
    CHECK(fcapu_stats()->pairs_per_frame_max <= APU_PAIRS_MAX_V2);
}

int main(void) {
    test_loop_inside_silence_and_retrigger();
    test_high_period_only_retriggers_on_gap_or_period_change();
    test_steal_restore_pause_and_volume();
    test_dpcm_first_and_preemption();
    test_queue_cap_and_drop_counter();
    return ctest_lite_result();
}
