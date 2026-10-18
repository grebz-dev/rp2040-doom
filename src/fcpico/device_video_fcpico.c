/* SPDX-License-Identifier: BSD-3-Clause */
/* RP2350 composed-frame conversion and cartridge publication. */

#include "fcpico_video_sink.h"
#include "fcvideo.h"
#include "fcbus_device.h"
#include "i_audio_fcpico.h"
#include "ui_capture.h"
#include "doom/m_menu.h"
#include "w_wad.h"
#if FCPICO_WORLD_SPRITES
#include "world_sprites.h"
#endif
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
#if FCPICO_WORLD_SPRITES
static fcworld_t world;
static uint32_t world_errors;
/* The generation built for the frame being rendered: submitted at resolve
 * when the scheduler is idle (world_early), else at frame end. */
static fcworld_frame_t world_frame;
static bool world_early;
static uint8_t world_early_gen;
static uint32_t world_early_frames;
static uint32_t world_loose_skipped;   /* loose gating: pictures without a new generation */
/* Frame-end waits for a world generation to release the back buffer. */
#define WORLD_WAIT_US 50000u
static uint32_t world_waits, world_wait_timeouts, world_wait_us_max;
static uint64_t world_wait_us_total;
static const uint8_t menu_sprite_palettes[MBX_WORLD_PAL_LEN] = FCWORLD_MENU_PALETTES_INIT;

static void __not_in_flash_func(world_hook)(void *user, uint8_t *mailbox, uint8_t pad2,
                                            bool swapped)
{
    (void)user;
    (void)swapped;
    if (bus.core.proto == FCBUS_PROTO_V5) fcworld_heartbeat(&world, mailbox, pad2);
}
#endif

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
static bool converter_ready;
static uint8_t err[FCVIDEO_ERR_BYTES];
static uint8_t palette_sets[FCVIDEO_PALETTE_SET_COUNT][MBX_PAL_LEN];
static uint8_t attributes[MBX_ATTR_LEN];
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
#if FCPICO_WORLD_SPRITES
    fcworld_reset(&world);
    if (!fcpico_world_init_default()) printf("world sprites: atlas missing or mismatched\n");
    uint32_t saved = save_and_disable_interrupts();
    fcbus_core_set_mailbox_hook(&bus.core, world_hook, NULL);
    restore_interrupts(saved);
#endif
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
        /* The 16 KB dither LUT is only read by fcvideo_init, which packs it;
         * build it in the converter's not yet used frame buffer. */
        _Static_assert(sizeof converter.frame >= FCVIDEO_LUT_BYTES, "LUT scratch");
        uint8_t *lut = converter.frame;
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

#if FCPICO_WORLD_SPRITES
/* Only ownership transfer excludes IRQs. The expensive tile-cache search and
 * OAM preparation run while the cartridge bus can keep rearming DMA. */
static uint8_t world_submit_frame(void) {
    uint32_t saved = save_and_disable_interrupts();
    bool reserved = fcworld_reserve(&world);
    if (fcworld_take_stall(&world)) world_errors++;
    restore_interrupts(saved);
    return reserved ? fcworld_submit_held(&world, &world_frame) : 0xFF;
}

/* Resolve runs before visplanes and wall columns are drawn: starting the
 * generation here overlaps its CHR uploads with the rest of the render.
 * Idle means the previous generation is shown, so palette
 * planning against shown_state stays valid. */
void fcpico_world_resolved(void)
{
    if (!converter_ready || world_early) return;
    uint32_t saved = save_and_disable_interrupts();
    bool ready = bus.core.proto == FCBUS_PROTO_V5 && fcworld_idle(&world);
    restore_interrupts(saved);
    if (!ready) return;
    (void)fcpico_world_prepare(&world_frame, world.shown_gen);
    world_early_gen = world_submit_frame();
    if (world_early_gen == 0xFF) return;
    world_early = true;
    world_early_frames++;
}
#endif

void fcvideo_frame_end(int palette_num, int video_type)
{
    (void)video_type;
    fcpico_video_device_poll_bootsel();
    fcbus_device_poll(&bus);
    if (!converter_ready) return;
#if FCPICO_WORLD_SPRITES
    /* A world generation still being presented holds the back buffer for a
     * few heartbeats. Waiting is cheaper than discarding this rendered frame
     * and rendering another. */
    if (!fcbus_device_back_is_free(&bus) && !fcworld_idle(&world)) {
        uint32_t start = time_us_32();
        uint32_t waited = 0;
        while (!fcbus_device_back_is_free(&bus) && !fcworld_idle(&world) &&
               (waited = time_us_32() - start) < WORLD_WAIT_US) {
            fcpico_video_device_poll_bootsel();
            fcbus_device_poll(&bus);
            fcpico_audio_update();
            tight_loop_contents();
        }
        waited = time_us_32() - start;
        world_waits++;
        world_wait_us_total += waited;
        if (waited > world_wait_us_max) world_wait_us_max = waited;
        if (!fcbus_device_back_is_free(&bus)) world_wait_timeouts++;
    }
#endif
    if (!fcbus_device_back_is_free(&bus)) {
#if FCPICO_WORLD_SPRITES
        if (world_early) {
            /* Not expected: an early submission needs the shown generation,
             * which frees the back buffer. Let it commit without a picture. */
            fcworld_release(&world);
            world_early = false;
        } else {
            (void)fcpico_world_take(&world_frame);
        }
#endif
        dropped_frames++;
        return;
    }
    if (palette_num < 0 || palette_num >= FCVIDEO_PALETTE_SET_COUNT) palette_num = 0;
    bool reset_hysteresis = converted_frames == 0 ||
                            fcbus_device_stats(&bus)->init_events != init_seen;
    uint32_t proto_lock = save_and_disable_interrupts();
    fcbus_proto_t frame_proto = bus.core.proto;
    restore_interrupts(proto_lock);
#if FCPICO_WORLD_SPRITES
    /* Takes effect from the next rendered frame. */
    fcpico_world_set_link(frame_proto == FCBUS_PROTO_V5);
    /* Usually submitted at resolve (fcpico_world_resolved); otherwise now.
     * Tiles and OAM halves upload during the rest of the render and the
     * conversion; only the commit waits for this picture's publication. */
    uint8_t world_gen = 0;
    if (world_early) {
        world_gen = world_early_gen;
        world_early = false;
    } else {
        (void)fcpico_world_prepare(&world_frame, world.shown_gen);
        if (frame_proto == FCBUS_PROTO_V5) {
#if FCPICO_WORLD_LOOSE
            /* Loose: a busy scheduler keeps the sprites it is presenting. */
            world_gen = fcworld_idle(&world) ? world_submit_frame() : 0xFE;
#else
            world_gen = world_submit_frame();
#endif
        }
    }
#endif
    fcui_status_t status;
    fcpico_ui_capture(&status, ui_generation);
    /* Takes effect from the next drawn menu. */
    M_SetNativeMenus(frame_proto >= FCBUS_PROTO_V4);
    bool native_status = frame_proto >= FCBUS_PROTO_V4 &&
        (status.flags & FCUI_FLAG_STATUS_VISIBLE);
    uint8_t frame_palette[MBX_PAL_LEN];
    memcpy(frame_palette, palette_sets[palette_num], sizeof frame_palette);
#if FCVIDEO_DEFAULT_PRESET != FCVIDEO_PRESET_SHARED_HUD
    if (native_status) {
        uint8_t *hud = frame_palette + FCVIDEO_HUD_PALETTE * 4;
        hud[0] = 0x0F; hud[1] = 0x00; hud[2] = 0x30; hud[3] = 0x16;
    }
#endif
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
    if (frame_proto >= FCBUS_PROTO_V4) {
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
#if FCPICO_WORLD_SPRITES
        fcworld_release(&world);
#endif
        restore_interrupts(saved);
        dropped_frames++;
        return;
    }
    uint32_t init_events = fcbus_device_stats(&bus)->init_events;
    if (init_events != init_seen) {
        init_seen = init_events;
        have_ui_packet = false;
        native_text_ready = false;
#if FCPICO_WORLD_SPRITES
        fcworld_reset(&world);
        fcpico_world_reset_console();
        if (frame_proto == FCBUS_PROTO_V5) {
            restore_interrupts(saved);
            world_gen = world_submit_frame();
            saved = save_and_disable_interrupts();
            if (bus.core.proto != frame_proto) {
                fcworld_release(&world);
                restore_interrupts(saved);
                dropped_frames++;
                return;
            }
        }
#endif
        /* v2 carries palette and attributes in every mailbox, so there is
         * no blocking v1 bulk transfer to request on console init. */
    }
    fcbus_core_palette(&bus.core, frame_palette);
    fcbus_core_attr_table(&bus.core, attributes);
#if FCPICO_WORLD_SPRITES
    /* A menu takes world OAM slots and sprite palettes 0/2: release it only
     * once the console shows no world generation and menu palettes. */
    bool hold_menu = frame_proto == FCBUS_PROTO_V5 && (status.flags & FCUI_FLAG_MENU) &&
                     !fcworld_quiescent(&world, menu_sprite_palettes);
    if (!hold_menu)
#endif
    (void)fcbus_core_ui_snapshot(&bus.core, ui_packet);
    if (frame_proto >= FCBUS_PROTO_V4) {
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
#if FCPICO_WORLD_SPRITES
    if (frame_proto == FCBUS_PROTO_V5) {
        uint8_t gen = world_gen;
        if (fcworld_take_stall(&world) || gen == 0xFF) {
            world_errors++;
            gen = world.shown_gen;
        }
        fcworld_release(&world);
#if FCPICO_WORLD_LOOSE
        /* Plan 14c option 5: the picture never waits for its sprites, which
         * follow when the transport catches up (they may lag the picture). */
        fcbus_device_publish(&bus);
        if (gen != 0xFE) fcpico_world_note_submitted(&world_frame);
        else world_loose_skipped++;
#else
        fcbus_core_publish_world(&bus.core, gen);
        fcpico_world_note_submitted(&world_frame);
#endif
    } else
#endif
    fcbus_device_publish(&bus);
    restore_interrupts(saved);
    converted_frames++;

    if (converted_frames % 60 == 0) {
        const fcbus_stats_t *stats = fcbus_device_stats(&bus);
        /* Published pictures per second over the last 60, in tenths. */
        /* rendered = engine frames including those dropped while both
         * buffers were busy; fps = published pictures. Both in tenths/s. */
        static uint32_t last_report_us, last_dropped;
        uint32_t now_us = time_us_32();
        uint32_t window = now_us - last_report_us;
        uint32_t fps10 = 0, rendered10 = 0;
        if (last_report_us != 0 && window != 0) {
            fps10 = (uint32_t)(600000000ull / window);
            rendered10 = (uint32_t)((60ull + dropped_frames - last_dropped) * 10000000ull / window);
        }
        last_report_us = now_us;
        last_dropped = dropped_frames;
        printf("video fps=%lu.%lu rendered=%lu.%lu frames=%lu conversion_us=%lu/%lu drops=%lu "
               "ppu_count=%lu resyncs=%lu\n",
               (unsigned long)(fps10 / 10), (unsigned long)(fps10 % 10),
               (unsigned long)(rendered10 / 10), (unsigned long)(rendered10 % 10),
               (unsigned long)converted_frames,
               (unsigned long)(conversion_total_us / converted_frames),
               (unsigned long)conversion_max_us,
               (unsigned long)dropped_frames,
               (unsigned long)stats->last_count,
               (unsigned long)stats->resyncs);
#if FCPICO_WORLD_SPRITES
        const fcpico_world_stats_t *ws = fcpico_world_stats();
        printf("world admitted=%lu candidates=%lu streamed=%lu/%lu/%lu gens=%lu packets=%lu "
               "resends=%lu tiles=%lu hits=%lu deferred=%lu busy_max=%lu stalls=%lu errors=%lu\n",
               (unsigned long)ws->admitted, (unsigned long)ws->candidates,
               (unsigned long)ws->streamed_budget, (unsigned long)ws->streamed_effect,
               (unsigned long)ws->streamed_lookup, (unsigned long)world.stats.generations,
               (unsigned long)world.stats.packets, (unsigned long)world.stats.resends,
               (unsigned long)world.stats.chr_tiles, (unsigned long)world.stats.cache_hits,
               (unsigned long)world.stats.deferred_heartbeats,
               (unsigned long)world.stats.busy_heartbeats_max,
               (unsigned long)world.stats.stalls, (unsigned long)world_errors);
        if (ws->timed_frames) {
            printf("world_us resolve=%lu/%lu build=%lu/%lu frames=%lu\n",
                   (unsigned long)(ws->resolve_us_total / ws->timed_frames),
                   (unsigned long)ws->resolve_us_max,
                   (unsigned long)(ws->build_us_total / ws->timed_frames),
                   (unsigned long)ws->build_us_max, (unsigned long)ws->timed_frames);
        }
#if FCPICO_SPRITE_FETCH_PROBE
        {
            /* Plan 14c option 5: PPU reads with A12 high, and those that also
             * select the cartridge (CS1), per console frame. */
            static uint32_t last_pa12, last_both, last_frame;
            uint32_t pa12, both, frame_no = bus.core.frame_no;
            fcbus_device_probe_counts(&pa12, &both);
            uint32_t frames = frame_no - last_frame;
            if (last_frame != 0 && frames != 0) {
                printf("probe pa12_per_frame=%lu cs1_pa12_per_frame=%lu frames=%lu\n",
                       (unsigned long)((pa12 - last_pa12) / frames),
                       (unsigned long)((both - last_both) / frames), (unsigned long)frames);
            }
            last_pa12 = pa12;
            last_both = both;
            last_frame = frame_no;
        }
#endif
        printf("world_early frames=%lu loose_skipped=%lu resolved_at_us=%lu\n",
               (unsigned long)world_early_frames, (unsigned long)world_loose_skipped,
               ws->timed_frames ? (unsigned long)(ws->resolved_at_us_total / ws->timed_frames) : 0ul);
        if (world_waits) {
            printf("world_wait count=%lu avg_us=%lu max_us=%lu timeouts=%lu\n",
                   (unsigned long)world_waits,
                   (unsigned long)(world_wait_us_total / world_waits),
                   (unsigned long)world_wait_us_max, (unsigned long)world_wait_timeouts);
        }
#endif
    }
}
