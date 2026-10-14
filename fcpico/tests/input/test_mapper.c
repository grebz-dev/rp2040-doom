/* SPDX-License-Identifier: BSD-3-Clause */
#include "fcinput.h"
#include "ctest_lite.h"

enum { EVENT_CAPACITY = 128 };

typedef struct {
    fcinput_t input;
    fcinput_event_t storage[EVENT_CAPACITY];
    fcinput_event_ring_t ring;
} fixture_t;

static void setup(fixture_t *f) {
    fcinput_config_t config = {.always_run = true};
    for (int i = 0; i < FCINPUT_KEY_COUNT; i++) {
        config.code[i] = 100 + i;
    }
    fcinput_init(&f->input, &config);
    fcinput_ring_init(&f->ring, f->storage, EVENT_CAPACITY);
}

static void frame(fixture_t *f, uint8_t pad) {
    fcinput_latch_frame(&f->input, pad, 0);
}

static void expect_event(fixture_t *f, fcinput_key_slot_t slot, bool down) {
    fcinput_event_t event;
    CHECK(fcinput_ring_pop(&f->ring, &event));
    CHECK_EQ(event.key, 100 + slot);
    CHECK_EQ(event.down, down);
}

static void test_direction_action_and_always_run(void) {
    fixture_t f;
    setup(&f);
    frame(&f, FCINPUT_PAD_UP | FCINPUT_PAD_A);
    fcinput_poll(&f.input, &f.ring);
    expect_event(&f, FCINPUT_KEY_UP, true);
    expect_event(&f, FCINPUT_KEY_FIRE, true);
    expect_event(&f, FCINPUT_KEY_SPEED, true);
    CHECK_EQ(f.ring.count, 0);

    frame(&f, 0);
    fcinput_poll(&f.input, &f.ring);
    expect_event(&f, FCINPUT_KEY_UP, false);
    expect_event(&f, FCINPUT_KEY_FIRE, false);
    expect_event(&f, FCINPUT_KEY_SPEED, false);
}

static void test_sticky_press_survives_tic_boundary(void) {
    fixture_t f;
    setup(&f);
    frame(&f, FCINPUT_PAD_A);
    frame(&f, 0);
    fcinput_poll(&f.input, &f.ring);
    expect_event(&f, FCINPUT_KEY_FIRE, true);
    fcinput_poll(&f.input, &f.ring);
    expect_event(&f, FCINPUT_KEY_FIRE, false);
}

static void test_b_tap_uses_and_hold_strafes(void) {
    fixture_t f;
    setup(&f);
    frame(&f, FCINPUT_PAD_B);
    frame(&f, 0);
    fcinput_poll(&f.input, &f.ring);
    expect_event(&f, FCINPUT_KEY_USE, true);
    CHECK_EQ(f.ring.count, 0);
    fcinput_poll(&f.input, &f.ring);
    expect_event(&f, FCINPUT_KEY_USE, false);

    frame(&f, FCINPUT_PAD_B | FCINPUT_PAD_LEFT);
    fcinput_poll(&f.input, &f.ring);
    expect_event(&f, FCINPUT_KEY_STRAFE_LEFT, true);
    expect_event(&f, FCINPUT_KEY_SPEED, true);
    CHECK_EQ(f.ring.count, 0);
}

static void test_select_tap_and_hold_are_distinct(void) {
    fixture_t f;
    setup(&f);
    frame(&f, FCINPUT_PAD_SELECT);
    frame(&f, 0);
    fcinput_poll(&f.input, &f.ring);
    expect_event(&f, FCINPUT_KEY_NEXT_WEAPON, true);
    expect_event(&f, FCINPUT_KEY_NEXT_WEAPON, false);

    for (int i = 0; i < 20; i++) {
        frame(&f, FCINPUT_PAD_SELECT);
    }
    fcinput_poll(&f.input, &f.ring);
    expect_event(&f, FCINPUT_KEY_AUTOMAP, true);
    expect_event(&f, FCINPUT_KEY_AUTOMAP, false);

    frame(&f, 0);
    fcinput_poll(&f.input, &f.ring);
    CHECK_EQ(f.ring.count, 0);
}

static void test_select_hold_released_before_poll_still_opens_automap(void) {
    fixture_t f;
    setup(&f);

    for (int i = 0; i < 20; i++) {
        frame(&f, FCINPUT_PAD_SELECT);
    }
    frame(&f, 0);
    fcinput_poll(&f.input, &f.ring);
    expect_event(&f, FCINPUT_KEY_AUTOMAP, true);
    expect_event(&f, FCINPUT_KEY_AUTOMAP, false);
    CHECK_EQ(f.ring.count, 0);
}

static void test_select_start_pauses_without_opening_menu(void) {
    fixture_t f;
    setup(&f);
    frame(&f, FCINPUT_PAD_SELECT | FCINPUT_PAD_START);
    fcinput_poll(&f.input, &f.ring);
    expect_event(&f, FCINPUT_KEY_PAUSE, true);
    expect_event(&f, FCINPUT_KEY_PAUSE, false);
    CHECK_EQ(f.ring.count, 0);
}

static void test_cheat_swallow_select_release(void) {
    fixture_t f;
    setup(&f);
    const uint8_t sequence[] = {FCINPUT_PAD_UP, FCINPUT_PAD_UP, FCINPUT_PAD_DOWN,
                                FCINPUT_PAD_DOWN, FCINPUT_PAD_LEFT, FCINPUT_PAD_RIGHT,
                                FCINPUT_PAD_A};
    for (size_t i = 0; i < sizeof(sequence); i++) {
        frame(&f, FCINPUT_PAD_SELECT | sequence[i]);
        frame(&f, FCINPUT_PAD_SELECT);
    }
    fcinput_poll(&f.input, &f.ring);
    const char *text = "iddqd";
    for (size_t i = 0; text[i] != '\0'; i++) {
        fcinput_event_t event;
        CHECK(fcinput_ring_pop(&f.ring, &event));
        CHECK_EQ(event.key, text[i]);
        CHECK(event.down);
        CHECK(fcinput_ring_pop(&f.ring, &event));
        CHECK_EQ(event.key, text[i]);
        CHECK(!event.down);
    }
    frame(&f, 0);
    fcinput_poll(&f.input, &f.ring);
    CHECK_EQ(f.ring.count, 0);
}

int main(void) {
    test_direction_action_and_always_run();
    test_sticky_press_survives_tic_boundary();
    test_b_tap_uses_and_hold_strafes();
    test_select_tap_and_hold_are_distinct();
    test_select_hold_released_before_poll_still_opens_automap();
    test_select_start_pauses_without_opening_menu();
    test_cheat_swallow_select_release();
    return ctest_lite_result();
}
