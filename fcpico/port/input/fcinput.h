/* SPDX-License-Identifier: BSD-3-Clause */
#ifndef FCINPUT_H
#define FCINPUT_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

enum {
    FCINPUT_PAD_RIGHT = 0x01,
    FCINPUT_PAD_LEFT = 0x02,
    FCINPUT_PAD_DOWN = 0x04,
    FCINPUT_PAD_UP = 0x08,
    FCINPUT_PAD_START = 0x10,
    FCINPUT_PAD_SELECT = 0x20,
    FCINPUT_PAD_B = 0x40,
    FCINPUT_PAD_A = 0x80
};

typedef enum {
    FCINPUT_KEY_UP = 0,
    FCINPUT_KEY_DOWN,
    FCINPUT_KEY_LEFT,
    FCINPUT_KEY_RIGHT,
    FCINPUT_KEY_STRAFE_LEFT,
    FCINPUT_KEY_STRAFE_RIGHT,
    FCINPUT_KEY_FIRE,
    FCINPUT_KEY_USE,
    FCINPUT_KEY_NEXT_WEAPON,
    FCINPUT_KEY_AUTOMAP,
    FCINPUT_KEY_MENU,
    FCINPUT_KEY_PAUSE,
    FCINPUT_KEY_SPEED,
    FCINPUT_KEY_COUNT
} fcinput_key_slot_t;

typedef struct {
    int code[FCINPUT_KEY_COUNT];
    bool always_run;
} fcinput_config_t;

typedef struct {
    int key;
    bool down;
} fcinput_event_t;

typedef struct {
    fcinput_event_t *events;
    size_t capacity;
    size_t read_pos;
    size_t count;
    uint32_t dropped;
} fcinput_event_ring_t;

typedef struct {
    fcinput_config_t config;
    uint8_t frame_pad;
    uint8_t sticky_pressed;
    uint8_t logical_pad;
    uint8_t b_frames;
    uint8_t select_frames;
    bool b_dpad_used;
    bool select_hold_consumed;
    bool pending_automap;
    bool pending_use;
    bool pending_next_weapon;
    bool pending_pause;
    uint32_t logical_keys;
#ifdef FCINPUT_ENABLE_CHEATS
    uint8_t cheat_buttons[16];
    uint8_t cheat_length;
    bool swallow_select_release;
#endif
} fcinput_t;

void fcinput_ring_init(fcinput_event_ring_t *ring, fcinput_event_t *storage, size_t capacity);
bool fcinput_ring_pop(fcinput_event_ring_t *ring, fcinput_event_t *event);
void fcinput_init(fcinput_t *input, const fcinput_config_t *config);
void fcinput_latch_frame(fcinput_t *input, uint8_t pad1, uint8_t pad2);
void fcinput_poll(fcinput_t *input, fcinput_event_ring_t *ring);

#endif
