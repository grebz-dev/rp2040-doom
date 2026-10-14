/* SPDX-License-Identifier: BSD-3-Clause */
#include "testpattern.h"

#include <limits.h>

static uint8_t pattern_pixel(uint32_t pattern, uint32_t frame, int x, int y) {
    switch (pattern) {
    case 0:
        return (uint8_t)((x / 34) & 3);
    case 1:
        return (uint8_t)(((x / 8) ^ (y / 8) ^ (frame / 30)) & 3);
    case 2:
        return (uint8_t)(((x / 16) + (frame / 15)) & 3);
    default:
        if (y < 16) {
            return (uint8_t)((frame >> ((x / 16) & 7)) & 3);
        }
        return (uint8_t)(((x + y + (int)frame) >> 3) & 3);
    }
}

static void build_pattern(uint16_t *stream, uint32_t pattern, uint32_t frame) {
    for (int y = 0; y < VRAM_LINES; ++y) {
        for (int tile = 0; tile < VRAM_TILE_COLS; ++tile) {
            uint16_t word = 0;
            for (int pixel = 0; pixel < 8; ++pixel) {
                uint8_t colour = pattern_pixel(pattern, frame, tile * 8 + pixel, y);
                word = (uint16_t)((word << 1) | (colour & 1u) | ((colour & 2u) << 7));
            }
            *fcbus_stream_word(stream, y, tile) = word;
        }
    }
}

static void fill_tables(fcbus_core_t *core, uint32_t frame) {
    static const uint8_t palette[MBX_PAL_LEN] = {
        0x0f, 0x00, 0x10, 0x20, 0x0f, 0x06, 0x16, 0x26,
        0x0f, 0x09, 0x19, 0x29, 0x0f, 0x01, 0x21, 0x31,
    };
    uint8_t attributes[MBX_ATTR_LEN];

    for (size_t index = 0; index < sizeof attributes; ++index) {
        attributes[index] = (uint8_t)(((index + frame / 30u) & 1u) ? 0x1b : 0xe4);
    }
    fcbus_core_palette(core, palette);
    fcbus_core_attr_table(core, attributes);
}

static void publish(fcpico_testpattern_t *app, uint32_t pattern, uint32_t frame) {
    build_pattern(fcbus_core_stream_back(app->core), pattern, frame);
    fill_tables(app->core, frame);
    fcbus_core_publish(app->core);
    app->last_frame = frame;
}

void fcpico_testpattern_init(fcpico_testpattern_t *app, fcbus_core_t *core) {
    app->core = core;
    app->last_frame = UINT32_MAX;
}

bool fcpico_testpattern_console_init(fcpico_testpattern_t *app, uint8_t stage,
                                     uint32_t pattern) {
    (void)stage;
    if (!fcbus_core_back_is_free(app->core)) {
        return false;
    }

    uint32_t frame = fcbus_core_stats(app->core)->frames;
    build_pattern(fcbus_core_stream_back(app->core), pattern, frame);
    /* DMOD must precede v1 table diffs in the 14-byte command area. */
    fcbus_core_request_data_mode(app->core);
    fill_tables(app->core, frame);
    fcbus_core_publish(app->core);
    app->last_frame = frame;
    return true;
}

bool fcpico_testpattern_poll(fcpico_testpattern_t *app, uint32_t pattern) {
    uint32_t frame = fcbus_core_stats(app->core)->frames;
    if (frame == app->last_frame || !fcbus_core_back_is_free(app->core)) {
        return false;
    }
    publish(app, pattern, frame);
    return true;
}
