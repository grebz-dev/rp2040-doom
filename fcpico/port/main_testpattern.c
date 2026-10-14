/* SPDX-License-Identifier: BSD-3-Clause */
#include <stdio.h>

#include "fcbus_device.h"
#include "hardware/flash.h"
#include "pico/stdlib.h"
#include "pico/unique_id.h"
#include "cli.h"
#include "flash_layout.h"
#include "testpattern.h"
#include "trace.h"

extern const unsigned char fcpico_bootrom[];
extern const int fcpico_bootrom_length;

static fcbus_device_t bus;
static fcpico_testpattern_t pattern_app;
static volatile uint32_t selected_pattern;

int main(void) {
    stdio_init_all();
    gpio_init(PICO_DEFAULT_LED_PIN);
    gpio_set_dir(PICO_DEFAULT_LED_PIN, GPIO_OUT);

    char board_id[PICO_UNIQUE_BOARD_ID_SIZE_BYTES * 2 + 1];
    pico_get_unique_board_id_string(board_id, sizeof board_id);
    uint32_t flash_size = flash_devinfo_size_to_bytes(flash_devinfo_get_cs_size(0));
    printf("FC PICO test pattern\nUnique ID: %s\nflash=%u expected=%u\n", board_id,
           (unsigned)flash_size, (unsigned)FLASH_TOTAL_BYTES);
    if (fcpico_bootrom_length != FCBUS_ROM_INES_HDR + FCBUS_ROM_PRG_BYTES) {
        panic("invalid tutorial boot ROM length");
    }

    fcbus_config_t config = {
        .rom_image = fcpico_bootrom + FCBUS_ROM_INES_HDR,
        .proto_default = FCBUS_PROTO_V1,
    };
    fcbus_device_init(&bus, &config);
    fcpico_testpattern_init(&pattern_app, &bus.core);
    bool trace_available = trace_init();
    printf("trace=%s\n", trace_available ? "ready" : "unavailable");

    cli_t cli;
    cli_init(&cli, &bus, &selected_pattern);
    uint32_t handled_init_events = 0;
    while (true) {
        fcbus_device_poll(&bus);
        const fcbus_stats_t *stats = fcbus_device_stats(&bus);
        bool published = false;
        if (stats->init_events != handled_init_events) {
            published = fcpico_testpattern_console_init(
                &pattern_app, stats->last_init_stage, selected_pattern);
            if (published) {
                handled_init_events = stats->init_events;
            }
        } else {
            published = fcpico_testpattern_poll(&pattern_app, selected_pattern);
        }
        if (published) {
            uint32_t frame = stats->frames;
            gpio_put(PICO_DEFAULT_LED_PIN, (frame >> 4) & 1u);
        }
        cli_poll(&cli);
    }
}
