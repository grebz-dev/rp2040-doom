/*
 * test_cartmodel.c -- integration tests for the bounded Mesen cartridge adapter.
 *
 * SPDX-License-Identifier: BSD-3-Clause
 */
#include <string.h>

#include "ctest_lite.h"
#include "fcbus_host.h"
#include "fcpico_cart.h"

static uint8_t g_prg[FCBUS_ROM_PRG_BYTES];

static void init_cart(void) {
    memset(g_prg, 0, sizeof g_prg);
    CHECK(fcpico_cart_init(g_prg, sizeof g_prg));
}

static void read_n(size_t count) {
    for (size_t i = 0; i < count; ++i) {
        (void)fcpico_cart_ppu_read();
    }
}

static void beat_in_phase(void) {
    read_n(PPU_COUNT_VAL_V1 + PPU_COUNTER_REPORT_BIAS);
    fcpico_cart_ppu_write(0);
}

static void start_tutorial_scene(void) {
    fcpico_cart_ppu_write(FP_COM_INI);
    fcpico_cart_ppu_write(0);
}

static void start_tutorial_scene_at(uint8_t stage) {
    fcpico_cart_ppu_write(FP_COM_INI);
    fcpico_cart_ppu_write(stage);
}

static void test_input_validation_and_open_bus(void) {
    fcpico_cart_shutdown();
    CHECK(!fcpico_cart_init(NULL, sizeof g_prg));
    CHECK(!fcpico_cart_init(g_prg, sizeof g_prg - 1));
    CHECK(fcpico_cart_stats() == NULL);
    CHECK_EQ(fcpico_cart_ppu_read(), 0xff);

    init_cart();
    CHECK_EQ(fcpico_cart_ppu_read(), 0xff);
    CHECK_EQ(fcpico_cart_metrics()->open_bus_reads, 1);
    CHECK_EQ(fcpico_cart_metrics()->ppu_reads, 1);
}

static void test_init_uses_the_tutorial_bulk_upload_protocol(void) {
    static const uint8_t palette[MBX_PAL_LEN] = {
        0x0f, 0x00, 0x10, 0x20, 0x0f, 0x06, 0x16, 0x26,
        0x0f, 0x09, 0x19, 0x29, 0x0f, 0x01, 0x21, 0x31,
    };
    uint8_t header[8];

    init_cart();
    start_tutorial_scene();
    CHECK_EQ(fcpico_cart_metrics()->init_actions, 1);
    CHECK_EQ(fcpico_cart_metrics()->last_init_stage, 0);
    CHECK(fcbus_host_core()->publish_pending);
    CHECK(fcbus_host_core()->data_mode_requested);

    /* The exact frame count is supplied by the host backend; this test does
     * not manufacture a passing count or alter its state. */
    beat_in_phase();
    CHECK_EQ(fcpico_cart_stats()->frames, 1);
    const uint8_t *front = (const uint8_t *)fcbus_core_stream_front(fcbus_host_core());
    CHECK_EQ(front[VRAM_MAILBOX_OFF_V1 + MBX_CMD], PF_COM_DMOD);

    /* The tutorial sees PF_COM_DMOD then emits DRQ/DLD.  The adapter uses the
     * same host dispatcher, so the palette header and full page are real wire
     * responses rather than a direct mutation of PPU memory. */
    fcpico_cart_ppu_write(FP_COM_DRQ);
    for (size_t i = 0; i < sizeof header; ++i) {
        header[i] = fcpico_cart_ppu_read();
    }
    CHECK_EQ(header[0], PF_DAT_VRAM);
    CHECK_EQ(header[1], PF_MAGIC_NO);
    CHECK_EQ(header[2], 0x00);
    CHECK_EQ(header[3], 0x3f);
    CHECK_EQ(header[4], 32);
    CHECK_EQ(header[5], 0);

    fcpico_cart_ppu_write(FP_COM_DLD);
    fcpico_cart_ppu_write(0);
    for (size_t i = 0; i < MBX_PAL_LEN; ++i) {
        CHECK_EQ(fcpico_cart_ppu_read(), palette[i]);
    }
    for (size_t i = MBX_PAL_LEN; i < 32; ++i) {
        /* The second half is uploaded too. It must mirror the first half,
         * since $3F10/$14/$18/$1C alias the universal BG entries. */
        CHECK_EQ(fcpico_cart_ppu_read(), palette[i - MBX_PAL_LEN]);
    }

    fcpico_cart_ppu_write(FP_COM_DRQ);
    for (size_t i = 0; i < sizeof header; ++i) {
        header[i] = fcpico_cart_ppu_read();
    }
    CHECK_EQ(header[0], PF_DAT_VRAM);
    CHECK_EQ(header[1], PF_MAGIC_NO);
    CHECK_EQ(header[2], 0xc0);
    CHECK_EQ(header[3], 0x23);
    CHECK_EQ(header[4], MBX_ATTR_LEN);

    fcpico_cart_ppu_write(FP_COM_DLD);
    fcpico_cart_ppu_write(0);
    for (size_t i = 0; i < MBX_ATTR_LEN; ++i) {
        CHECK_EQ(fcpico_cart_ppu_read(), (i & 1u) ? 0x1b : 0xe4);
    }
}

static void test_pattern_and_stats_come_from_the_host_backend(void) {
    init_cart();
    start_tutorial_scene();
    beat_in_phase();

    /* fcbus_host contributes the four OSR bytes; the first selected stripe
     * follows at line 0, tile 5 (x = 40), as in main_testpattern.c. */
    read_n(VRAM_HEAD_WORDS * sizeof(uint16_t) + 5 * sizeof(uint16_t));
    CHECK_EQ(fcpico_cart_ppu_read(), 0xff);
    CHECK_EQ(fcpico_cart_ppu_read(), 0x00);
    CHECK_EQ(fcpico_cart_stats()->last_count, PPU_COUNT_VAL_V1);
    CHECK(fcpico_cart_metrics()->pattern_frames >= 2);
}

static void test_tick_forwards_the_heartbeat_watchdog(void) {
    init_cart();
    start_tutorial_scene();
    beat_in_phase();
    CHECK(fcpico_cart_stats()->frames == 1);

    fcpico_cart_tick_ms(1000);
    CHECK_EQ(fcpico_cart_stats()->hb_timeouts, 0);
    fcpico_cart_tick_ms(3001);
    CHECK_EQ(fcpico_cart_stats()->hb_timeouts, 1);
    CHECK_EQ(fcpico_cart_ppu_read(), 0xff);
    CHECK(fcpico_cart_metrics()->open_bus_reads > 0);
}

static void test_literal_ff_payloads_are_not_init_actions(void) {
    /* The cartridge copies the PRG at init, like firmware with a compiled-in image. */
    memset(g_prg, 0, sizeof g_prg);
    g_prg[0x7f00] = 0x5a;
    CHECK(fcpico_cart_init(g_prg, sizeof g_prg));
    start_tutorial_scene_at(0xff);
    CHECK_EQ(fcpico_cart_metrics()->init_actions, 1);

    /* $FF is a legal ROM-page selector (masked to page $7f by fcbus_core),
     * not an init opcode while FP_COM_ROM awaits its page argument.  Follow it
     * with a new FP_COM_INI opcode without its stage: this catches an adapter
     * that carries stale INIT state across payload bytes. */
    fcpico_cart_ppu_write(FP_COM_ROM);
    fcpico_cart_ppu_write(0xff);
    CHECK_EQ(fcpico_cart_metrics()->init_actions, 1);
    CHECK_EQ(fcpico_cart_ppu_read(), 0x5a);
    fcpico_cart_ppu_write(FP_COM_INI);
    CHECK_EQ(fcpico_cart_metrics()->init_actions, 1);

    /* Complete that real init, then put both FP_COM_INI and $FF in an active
     * log payload.  Neither payload byte may manufacture another INIT action. */
    fcpico_cart_ppu_write(1);
    CHECK_EQ(fcpico_cart_metrics()->init_actions, 2);
    fcpico_cart_ppu_write(FP_COM_LOG);
    fcpico_cart_ppu_write(FP_COM_INI);
    fcpico_cart_ppu_write(0xff);
    CHECK_EQ(fcpico_cart_metrics()->init_actions, 2);
}

static void test_reset_recreates_the_host_link(void) {
    init_cart();
    start_tutorial_scene();
    beat_in_phase();
    CHECK_EQ(fcpico_cart_stats()->frames, 1);
    CHECK_EQ(fcpico_cart_metrics()->init_actions, 1);

    fcpico_cart_ppu_write(FP_COM_RST);
    CHECK_EQ(fcbus_host_core()->state, FCBUS_ST_IDLE);
    CHECK_EQ(fcpico_cart_stats()->frames, 0);
    CHECK_EQ(fcpico_cart_metrics()->ppu_writes, 0);
    CHECK_EQ(fcpico_cart_metrics()->init_actions, 0);
    CHECK_EQ(fcpico_cart_ppu_read(), 0xff);
    CHECK_EQ(fcpico_cart_metrics()->open_bus_reads, 1);
}

int main(void) {
    test_input_validation_and_open_bus();
    test_init_uses_the_tutorial_bulk_upload_protocol();
    test_pattern_and_stats_come_from_the_host_backend();
    test_tick_forwards_the_heartbeat_watchdog();
    test_literal_ff_payloads_are_not_init_actions();
    test_reset_recreates_the_host_link();
    fcpico_cart_shutdown();
    return ctest_lite_result();
}
