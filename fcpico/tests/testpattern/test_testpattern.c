/* SPDX-License-Identifier: BSD-3-Clause */
#include "testpattern.h"

#include <string.h>

#include "ctest_lite.h"

static uint8_t g_rom[FCBUS_ROM_PRG_BYTES];

static void test_console_init_publishes_pattern_and_requests_data_mode(void) {
    fcbus_core_t core;
    fcbus_config_t config = {
        .rom_image = g_rom,
        .proto_default = FCBUS_PROTO_V1,
    };
    fcbus_core_init(&core, &config);

    fcpico_testpattern_t app;
    fcpico_testpattern_init(&app, &core);

    /* The console's init packet clears any frame prepared before AplGame starts. */
    fcbus_core_rx_byte(&core, FP_COM_INI);
    fcbus_core_rx_byte(&core, 0);
    CHECK(fcpico_testpattern_console_init(&app, 0, 0));
    CHECK(!fcbus_core_back_is_free(&core));
    CHECK_EQ(fcbus_core_mailbox_next(&core)[MBX_CMD], PF_COM_DMOD);

    (void)fcbus_core_heartbeat(&core, PPU_COUNT_VAL_V1);
    const uint16_t *front = fcbus_core_stream_front(&core);
    CHECK_EQ(front[VRAM_HEAD_WORDS + 5], 0x00ff);
    CHECK_EQ(front[VRAM_HEAD_WORDS + 9], 0xff00);
    CHECK_EQ(front[VRAM_HEAD_WORDS + 13], 0xffff);

    const uint8_t *bytes = (const uint8_t *)front;
    CHECK_EQ(bytes[VRAM_MAILBOX_OFF_V1 + MBX_CMD], PF_COM_DMOD);
}

int main(void) {
    test_console_init_publishes_pattern_and_requests_data_mode();
    return ctest_lite_result();
}
