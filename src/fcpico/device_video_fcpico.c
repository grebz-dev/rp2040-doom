/* SPDX-License-Identifier: BSD-3-Clause */
/* RP2350 composed-frame conversion and cartridge publication. */

#include "fcpico_video_sink.h"
#include "fcvideo.h"
#include "fcbus_device.h"
#include "i_audio_fcpico.h"
#include "ui_capture.h"
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
static uint32_t audio_irq_max_us;

static void audio_heartbeat(void *user, uint32_t frame)
{
    (void)user;
    uint32_t start = time_us_32();
    I_FCPicoAudioPump(frame);
    uint32_t elapsed = time_us_32() - start;
    if (elapsed > audio_irq_max_us) audio_irq_max_us = elapsed;
}

bool fcpico_audio_write(void *user, uint8_t reg, uint8_t value)
{
    (void)user;
    /* Sequencer calls hold the core 0 interrupt guard. */
    return fcbus_core_apu_write(&bus.core, reg, value);
}

void fcpico_audio_update(void)
{
    /* Rendering wait loops may poll from core 1; sequencer state belongs to
     * the engine on core 0. The bus IRQ runs there too. */
    if (get_core_num() != 0) return;
    uint32_t saved = save_and_disable_interrupts();
    I_FCPicoAudioPump(bus.core.frame_no);
    restore_interrupts(saved);
}
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
static uint8_t ui_generation;
static uint8_t last_ui_packet[MBX_UI_LEN];
static bool have_ui_packet;
static uint8_t native_text_sent[NATIVE_TEXT_TILES];
static bool native_text_ready;

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
    fcbus_device_set_heartbeat_callback(&bus, audio_heartbeat, NULL);
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
    fcapu_stats_t audio_stats = *fcapu_stats();
    uint32_t audio_irq_us = audio_irq_max_us;
    bool audio_bank_present = I_FCPicoAudioHasBank();
    bool music_playing = fcapu_music_playing();
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
    printf("[audio] bank=%u music=%u frames=%lu pairs_max=%u deferred=%lu drops=%lu irq_max_us=%lu\n",
           (unsigned)audio_bank_present, (unsigned)music_playing,
           (unsigned long)audio_stats.frames, (unsigned)audio_stats.pairs_per_frame_max,
           (unsigned long)audio_stats.deferred, (unsigned long)audio_stats.dropped,
           (unsigned long)audio_irq_us);
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
    bool reset_hysteresis = converted_frames == 0 ||
                            fcbus_device_stats(&bus)->init_events != init_seen;
    uint32_t proto_lock = save_and_disable_interrupts();
    fcbus_proto_t frame_proto = bus.core.proto;
    restore_interrupts(proto_lock);
    fcui_status_t status;
    fcpico_ui_capture(&status, ui_generation);
    bool native_status = frame_proto == FCBUS_PROTO_V4 &&
        (status.flags & FCUI_FLAG_STATUS_VISIBLE);
    uint8_t frame_palette[MBX_PAL_LEN];
    memcpy(frame_palette, palette_sets[palette_num], sizeof frame_palette);
    if (native_status) {
        frame_palette[12] = 0x0F;
        frame_palette[13] = 0x00;
        frame_palette[14] = 0x30;
        frame_palette[15] = 0x16;
    }
    fcvideo_set_palette(&converter, frame_palette);
    fcvideo_set_native_status(&converter, native_status);
    fcvideo_set_status_snapshot(&converter, &status);
    if (native_status) {
        fcvideo_blank_status(&converter);
    }
    uint32_t start = time_us_32();
    fcvideo_convert_staged(&converter,
                           (uint8_t *)fcbus_device_stream_back(&bus), attributes,
                           reset_hysteresis);
    if (frame_proto == FCBUS_PROTO_V4) {
        fcvideo_compact_native_text((uint8_t *)fcbus_device_stream_back(&bus));
        if ((status.flags & FCUI_FLAG_MENU) &&
            (status.ready_weapon >> 4) == 2) {
            fcvideo_overlay_episode_menu(
                (uint8_t *)fcbus_device_stream_back(&bus), attributes,
                frame_palette);
        }
    }
    uint32_t elapsed = time_us_32() - start;
    if (elapsed > conversion_max_us) conversion_max_us = elapsed;
    conversion_total_us += elapsed;

    uint8_t ui_packet[MBX_UI_LEN];
    fcui_pack_status(ui_packet, &status);
    if (!have_ui_packet || memcmp(ui_packet + 1, last_ui_packet + 1, 14) != 0) {
        ui_generation++;
        status.generation = ui_generation;
        fcui_pack_status(ui_packet, &status);
        memcpy(last_ui_packet, ui_packet, MBX_UI_LEN);
        have_ui_packet = true;
    }

    /* The IRQ may arm the front buffer, but cannot swap this completed back
     * buffer until publish. Keep mailbox commands and publication atomic. */
    uint32_t saved = save_and_disable_interrupts();
    if (bus.core.proto != frame_proto) {
        restore_interrupts(saved);
        dropped_frames++;
        return;
    }
    uint32_t init_events = fcbus_device_stats(&bus)->init_events;
    if (init_events != init_seen) {
        init_seen = init_events;
        have_ui_packet = false;
        native_text_ready = false;
        /* v2 carries palette and attributes in every mailbox, so there is
         * no blocking v1 bulk transfer to request on console init. */
    }
    fcbus_core_palette(&bus.core, frame_palette);
    fcbus_core_attr_table(&bus.core, attributes);
    (void)fcbus_core_ui_snapshot(&bus.core, ui_packet);
    if (frame_proto == FCBUS_PROTO_V4) {
        uint8_t desired[NATIVE_TEXT_TILES];
        fcui_format_ammo_row(desired, &status);
        if (!native_text_ready) {
            memset(native_text_sent, ' ', sizeof native_text_sent);
            native_text_ready = true;
        }
        unsigned queued = 0;
        for (unsigned i = 0; i < NATIVE_TEXT_TILES && queued < 2; ++i) {
            if (native_text_sent[i] != desired[i] &&
                fcbus_core_cmd_vram(&bus.core,
                                    (uint16_t)(0x23A2u + i), desired[i])) {
                native_text_sent[i] = desired[i];
                queued++;
            }
        }
    } else {
        native_text_ready = false;
    }
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
