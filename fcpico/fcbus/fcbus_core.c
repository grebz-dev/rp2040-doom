/*
 * fcbus_core.c -- backend-independent FC PICO cartridge bus logic.
 *
 * SPDX-License-Identifier: BSD-3-Clause
 *
 * See fcbus_core.h for the module overview and the tutorial functions each piece
 * mirrors. This file has no I/O and performs no dynamic allocation.
 */
#include "fcbus_core.h"

#include <string.h>

/* ========================================================================== */
/* Action ring                                                                    */
/* ========================================================================== */

static void push_action(fcbus_core_t *c, fcbus_action_kind_t kind, uint32_t arg,
                         const uint8_t *data, uint16_t len) {
    if (c->action_count >= FCBUS_ACTION_RING_SIZE) {
        /* Ring full: every byte fed to the core produces at most one action, and
         * backends are expected to drain after each byte, so this should not happen
         * in practice. Drop the oldest rather than lose the newest silently. */
        c->action_tail = (uint8_t)((c->action_tail + 1) % FCBUS_ACTION_RING_SIZE);
        c->action_count--;
    }
    fcbus_action_t *slot = &c->action_ring[c->action_head];
    slot->kind = kind;
    slot->arg = arg;
    slot->data = data;
    slot->len = len;
    c->action_head = (uint8_t)((c->action_head + 1) % FCBUS_ACTION_RING_SIZE);
    c->action_count++;
}

bool fcbus_core_pop_action(fcbus_core_t *c, fcbus_action_t *out) {
    if (c->action_count == 0) {
        return false;
    }
    *out = c->action_ring[c->action_tail];
    c->action_tail = (uint8_t)((c->action_tail + 1) % FCBUS_ACTION_RING_SIZE);
    c->action_count--;
    return true;
}

/* ========================================================================== */
/* Mailbox builder: rp_system::initFC_COM_BUF / setPF_COM / setPF_VRAM / setPF_APU */
/* ========================================================================== */

/* Resets mailbox_next to its just-stamped, empty state, mirroring
 * rp_system::initFC_COM_BUF() extended with the v2 flags byte.
 * Also where a pending fcbus_core_request_data_mode() re-queues PF_COM_DMOD for the
 * mailbox about to be built, so it happens "every frame" as specified rather than once. */
static void reset_next_mailbox(fcbus_core_t *c) {
    memset(c->mailbox_next, 0, sizeof c->mailbox_next);
    c->mailbox_next[MBX_MAGIC] = PF_MAGIC_NO;
    c->mailbox_next[MBX_FLAGS] = c->proto >= FCBUS_PROTO_V4 ?
        (MBX_FLAG_V2 | MBX_FLAG_V3 | MBX_FLAG_V4) : c->proto == FCBUS_PROTO_V3 ?
        (MBX_FLAG_V2 | MBX_FLAG_V3) : c->proto == FCBUS_PROTO_V2 ? MBX_FLAG_V2 : 0;
    c->cmd_cursor = MBX_CMD;
    c->apu_cursor = MBX_APU;
    c->apu_pairs = 0;
    c->mailbox_next[MBX_APU] = 0xFF;
    if (c->proto >= FCBUS_PROTO_V3 && c->ui_have) {
        memcpy(&c->mailbox_next[MBX_UI], c->ui_want, MBX_UI_LEN);
        c->mailbox_next[MBX_FLAGS] |= MBX_FLAG_UI_VALID;
    }

    if (c->data_mode_requested && c->state != FCBUS_ST_DATA) {
        (void)fcbus_core_cmd(c, PF_COM_DMOD);
    }
}

bool fcbus_core_cmd(fcbus_core_t *c, uint8_t com) {
    if (c->cmd_cursor >= FC_COM_BUF_SIZE16) {
        return false;
    }
    c->mailbox_next[c->cmd_cursor++] = com;
    return true;
}

bool fcbus_core_cmd_vram(fcbus_core_t *c, uint16_t addr, uint8_t val) {
    if ((unsigned)c->cmd_cursor + 3 > FC_COM_BUF_SIZE16) {
        return false;
    }
    addr &= 0x3FFF;
    c->mailbox_next[c->cmd_cursor++] = (uint8_t)(PF_COM_VRAM | ((addr >> 8) & 0xFF));
    c->mailbox_next[c->cmd_cursor++] = (uint8_t)(addr & 0xFF);
    c->mailbox_next[c->cmd_cursor++] = val;
    return true;
}

bool fcbus_core_apu_write(fcbus_core_t *c, uint8_t reg, uint8_t val) {
    uint8_t max_pairs = (c->proto >= FCBUS_PROTO_V2) ? APU_PAIRS_MAX_V2 : APU_PAIRS_MAX_V1;
    if (c->apu_pairs >= max_pairs) {
        return false;
    }
    c->mailbox_next[c->apu_cursor++] = reg;
    c->mailbox_next[c->apu_cursor++] = val;
    c->mailbox_next[c->apu_cursor] = 0xFF;
    c->apu_pairs++;
    if (c->proto >= FCBUS_PROTO_V2) {
        c->mailbox_next[MBX_FLAGS] |= MBX_FLAG_APU_VALID;
    }
    return true;
}

void fcbus_core_attr_table(fcbus_core_t *c, const uint8_t attr[64]) {
    /* What the application wants displayed, kept whether or not it reaches the console
     * this frame: this is rp_system's m_ATR_W, and it is what data mode uploads. */
    memcpy(c->attr_want, attr, MBX_ATTR_LEN);

    if (c->proto >= FCBUS_PROTO_V2) {
        memcpy(&c->mailbox_next[MBX_ATTR], attr, MBX_ATTR_LEN);
        c->mailbox_next[MBX_FLAGS] |= MBX_FLAG_ATTR_VALID;
        /* The whole table rides in the mailbox, so the console will hold all of it. */
        memcpy(c->attr_sent, attr, MBX_ATTR_LEN);
        return;
    }
    /* v1 (and still-unknown proto): diff want against sent, like rp_system::update()'s
     * attribute loop. Only bytes actually queued count as sent, so a run that fills the
     * command area picks up where it left off on the next call. */
    for (int i = 0; i < MBX_ATTR_LEN; i++) {
        if (c->attr_want[i] != c->attr_sent[i]) {
            if (!fcbus_core_cmd_vram(c, (uint16_t)(0x23C0 + i), c->attr_want[i])) {
                break;
            }
            c->attr_sent[i] = c->attr_want[i];
        }
    }
}

void fcbus_core_palette(fcbus_core_t *c, const uint8_t pal[16]) {
    memcpy(c->pal_want, pal, MBX_PAL_LEN);

    if (c->proto >= FCBUS_PROTO_V2) {
        memcpy(&c->mailbox_next[MBX_PAL], pal, MBX_PAL_LEN);
        c->mailbox_next[MBX_FLAGS] |= MBX_FLAG_PAL_VALID;
        memcpy(c->pal_sent, pal, MBX_PAL_LEN);
        return;
    }
    for (int i = 0; i < MBX_PAL_LEN; i++) {
        if (c->pal_want[i] != c->pal_sent[i]) {
            if (!fcbus_core_cmd_vram(c, (uint16_t)(0x3F00 + i), c->pal_want[i])) {
                break;
            }
            c->pal_sent[i] = c->pal_want[i];
        }
    }
}

bool fcbus_core_ui_snapshot(fcbus_core_t *c, const uint8_t snapshot[MBX_UI_LEN]) {
    if (c->proto < FCBUS_PROTO_V3) return false;
    memcpy(c->ui_want, snapshot, MBX_UI_LEN);
    c->ui_have = true;
    memcpy(&c->mailbox_next[MBX_UI], snapshot, MBX_UI_LEN);
    c->mailbox_next[MBX_FLAGS] |= MBX_FLAG_UI_VALID;
    return true;
}

/* ========================================================================== */
/* Stream / mailbox buffer access                                                 */
/* ========================================================================== */

uint16_t *fcbus_core_stream_back(fcbus_core_t *c) {
    return (uint16_t *)c->stream[1 - c->front];
}

const uint16_t *fcbus_core_stream_front(const fcbus_core_t *c) {
    return (const uint16_t *)c->stream[c->front];
}

uint8_t *fcbus_core_mailbox_next(fcbus_core_t *c) {
    return c->mailbox_next;
}

bool fcbus_core_back_is_free(const fcbus_core_t *c) {
    return !c->publish_pending;
}

void fcbus_core_publish(fcbus_core_t *c) {
    c->publish_pending = true;
    c->publish_gated = false;
}

void fcbus_core_publish_world(fcbus_core_t *c, uint8_t gen) {
    c->publish_pending = true;
    c->publish_gated = true;
    c->publish_gen = (uint8_t)(gen & WORLD_GEN_MASK);
    c->publish_tables = (uint8_t)(c->mailbox_next[MBX_FLAGS] &
                                  (MBX_FLAG_ATTR_VALID | MBX_FLAG_PAL_VALID));
}

void fcbus_core_release_gate(fcbus_core_t *c) {
    c->publish_gated = false;
}

void fcbus_core_set_mailbox_hook(fcbus_core_t *c, fcbus_mailbox_hook_t hook, void *user) {
    c->mailbox_hook = hook;
    c->mailbox_hook_user = user;
}

/* ========================================================================== */
/* Frame sync: rp_system::ppu_dma()                                               */
/* ========================================================================== */

fcbus_sync_t fcbus_sync_decide(uint32_t count, uint32_t expected) {
    int64_t d = (int64_t)count - (int64_t)expected;
    if (d > (int64_t)PPU_COUNT_WINDOW || d < -(int64_t)PPU_COUNT_WINDOW) {
        return FCBUS_STOP;
    }
    if (d == -2) {
        return FCBUS_ARM_NUDGE2;
    }
    if (d == -1) {
        return FCBUS_ARM_NUDGE1;
    }
    return FCBUS_ARM;
}

fcbus_sync_t fcbus_core_heartbeat(fcbus_core_t *c, uint32_t count) {
    if (c->state == FCBUS_ST_IDLE || c->state == FCBUS_ST_INIT) {
        c->state = FCBUS_ST_RUN;
    }

    uint32_t expected = c->proto >= FCBUS_PROTO_V4 ? PPU_COUNT_VAL_V4 :
        c->proto == FCBUS_PROTO_V3 ? PPU_COUNT_VAL_V3 :
        c->proto == FCBUS_PROTO_V2 ? PPU_COUNT_VAL_V2 : PPU_COUNT_VAL_V1;
    fcbus_sync_t decision = fcbus_sync_decide(count, expected);

    c->stats.frames++;
    c->stats.last_count = count;
    int64_t d = (int64_t)count - (int64_t)expected;
    if (d < -4) {
        d = -4;
    } else if (d > 4) {
        d = 4;
    }
    c->stats.count_hist[d + 4]++;
    c->heartbeat_seen = true;

    if (decision == FCBUS_STOP) {
        c->stats.dma_stops++;
        c->stopped_last_heartbeat = true;
        push_action(c, FCBUS_ACT_STOP_DMA, (uint32_t)decision, NULL, 0);
    } else {
        if (c->stopped_last_heartbeat) {
            c->stats.resyncs++;
        }
        c->stopped_last_heartbeat = false;

        bool swapped = false;
        if (c->publish_pending) {
            uint8_t shown = (uint8_t)((c->pad2 >> WORLD_HB_GEN_SHIFT) & WORLD_GEN_MASK);
            if (!c->publish_gated || shown == c->publish_gen) {
                c->front = 1 - c->front;
                c->publish_pending = false;
                swapped = true;
                if (c->publish_gated) {
                    /* The tables belong to this picture, not to the frames
                     * repeated while its native actors were uploading. */
                    memcpy(&c->mailbox_next[MBX_PAL], c->pal_want, MBX_PAL_LEN);
                    memcpy(&c->mailbox_next[MBX_ATTR], c->attr_want, MBX_ATTR_LEN);
                    c->mailbox_next[MBX_FLAGS] |= c->publish_tables;
                }
                c->publish_gated = false;
            } else if (c->publish_gated) {
                c->mailbox_next[MBX_FLAGS] &=
                    (uint8_t)~(MBX_FLAG_ATTR_VALID | MBX_FLAG_PAL_VALID);
            }
        }
        if (c->mailbox_hook != NULL) {
            c->mailbox_hook(c->mailbox_hook_user, c->mailbox_next, c->pad2, swapped);
        }

        size_t mbx_len = c->proto >= FCBUS_PROTO_V3 ? FC_COM_BUF_SIZE_V3 :
            c->proto == FCBUS_PROTO_V2 ? FC_COM_BUF_SIZE_V2 : FC_COM_BUF_SIZE_V1;
        size_t mbx_off = c->proto >= FCBUS_PROTO_V4 ? VRAM_MAILBOX_OFF_V4 :
            c->proto == FCBUS_PROTO_V3 ? VRAM_MAILBOX_OFF_V3 :
            c->proto == FCBUS_PROTO_V2 ? VRAM_MAILBOX_OFF_V2 : VRAM_MAILBOX_OFF_V1;
        uint8_t *front_bytes = (uint8_t *)c->stream[c->front];
        memcpy(front_bytes + mbx_off, c->mailbox_next, mbx_len);

        reset_next_mailbox(c);
        push_action(c, FCBUS_ACT_HEARTBEAT, (uint32_t)decision, NULL, 0);
    }

    c->frame_no++;
    return decision;
}

uint16_t fcbus_core_pads(const fcbus_core_t *c) {
    return (uint16_t)(((uint16_t)c->pad2 << 8) | c->pad1);
}

void fcbus_core_tick_ms(fcbus_core_t *c, uint32_t now_ms) {
    if (c->heartbeat_seen) {
        c->last_heartbeat_ms = now_ms;
        c->have_heartbeat_ms = true;
        c->heartbeat_seen = false;
        return;
    }
    if (c->state == FCBUS_ST_RUN && c->have_heartbeat_ms) {
        uint32_t elapsed = now_ms - c->last_heartbeat_ms; /* wraps correctly if unsigned */
        if (elapsed > 2000) {
            c->state = FCBUS_ST_INIT;
            c->stats.hb_timeouts++;
            push_action(c, FCBUS_ACT_STOP_DMA, 0, NULL, 0);
        }
    }
}

/* ========================================================================== */
/* Bulk data mode: rp_system::startDataMode() / jobFP_COM_DRQ() / drq_ret()       */
/* ========================================================================== */

void fcbus_core_request_data_mode(fcbus_core_t *c) {
    c->data_mode_requested = true;
    c->dm_pal_pending = true;
    c->dm_attr_pending = true;
    if (c->state != FCBUS_ST_DATA) {
        (void)fcbus_core_cmd(c, PF_COM_DMOD);
    }
}

void fcbus_core_set_step(fcbus_core_t *c, uint8_t step) {
    c->dm_step = step;
}

/* Builds the 8-byte data-mode response header exactly as rp_system::drq_ret() lays out
 * its two little-endian words: word0 = FCBUS_DRQ_MAGIC_BASE + com + (adr << 16),
 * word1 = size. Queues it as a STREAM_RESPONSE. */
static void emit_drq_header(fcbus_core_t *c, uint8_t com, uint16_t adr, uint16_t size) {
    uint32_t word0 = FCBUS_DRQ_MAGIC_BASE + com + ((uint32_t)adr << 16);
    uint32_t word1 = size;
    c->drq_header[0] = (uint8_t)(word0 & 0xFF);
    c->drq_header[1] = (uint8_t)((word0 >> 8) & 0xFF);
    c->drq_header[2] = (uint8_t)((word0 >> 16) & 0xFF);
    c->drq_header[3] = (uint8_t)((word0 >> 24) & 0xFF);
    c->drq_header[4] = (uint8_t)(word1 & 0xFF);
    c->drq_header[5] = (uint8_t)((word1 >> 8) & 0xFF);
    c->drq_header[6] = (uint8_t)((word1 >> 16) & 0xFF);
    c->drq_header[7] = (uint8_t)((word1 >> 24) & 0xFF);
    push_action(c, FCBUS_ACT_STREAM_RESPONSE, 0, c->drq_header, 8);
}

/* Services one FP_COM_DRQ in the tutorial's fixed priority order (docs/pages/protocol.md,
 * "Bulk data mode"): pending palette, then pending attributes, then a pending step, then
 * PF_COM_NONE to end the mode. The payload is the *wanted* table, as
 * rp_system::jobFP_COM_DRQ() points m_pDRQ at m_PAL_W / m_ATR_W rather than at the
 * last-sent copies -- see the field comment in fcbus_core.h. */
static void service_drq(fcbus_core_t *c) {
    if (c->state != FCBUS_ST_DATA) {
        c->state = FCBUS_ST_DATA;
    }

    if (c->dm_pal_pending) {
        c->dm_pal_pending = false;
        memset(c->data_payload, 0, sizeof c->data_payload);
        memcpy(c->data_payload, c->pal_want, sizeof c->pal_want);
        /* The tutorial ROM requests and copies 32 palette bytes. The NES
         * mirrors $3F10/$3F14/$3F18/$3F1C over the backdrop entries, so
         * zeros in the second half would erase all four backdrop colours. */
        memcpy(c->data_payload + MBX_PAL_LEN, c->pal_want, MBX_PAL_LEN);
        /* A bulk upload delivers the whole table, so the console now holds what we want
         * and the per-frame diff has nothing left to trickle. */
        memcpy(c->pal_sent, c->pal_want, sizeof c->pal_sent);
        emit_drq_header(c, PF_DAT_VRAM, 0x3F00, (uint16_t)sizeof c->pal_want);
        return;
    }
    if (c->dm_attr_pending) {
        c->dm_attr_pending = false;
        memset(c->data_payload, 0, sizeof c->data_payload);
        memcpy(c->data_payload, c->attr_want, sizeof c->attr_want);
        memcpy(c->attr_sent, c->attr_want, sizeof c->attr_sent);
        emit_drq_header(c, PF_DAT_VRAM, 0x23C0, (uint16_t)sizeof c->attr_want);
        return;
    }
    if (c->dm_step != 0) {
        uint8_t step = c->dm_step;
        c->dm_step = 0;
        c->data_mode_requested = false;
        c->state = FCBUS_ST_RUN;
        emit_drq_header(c, PF_DAT_STEP, step, 0);
        return;
    }
    c->data_mode_requested = false;
    c->state = FCBUS_ST_RUN;
    emit_drq_header(c, PF_COM_NONE, 0, 0);
}

/* ========================================================================== */
/* FP_COM_VER: rp_system::ver_dma()                                               */
/* ========================================================================== */

static void emit_ver_response(fcbus_core_t *c) {
    /* Sync word bytes, exactly the order fcppu_r shifts FCBUS_VER_SYNC_W0/W1 out
     * (LSB-first autopull): 0x21212121 then 0x43462321. */
    static const uint8_t sync[8] = {0x21, 0x21, 0x21, 0x21, 0x21, 0x23, 0x46, 0x43};
    memcpy(c->ver_scratch, sync, sizeof sync);
    memcpy(c->ver_scratch + sizeof sync, c->rom_image + FCBUS_ROM_STAMP_OFF, 16);
    push_action(c, FCBUS_ACT_STREAM_RESPONSE, 0, c->ver_scratch, (uint16_t)sizeof c->ver_scratch);
}

/* ========================================================================== */
/* Frame-state reset: rp_system::init2() (minus the hardware bring-up)            */
/* ========================================================================== */

static void reset_frame_state(fcbus_core_t *c) {
    /* FP_COM_INI means the console is starting over: it holds nothing, and nothing is
     * wanted yet (rp_system::init2() -> clearAtrData() and an unset palette). */
    memset(c->attr_want, 0, sizeof c->attr_want);
    memset(c->attr_sent, 0, sizeof c->attr_sent);
    memset(c->pal_want, 0, sizeof c->pal_want);
    memset(c->pal_sent, 0, sizeof c->pal_sent);
    c->pad1 = 0;
    c->pad2 = 0;
    c->publish_pending = false;
    c->publish_gated = false;
    c->data_mode_requested = false;
    c->dm_pal_pending = false;
    c->dm_attr_pending = false;
    c->dm_step = 0;
    c->ui_have = false;
    reset_next_mailbox(c);
}

/* ========================================================================== */
/* Receive dispatcher: rp_system::jobRcvCom()                                     */
/* ========================================================================== */

void fcbus_core_set_read_count(fcbus_core_t *c, uint32_t count) {
    c->pending_read_count = count;
}

bool fcbus_core_rx_will_heartbeat(const fcbus_core_t *c, uint8_t b) {
    if (c->rxwait == FCBUS_RXW_KEY_PAD2) {
        return true;
    }
    if (c->rxwait != FCBUS_RXW_NONE || c->proto >= FCBUS_PROTO_V2) {
        return false;
    }
    switch (b) {
    case FP_COM_VER:
    case FP_COM_ROM:
    case FP_COM_LOG:
    case FP_COM_DRQ:
    case FP_COM_DLD:
    case FP_COM_RST:
    case FP_COM_INI:
    case FP_COM_KEY:
    case FP_COM_HELLO:
        return false;
    default:
        return true;
    }
}

static void do_heartbeat_from_rx(fcbus_core_t *c) {
    (void)fcbus_core_heartbeat(c, c->pending_read_count);
}

void fcbus_core_rx_idle(fcbus_core_t *c) {
    if (c->rxwait == FCBUS_RXW_LOG) {
        push_action(c, FCBUS_ACT_LOG, 0, c->log_buf, c->log_len);
        c->rxwait = FCBUS_RXW_NONE;
        c->log_len = 0;
    }
}

void fcbus_core_rx_byte(fcbus_core_t *c, uint8_t b) {
    switch (c->rxwait) {
    case FCBUS_RXW_ROM_PAGE: {
        uint32_t off = ((uint32_t)(b & 0x7F)) << 8;
        /* ROM_UPDATE reads $2007 once to fill the PPU buffer, then once for
         * each of 256 programmed bytes. A 256-byte DMA stalls on that last
         * fetch and can consume the next page's first byte while recovering.
         * Keep one extra word queued, as the tutorial's long ROM DMA does. */
        memcpy(c->rom_scratch, c->rom_image + off, 256);
        memset(c->rom_scratch + 256, 0xFF, 4);
        push_action(c, FCBUS_ACT_STREAM_RESPONSE, 0, c->rom_scratch,
                    (uint16_t)sizeof c->rom_scratch);
        c->rxwait = FCBUS_RXW_NONE;
        return;
    }
    case FCBUS_RXW_DLD_PAGE:
        /* Only one page of data-mode payload is ever staged at a time (palette,
         * attributes and the step reply all fit in a single 256-byte page), so the page
         * argument itself carries no further information here; it is still read off the
         * bus so the byte stream stays in step with the 6502. */
        (void)b;
        push_action(c, FCBUS_ACT_STREAM_RESPONSE, 0, c->data_payload,
                    (uint16_t)sizeof c->data_payload);
        c->rxwait = FCBUS_RXW_NONE;
        return;
    case FCBUS_RXW_INI_STAGE:
        reset_frame_state(c);
        c->proto = FCBUS_PROTO_UNKNOWN;
        c->state = FCBUS_ST_INIT;
        c->rxwait = FCBUS_RXW_NONE;
        c->stats.init_events++;
        c->stats.last_init_stage = b;
        push_action(c, FCBUS_ACT_INIT, b, NULL, 0);
        return;
    case FCBUS_RXW_HELLO_VER:
        c->rxwait = FCBUS_RXW_NONE;
        if (b == FCBUS_PROTOCOL_V5 || b == FCBUS_PROTOCOL_V4) {
            c->proto = b == FCBUS_PROTOCOL_V5 ? FCBUS_PROTO_V5 : FCBUS_PROTO_V4;
            c->mailbox_next[MBX_FLAGS] |= MBX_FLAG_V2 | MBX_FLAG_V3 | MBX_FLAG_V4;
        } else if (b == FCBUS_PROTOCOL_V3) {
            c->proto = FCBUS_PROTO_V3;
            c->mailbox_next[MBX_FLAGS] |= MBX_FLAG_V2 | MBX_FLAG_V3;
        } else if (b == FCBUS_PROTOCOL_V2) {
            c->proto = FCBUS_PROTO_V2;
        } else {
            c->stats.proto_errors++;
            push_action(c, FCBUS_ACT_PROTO_ERROR, b, NULL, 0);
        }
        return;
    case FCBUS_RXW_KEY_PAD1:
        c->pad1 = b;
        c->rxwait = FCBUS_RXW_KEY_PAD2;
        return;
    case FCBUS_RXW_KEY_PAD2:
        c->pad2 = b;
        c->rxwait = FCBUS_RXW_NONE;
        if (c->proto == FCBUS_PROTO_UNKNOWN) {
            c->proto = FCBUS_PROTO_V2;
        }
        do_heartbeat_from_rx(c);
        return;
    case FCBUS_RXW_LOG:
        if (c->log_len < FCBUS_LOG_MAX) {
            c->log_buf[c->log_len++] = b;
        }
        if (c->log_len >= FCBUS_LOG_MAX) {
            push_action(c, FCBUS_ACT_LOG, 0, c->log_buf, c->log_len);
            c->rxwait = FCBUS_RXW_NONE;
            c->log_len = 0;
        }
        return;
    case FCBUS_RXW_NONE:
    default:
        break;
    }

    switch (b) {
    case FP_COM_VER:
        emit_ver_response(c);
        return;
    case FP_COM_ROM:
        c->rxwait = FCBUS_RXW_ROM_PAGE;
        return;
    case FP_COM_LOG:
        c->rxwait = FCBUS_RXW_LOG;
        c->log_len = 0;
        return;
    case FP_COM_DRQ:
        service_drq(c);
        return;
    case FP_COM_DLD:
        c->rxwait = FCBUS_RXW_DLD_PAGE;
        return;
    case FP_COM_RST:
        c->state = FCBUS_ST_IDLE;
        push_action(c, FCBUS_ACT_RESET, 0, NULL, 0);
        return;
    case FP_COM_INI:
        c->rxwait = FCBUS_RXW_INI_STAGE;
        return;
    case FP_COM_KEY:
        c->rxwait = FCBUS_RXW_KEY_PAD1;
        return;
    case FP_COM_HELLO:
        c->rxwait = FCBUS_RXW_HELLO_VER;
        return;
    default:
        /* Any other byte is a v1 controller state byte (docs/pages/protocol.md,
         * "joypad-as-default-case") -- unless a v2 session is already established, in
         * which case the 6502 should never send one and this is a protocol error. */
        if (c->proto >= FCBUS_PROTO_V2) {
            c->stats.proto_errors++;
            push_action(c, FCBUS_ACT_PROTO_ERROR, b, NULL, 0);
            return;
        }
        c->pad1 = b;
        if (c->proto == FCBUS_PROTO_UNKNOWN) {
            c->proto = FCBUS_PROTO_V1;
        }
        do_heartbeat_from_rx(c);
        return;
    }
}

/* ========================================================================== */
/* Lifecycle                                                                      */
/* ========================================================================== */

void fcbus_core_init(fcbus_core_t *c, const fcbus_config_t *cfg) {
    memset(c, 0, sizeof *c);
    c->rom_image = cfg->rom_image;
    c->proto_default = cfg->proto_default;
    c->proto = cfg->proto_default;
    c->state = FCBUS_ST_IDLE;
    c->front = 0;
    reset_next_mailbox(c);
}

const fcbus_stats_t *fcbus_core_stats(const fcbus_core_t *c) {
    return &c->stats;
}
