/* SPDX-License-Identifier: BSD-3-Clause */
#include "cli.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "hardware/watchdog.h"
#include "pico/bootrom.h"
#include "pico/stdlib.h"
#include "trace.h"

static void dump_bytes(const char *name, const uint8_t *data, size_t length) {
    printf("%s (%u bytes):\n", name, (unsigned)length);
    for (size_t index = 0; index < length; ++index) {
        printf("%02x%c", data[index], (index & 15u) == 15u ? '\n' : ' ');
    }
    if ((length & 15u) != 0u) {
        putchar('\n');
    }
}

static void print_stats(cli_t *cli) {
    const fcbus_stats_t *stats = fcbus_device_stats(cli->bus);
    uint64_t elapsed = time_us_64() - cli->started_us;
    uint32_t frames = stats->frames - cli->started_frames;
    printf("fps=%.2f conversion_us=0/0/0 ppu_count=%lu histogram=",
           elapsed ? (double)frames * 1000000.0 / (double)elapsed : 0.0,
           (unsigned long)stats->last_count);
    for (unsigned index = 0; index < 9; ++index) {
        printf("%s%u", index ? "," : "", stats->count_hist[index]);
    }
    printf(" resyncs=%lu timeouts=%lu dma_stops=%lu inits=%lu init_stage=%u"
           " apu_drops=0 free_zone=0\n",
           (unsigned long)stats->resyncs, (unsigned long)stats->hb_timeouts,
           (unsigned long)stats->dma_stops, (unsigned long)stats->init_events,
           stats->last_init_stage);
}

static void run_command(cli_t *cli, char *line) {
    if (strcmp(line, "stats") == 0) {
        print_stats(cli);
    } else if (strncmp(line, "pattern ", 8) == 0) {
        char *end;
        unsigned long value = strtoul(line + 8, &end, 0);
        if (*end != '\0' || value > 3) {
            puts("usage: pattern 0..3");
        } else {
            *cli->pattern = (uint32_t)value;
            printf("pattern=%lu\n", value);
        }
    } else if (strcmp(line, "dump attr") == 0) {
        dump_bytes("attr", fcbus_device_attributes(cli->bus), 64);
    } else if (strcmp(line, "dump pal") == 0) {
        dump_bytes("pal", fcbus_device_palette(cli->bus), 16);
    } else if (strcmp(line, "dump mailbox") == 0) {
        dump_bytes("mailbox", fcbus_device_mailbox(cli->bus), FC_COM_BUF_SIZE_V2);
    } else if (strcmp(line, "trace") == 0) {
        uint32_t frame = fcbus_device_stats(cli->bus)->frames;
        absolute_time_t timeout = make_timeout_time_ms(1000);
        while (fcbus_device_stats(cli->bus)->frames == frame &&
               !time_reached(timeout)) {
            tight_loop_contents();
        }
        if (trace_capture()) {
            trace_dump();
        } else {
            puts("trace unavailable");
        }
    } else if (strcmp(line, "reboot") == 0) {
        watchdog_reboot(0, 0, 0);
    } else if (strcmp(line, "bootsel") == 0) {
        reset_usb_boot(0, 0);
    } else if (*line != '\0') {
        puts("commands: stats, pattern N, trace, dump attr|pal|mailbox, reboot, bootsel");
    }
}

void cli_init(cli_t *cli, fcbus_device_t *bus, volatile uint32_t *pattern) {
    cli->bus = bus;
    cli->pattern = pattern;
    cli->started_us = time_us_64();
    cli->started_frames = fcbus_device_stats(bus)->frames;
}

void cli_poll(cli_t *cli) {
    static char line[80];
    static size_t length;
    int ch;
    while ((ch = getchar_timeout_us(0)) != PICO_ERROR_TIMEOUT) {
        if (ch == '\r' || ch == '\n') {
            line[length] = '\0';
            run_command(cli, line);
            length = 0;
        } else if (ch == '\b' || ch == 127) {
            if (length != 0) {
                --length;
            }
        } else if (ch >= 32 && ch < 127 && length + 1 < sizeof line) {
            line[length++] = (char)ch;
        }
    }
}
