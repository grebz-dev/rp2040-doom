/* SPDX-License-Identifier: BSD-3-Clause */
/* RP2350 composed-frame conversion and cartridge publication. */

#include "fcpico_video_sink.h"
#include "fcvideo.h"
#include "fcbus_device.h"
#include "w_wad.h"
#include "z_zone.h"
#include "pico/stdlib.h"
#include "pico/bootrom.h"
#include "hardware/sync.h"

#include <stdio.h>
#include <string.h>

extern const unsigned char fcpico_bootrom[];
extern const int fcpico_bootrom_length;

static fcbus_device_t bus;
static fcvideo_t converter;
static uint8_t err[FCVIDEO_ERR_BYTES];
static uint8_t lut[FCVIDEO_LUT_BYTES];
static uint8_t palette_sets[FCVIDEO_PALETTE_SET_COUNT][MBX_PAL_LEN];
static uint8_t attributes[MBX_ATTR_LEN];
static bool converter_ready;
static uint32_t init_seen;
static uint32_t converted_frames;
static uint32_t dropped_frames;
static uint32_t conversion_max_us;
static uint64_t conversion_total_us;

bool fcpico_read_pad_frame(uint8_t *pad1)
{
    return fcbus_device_pop_pad_frame(&bus, pad1);
}

void fcpico_video_device_poll_bootsel(void)
{
    static char line[16];
    static size_t length;
    static bool overflow;
    int ch;

    while ((ch = getchar_timeout_us(0)) != PICO_ERROR_TIMEOUT) {
        if (ch == '\r' || ch == '\n') {
            if (!overflow && length == 7 && memcmp(line, "bootsel", 7) == 0) {
                reset_usb_boot(0, 0);
            }
            length = 0;
            overflow = false;
        } else if (ch == '\b' || ch == 127) {
            if (length != 0) --length;
        } else if (ch >= 32 && ch < 127) {
            if (length < sizeof line) line[length++] = (char)ch;
            else overflow = true;
        }
    }
}

void fcpico_video_device_init(void)
{
    if (fcpico_bootrom_length != FCBUS_ROM_INES_HDR + FCBUS_ROM_PRG_BYTES) {
        panic("invalid Doom boot ROM length");
    }
    const fcbus_config_t config = {
        .rom_image = fcpico_bootrom + FCBUS_ROM_INES_HDR,
        .proto_default = FCBUS_PROTO_V2,
    };
    if (!fcbus_device_init(&bus, &config)) panic("cartridge bus init failed");
}

#if FCPICO_DIAGNOSTIC_ENGINE_DELAY
void fcpico_video_device_diag_tick(void)
{
    static uint32_t last_us;
    uint32_t now = time_us_32();
    if (now - last_us < 2000000u) return;
    last_us = now;

    uint32_t saved = save_and_disable_interrupts();
    fcbus_stats_t stats = *fcbus_device_stats(&bus);
    fcbus_proto_t proto = bus.core.proto;
    fcbus_state_t state = bus.core.state;
    bool pending = bus.core.publish_pending;
    uint32_t raw_count = bus.diag_raw_read_count;
    restore_interrupts(saved);

    printf("[DEBUG-hr3] bus state=%u proto=%u init=%lu hb=%lu count=%lu raw=%lu "
           "stops=%lu resyncs=%lu timeouts=%lu errors=%lu "
           "ready=%u converted=%lu dropped=%lu pending=%u\n",
           (unsigned)state, (unsigned)proto, (unsigned long)stats.init_events,
           (unsigned long)stats.frames, (unsigned long)stats.last_count,
           (unsigned long)raw_count,
           (unsigned long)stats.dma_stops, (unsigned long)stats.resyncs,
           (unsigned long)stats.hb_timeouts, (unsigned long)stats.proto_errors,
           (unsigned)converter_ready, (unsigned long)converted_frames,
           (unsigned long)dropped_frames, (unsigned)pending);
}
#endif

void fcvideo_line_sink(int y, const uint8_t *line320)
{
    if (!converter_ready) {
        const uint8_t *playpal = W_CacheLumpNum(W_GetNumForName("PLAYPAL"), PU_STATIC);
        fcvideo_tables_t tables = {err, lut, {0}};
        fcvideo_build_tables(playpal, &fcvideo_presets[FCVIDEO_DEFAULT_PRESET],
                             err, lut, tables.palette);
        fcvideo_build_palette_sets(&fcvideo_presets[FCVIDEO_DEFAULT_PRESET], palette_sets);
        fcvideo_init(&converter, &tables);
        converter_ready = true;
    }
    if (y == 0) fcvideo_frame_begin(&converter);
    fcvideo_push_line(&converter, y, line320);
}

void fcvideo_frame_end(int palette_num, int video_type)
{
    (void)video_type;
    fcpico_video_device_poll_bootsel();
    fcbus_device_poll(&bus);
    if (!converter_ready) return;
    if (!fcbus_device_back_is_free(&bus)) {
        dropped_frames++;
        return;
    }
    if (palette_num < 0 || palette_num >= FCVIDEO_PALETTE_SET_COUNT) palette_num = 0;
    fcvideo_set_palette(&converter, palette_sets[palette_num]);
    bool reset_hysteresis = converted_frames == 0 ||
                            fcbus_device_stats(&bus)->init_events != init_seen;
    uint32_t start = time_us_32();
    fcvideo_convert_staged(&converter,
                           (uint8_t *)fcbus_device_stream_back(&bus), attributes,
                           reset_hysteresis);
    uint32_t elapsed = time_us_32() - start;
    if (elapsed > conversion_max_us) conversion_max_us = elapsed;
    conversion_total_us += elapsed;

    /* The IRQ may arm the front buffer, but cannot swap this completed back
     * buffer until publish. Keep mailbox commands and publication atomic. */
    uint32_t saved = save_and_disable_interrupts();
    uint32_t init_events = fcbus_device_stats(&bus)->init_events;
    if (init_events != init_seen) {
        init_seen = init_events;
        /* v2 carries palette and attributes in every mailbox, so there is
         * no blocking v1 bulk transfer to request on console init. */
    }
    fcbus_core_palette(&bus.core, palette_sets[palette_num]);
    fcbus_core_attr_table(&bus.core, attributes);
    fcbus_device_publish(&bus);
    restore_interrupts(saved);
    converted_frames++;

    if (converted_frames % 60 == 0) {
        const fcbus_stats_t *stats = fcbus_device_stats(&bus);
        printf("video frames=%lu conversion_us=%lu/%lu drops=%lu ppu_count=%lu resyncs=%lu\n",
               (unsigned long)converted_frames,
               (unsigned long)(conversion_total_us / converted_frames),
               (unsigned long)conversion_max_us,
               (unsigned long)dropped_frames,
               (unsigned long)stats->last_count,
               (unsigned long)stats->resyncs);
    }
}
