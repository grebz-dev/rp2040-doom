/* SPDX-License-Identifier: BSD-3-Clause */
#ifndef FCINPUT_CHEATS_H
#define FCINPUT_CHEATS_H

#include <stddef.h>
#include <stdint.h>

typedef struct {
    const uint8_t *buttons;
    uint8_t button_count;
    const char *text;
} fcinput_cheat_t;

static const uint8_t fcinput_cheat_god_buttons[] = {
    0x08, 0x08, 0x04, 0x04, 0x02, 0x01, 0x80
};

static const fcinput_cheat_t fcinput_cheats[] = {
    {fcinput_cheat_god_buttons, sizeof(fcinput_cheat_god_buttons), "iddqd"},
};

#define FCINPUT_CHEAT_COUNT (sizeof(fcinput_cheats) / sizeof(fcinput_cheats[0]))

#endif
