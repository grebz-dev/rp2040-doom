/* SPDX-License-Identifier: BSD-3-Clause */
#include "fcinput.h"

#include <string.h>

#ifdef FCINPUT_ENABLE_CHEATS
#include "fcinput_cheats.h"
#endif

#define FCINPUT_B_TAP_FRAMES 8u
#define FCINPUT_SELECT_HOLD_FRAMES 20u

static void post(fcinput_event_ring_t *ring, int key, bool down) {
    if (ring->count == ring->capacity) {
        ring->dropped++;
        return;
    }
    size_t write_pos = (ring->read_pos + ring->count) % ring->capacity;
    ring->events[write_pos].key = key;
    ring->events[write_pos].down = down;
    ring->count++;
}

static void pulse(fcinput_event_ring_t *ring, int key) {
    post(ring, key, true);
    post(ring, key, false);
}

void fcinput_ring_init(fcinput_event_ring_t *ring, fcinput_event_t *storage, size_t capacity) {
    ring->events = storage;
    ring->capacity = capacity;
    ring->read_pos = 0;
    ring->count = 0;
    ring->dropped = 0;
}

bool fcinput_ring_pop(fcinput_event_ring_t *ring, fcinput_event_t *event) {
    if (ring->count == 0) {
        return false;
    }
    *event = ring->events[ring->read_pos];
    ring->read_pos = (ring->read_pos + 1) % ring->capacity;
    ring->count--;
    return true;
}

void fcinput_init(fcinput_t *input, const fcinput_config_t *config) {
    memset(input, 0, sizeof(*input));
    input->config = *config;
}

#ifdef FCINPUT_ENABLE_CHEATS
static void record_cheat_button(fcinput_t *input, uint8_t button) {
    if (input->cheat_length == sizeof(input->cheat_buttons)) {
        memmove(input->cheat_buttons, input->cheat_buttons + 1,
                sizeof(input->cheat_buttons) - 1);
        input->cheat_length--;
    }
    input->cheat_buttons[input->cheat_length++] = button;
}

static const char *matched_cheat(const fcinput_t *input) {
    for (size_t i = 0; i < FCINPUT_CHEAT_COUNT; i++) {
        const fcinput_cheat_t *cheat = &fcinput_cheats[i];
        if (input->cheat_length >= cheat->button_count &&
            memcmp(input->cheat_buttons + input->cheat_length - cheat->button_count,
                   cheat->buttons, cheat->button_count) == 0) {
            return cheat->text;
        }
    }
    return NULL;
}
#endif

void fcinput_latch_frame(fcinput_t *input, uint8_t pad1, uint8_t pad2) {
    (void)pad2;
    uint8_t pressed = (uint8_t)(pad1 & (uint8_t)~input->frame_pad);
    uint8_t released = (uint8_t)(input->frame_pad & (uint8_t)~pad1);
    input->sticky_pressed |= pressed;

    if (pad1 & FCINPUT_PAD_B) {
        if (input->b_frames < UINT8_MAX) {
            input->b_frames++;
        }
        if (pad1 & (FCINPUT_PAD_UP | FCINPUT_PAD_DOWN | FCINPUT_PAD_LEFT | FCINPUT_PAD_RIGHT)) {
            input->b_dpad_used = true;
        }
    }
    if (released & FCINPUT_PAD_B) {
        if (input->b_frames <= FCINPUT_B_TAP_FRAMES && !input->b_dpad_used) {
            input->pending_use = true;
        }
        input->b_frames = 0;
        input->b_dpad_used = false;
    }

    if (pad1 & FCINPUT_PAD_SELECT) {
        if (input->select_frames < UINT8_MAX) {
            input->select_frames++;
        }
        if (input->select_frames == FCINPUT_SELECT_HOLD_FRAMES &&
            !input->select_hold_consumed) {
            input->select_hold_consumed = true;
            input->pending_automap = true;
        }
    }
    if (released & FCINPUT_PAD_SELECT) {
#ifdef FCINPUT_ENABLE_CHEATS
        if (input->swallow_select_release) {
            input->swallow_select_release = false;
        } else
#endif
        if (!input->select_hold_consumed) {
            input->pending_next_weapon = true;
        }
        input->select_frames = 0;
        input->select_hold_consumed = false;
#ifdef FCINPUT_ENABLE_CHEATS
        input->cheat_length = 0;
#endif
    }

    if ((pressed & FCINPUT_PAD_START) && (pad1 & FCINPUT_PAD_SELECT)) {
        input->pending_pause = true;
        input->sticky_pressed &= (uint8_t)~FCINPUT_PAD_START;
    }

#ifdef FCINPUT_ENABLE_CHEATS
    if (pad1 & FCINPUT_PAD_SELECT) {
        uint8_t cheat_pressed = pressed & (FCINPUT_PAD_UP | FCINPUT_PAD_DOWN |
                                            FCINPUT_PAD_LEFT | FCINPUT_PAD_RIGHT |
                                            FCINPUT_PAD_A | FCINPUT_PAD_B);
        static const uint8_t order[] = {FCINPUT_PAD_UP, FCINPUT_PAD_DOWN, FCINPUT_PAD_LEFT,
                                        FCINPUT_PAD_RIGHT, FCINPUT_PAD_A, FCINPUT_PAD_B};
        for (size_t i = 0; i < sizeof(order); i++) {
            if (cheat_pressed & order[i]) {
                record_cheat_button(input, order[i]);
            }
        }
    }
#endif
    input->frame_pad = pad1;
}

static void set_key(fcinput_t *input, fcinput_event_ring_t *ring,
                    fcinput_key_slot_t slot, bool wanted) {
    uint32_t bit = UINT32_C(1) << slot;
    bool active = (input->logical_keys & bit) != 0;
    if (wanted == active) {
        return;
    }
    post(ring, input->config.code[slot], wanted);
    if (wanted) {
        input->logical_keys |= bit;
    } else {
        input->logical_keys &= ~bit;
    }
}

void fcinput_poll(fcinput_t *input, fcinput_event_ring_t *ring) {
    uint8_t pad = input->frame_pad | input->sticky_pressed;
#ifdef FCINPUT_ENABLE_CHEATS
    if ((input->frame_pad & FCINPUT_PAD_SELECT) && input->cheat_length != 0) {
        pad &= (uint8_t)~(FCINPUT_PAD_UP | FCINPUT_PAD_DOWN | FCINPUT_PAD_LEFT |
                         FCINPUT_PAD_RIGHT | FCINPUT_PAD_A | FCINPUT_PAD_B);
    }
#endif
    bool strafe = (input->frame_pad & FCINPUT_PAD_B) != 0;
    bool moving = (pad & (FCINPUT_PAD_UP | FCINPUT_PAD_DOWN |
                          FCINPUT_PAD_LEFT | FCINPUT_PAD_RIGHT)) != 0;

    set_key(input, ring, FCINPUT_KEY_UP, (pad & FCINPUT_PAD_UP) != 0);
    set_key(input, ring, FCINPUT_KEY_DOWN, (pad & FCINPUT_PAD_DOWN) != 0);
    set_key(input, ring, FCINPUT_KEY_LEFT, !strafe && (pad & FCINPUT_PAD_LEFT) != 0);
    set_key(input, ring, FCINPUT_KEY_RIGHT, !strafe && (pad & FCINPUT_PAD_RIGHT) != 0);
    set_key(input, ring, FCINPUT_KEY_STRAFE_LEFT, strafe && (pad & FCINPUT_PAD_LEFT) != 0);
    set_key(input, ring, FCINPUT_KEY_STRAFE_RIGHT, strafe && (pad & FCINPUT_PAD_RIGHT) != 0);
    set_key(input, ring, FCINPUT_KEY_FIRE, (pad & FCINPUT_PAD_A) != 0);
    set_key(input, ring, FCINPUT_KEY_SPEED, input->config.always_run && moving);

    if (input->pending_pause) {
        pulse(ring, input->config.code[FCINPUT_KEY_PAUSE]);
        input->pending_pause = false;
    } else if (input->sticky_pressed & FCINPUT_PAD_START) {
        pulse(ring, input->config.code[FCINPUT_KEY_MENU]);
    }
    /* Doom samples key state after all events for this tic are processed.
     * Keep a B tap down until the following tic instead of posting both
     * edges in one poll. */
    set_key(input, ring, FCINPUT_KEY_USE, input->pending_use);
    input->pending_use = false;
    if (input->pending_next_weapon) {
        pulse(ring, input->config.code[FCINPUT_KEY_NEXT_WEAPON]);
        input->pending_next_weapon = false;
    }
    if (input->pending_automap) {
        pulse(ring, input->config.code[FCINPUT_KEY_AUTOMAP]);
        input->pending_automap = false;
    }

#ifdef FCINPUT_ENABLE_CHEATS
    const char *cheat = matched_cheat(input);
    if (cheat != NULL) {
        for (; *cheat != '\0'; cheat++) {
            pulse(ring, (unsigned char)*cheat);
        }
        input->swallow_select_release = true;
        input->cheat_length = 0;
    }
#endif

    input->logical_pad = input->frame_pad;
    input->sticky_pressed = 0;
}
