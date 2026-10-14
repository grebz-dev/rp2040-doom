/*
 * fcbus_host.c -- host backend for fcbus_core: a byte-stream model of the PPU bus.
 *
 * SPDX-License-Identifier: BSD-3-Clause
 *
 * See fcbus_host.h for the module overview.
 */
#include "fcbus_host.h"

#include <string.h>

/* Serving state: what fcbus_host_ppu_read() hands back next. */
typedef enum {
    HOST_SERVE_STOPPED, /* DMA stopped: reads return -1, uncounted. */
    HOST_SERVE_RESPONSE, /* Streaming a queued FCBUS_ACT_STREAM_RESPONSE buffer. */
    HOST_SERVE_FF,       /* Response exhausted; filler until the next heartbeat. */
    HOST_SERVE_STREAM,   /* Front stream buffer, advanced by any phase nudge. */
} host_serve_t;

#define FCBUS_HOST_LOG_CAP 32

static fcbus_core_t g_core;
static uint32_t g_read_count;

static host_serve_t g_mode;
static const uint8_t *g_resp_data;
static uint16_t g_resp_len;
static uint16_t g_resp_pos;
static bool g_resp_lead_byte;
static bool g_bulk_lead_byte_enabled;
static uint32_t g_stream_pos;

static fcbus_host_log_entry_t g_log[FCBUS_HOST_LOG_CAP];
static size_t g_log_len;

static void log_action(const fcbus_action_t *act) {
    if (g_log_len >= FCBUS_HOST_LOG_CAP) {
        memmove(&g_log[0], &g_log[1], sizeof(g_log[0]) * (FCBUS_HOST_LOG_CAP - 1));
        g_log_len--;
    }
    fcbus_host_log_entry_t *e = &g_log[g_log_len++];
    e->kind = act->kind;
    e->arg = act->arg;
    e->len = 0;
    if (act->data != NULL && act->len > 0) {
        uint16_t n = act->len;
        if (n > FCBUS_LOG_MAX) {
            n = FCBUS_LOG_MAX;
        }
        memcpy(e->data, act->data, n);
        e->len = n;
    }
}

static void begin_stream(fcbus_sync_t decision) {
    uint32_t nudges = 0;
    if (decision == FCBUS_ARM_NUDGE1) {
        nudges = 1;
    } else if (decision == FCBUS_ARM_NUDGE2) {
        nudges = 2;
    }
    /* A manual OUT consumes one buffer byte before normal PPU reads resume. */
    g_stream_pos = nudges;
    g_mode = HOST_SERVE_STREAM;
}

static void drain_actions(void) {
    fcbus_action_t act;
    while (fcbus_core_pop_action(&g_core, &act)) {
        switch (act.kind) {
        case FCBUS_ACT_STREAM_RESPONSE:
            g_resp_data = act.data;
            g_resp_len = act.len;
            g_resp_pos = 0;
            /* The tutorial ROM takes an extra $2007 dummy read for attribute
             * uploads. The hardware PIO restart presents one stale byte first;
             * Mesen's byte-stream backend must reproduce that for $23C0 only.
             * Palette reads are unbuffered and need no extra lead byte. */
            g_resp_lead_byte = g_bulk_lead_byte_enabled &&
                act.data == g_core.data_payload &&
                g_core.drq_header[2] == 0xC0 && g_core.drq_header[3] == 0x23;
            g_mode = (g_resp_len > 0) ? HOST_SERVE_RESPONSE : HOST_SERVE_FF;
            break;
        case FCBUS_ACT_HEARTBEAT:
            begin_stream((fcbus_sync_t)act.arg);
            g_read_count = 0;
            break;
        case FCBUS_ACT_STOP_DMA:
            g_mode = HOST_SERVE_STOPPED;
            g_read_count = 0;
            break;
        case FCBUS_ACT_INIT:
        case FCBUS_ACT_RESET:
        case FCBUS_ACT_LOG:
        case FCBUS_ACT_PROTO_ERROR:
        default:
            log_action(&act);
            break;
        }
    }
}

void fcbus_host_init(const fcbus_config_t *cfg) {
    fcbus_core_init(&g_core, cfg);
    g_read_count = 0;
    g_mode = HOST_SERVE_STOPPED;
    g_resp_data = NULL;
    g_resp_len = 0;
    g_resp_pos = 0;
    g_resp_lead_byte = false;
    g_bulk_lead_byte_enabled = false;
    g_stream_pos = 0;
    g_log_len = 0;
}

void fcbus_host_set_bulk_lead_byte(bool enabled) {
    g_bulk_lead_byte_enabled = enabled;
}

int fcbus_host_ppu_read(void) {
    switch (g_mode) {
    case HOST_SERVE_STOPPED:
        /* The PPU keeps fetching while the DMA is stopped and `fcppu_rna` keeps counting
         * those reads -- that continued advance is precisely how the link recovers phase,
         * since rp_system::ppu_dma() compares the count of the frame that just ended
         * against PPU_COUNT_VAL. So count the read, but report "no valid data on the bus"
         * with -1 rather than inventing a byte: while stopped the real bus shows whatever
         * the PIO's stale OSR contents happen to be, which nothing should rely on. */
        g_read_count++;
        return -1;

    case HOST_SERVE_RESPONSE: {
        if (g_resp_lead_byte) {
            g_resp_lead_byte = false;
            g_read_count++;
            return 0;
        }
        uint8_t byte = g_resp_data[g_resp_pos++];
        g_read_count++;
        if (g_resp_pos >= g_resp_len) {
            g_mode = HOST_SERVE_FF;
        }
        return byte;
    }

    case HOST_SERVE_FF:
        g_read_count++;
        return 0xFF;

    case HOST_SERVE_STREAM: {
        g_read_count++;
        const uint8_t *front = (const uint8_t *)fcbus_core_stream_front(&g_core);
        size_t limit = g_core.proto >= FCBUS_PROTO_V3 ? VRAM_BUF_BYTES_V3 : VRAM_BUF_BYTES_V2;
        uint8_t byte = (g_stream_pos < limit) ? front[g_stream_pos] : 0xFF;
        g_stream_pos++;
        return byte;
    }
    }
    return -1;
}

void fcbus_host_ppu_write(uint8_t b) {
    /* fcppu_rna copies !X into ISR before decrementing X. After N physical
     * qualifying reads, the pushed value is therefore N-1 (or zero before
     * the first read), not N. */
    uint32_t reported = g_read_count > 0 ? g_read_count - PPU_COUNTER_REPORT_BIAS : 0;
    fcbus_core_set_read_count(&g_core, reported);
    fcbus_core_rx_byte(&g_core, b);
    drain_actions();
}

void fcbus_host_write_burst(const uint8_t *data, size_t len) {
    for (size_t i = 0; i < len; i++) {
        fcbus_host_ppu_write(data[i]);
    }
    fcbus_core_rx_idle(&g_core);
    drain_actions();
}

void fcbus_host_tick_ms(uint32_t now_ms) {
    fcbus_core_tick_ms(&g_core, now_ms);
    drain_actions();
}

fcbus_core_t *fcbus_host_core(void) {
    return &g_core;
}

const fcbus_host_log_entry_t *fcbus_host_action_log(void) {
    return g_log;
}

size_t fcbus_host_action_log_count(void) {
    return g_log_len;
}

void fcbus_host_action_log_clear(void) {
    g_log_len = 0;
}
