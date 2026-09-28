/* SPDX-License-Identifier: GPL-2.0-or-later */
#include "ui_capture.h"

#include "doom/d_items.h"
#include "doom/doomstat.h"
#include "doom/m_menu.h"
#include "doom/st_stuff.h"

#include <string.h>

static uint16_t count_value(int value) {
    if (value <= 0) return 0;
    return value >= 999 ? 999 : (uint16_t)value;
}

void fcpico_ui_capture(fcui_status_t *status, uint8_t generation) {
    memset(status, 0, sizeof *status);
    status->generation = generation;
    if (menuactive) {
        status->flags |= FCUI_FLAG_MENU;
        status->flags |= (uint8_t)((M_NativeMenuSelection() & 7) << 4);
        status->ready_weapon = (uint8_t)(M_NativeMenuId() << 4);
    }
    if (gamestate != GS_LEVEL) return;
    const player_t *player = &players[consoleplayer];
    if (ST_NativeStatusVisible()) status->flags |= FCUI_FLAG_STATUS_VISIBLE;
    if (automapactive) status->flags |= FCUI_FLAG_AUTOMAP;
    int face = ST_NativeFaceIndex();
    if (face >= 0 && face < 40 && face % 8 == 7 && player->damagecount > 0) {
        face -= 2;  // head-on hit uses the resident ouch expression
    }
    status->face = (uint8_t)(face < 0 ? 0 : face);
    status->ready_weapon |= (uint8_t)player->readyweapon;
    for (int i = 0; i < NUMCARDS && i < 8; ++i) {
        if (player->cards[i]) status->keys |= (uint8_t)(1u << i);
    }
    for (int i = 1; i < NUMWEAPONS && i <= 8; ++i) {
        if (player->weaponowned[i]) status->weapons |= (uint8_t)(1u << (i - 1));
    }
    status->health = count_value(player->health);
    status->armor = count_value(player->armorpoints);
    for (int i = 0; i < NUMAMMO && i < 4; ++i) {
        status->ammo[i] = count_value(player->ammo[i]);
    }
}
