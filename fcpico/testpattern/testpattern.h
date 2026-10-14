/* SPDX-License-Identifier: BSD-3-Clause */
#ifndef FCPICO_TESTPATTERN_H
#define FCPICO_TESTPATTERN_H

#include <stdbool.h>
#include <stdint.h>

#include "fcbus_core.h"

typedef struct {
    fcbus_core_t *core;
    uint32_t last_frame;
} fcpico_testpattern_t;

void fcpico_testpattern_init(fcpico_testpattern_t *app, fcbus_core_t *core);
bool fcpico_testpattern_console_init(fcpico_testpattern_t *app, uint8_t stage,
                                     uint32_t pattern);
bool fcpico_testpattern_poll(fcpico_testpattern_t *app, uint32_t pattern);

#endif
