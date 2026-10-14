/* SPDX-License-Identifier: BSD-3-Clause */
#include "fcui.h"
#include "ctest_lite.h"

#include <string.h>

static void test_status_packet(void) {
    fcui_status_t state = {
        .generation = 255, .flags = FCUI_FLAG_STATUS_VISIBLE | FCUI_FLAG_AUTOMAP,
        .face = 17, .keys = 0x35, .weapons = 0x7f, .ready_weapon = 6,
        .health = 200, .armor = 999, .ammo = {400, 100, 50, 600},
    };
    uint8_t bytes[MBX_UI_LEN];
    fcui_pack_status(bytes, &state);
    fcui_status_t restored = {0};
    CHECK(fcui_unpack_status(&restored, bytes));
    CHECK_EQ(restored.generation, state.generation);
    CHECK_EQ(restored.flags, state.flags);
    CHECK_EQ(restored.face, state.face);
    CHECK_EQ(restored.keys, state.keys);
    CHECK_EQ(restored.weapons, state.weapons);
    CHECK_EQ(restored.ready_weapon, state.ready_weapon);
    CHECK_EQ(restored.health, state.health);
    CHECK_EQ(restored.armor, state.armor);
    for (int i = 0; i < 4; ++i) CHECK_EQ(restored.ammo[i], state.ammo[i]);
    bytes[12] ^= 0x01;
    CHECK(!fcui_unpack_status(&restored, bytes));
}

static void test_value_bounds(void) {
    fcui_status_t state = {.health = 65535, .armor = 1000,
                           .ammo = {0, 1, 999, 1023}};
    uint8_t bytes[MBX_UI_LEN];
    fcui_pack_status(bytes, &state);
    fcui_status_t restored = {0};
    CHECK(fcui_unpack_status(&restored, bytes));
    CHECK_EQ(restored.health, 999);
    CHECK_EQ(restored.armor, 999);
    CHECK_EQ(restored.ammo[0], 0);
    CHECK_EQ(restored.ammo[1], 1);
    CHECK_EQ(restored.ammo[2], 999);
    CHECK_EQ(restored.ammo[3], 999);
}

static void test_ammo_row(void) {
    fcui_status_t state = {
        .flags = FCUI_FLAG_STATUS_VISIBLE,
        .ammo = {50, 8, 300, 4},
    };
    uint8_t row[NATIVE_TEXT_TILES];
    fcui_format_ammo_row(row, &state);
    CHECK_MEM(row, "            ####            ", NATIVE_TEXT_TILES);
    state.flags |= FCUI_FLAG_MENU;
    fcui_format_ammo_row(row, &state);
    CHECK_MEM(row, "            ####            ", NATIVE_TEXT_TILES);
}

int main(void) {
    test_status_packet();
    test_value_bounds();
    test_ammo_row();
    return ctest_lite_result();
}
