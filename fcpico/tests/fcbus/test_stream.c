/*
 * test_stream.c -- stream-buffer addressing: fcbus_stream_word() and buffer sizes.
 *
 * SPDX-License-Identifier: BSD-3-Clause
 *
 * The word layout is not a design choice we are free to make: it is whatever
 * rp_system::convVram() writes, because the PPU consumes the buffer linearly and the
 * hardware trace measures 32 selected words per visible scanline. convVram()'s
 * source index is flat and continuous, so its 34-iteration outer-loop batches do
 * not define physical scanline boundaries. Word index = 31 + 32*y + x.
 */
#include "fcbus_core.h"
#include "ctest_lite.h"

static void test_word_offsets(void) {
    uint16_t buf[VRAM_BUF_BYTES_V2 / sizeof(uint16_t)];

    CHECK_EQ(fcbus_stream_word(buf, 0, 0) - buf, VRAM_HEAD_WORDS);
    CHECK_EQ(fcbus_stream_word(buf, 0, 0) - buf, 31);
    CHECK_EQ(fcbus_stream_word(buf, 1, 0) - buf, 63);
    CHECK_EQ(fcbus_stream_word(buf, 0, 31) - buf, 31 + 31);
    /* Last visible word; four bytes of alignment follow before the mailbox. */
    CHECK_EQ(fcbus_stream_word(buf, 239, 31) - buf, 31 + 32 * 239 + 31);
    CHECK_EQ(fcbus_stream_word(buf, 239, 31) - buf, 7710);

    /* Every line starts one full stride after the previous one. */
    for (int y = 1; y < VRAM_LINES; y++) {
        CHECK_EQ(fcbus_stream_word(buf, y, 0) - fcbus_stream_word(buf, y - 1, 0),
                 VRAM_TILE_COLS);
    }
}

static void test_converter_writes_stay_inside_the_buffer(void) {
    /* The whole picture, mailbox included, must fit in both protocols' buffers. */
    size_t last_picture_byte = (size_t)(31 + 32 * 239 + 31) * sizeof(uint16_t) + 1;
    CHECK(last_picture_byte < VRAM_BUF_BYTES_V1);
    CHECK(last_picture_byte < VRAM_BUF_BYTES_V2);

    CHECK_EQ(VRAM_BUF_BYTES_V1, 17344);
    CHECK_EQ(VRAM_BUF_BYTES_V2, 17408);
    CHECK_EQ(VRAM_BUF_BYTES_V2 - VRAM_BUF_BYTES_V1,
             FC_COM_BUF_SIZE_V2 - FC_COM_BUF_SIZE_V1);

    /* Both mailboxes sit after the picture at the same position. */
    CHECK_EQ(VRAM_MAILBOX_OFF_V1, VRAM_MAILBOX_OFF_V2);
    CHECK_EQ(VRAM_MAILBOX_OFF_V1 + FC_COM_BUF_SIZE_V1, PPU_COUNT_VAL_V1);
    CHECK_EQ(VRAM_MAILBOX_OFF_V2 + FC_COM_BUF_SIZE_V2, PPU_COUNT_VAL_V2);
    /* The buffer is allocated for 36 tile columns but only 34 are emitted, so there is
     * slack past the mailbox: rp_system.h's VRAM_BUF_SIZE comment calls it "slack so the
     * DMA never runs off the end". */
    CHECK(VRAM_MAILBOX_OFF_V2 + FC_COM_BUF_SIZE_V2 < VRAM_BUF_BYTES_V1);
}

static void test_back_and_front_are_distinct_and_stable(void) {
    static fcbus_core_t c;
    static const uint8_t rom[FCBUS_ROM_PRG_BYTES] = {0};
    fcbus_config_t cfg = {.rom_image = rom, .proto_default = FCBUS_PROTO_V1};
    fcbus_core_init(&c, &cfg);

    uint16_t *back = fcbus_core_stream_back(&c);
    const uint16_t *front = fcbus_core_stream_front(&c);
    CHECK(back != front);
    /* Repeated calls are stable until a publish+heartbeat swaps the roles. */
    CHECK(fcbus_core_stream_back(&c) == back);
    CHECK(fcbus_core_stream_front(&c) == front);

    /* Writing through the back pointer must not touch the front buffer. */
    *fcbus_stream_word(back, 120, 16) = 0xBEEF;
    CHECK_EQ(front[VRAM_HEAD_WORDS + 120 * VRAM_TILE_COLS + 16], 0);

    fcbus_core_publish(&c);
    (void)fcbus_core_heartbeat(&c, PPU_COUNT_VAL_V1);
    CHECK(fcbus_core_stream_front(&c) == back); /* the published buffer is now the front */
    CHECK_EQ(fcbus_core_stream_front(&c)[VRAM_HEAD_WORDS + 120 * VRAM_TILE_COLS + 16], 0xBEEF);
}

int main(void) {
    test_word_offsets();
    test_converter_writes_stay_inside_the_buffer();
    test_back_and_front_are_distinct_and_stable();
    return ctest_lite_result();
}
