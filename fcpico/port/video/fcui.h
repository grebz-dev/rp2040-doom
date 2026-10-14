/* SPDX-License-Identifier: BSD-3-Clause */
#ifndef FCUI_H
#define FCUI_H

#include <stdbool.h>
#include <stdint.h>

#include "fcbus_protocol.h"

/* V3's compact status payload. In v4 menu mode, flags bits 4..6 carry the
 * selected item and ready_weapon bits 4..7 carry the native menu id. The
 * low nibble remains the selected weapon. Ammo is capped at 999. Byte 15
 * checks the preceding 15 bytes. */
typedef struct {
    uint8_t generation;
    uint8_t flags;
    uint8_t face;
    uint8_t keys;
    uint8_t weapons;
    uint8_t ready_weapon;
    uint16_t health;
    uint16_t armor;
    uint16_t ammo[4];
    /* Host renderer only; the 16-byte v4 packet retains current values. */
    uint16_t maxammo[4];
} fcui_status_t;

#define FCUI_FLAG_STATUS_VISIBLE 0x01
#define FCUI_FLAG_AUTOMAP        0x02
#define FCUI_FLAG_MENU           0x04

void fcui_pack_status(uint8_t out[MBX_UI_LEN], const fcui_status_t *status);
bool fcui_unpack_status(fcui_status_t *status, const uint8_t data[MBX_UI_LEN]);
/* Fixed v4 bottom row: four black backing tiles under the face while the
 * status is visible. Returns spaces outside gameplay. */
void fcui_format_ammo_row(uint8_t out[NATIVE_TEXT_TILES],
                          const fcui_status_t *status);

#endif
