/*
 * test_rx.c -- the receive dispatcher: opcodes, packets, and the joypad default case.
 *
 * SPDX-License-Identifier: BSD-3-Clause
 *
 * Mirrors rp_system::jobRcvCom(). The rule that matters most is the one
 * docs/pages/protocol.md calls "joypad-as-default-case": every byte that is not a
 * recognised FP_COM_* opcode is controller state, and receiving one is the frame
 * heartbeat. Protocol v2 keeps that for v1 consoles and
 * adds FP_COM_KEY / FP_COM_HELLO so a v2 console never sends a bare byte at all.
 */
#include "fcbus_core.h"
#include "ctest_lite.h"

static uint8_t g_rom[FCBUS_ROM_PRG_BYTES];
static fcbus_core_t g_c;

static void init_core(fcbus_proto_t proto) {
    fcbus_config_t cfg = {.rom_image = g_rom, .proto_default = proto};
    fcbus_core_init(&g_c, &cfg);
}

/** True if `b` is one of the opcodes the dispatcher recognises. */
static bool is_opcode(uint8_t b) {
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
        return true;
    default:
        return false;
    }
}

static int count_actions(fcbus_action_kind_t kind) {
    fcbus_action_t act;
    int n = 0;
    while (fcbus_core_pop_action(&g_c, &act)) {
        if (act.kind == kind) {
            n++;
        }
    }
    return n;
}

static void drain_actions(void) {
    fcbus_action_t act;
    while (fcbus_core_pop_action(&g_c, &act)) {
    }
}

/* ---------------------------------------------------------------------- */

static void test_every_byte_value_v1(void) {
    /* In v1 every non-opcode byte is a controller state byte and a heartbeat. */
    for (int b = 0; b < 256; b++) {
        init_core(FCBUS_PROTO_V1);
        (void)fcbus_core_heartbeat(&g_c, PPU_COUNT_VAL_V1); /* reach RUN */
        uint32_t frames_before = fcbus_core_stats(&g_c)->frames;
        drain_actions();

        fcbus_core_set_read_count(&g_c, PPU_COUNT_VAL_V1);
        fcbus_core_rx_byte(&g_c, (uint8_t)b);
        uint32_t frames_after = fcbus_core_stats(&g_c)->frames;

        if (is_opcode((uint8_t)b)) {
            CHECK_EQ(frames_after, frames_before); /* opcodes never beat */
        } else {
            CHECK_EQ(frames_after, frames_before + 1);
            CHECK_EQ(fcbus_core_pads(&g_c) & 0xFF, (uint32_t)b);
        }
    }

    /* FP_COM_ACK / FP_COM_NAK are defined but unhandled on both sides (the tutorial's
     * firmware has them commented out), so in v1 they are ordinary controller bytes.
     * Pinned down here so that giving them behaviour later is a deliberate change. */
    CHECK(!is_opcode(FP_COM_ACK));
    CHECK(!is_opcode(FP_COM_NAK));
}

static void test_every_byte_value_v2(void) {
    /* Once v2 is established a bare byte is a protocol error, never a heartbeat. */
    for (int b = 0; b < 256; b++) {
        init_core(FCBUS_PROTO_V2);
        (void)fcbus_core_heartbeat(&g_c, PPU_COUNT_VAL_V2);
        uint32_t frames_before = fcbus_core_stats(&g_c)->frames;
        drain_actions();

        fcbus_core_set_read_count(&g_c, PPU_COUNT_VAL_V2);
        fcbus_core_rx_byte(&g_c, (uint8_t)b);

        CHECK_EQ(fcbus_core_stats(&g_c)->frames, frames_before);
        if (!is_opcode((uint8_t)b)) {
            CHECK_EQ(fcbus_core_stats(&g_c)->proto_errors, 1);
            CHECK_EQ(count_actions(FCBUS_ACT_PROTO_ERROR), 1);
        }
    }
}

static void test_key_packet(void) {
    init_core(FCBUS_PROTO_V2);
    (void)fcbus_core_heartbeat(&g_c, PPU_COUNT_VAL_V2);
    uint32_t frames_before = fcbus_core_stats(&g_c)->frames;
    drain_actions();

    fcbus_core_set_read_count(&g_c, PPU_COUNT_VAL_V2);
    fcbus_core_rx_byte(&g_c, FP_COM_KEY);
    CHECK_EQ(fcbus_core_stats(&g_c)->frames, frames_before); /* waiting for pad 1 */
    fcbus_core_rx_byte(&g_c, KEY_A | KEY_RIGHT);
    CHECK_EQ(fcbus_core_stats(&g_c)->frames, frames_before); /* waiting for pad 2 */
    fcbus_core_rx_byte(&g_c, KEY_B);
    CHECK_EQ(fcbus_core_stats(&g_c)->frames, frames_before + 1); /* beat on pad 2 */

    CHECK_EQ(fcbus_core_pads(&g_c), (uint16_t)((KEY_B << 8) | (KEY_A | KEY_RIGHT)));
    CHECK_EQ(count_actions(FCBUS_ACT_HEARTBEAT), 1);

    /* A pad byte that happens to equal an opcode is still just data. */
    fcbus_core_set_read_count(&g_c, PPU_COUNT_VAL_V2);
    fcbus_core_rx_byte(&g_c, FP_COM_KEY);
    fcbus_core_rx_byte(&g_c, FP_COM_INI); /* 0xFF as pad 1 = every button held */
    fcbus_core_rx_byte(&g_c, FP_COM_VER); /* 0x2F as pad 2 */
    CHECK_EQ(fcbus_core_pads(&g_c), (uint16_t)((FP_COM_VER << 8) | FP_COM_INI));
    CHECK_EQ(fcbus_core_stats(&g_c)->frames, frames_before + 2);
}

static void test_read_counter_sampled_only_on_heartbeat(void) {
    init_core(FCBUS_PROTO_V2);
    CHECK(!fcbus_core_rx_will_heartbeat(&g_c, FP_COM_KEY));
    fcbus_core_rx_byte(&g_c, FP_COM_KEY);
    CHECK(!fcbus_core_rx_will_heartbeat(&g_c, KEY_A));
    fcbus_core_rx_byte(&g_c, KEY_A);
    CHECK(fcbus_core_rx_will_heartbeat(&g_c, FP_COM_VER));
    fcbus_core_set_read_count(&g_c, PPU_COUNT_VAL_V2);
    fcbus_core_rx_byte(&g_c, FP_COM_VER);
    CHECK_EQ(fcbus_core_stats(&g_c)->last_count, PPU_COUNT_VAL_V2);
    CHECK_EQ(fcbus_core_stats(&g_c)->dma_stops, 0);

    init_core(FCBUS_PROTO_V1);
    CHECK(!fcbus_core_rx_will_heartbeat(&g_c, FP_COM_INI));
    CHECK(fcbus_core_rx_will_heartbeat(&g_c, KEY_UP));
    fcbus_core_rx_byte(&g_c, FP_COM_INI);
    CHECK(!fcbus_core_rx_will_heartbeat(&g_c, KEY_UP));
    fcbus_core_rx_byte(&g_c, 0);
    CHECK(fcbus_core_rx_will_heartbeat(&g_c, KEY_UP));
}

static void test_protocol_identification(void) {
    /* A bare byte identifies a v1 console. */
    init_core(FCBUS_PROTO_UNKNOWN);
    fcbus_core_set_read_count(&g_c, PPU_COUNT_VAL_V1);
    fcbus_core_rx_byte(&g_c, KEY_UP);
    CHECK_EQ(g_c.proto, FCBUS_PROTO_V1);

    /* FP_COM_HELLO 2 identifies a v2 console. */
    init_core(FCBUS_PROTO_UNKNOWN);
    fcbus_core_rx_byte(&g_c, FP_COM_HELLO);
    fcbus_core_rx_byte(&g_c, FCBUS_PROTOCOL_V2);
    CHECK_EQ(g_c.proto, FCBUS_PROTO_V2);
    CHECK_EQ(fcbus_core_stats(&g_c)->proto_errors, 0);

    /* FP_COM_HELLO 3 selects the longer native-HUD mailbox. */
    init_core(FCBUS_PROTO_UNKNOWN);
    fcbus_core_rx_byte(&g_c, FP_COM_HELLO);
    fcbus_core_rx_byte(&g_c, FCBUS_PROTOCOL_V3);
    CHECK_EQ(g_c.proto, FCBUS_PROTO_V3);
    CHECK_EQ(fcbus_core_stats(&g_c)->proto_errors, 0);

    init_core(FCBUS_PROTO_UNKNOWN);
    fcbus_core_rx_byte(&g_c, FP_COM_HELLO);
    fcbus_core_rx_byte(&g_c, FCBUS_PROTOCOL_V4);
    CHECK_EQ(g_c.proto, FCBUS_PROTO_V4);
    CHECK_EQ(fcbus_core_stats(&g_c)->proto_errors, 0);

    /* v5 keeps the v4 stream geometry and mailbox flags. */
    init_core(FCBUS_PROTO_UNKNOWN);
    fcbus_core_rx_byte(&g_c, FP_COM_HELLO);
    fcbus_core_rx_byte(&g_c, FCBUS_PROTOCOL_V5);
    CHECK_EQ(g_c.proto, FCBUS_PROTO_V5);
    CHECK_EQ(g_c.mailbox_next[MBX_FLAGS], MBX_FLAG_V2 | MBX_FLAG_V3 | MBX_FLAG_V4);
    CHECK_EQ(fcbus_core_stats(&g_c)->proto_errors, 0);

    /* An unknown version is refused and leaves the protocol undecided. */
    init_core(FCBUS_PROTO_UNKNOWN);
    fcbus_core_rx_byte(&g_c, FP_COM_HELLO);
    fcbus_core_rx_byte(&g_c, 6);
    CHECK_EQ(g_c.proto, FCBUS_PROTO_UNKNOWN);
    CHECK_EQ(fcbus_core_stats(&g_c)->proto_errors, 1);
    CHECK_EQ(count_actions(FCBUS_ACT_PROTO_ERROR), 1);

    /* A KEY packet also identifies v2 (a console that skipped the hello). */
    init_core(FCBUS_PROTO_UNKNOWN);
    fcbus_core_set_read_count(&g_c, PPU_COUNT_VAL_V2);
    fcbus_core_rx_byte(&g_c, FP_COM_KEY);
    fcbus_core_rx_byte(&g_c, 0);
    fcbus_core_rx_byte(&g_c, 0);
    CHECK_EQ(g_c.proto, FCBUS_PROTO_V2);
    /* ... and the count was judged against the v2 expectation, so this was in phase. */
    CHECK_EQ(fcbus_core_stats(&g_c)->dma_stops, 0);
}

static void test_opcode_arguments_are_not_reinterpreted(void) {
    /* Each argument byte must be consumed as data even when it looks like an opcode. */
    init_core(FCBUS_PROTO_V1);
    (void)fcbus_core_heartbeat(&g_c, PPU_COUNT_VAL_V1);
    uint32_t frames = fcbus_core_stats(&g_c)->frames;
    drain_actions();

    fcbus_core_rx_byte(&g_c, FP_COM_ROM);
    fcbus_core_set_read_count(&g_c, PPU_COUNT_VAL_V1);
    fcbus_core_rx_byte(&g_c, KEY_A); /* the page number, not a controller byte */
    CHECK_EQ(fcbus_core_stats(&g_c)->frames, frames); /* no heartbeat */
    CHECK_EQ(count_actions(FCBUS_ACT_STREAM_RESPONSE), 1);

    fcbus_core_rx_byte(&g_c, FP_COM_INI);
    fcbus_core_rx_byte(&g_c, FP_COM_VER); /* the stage number */
    CHECK_EQ(count_actions(FCBUS_ACT_INIT), 1);
    CHECK_EQ(fcbus_core_stats(&g_c)->frames, frames);
}

static void test_version_response(void) {
    for (int i = 0; i < 16; i++) {
        g_rom[FCBUS_ROM_STAMP_OFF + i] = (uint8_t)("DOOM-02-000001"[i % 14]);
    }
    init_core(FCBUS_PROTO_V1);
    fcbus_core_rx_byte(&g_c, FP_COM_VER);

    fcbus_action_t act;
    CHECK(fcbus_core_pop_action(&g_c, &act));
    CHECK_EQ(act.kind, FCBUS_ACT_STREAM_RESPONSE);
    CHECK_EQ(act.len, 8 + 16);
    /* The sync word rp_system::ver_dma() pushes before the stamp: the fix bank's
     * CHK_ROMVER skips bytes until it sees 'C' (0x43), which is the last of these. */
    static const uint8_t sync[8] = {0x21, 0x21, 0x21, 0x21, 0x21, 0x23, 0x46, 0x43};
    CHECK_MEM(act.data, sync, sizeof sync);
    CHECK_MEM(act.data + 8, g_rom + FCBUS_ROM_STAMP_OFF, 16);
}

static void test_rom_page_response(void) {
    for (int i = 0; i < FCBUS_ROM_PRG_BYTES; i++) {
        g_rom[i] = (uint8_t)(i >> 8);
    }
    init_core(FCBUS_PROTO_V1);

    /* Page numbers are masked to 7 bits, as rp_system::rom_dma() does: the console only
     * ever asks for pages $80..$EF of CPU space, which map to PRG offsets $00..$6F. */
    const uint8_t pages[] = {0x00, 0x01, 0x6F, 0x80, 0xEF};
    for (size_t i = 0; i < sizeof pages / sizeof pages[0]; i++) {
        fcbus_core_rx_byte(&g_c, FP_COM_ROM);
        fcbus_core_rx_byte(&g_c, pages[i]);
        fcbus_action_t act;
        CHECK(fcbus_core_pop_action(&g_c, &act));
        CHECK_EQ(act.kind, FCBUS_ACT_STREAM_RESPONSE);
        /* ROM_UPDATE makes one dummy $2007 fetch plus 256 programming fetches.
         * DMA must still have a word queued after the page's last data byte. */
        CHECK_EQ(act.len, 260);
        CHECK_EQ(act.data[0], pages[i] & 0x7F);
        CHECK_MEM(act.data, g_rom + ((pages[i] & 0x7F) << 8), 256);
        if (act.len >= 260) {
            for (int tail = 256; tail < 260; tail++) CHECK_EQ(act.data[tail], 0xFF);
        }
    }
}

static void test_log_burst(void) {
    /* A full burst flushes itself at FCBUS_LOG_MAX bytes. */
    init_core(FCBUS_PROTO_V1);
    fcbus_core_rx_byte(&g_c, FP_COM_LOG);
    for (int i = 0; i < FCBUS_LOG_MAX; i++) {
        fcbus_core_rx_byte(&g_c, (uint8_t)(0xA0 + i));
    }
    fcbus_action_t act;
    CHECK(fcbus_core_pop_action(&g_c, &act));
    CHECK_EQ(act.kind, FCBUS_ACT_LOG);
    CHECK_EQ(act.len, FCBUS_LOG_MAX);
    CHECK_EQ(act.data[0], 0xA0);
    CHECK_EQ(act.data[FCBUS_LOG_MAX - 1], 0xA0 + FCBUS_LOG_MAX - 1);
    /* The dispatcher is back to normal: the next byte is a controller byte again. */
    fcbus_core_set_read_count(&g_c, PPU_COUNT_VAL_V1);
    uint32_t frames = fcbus_core_stats(&g_c)->frames;
    fcbus_core_rx_byte(&g_c, KEY_B);
    CHECK_EQ(fcbus_core_stats(&g_c)->frames, frames + 1);

    /* A short burst is flushed when the receive FIFO goes empty. setErrorLog() in
     * SysPico.asm sends exactly three bytes: the opcode, $EA and an error code. */
    init_core(FCBUS_PROTO_V1);
    fcbus_core_rx_byte(&g_c, FP_COM_LOG);
    fcbus_core_rx_byte(&g_c, 0xEA);
    fcbus_core_rx_byte(&g_c, 0x01);
    CHECK(!fcbus_core_pop_action(&g_c, &act)); /* nothing yet */
    fcbus_core_rx_idle(&g_c);
    CHECK(fcbus_core_pop_action(&g_c, &act));
    CHECK_EQ(act.kind, FCBUS_ACT_LOG);
    CHECK_EQ(act.len, 2);
    CHECK_EQ(act.data[0], 0xEA);
    CHECK_EQ(act.data[1], 0x01);
    /* Idle with no burst in progress does nothing. */
    fcbus_core_rx_idle(&g_c);
    CHECK(!fcbus_core_pop_action(&g_c, &act));
}

static void test_init_and_reset(void) {
    init_core(FCBUS_PROTO_V2);
    /* Dirty the state that FP_COM_INI must clear. */
    uint8_t attr[MBX_ATTR_LEN];
    memset(attr, 0x55, sizeof attr);
    fcbus_core_attr_table(&g_c, attr);
    fcbus_core_set_read_count(&g_c, PPU_COUNT_VAL_V2);
    fcbus_core_rx_byte(&g_c, FP_COM_KEY);
    fcbus_core_rx_byte(&g_c, KEY_A);
    fcbus_core_rx_byte(&g_c, KEY_B);
    fcbus_core_publish(&g_c);
    CHECK(!fcbus_core_back_is_free(&g_c));
    drain_actions();

    fcbus_core_rx_byte(&g_c, FP_COM_INI);
    fcbus_core_rx_byte(&g_c, 7);
    fcbus_action_t act;
    CHECK(fcbus_core_pop_action(&g_c, &act));
    CHECK_EQ(act.kind, FCBUS_ACT_INIT);
    CHECK_EQ(act.arg, 7);
    CHECK_EQ(fcbus_core_stats(&g_c)->init_events, 1);
    CHECK_EQ(fcbus_core_stats(&g_c)->last_init_stage, 7);
    CHECK_EQ(g_c.state, FCBUS_ST_INIT);
    CHECK_EQ(g_c.proto, FCBUS_PROTO_UNKNOWN); /* re-identified from the next packet */
    CHECK_EQ(fcbus_core_pads(&g_c), 0);
    CHECK(fcbus_core_back_is_free(&g_c)); /* the pending publish was dropped */
    CHECK_EQ(fcbus_core_mailbox_next(&g_c)[MBX_MAGIC], PF_MAGIC_NO);
    CHECK_EQ(fcbus_core_mailbox_next(&g_c)[MBX_ATTR], 0);

    /* FP_COM_RST takes the link back to idle and asks the backend to restart. */
    fcbus_core_rx_byte(&g_c, FP_COM_RST);
    CHECK(fcbus_core_pop_action(&g_c, &act));
    CHECK_EQ(act.kind, FCBUS_ACT_RESET);
    CHECK_EQ(g_c.state, FCBUS_ST_IDLE);
}

int main(void) {
    test_every_byte_value_v1();
    test_every_byte_value_v2();
    test_key_packet();
    test_read_counter_sampled_only_on_heartbeat();
    test_protocol_identification();
    test_opcode_arguments_are_not_reinterpreted();
    test_version_response();
    test_rom_page_response();
    test_log_burst();
    test_init_and_reset();
    return ctest_lite_result();
}
