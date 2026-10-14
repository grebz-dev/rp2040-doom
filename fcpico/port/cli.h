/* SPDX-License-Identifier: BSD-3-Clause */
#ifndef FCPICO_CLI_H
#define FCPICO_CLI_H

#include <stdint.h>

#include "fcbus_device.h"

typedef struct {
    fcbus_device_t *bus;
    volatile uint32_t *pattern;
    uint64_t started_us;
    uint32_t started_frames;
} cli_t;

void cli_init(cli_t *cli, fcbus_device_t *bus, volatile uint32_t *pattern);
void cli_poll(cli_t *cli);

#endif
