// SPDX-License-Identifier: BSD-3-Clause
#ifndef _BOARDS_FCPICO_H
#define _BOARDS_FCPICO_H

#define PICO_FLASH_SIZE_BYTES (4 * 1024 * 1024)
#define PICO_DEFAULT_LED_PIN 25
#define PICO_DEFAULT_LED_PIN_INVERTED 0

#define FCPICO_LED_PIN 25
#define FCPICO_USER_KEY_PIN 24
#define FCPICO_PPU_D0_PIN 6
#define FCPICO_PPU_D7_PIN 13
#define FCPICO_PPU_CS1_PIN 17
#define FCPICO_PPU_RD_PIN 20
#define FCPICO_PPU_WR_PIN 21

#include "boards/pico2.h"

#endif
