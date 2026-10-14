/* SPDX-License-Identifier: BSD-3-Clause */
#include "fcui.h"

#include <string.h>

static uint16_t cap999(uint16_t value) { return value > 999 ? 999 : value; }

void fcui_pack_status(uint8_t out[MBX_UI_LEN], const fcui_status_t *status) {
    memset(out, 0, MBX_UI_LEN);
    out[0] = status->generation;
    out[1] = status->flags;
    out[2] = status->face;
    out[3] = status->keys;
    out[4] = status->weapons;
    out[5] = status->ready_weapon;
    uint16_t health = cap999(status->health);
    uint16_t armor = cap999(status->armor);
    out[6] = (uint8_t)health;
    out[7] = (uint8_t)(health >> 8);
    out[8] = (uint8_t)armor;
    out[9] = (uint8_t)(armor >> 8);
    for (int i = 0; i < 4; ++i) {
        uint16_t value = cap999(status->ammo[i]);
        unsigned bit = (unsigned)i * 10u;
        unsigned byte = bit / 8u;
        unsigned shift = bit % 8u;
        out[10 + byte] |= (uint8_t)(value << shift);
        out[11 + byte] |= (uint8_t)(value >> (8u - shift));
        if (shift > 6u) out[12 + byte] |= (uint8_t)(value >> (16u - shift));
    }
    uint8_t check = 0xA5;
    for (int i = 0; i < 15; ++i) check ^= out[i];
    out[15] = check;
}

bool fcui_unpack_status(fcui_status_t *status, const uint8_t data[MBX_UI_LEN]) {
    uint8_t check = 0xA5;
    for (int i = 0; i < MBX_UI_LEN; ++i) check ^= data[i];
    if (check != 0) return false;
    status->generation = data[0];
    status->flags = data[1];
    status->face = data[2];
    status->keys = data[3];
    status->weapons = data[4];
    status->ready_weapon = data[5];
    status->health = (uint16_t)data[6] | (uint16_t)data[7] << 8;
    status->armor = (uint16_t)data[8] | (uint16_t)data[9] << 8;
    for (int i = 0; i < 4; ++i) {
        unsigned bit = (unsigned)i * 10u;
        unsigned byte = bit / 8u;
        unsigned shift = bit % 8u;
        uint32_t word = (uint32_t)data[10 + byte] |
                        (uint32_t)data[11 + byte] << 8;
        if (byte + 2u < 5u) word |= (uint32_t)data[12 + byte] << 16;
        status->ammo[i] = (uint16_t)((word >> shift) & 0x3FFu);
    }
    return true;
}

void fcui_format_ammo_row(uint8_t out[NATIVE_TEXT_TILES],
                          const fcui_status_t *status) {
    memset(out, ' ', NATIVE_TEXT_TILES);
    if (!(status->flags & FCUI_FLAG_STATUS_VISIBLE)) return;
    (void)status;
    memset(out + 12, '#', 4); /* Black backing beneath the face. */
}
