/*
 * fcpico_cart.c -- bounded host cartridge adapter for Mesen co-simulation.
 *
 * SPDX-License-Identifier: BSD-3-Clause
 */
#include "fcpico_cart.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "fcbus_host.h"
#include "fcworld.h"
#include "testpattern.h"

static bool g_initialized;
static uint8_t g_prg[FCBUS_ROM_PRG_BYTES];
static fcpico_cart_metrics_t g_metrics;
static fcpico_testpattern_t g_pattern_app;
static uint8_t g_stream[VRAM_BUF_BYTES_V3];
static size_t g_stream_length;
static bool g_stream_enabled;
static bool g_rom_v2;
static bool g_rom_v3;
static bool g_rom_v4;
static bool g_rom_v5;

/* FCPICO_WORLD_SEQUENCE: replay recorded engine frames (stream/frameN.bin plus
 * world/frameN.world) through the real v5 scheduler and gated publication,
 * one recorded frame per free back buffer. FCPICO_WORLD_LOG records which
 * recorded frame each heartbeat armed and the console's acknowledgement. */
static const char *g_sequence;
static unsigned g_seq_next, g_seq_count;
/* Device engine clock, in tenths of a heartbeat: FCPICO_WORLD_RENDER_TENTHS
 * per rendered frame (0 = lockstep: every recorded frame is shown, none
 * dropped) and FCPICO_WORLD_CONVERT_TENTHS of blocking conversion before each
 * publish. FCPICO_WORLD_EARLY=1 submits the world generation held when
 * conversion starts, as the device does. */
static int g_seq_render_tenths, g_seq_convert_tenths;
/* FCPICO_WORLD_SUBMIT_TENTHS: with FCPICO_WORLD_EARLY, the generation is
 * ready this far into the render (device: at resolve); 0 = at frame end. */
static int g_seq_submit_tenths;
static bool g_seq_early;
/* FCPICO_WORLD_WAIT=1: a finished frame waits for the back buffer instead of
 * being dropped (candidate device policy). */
static bool g_seq_wait;
/* FCPICO_WORLD_LOOSE=1: pictures publish ungated; a generation is submitted
 * only when the scheduler is idle (candidate device policy). */
static bool g_seq_loose;
static int g_buffer_frame[2] = {-1, -1};
static fcworld_t g_world;
static FILE *g_world_log;
static uint32_t g_world_errors;
static uint32_t g_stage_beats[4];   /* heartbeats spent in each scheduler stage */

/* The console's PRG is flash and may be rewritten by the fix bank while the
 * firmware keeps serving its own compiled-in image, so the model keeps a copy. */
static bool load_served_rom(const char *path) {
    FILE *input = fopen(path, "rb");
    if (input == NULL) return false;
    uint8_t header[FCBUS_ROM_INES_HDR];
    bool valid = fread(header, 1, sizeof header, input) == sizeof header &&
                 memcmp(header, "NES\x1a", 4) == 0 &&
                 fread(g_prg, 1, sizeof g_prg, input) == sizeof g_prg && fgetc(input) == EOF;
    if (fclose(input) != 0) valid = false;
    return valid;
}

static bool load_stream(const char *path) {
    FILE *input = fopen(path, "rb");
    if (input == NULL) return false;
    g_stream_length = fread(g_stream, 1, sizeof g_stream, input);
    bool valid = (g_stream_length == VRAM_BUF_BYTES_V2 ||
                  g_stream_length == VRAM_BUF_BYTES_V3) && fgetc(input) == EOF;
    if (fclose(input) != 0) valid = false;
    if (!valid) return false;
    const uint8_t *mbx_plain = g_stream + VRAM_MAILBOX_OFF_V2;
    const uint8_t *mbx_text = g_stream + VRAM_MAILBOX_OFF_V4;
    const uint8_t required = MBX_FLAG_V2 | MBX_FLAG_ATTR_VALID | MBX_FLAG_PAL_VALID;
    return (mbx_plain[MBX_MAGIC] == PF_MAGIC_NO &&
            (mbx_plain[MBX_FLAGS] & required) == required) ||
           (g_stream_length == VRAM_BUF_BYTES_V4 && mbx_text[MBX_MAGIC] == PF_MAGIC_NO &&
            (mbx_text[MBX_FLAGS] & (required | MBX_FLAG_V4)) ==
                (required | MBX_FLAG_V4));
}

static bool publish_stream(bool init) {
    fcbus_core_t *core = fcbus_host_core();
    if (!fcbus_core_back_is_free(core)) return false;
    const uint8_t *mailbox = g_stream +
        (g_rom_v4 ? VRAM_MAILBOX_OFF_V4 : VRAM_MAILBOX_OFF_V2);
    memcpy(fcbus_core_stream_back(core), g_stream, g_stream_length);
    if (init && !g_rom_v2 && !g_rom_v3 && !g_rom_v4) fcbus_core_request_data_mode(core);
    fcbus_core_palette(core, mailbox + MBX_PAL);
    fcbus_core_attr_table(core, mailbox + MBX_ATTR);
    if ((g_rom_v3 || g_rom_v4) && g_stream_length == VRAM_BUF_BYTES_V3) {
        (void)fcbus_core_ui_snapshot(core, mailbox + MBX_UI);
    }
    fcbus_core_publish(core);
    return true;
}

static bool sequence_path(char *path, size_t size, const char *kind, unsigned n,
                          const char *suffix) {
    return snprintf(path, size, "%s/%s/frame%06u.%s", g_sequence, kind, n, suffix) < (int)size;
}

static bool read_file(const char *path, uint8_t *data, size_t capacity, size_t *length) {
    FILE *input = fopen(path, "rb");
    if (input == NULL) return false;
    *length = fread(data, 1, capacity, input);
    bool valid = fgetc(input) == EOF;
    return fclose(input) == 0 && valid;
}

static bool load_world_frame(unsigned n, fcworld_frame_t *frame) {
    char path[1024];
    uint8_t record[16 + FCWORLD_MAX_TILES * 16 + WORLD_OAM_ENTRIES * 4];
    size_t length;
    if (!sequence_path(path, sizeof path, "world", n, "world") ||
        !read_file(path, record, sizeof record, &length) || length < 14 ||
        memcmp(record, "FCWF", 4) != 0 || record[5] > FCWORLD_MAX_TILES ||
        record[6] > WORLD_OAM_ENTRIES || length != 14u + record[5] * 16u + record[6] * 4u) {
        return false;
    }
    frame->tile_count = record[5];
    frame->oam_count = record[6];
    frame->peak_line = record[7];
    memcpy(frame->palettes, record + 8, MBX_WORLD_PAL_LEN);
    memcpy(frame->tiles, record + 14, record[5] * 16u);
    memcpy(frame->oam, record + 14 + record[5] * 16u, record[6] * 4u);
    return true;
}

/* Publish the next recorded frame when its picture can be staged. */
static bool load_sequence_frame(unsigned n, fcworld_frame_t *frame) {
    char path[1024];
    return sequence_path(path, sizeof path, "stream", n, "bin") &&
           read_file(path, g_stream, sizeof g_stream, &g_stream_length) &&
           g_stream_length == VRAM_BUF_BYTES_V4 && load_world_frame(n, frame);
}

static void publish_sequence(void) {
    fcbus_core_t *core = fcbus_host_core();
    static fcworld_frame_t frame;
    static uint8_t gen;
    static bool converting, submitted, loaded;
    static int remaining;   /* tenths of a heartbeat left in this engine step */
    static int elapsed;     /* tenths into the current render */
    static int waited;      /* tenths spent waiting for the back buffer */
    if (g_seq_next >= g_seq_count) return;
    remaining -= 10;
    if (!converting) elapsed += 10;
    /* Device: the generation is built at resolve, part-way through the
     * render, and submitted held when the scheduler is idle. */
    if (g_seq_early && !converting && !submitted && g_seq_submit_tenths > 0 &&
        elapsed >= g_seq_submit_tenths && fcworld_idle(&g_world)) {
        if (!load_sequence_frame(g_seq_next, &frame)) {
            g_seq_count = g_seq_next;
            return;
        }
        loaded = true;
        gen = fcworld_submit_held(&g_world, &frame);
        submitted = true;
    }
    if (remaining > 0) return;
    if (!converting) {
        /* The engine finished rendering recorded frame g_seq_next. */
        bool free = fcbus_core_back_is_free(core) &&
                    (g_seq_loose || submitted || fcworld_idle(&g_world));
        if (!free) {
            /* Device: wait up to 50 ms (3 heartbeats) while a world generation
             * holds the buffer, then drop. */
            bool world_busy = !fcworld_idle(&g_world);
            if (g_seq_render_tenths == 0 || (g_seq_wait && world_busy && waited < 30)) {
                waited += 10;
                remaining = 0;
                return;
            }
            waited = 0;
            if (submitted) fcworld_release(&g_world);   /* commits without a picture */
            if (g_world_log) fprintf(g_world_log, "drop,%u,%u\n", core->stats.frames, g_seq_next);
            g_seq_next++;
            submitted = loaded = false;
            elapsed = 0;
            remaining += g_seq_render_tenths;
            return;
        }
        waited = 0;
        if (!loaded && !load_sequence_frame(g_seq_next, &frame)) {
            g_seq_count = g_seq_next;  /* end of the recording */
            return;
        }
        loaded = true;
        /* Otherwise the device submits held when conversion starts. */
        if (g_seq_early && !submitted && (!g_seq_loose || fcworld_idle(&g_world))) {
            gen = fcworld_submit_held(&g_world, &frame);
            submitted = true;
        }
        converting = true;
        remaining += g_seq_convert_tenths;
        if (remaining > 0) return;
    }
    converting = false;
    remaining += g_seq_render_tenths;
    elapsed = 0;
    const uint8_t *mailbox = g_stream + VRAM_MAILBOX_OFF_V4;
    memcpy(fcbus_core_stream_back(core), g_stream, g_stream_length);
    fcbus_core_palette(core, mailbox + MBX_PAL);
    fcbus_core_attr_table(core, mailbox + MBX_ATTR);
    (void)fcbus_core_ui_snapshot(core, mailbox + MBX_UI);
    if (g_seq_loose) {
        if (!submitted && fcworld_idle(&g_world)) gen = fcworld_submit(&g_world, &frame);
        submitted = loaded = false;
        fcworld_release(&g_world);
        fcbus_core_publish(core);
        g_buffer_frame[1 - core->front] = (int)g_seq_next;
        if (g_world_log) fprintf(g_world_log, "publish,%u,%u,%u\n", core->stats.frames, g_seq_next, 0u);
        g_seq_next++;
        return;
    }
    if (!submitted) gen = fcworld_submit(&g_world, &frame);
    submitted = loaded = false;
    if (gen == 0xFF) {
        g_world_errors++;
        gen = g_world.shown_gen;
    }
    fcworld_release(&g_world);
    fcbus_core_publish_world(core, gen);
    g_buffer_frame[1 - core->front] = (int)g_seq_next;
    if (g_world_log) {
        fprintf(g_world_log, "publish,%u,%u,%u\n", core->stats.frames, g_seq_next, gen);
    }
    g_seq_next++;
}

static void world_hook(void *user, uint8_t *mailbox, uint8_t pad2, bool swapped) {
    (void)user;
    unsigned stage = g_world.stage;
    if (stage < 4) g_stage_beats[stage]++;
    fcworld_heartbeat(&g_world, mailbox, pad2);
    if (fcworld_take_stall(&g_world)) {
        g_world_errors++;
        fcbus_core_release_gate(fcbus_host_core());
    }
    if (g_world_log) {
        /* The swap, if any, is already applied: front is the armed picture. */
        const fcbus_core_t *core = fcbus_host_core();
        fprintf(g_world_log, "heartbeat,%u,%d,%u,%u,%u,%u\n", core->stats.frames,
                g_buffer_frame[core->front], pad2 >> 4, pad2 & 15, (unsigned)swapped, stage);
    }
}

static void publish_pattern(uint32_t frame) {
    (void)frame;
    if (fcpico_testpattern_poll(&g_pattern_app, 0)) {
        g_metrics.pattern_frames++;
    }
}

static void handle_init(uint8_t stage) {
    if (g_sequence) {
        fcworld_reset(&g_world);
        g_buffer_frame[0] = g_buffer_frame[1] = -1;
        publish_sequence();
    } else if (g_stream_enabled) {
        (void)publish_stream(true);
    } else if (fcpico_testpattern_console_init(&g_pattern_app, stage, 0)) {
        g_metrics.pattern_frames++;
    }
    g_metrics.init_actions++;
    g_metrics.last_init_stage = stage;
}

static void reset_link(void) {
    fcbus_config_t config = {
        .rom_image = g_prg,
        /* Match the device: the Doom firmware assumes v2 until told otherwise. */
        .proto_default = g_rom_v5 ? FCBUS_PROTO_V5 :
                         g_rom_v4 ? FCBUS_PROTO_V4 :
                         g_rom_v3 ? FCBUS_PROTO_V3 :
                         g_rom_v2 ? FCBUS_PROTO_V2 : FCBUS_PROTO_V1,
    };
    fcbus_host_init(&config);
    fcbus_host_set_bulk_lead_byte(g_stream_enabled && !g_rom_v2 && !g_rom_v3 && !g_rom_v4);
    fcpico_testpattern_init(&g_pattern_app, fcbus_host_core());
    if (g_sequence) {
        fcworld_reset(&g_world);
        fcbus_core_set_mailbox_hook(fcbus_host_core(), world_hook, NULL);
    }
    memset(&g_metrics, 0, sizeof g_metrics);
}

/* The adapter owns the host action log.  Clear it before every input byte, then
 * consume only the actions that byte actually caused.  In particular, this
 * avoids treating an opcode-looking byte in a ROM-page or FP_COM_LOG payload
 * as a second parser would. */
static void consume_actions(void) {
    const fcbus_host_log_entry_t *entries = fcbus_host_action_log();
    size_t count = fcbus_host_action_log_count();
    bool reset = false;

    for (size_t index = 0; index < count; ++index) {
        switch (entries[index].kind) {
        case FCBUS_ACT_INIT:
            handle_init((uint8_t)entries[index].arg);
            break;
        case FCBUS_ACT_RESET:
            reset = true;
            break;
        case FCBUS_ACT_LOG:
        case FCBUS_ACT_PROTO_ERROR:
        case FCBUS_ACT_STREAM_RESPONSE:
        case FCBUS_ACT_HEARTBEAT:
        case FCBUS_ACT_STOP_DMA:
        default:
            break;
        }
    }

    if (reset) {
        /* FP_COM_RST means restart the cartridge firmware.  Retaining the
         * caller-owned PRG is enough to recreate the bounded host model; its
         * public protocol and adapter metrics intentionally restart at zero. */
        reset_link();
        return;
    }
    fcbus_host_action_log_clear();
}

bool fcpico_cart_init(const uint8_t *prg, size_t prg_len) {
    if (prg == NULL || prg_len != FCBUS_ROM_PRG_BYTES) {
        return false;
    }

    g_sequence = getenv("FCPICO_WORLD_SEQUENCE");
    if (g_sequence != NULL && g_sequence[0] == '\0') g_sequence = NULL;
    if (g_sequence) {
        const char *count = getenv("FCPICO_WORLD_FRAMES");
        g_seq_count = count ? (unsigned)strtoul(count, NULL, 10) : 100000u;
        g_seq_next = 0;
        const char *render = getenv("FCPICO_WORLD_RENDER_TENTHS");
        const char *convert = getenv("FCPICO_WORLD_CONVERT_TENTHS");
        g_seq_render_tenths = render ? (int)strtol(render, NULL, 10) : 0;
        g_seq_convert_tenths = convert ? (int)strtol(convert, NULL, 10) : 0;
        const char *submit = getenv("FCPICO_WORLD_SUBMIT_TENTHS");
        g_seq_submit_tenths = submit ? (int)strtol(submit, NULL, 10) : 0;
        const char *loose = getenv("FCPICO_WORLD_LOOSE");
        g_seq_loose = loose != NULL && loose[0] == '1';
        const char *wait = getenv("FCPICO_WORLD_WAIT");
        g_seq_wait = wait != NULL && wait[0] == '1';
        const char *early = getenv("FCPICO_WORLD_EARLY");
        g_seq_early = early != NULL && early[0] == '1';
        const char *log = getenv("FCPICO_WORLD_LOG");
        if (log && log[0]) {
            g_world_log = fopen(log, "w");
            if (g_world_log == NULL) return false;
            fprintf(g_world_log, "event,heartbeat,a,b,c,d\n");
        }
    }
    const char *stream_path = getenv("FCPICO_STREAM_FILE");
    g_stream_enabled = stream_path != NULL && stream_path[0] != '\0';
    if (g_stream_enabled && !load_stream(stream_path)) return false;
    const char *serve_path = getenv("FCPICO_SERVE_ROM");
    if (serve_path != NULL && serve_path[0] != '\0') {
        if (!load_served_rom(serve_path)) return false;
    } else {
        memcpy(g_prg, prg, sizeof g_prg);
    }
    g_rom_v2 = memcmp(g_prg + FCBUS_ROM_STAMP_OFF, "20DOOM-02-", 10) == 0;
    g_rom_v3 = memcmp(g_prg + FCBUS_ROM_STAMP_OFF, "20DOOM-03-", 10) == 0;
    g_rom_v4 = memcmp(g_prg + FCBUS_ROM_STAMP_OFF, "20DOOM-04-", 10) == 0;
    g_rom_v5 = memcmp(g_prg + FCBUS_ROM_STAMP_OFF, "20DOOM-05-", 10) == 0;
    if (g_sequence && !g_rom_v5) return false;
    if ((g_rom_v3 || g_rom_v4) && g_stream_enabled &&
        g_stream_length != VRAM_BUF_BYTES_V3) return false;
    reset_link();
    g_initialized = true;
    return true;
}

void fcpico_cart_shutdown(void) {
    g_initialized = false;
    g_stream_enabled = false;
    g_rom_v2 = false;
    g_rom_v3 = false;
    g_rom_v4 = false;
    g_rom_v5 = false;
    if (g_world_log) {
        fprintf(g_world_log, "end,%u,%u,%u,%u,%u\n", g_seq_next, g_world.stats.generations,
                g_world.stats.packets, g_world.stats.resends, g_world_errors);
        const fcworld_stats_t *ws = &g_world.stats;
        fprintf(g_world_log,
                "stats,idle=%u,chr=%u,oam=%u,wait_shown=%u,chr_tiles=%u,cache_hits=%u,"
                "commits=%u,deferred=%u,unchanged=%u,clears=%u,busy_max=%u\n",
                g_stage_beats[FCWORLD_IDLE], g_stage_beats[FCWORLD_CHR], g_stage_beats[FCWORLD_OAM],
                g_stage_beats[FCWORLD_WAIT_SHOWN], ws->chr_tiles, ws->cache_hits, ws->commits,
                ws->deferred_heartbeats, ws->unchanged, ws->clears, ws->busy_heartbeats_max);
        fclose(g_world_log);
        g_world_log = NULL;
    }
    g_sequence = NULL;
    memset(&g_metrics, 0, sizeof g_metrics);
}

uint8_t fcpico_cart_ppu_read(void) {
    if (!g_initialized) {
        return 0xff;
    }

    int value = fcbus_host_ppu_read();
    g_metrics.ppu_reads++;
    if (value < 0) {
        g_metrics.open_bus_reads++;
        return 0xff;
    }
    return (uint8_t)value;
}

void fcpico_cart_ppu_write(uint8_t value) {
    if (!g_initialized) {
        return;
    }

    uint32_t frames_before = fcbus_core_stats(fcbus_host_core())->frames;
    fcbus_host_action_log_clear();
    fcbus_host_ppu_write(value);
    g_metrics.ppu_writes++;
    consume_actions();

    /* The device test-pattern main loop notices a completed heartbeat and
     * fills the now-free back buffer for the next one.  Do the same on this
     * emulator thread; no count, state, or stream layout is adjusted here. */
    if (fcbus_core_stats(fcbus_host_core())->frames != frames_before) {
        if (g_sequence) {
            publish_sequence();
        } else if (g_stream_enabled) {
            (void)publish_stream(false);
        } else {
            publish_pattern(fcbus_core_stats(fcbus_host_core())->frames);
        }
    }
}

void fcpico_cart_tick_ms(uint32_t now_ms) {
    if (g_initialized) {
        fcbus_host_tick_ms(now_ms);
    }
}

const fcbus_stats_t *fcpico_cart_stats(void) {
    return g_initialized ? fcbus_core_stats(fcbus_host_core()) : NULL;
}

const fcpico_cart_metrics_t *fcpico_cart_metrics(void) {
    return g_initialized ? &g_metrics : NULL;
}
