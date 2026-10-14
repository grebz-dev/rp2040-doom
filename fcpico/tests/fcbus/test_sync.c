/*
 * test_sync.c -- frame synchronisation: the decision table and what a heartbeat does.
 *
 * SPDX-License-Identifier: BSD-3-Clause
 *
 * Mirrors rp_system::ppu_dma(): compare the qualifying-read count of the frame that just
 * ended against PPU_COUNT_VAL, re-arm the DMA inside a +/-2 window (nudging the transmit
 * state machine by one or two bytes when the count is short), stop it otherwise, and
 * append the mailbox to the tail of the buffer being streamed.
 */
#include "fcbus_core.h"
#include "ctest_lite.h"

static const uint8_t g_rom[FCBUS_ROM_PRG_BYTES] = {0};
static fcbus_core_t g_c;

static void init_core(fcbus_proto_t proto) {
    fcbus_config_t cfg = {.rom_image = g_rom, .proto_default = proto};
    fcbus_core_init(&g_c, &cfg);
}

static void test_decision_table(void) {
    const uint32_t e = PPU_COUNT_VAL_V1;
    CHECK_EQ(fcbus_sync_decide(e - 4, e), FCBUS_STOP);
    CHECK_EQ(fcbus_sync_decide(e - 3, e), FCBUS_STOP);
    CHECK_EQ(fcbus_sync_decide(e - 2, e), FCBUS_ARM_NUDGE2);
    CHECK_EQ(fcbus_sync_decide(e - 1, e), FCBUS_ARM_NUDGE1);
    CHECK_EQ(fcbus_sync_decide(e + 0, e), FCBUS_ARM);
    CHECK_EQ(fcbus_sync_decide(e + 1, e), FCBUS_ARM);
    CHECK_EQ(fcbus_sync_decide(e + 2, e), FCBUS_ARM);
    CHECK_EQ(fcbus_sync_decide(e + 3, e), FCBUS_STOP);
    CHECK_EQ(fcbus_sync_decide(e + 4, e), FCBUS_STOP);
    /* A count of zero (nothing read at all) must never look in-phase. */
    CHECK_EQ(fcbus_sync_decide(0, e), FCBUS_STOP);
    /* The window is symmetric on purpose: only the short side gets nudges, because only
     * a short count means the transmit state machine is behind by that many bytes. */
    CHECK_EQ(PPU_COUNT_WINDOW, 2);
}

static void test_expected_count_follows_the_protocol(void) {
    init_core(FCBUS_PROTO_V1);
    CHECK_EQ(fcbus_core_heartbeat(&g_c, PPU_COUNT_VAL_V1), FCBUS_ARM);
    CHECK_EQ(fcbus_core_heartbeat(&g_c, PPU_COUNT_VAL_V2), FCBUS_STOP);

    init_core(FCBUS_PROTO_V2);
    CHECK_EQ(fcbus_core_heartbeat(&g_c, PPU_COUNT_VAL_V2), FCBUS_ARM);
    CHECK_EQ(fcbus_core_heartbeat(&g_c, PPU_COUNT_VAL_V1), FCBUS_STOP);

    init_core(FCBUS_PROTO_V3);
    CHECK_EQ(fcbus_core_heartbeat(&g_c, PPU_COUNT_VAL_V3), FCBUS_ARM);
    CHECK_EQ(fcbus_core_heartbeat(&g_c, PPU_COUNT_VAL_V2), FCBUS_STOP);

    init_core(FCBUS_PROTO_V4);
    CHECK_EQ(fcbus_core_heartbeat(&g_c, PPU_COUNT_VAL_V4), FCBUS_ARM);
    CHECK_EQ(fcbus_core_heartbeat(&g_c, PPU_COUNT_VAL_V3), FCBUS_STOP);

    /* Before either side has identified itself, v1 is assumed. */
    init_core(FCBUS_PROTO_UNKNOWN);
    CHECK_EQ(fcbus_core_heartbeat(&g_c, PPU_COUNT_VAL_V1), FCBUS_ARM);
}

static void test_swap_only_on_arm(void) {
    init_core(FCBUS_PROTO_V1);
    const uint16_t *front0 = fcbus_core_stream_front(&g_c);

    /* No publish pending: an ARM must not swap. */
    (void)fcbus_core_heartbeat(&g_c, PPU_COUNT_VAL_V1);
    CHECK(fcbus_core_stream_front(&g_c) == front0);

    /* Publish, then a STOP: the swap must wait. */
    fcbus_core_publish(&g_c);
    CHECK(!fcbus_core_back_is_free(&g_c));
    (void)fcbus_core_heartbeat(&g_c, 0);
    CHECK(fcbus_core_stream_front(&g_c) == front0);
    CHECK(!fcbus_core_back_is_free(&g_c)); /* still pending */

    /* The next in-phase frame performs it. */
    (void)fcbus_core_heartbeat(&g_c, PPU_COUNT_VAL_V1 + 1);
    CHECK(fcbus_core_stream_front(&g_c) != front0);
    CHECK(fcbus_core_back_is_free(&g_c));
}

static void test_mailbox_copied_to_the_front_tail(void) {
    /* v1: 64 bytes at VRAM_MAILBOX_OFF_V1 of the buffer being streamed. */
    init_core(FCBUS_PROTO_V1);
    CHECK(fcbus_core_cmd(&g_c, PF_COM_FDIN));
    CHECK(fcbus_core_apu_write(&g_c, 0x00, 0x9F));
    (void)fcbus_core_heartbeat(&g_c, PPU_COUNT_VAL_V1);

    const uint8_t *front = (const uint8_t *)fcbus_core_stream_front(&g_c);
    const uint8_t *mbx = front + VRAM_MAILBOX_OFF_V1;
    CHECK_EQ(mbx[MBX_FLAGS], 0);
    CHECK_EQ(mbx[MBX_MAGIC], PF_MAGIC_NO);
    CHECK_EQ(mbx[MBX_CMD], PF_COM_FDIN);
    CHECK_EQ(mbx[MBX_CMD + 1], PF_COM_NONE);
    CHECK_EQ(mbx[MBX_APU + 0], 0x00);
    CHECK_EQ(mbx[MBX_APU + 1], 0x9F);
    CHECK_EQ(mbx[MBX_APU + 2], 0xFF);
    /* Nothing was written past the v1 mailbox: the v2 fields stay clear. */
    CHECK_EQ(front[VRAM_MAILBOX_OFF_V1 + FC_COM_BUF_SIZE_V1], 0);

    /* The mailbox is rearmed for the next frame, so the copy is not repeated. */
    CHECK_EQ(fcbus_core_mailbox_next(&g_c)[MBX_CMD], PF_COM_NONE);
    CHECK_EQ(fcbus_core_mailbox_next(&g_c)[MBX_APU], 0xFF);

    /* v2: 128 bytes, attribute table and palette included. */
    init_core(FCBUS_PROTO_V2);
    uint8_t attr[MBX_ATTR_LEN], pal[MBX_PAL_LEN];
    for (int i = 0; i < MBX_ATTR_LEN; i++) {
        attr[i] = (uint8_t)(0x40 + i);
    }
    for (int i = 0; i < MBX_PAL_LEN; i++) {
        pal[i] = (uint8_t)(0x10 + i);
    }
    fcbus_core_attr_table(&g_c, attr);
    fcbus_core_palette(&g_c, pal);
    (void)fcbus_core_heartbeat(&g_c, PPU_COUNT_VAL_V2);

    front = (const uint8_t *)fcbus_core_stream_front(&g_c);
    mbx = front + VRAM_MAILBOX_OFF_V2;
    CHECK_EQ(mbx[MBX_FLAGS] & MBX_FLAG_V2, MBX_FLAG_V2);
    CHECK_EQ(mbx[MBX_FLAGS] & MBX_FLAG_ATTR_VALID, MBX_FLAG_ATTR_VALID);
    CHECK_EQ(mbx[MBX_FLAGS] & MBX_FLAG_PAL_VALID, MBX_FLAG_PAL_VALID);
    CHECK_MEM(mbx + MBX_ATTR, attr, MBX_ATTR_LEN);
    CHECK_MEM(mbx + MBX_PAL, pal, MBX_PAL_LEN);
}

static void test_stats_and_frame_numbering(void) {
    init_core(FCBUS_PROTO_V1);
    const uint32_t e = PPU_COUNT_VAL_V1;

    (void)fcbus_core_heartbeat(&g_c, e);          /* in phase */
    (void)fcbus_core_heartbeat(&g_c, e - 1);      /* nudge 1 */
    (void)fcbus_core_heartbeat(&g_c, e - 2);      /* nudge 2 */
    (void)fcbus_core_heartbeat(&g_c, e + 9);      /* stop (clamped in the histogram) */
    (void)fcbus_core_heartbeat(&g_c, e);          /* resync */

    const fcbus_stats_t *s = fcbus_core_stats(&g_c);
    CHECK_EQ(s->frames, 5);
    CHECK_EQ(s->dma_stops, 1);
    CHECK_EQ(s->resyncs, 1);
    CHECK_EQ(s->last_count, e);
    CHECK_EQ(g_c.frame_no, 5);
    CHECK_EQ(s->count_hist[4], 2); /* the two exact-count frames */
    CHECK_EQ(s->count_hist[3], 1); /* expected-1 */
    CHECK_EQ(s->count_hist[2], 1); /* expected-2 */
    CHECK_EQ(s->count_hist[8], 1); /* expected+9, clamped to +4 */

    /* A run of stops counts one stop each but only one resync when it ends. */
    init_core(FCBUS_PROTO_V1);
    (void)fcbus_core_heartbeat(&g_c, 0);
    (void)fcbus_core_heartbeat(&g_c, 0);
    (void)fcbus_core_heartbeat(&g_c, 0);
    CHECK_EQ(fcbus_core_stats(&g_c)->dma_stops, 3);
    CHECK_EQ(fcbus_core_stats(&g_c)->resyncs, 0);
    (void)fcbus_core_heartbeat(&g_c, e);
    CHECK_EQ(fcbus_core_stats(&g_c)->resyncs, 1);
}

static void test_heartbeat_lifts_state_to_run(void) {
    init_core(FCBUS_PROTO_V1);
    CHECK_EQ(g_c.state, FCBUS_ST_IDLE);
    /* Even a stopped frame means the console is talking, so the link is live. */
    (void)fcbus_core_heartbeat(&g_c, 0);
    CHECK_EQ(g_c.state, FCBUS_ST_RUN);
}

static void test_heartbeat_timeout(void) {
    init_core(FCBUS_PROTO_V1);
    (void)fcbus_core_heartbeat(&g_c, PPU_COUNT_VAL_V1);
    CHECK_EQ(g_c.state, FCBUS_ST_RUN);

    fcbus_core_tick_ms(&g_c, 1000); /* consumes the "heartbeat seen" flag */
    fcbus_core_tick_ms(&g_c, 2500); /* 1500 ms of silence: still inside the window */
    CHECK_EQ(g_c.state, FCBUS_ST_RUN);
    CHECK_EQ(fcbus_core_stats(&g_c)->hb_timeouts, 0);

    fcbus_core_tick_ms(&g_c, 3001); /* 2001 ms: over the limit */
    CHECK_EQ(g_c.state, FCBUS_ST_INIT);
    CHECK_EQ(fcbus_core_stats(&g_c)->hb_timeouts, 1);

    /* The timeout queues a stop for the backend to act on. */
    fcbus_action_t act;
    bool saw_stop = false;
    while (fcbus_core_pop_action(&g_c, &act)) {
        if (act.kind == FCBUS_ACT_STOP_DMA) {
            saw_stop = true;
        }
    }
    CHECK(saw_stop);

    /* A heartbeat after the timeout restores RUN and clears the silence. */
    (void)fcbus_core_heartbeat(&g_c, PPU_COUNT_VAL_V1);
    CHECK_EQ(g_c.state, FCBUS_ST_RUN);
    fcbus_core_tick_ms(&g_c, 4000);
    fcbus_core_tick_ms(&g_c, 4100);
    CHECK_EQ(fcbus_core_stats(&g_c)->hb_timeouts, 1);
}

int main(void) {
    test_decision_table();
    test_expected_count_follows_the_protocol();
    test_swap_only_on_arm();
    test_mailbox_copied_to_the_front_tail();
    test_stats_and_frame_numbering();
    test_heartbeat_lifts_state_to_run();
    test_heartbeat_timeout();
    return ctest_lite_result();
}
