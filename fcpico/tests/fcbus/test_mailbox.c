/*
 * test_mailbox.c -- fcbus_core mailbox builder: layouts, terminators, overflow refusal.
 *
 * SPDX-License-Identifier: BSD-3-Clause
 *
 * Mirrors rp_system::initFC_COM_BUF() / setPF_COM() / setPF_VRAM() / setPF_APU() and
 * rp_system::update()'s palette/attribute diffing, extended with the v2 fields.
 */
#include "fcbus_core.h"
#include "ctest_lite.h"

static void init_core(fcbus_core_t *c, fcbus_proto_t proto) {
    static const uint8_t rom[FCBUS_ROM_PRG_BYTES] = {0};
    fcbus_config_t cfg = {.rom_image = rom, .proto_default = proto};
    fcbus_core_init(c, &cfg);
}

static void test_reset_v1(void) {
    fcbus_core_t c;
    init_core(&c, FCBUS_PROTO_V1);
    const uint8_t *m = fcbus_core_mailbox_next(&c);
    CHECK_EQ(m[MBX_FLAGS], 0); /* "v1: byte 0 = 0" */
    CHECK_EQ(m[MBX_MAGIC], PF_MAGIC_NO);
    CHECK_EQ(m[MBX_CMD], PF_COM_NONE); /* freshly zeroed command area */
    CHECK_EQ(m[MBX_APU], 0xFF);        /* terminator at the cursor */
    CHECK_EQ(c.cmd_cursor, MBX_CMD);
    CHECK_EQ(c.apu_cursor, MBX_APU);
}

static void test_reset_v2(void) {
    fcbus_core_t c;
    init_core(&c, FCBUS_PROTO_V2);
    const uint8_t *m = fcbus_core_mailbox_next(&c);
    CHECK_EQ(m[MBX_FLAGS], MBX_FLAG_V2);
    CHECK_EQ(m[MBX_MAGIC], PF_MAGIC_NO);
    CHECK_EQ(m[MBX_APU], 0xFF);
}

static void test_ui_snapshot_v3_only(void) {
    uint8_t ui[MBX_UI_LEN];
    for (int i = 0; i < MBX_UI_LEN; ++i) ui[i] = (uint8_t)(0x80 + i);
    fcbus_core_t c;
    init_core(&c, FCBUS_PROTO_V2);
    CHECK(!fcbus_core_ui_snapshot(&c, ui));
    CHECK_EQ(c.mailbox_next[MBX_FLAGS], MBX_FLAG_V2);
    init_core(&c, FCBUS_PROTO_V3);
    CHECK(fcbus_core_ui_snapshot(&c, ui));
    CHECK_EQ(c.mailbox_next[MBX_FLAGS],
             MBX_FLAG_V2 | MBX_FLAG_V3 | MBX_FLAG_UI_VALID);
    CHECK_MEM(c.mailbox_next + MBX_UI, ui, MBX_UI_LEN);
    CHECK_EQ(fcbus_core_heartbeat(&c, PPU_COUNT_VAL_V3), FCBUS_ARM);
    const uint8_t *front = (const uint8_t *)fcbus_core_stream_front(&c);
    CHECK_MEM(front + VRAM_MAILBOX_OFF_V3 + MBX_UI, ui, MBX_UI_LEN);
    CHECK_EQ(c.mailbox_next[MBX_FLAGS],
             MBX_FLAG_V2 | MBX_FLAG_V3 | MBX_FLAG_UI_VALID);
    CHECK_MEM(c.mailbox_next + MBX_UI, ui, MBX_UI_LEN);
}

static void test_ui_snapshot_v4_text_offset(void) {
    fcbus_core_t c;
    init_core(&c, FCBUS_PROTO_V4);
    uint8_t ui[MBX_UI_LEN];
    for (int i = 0; i < MBX_UI_LEN; ++i) ui[i] = (uint8_t)i;
    CHECK(fcbus_core_ui_snapshot(&c, ui));
    CHECK_EQ(fcbus_core_heartbeat(&c, PPU_COUNT_VAL_V4), FCBUS_ARM);
    const uint8_t *front = (const uint8_t *)fcbus_core_stream_front(&c);
    CHECK_EQ(front[VRAM_MAILBOX_OFF_V4 + MBX_FLAGS],
             MBX_FLAG_V2 | MBX_FLAG_V3 | MBX_FLAG_V4 | MBX_FLAG_UI_VALID);
    CHECK_MEM(front + VRAM_MAILBOX_OFF_V4 + MBX_UI, ui, MBX_UI_LEN);
}

static void test_cmd_append_and_overflow(void) {
    fcbus_core_t c;
    init_core(&c, FCBUS_PROTO_V1);
    /* Room is FC_COM_BUF_SIZE16 - MBX_CMD = 14 bytes. */
    int ok = 0;
    for (int i = 0; i < 14; i++) {
        ok += fcbus_core_cmd(&c, (uint8_t)(0x10 + i)) ? 1 : 0;
    }
    CHECK_EQ(ok, 14);
    CHECK_EQ(c.cmd_cursor, FC_COM_BUF_SIZE16);
    CHECK(!fcbus_core_cmd(&c, 0xAA)); /* full: refused */
    CHECK_EQ(c.cmd_cursor, FC_COM_BUF_SIZE16); /* unchanged */
    const uint8_t *m = fcbus_core_mailbox_next(&c);
    for (int i = 0; i < 14; i++) {
        CHECK_EQ(m[MBX_CMD + i], 0x10 + i);
    }
}

static void test_cmd_vram_bytes_and_masking(void) {
    fcbus_core_t c;
    init_core(&c, FCBUS_PROTO_V1);
    CHECK(fcbus_core_cmd_vram(&c, 0x1234, 0x56));
    const uint8_t *m = fcbus_core_mailbox_next(&c);
    CHECK_EQ(m[MBX_CMD + 0], (uint8_t)(PF_COM_VRAM | 0x12));
    CHECK_EQ(m[MBX_CMD + 1], 0x34);
    CHECK_EQ(m[MBX_CMD + 2], 0x56);
    CHECK_EQ(c.cmd_cursor, MBX_CMD + 3);

    /* addr is masked to 14 bits, mirroring rp_system::setPF_VRAM(). */
    fcbus_core_t c2;
    init_core(&c2, FCBUS_PROTO_V1);
    CHECK(fcbus_core_cmd_vram(&c2, 0x7234, 0x99));
    const uint8_t *m2 = fcbus_core_mailbox_next(&c2);
    CHECK_EQ(m2[MBX_CMD + 0], (uint8_t)(PF_COM_VRAM | 0x32));
    CHECK_EQ(m2[MBX_CMD + 1], 0x34);
}

static void test_cmd_vram_needs_three_bytes(void) {
    fcbus_core_t c;
    init_core(&c, FCBUS_PROTO_V1);
    /* Use up all but exactly 3 bytes of room (14 - 3 = 11 single-byte commands). */
    for (int i = 0; i < 11; i++) {
        CHECK(fcbus_core_cmd(&c, 0x01));
    }
    CHECK(fcbus_core_cmd_vram(&c, 0x3F00, 0x0F)); /* exactly 3 bytes left: fits */
    CHECK_EQ(c.cmd_cursor, FC_COM_BUF_SIZE16);
    CHECK(!fcbus_core_cmd_vram(&c, 0x3F00, 0x0F)); /* now full: refused */

    fcbus_core_t c2;
    init_core(&c2, FCBUS_PROTO_V1);
    for (int i = 0; i < 12; i++) {
        CHECK(fcbus_core_cmd(&c2, 0x01)); /* only 2 bytes left */
    }
    CHECK(!fcbus_core_cmd_vram(&c2, 0x3F00, 0x0F)); /* not enough room */
    CHECK_EQ(c2.cmd_cursor, FC_COM_BUF_SIZE16 - 2); /* untouched */
}

static void test_apu_write_pairs_and_cap(fcbus_proto_t proto, uint8_t max_pairs) {
    fcbus_core_t c;
    init_core(&c, proto);
    for (uint8_t i = 0; i < max_pairs; i++) {
        CHECK(fcbus_core_apu_write(&c, (uint8_t)(0x40 + i), (uint8_t)(0x80 + i)));
    }
    CHECK(!fcbus_core_apu_write(&c, 0xFF, 0xFF)); /* cap reached */
    const uint8_t *m = fcbus_core_mailbox_next(&c);
    for (uint8_t i = 0; i < max_pairs; i++) {
        CHECK_EQ(m[MBX_APU + 2 * i + 0], 0x40 + i);
        CHECK_EQ(m[MBX_APU + 2 * i + 1], 0x80 + i);
    }
    CHECK_EQ(m[MBX_APU + 2 * max_pairs], 0xFF); /* terminator kept right after the last pair */
    if (proto == FCBUS_PROTO_V2) {
        CHECK((m[MBX_FLAGS] & MBX_FLAG_APU_VALID) != 0);
    } else {
        CHECK((m[MBX_FLAGS] & MBX_FLAG_APU_VALID) == 0);
    }
}

static void test_attr_table_v2(void) {
    fcbus_core_t c;
    init_core(&c, FCBUS_PROTO_V2);
    uint8_t attr[MBX_ATTR_LEN];
    for (int i = 0; i < MBX_ATTR_LEN; i++) {
        attr[i] = (uint8_t)(i * 3 + 1);
    }
    fcbus_core_attr_table(&c, attr);
    const uint8_t *m = fcbus_core_mailbox_next(&c);
    CHECK_MEM(&m[MBX_ATTR], attr, MBX_ATTR_LEN);
    CHECK((m[MBX_FLAGS] & MBX_FLAG_ATTR_VALID) != 0);
    CHECK_MEM(c.attr_want, attr, MBX_ATTR_LEN);
    CHECK_MEM(c.attr_sent, attr, MBX_ATTR_LEN);
    CHECK_EQ(c.cmd_cursor, MBX_CMD); /* v2 path never touches the command area */
}

static void test_attr_table_v1_diff_and_carry_over(void) {
    fcbus_core_t c;
    init_core(&c, FCBUS_PROTO_V1);
    uint8_t attr[MBX_ATTR_LEN];
    for (int i = 0; i < MBX_ATTR_LEN; i++) {
        attr[i] = (uint8_t)(i + 1); /* all differ from the zeroed last-sent copy */
    }

    fcbus_core_attr_table(&c, attr);
    /* Room for commands is 14 bytes; each VRAM poke costs 3, so exactly 4 fit. */
    CHECK_EQ(c.cmd_cursor, MBX_CMD + 4 * 3);
    int sent_matches = 0;
    for (int i = 0; i < MBX_ATTR_LEN; i++) {
        if (c.attr_sent[i] == attr[i]) {
            sent_matches++;
        }
    }
    CHECK_EQ(sent_matches, 4);
    const uint8_t *m = fcbus_core_mailbox_next(&c);
    CHECK_EQ(m[MBX_CMD + 0], (uint8_t)(PF_COM_VRAM | ((0x23C0 >> 8) & 0xFF)));
    CHECK_EQ(m[MBX_CMD + 1], 0xC0);
    CHECK_EQ(m[MBX_CMD + 2], attr[0]);

    /* Next frame: mailbox_next resets (as fcbus_core_heartbeat() would do), the same
     * table is offered again, and the diff picks up where it left off. */
    for (int i = 0; i < MBX_ATTR_LEN; i++) {
        c.mailbox_next[i] = 0;
    }
    c.mailbox_next[MBX_MAGIC] = PF_MAGIC_NO;
    c.cmd_cursor = MBX_CMD;
    fcbus_core_attr_table(&c, attr);
    sent_matches = 0;
    for (int i = 0; i < MBX_ATTR_LEN; i++) {
        if (c.attr_sent[i] == attr[i]) {
            sent_matches++;
        }
    }
    CHECK_EQ(sent_matches, 8); /* four more picked up */
}

static void test_palette_v2_and_v1(void) {
    fcbus_core_t c2;
    init_core(&c2, FCBUS_PROTO_V2);
    uint8_t pal[MBX_PAL_LEN];
    for (int i = 0; i < MBX_PAL_LEN; i++) {
        pal[i] = (uint8_t)(0x10 + i);
    }
    fcbus_core_palette(&c2, pal);
    const uint8_t *m2 = fcbus_core_mailbox_next(&c2);
    CHECK_MEM(&m2[MBX_PAL], pal, MBX_PAL_LEN);
    CHECK((m2[MBX_FLAGS] & MBX_FLAG_PAL_VALID) != 0);
    CHECK_MEM(c2.pal_want, pal, MBX_PAL_LEN);
    CHECK_MEM(c2.pal_sent, pal, MBX_PAL_LEN);
    /* pal_want[16..31] (sprite palette) is never supplied by this 16-byte API and stays
     * at the post-init() zero. */
    for (int i = MBX_PAL_LEN; i < 32; i++) {
        CHECK_EQ(c2.pal_want[i], 0);
    }

    fcbus_core_t c1;
    init_core(&c1, FCBUS_PROTO_V1);
    fcbus_core_palette(&c1, pal);
    /* Room is 14 bytes, 3 per poke: 4 fit, 4 more bytes remain undelivered this frame. */
    CHECK_EQ(c1.cmd_cursor, MBX_CMD + 4 * 3);
}

static int g_hook_calls;
static uint8_t g_hook_pad2;
static bool g_hook_swapped;

static void record_hook(void *user, uint8_t *mailbox, uint8_t pad2, bool swapped) {
    (void)user;
    (void)mailbox;
    g_hook_calls++;
    g_hook_pad2 = pad2;
    g_hook_swapped = swapped;
}

/* A v5 world picture swaps only once the console reports its generation in
 * OAM; its tables ride in that swap mailbox, not in the repeated frames. */
static void test_world_publish_gate_v5(void) {
    fcbus_core_t c;
    init_core(&c, FCBUS_PROTO_V5);
    fcbus_core_set_mailbox_hook(&c, record_hook, NULL);
    uint8_t attr[MBX_ATTR_LEN], pal[MBX_PAL_LEN];
    for (int i = 0; i < MBX_ATTR_LEN; ++i) attr[i] = (uint8_t)(0x40 + i);
    for (int i = 0; i < MBX_PAL_LEN; ++i) pal[i] = (uint8_t)(0x10 + i);
    fcbus_core_palette(&c, pal);
    fcbus_core_attr_table(&c, attr);
    fcbus_core_publish_world(&c, 3);
    int front = c.front;

    c.pad2 = (2u << WORLD_HB_GEN_SHIFT) | 5u; /* previous generation still shown */
    CHECK_EQ(fcbus_core_heartbeat(&c, PPU_COUNT_VAL_V5), FCBUS_ARM);
    CHECK_EQ(c.front, front);
    CHECK(!fcbus_core_back_is_free(&c));
    CHECK_EQ(g_hook_calls, 1);
    CHECK_EQ(g_hook_pad2, (2u << WORLD_HB_GEN_SHIFT) | 5u);
    CHECK(!g_hook_swapped);
    const uint8_t *tail = (const uint8_t *)fcbus_core_stream_front(&c) + VRAM_MAILBOX_OFF_V5;
    CHECK_EQ(tail[MBX_FLAGS] & (MBX_FLAG_ATTR_VALID | MBX_FLAG_PAL_VALID), 0);

    c.pad2 = 3u << WORLD_HB_GEN_SHIFT;
    CHECK_EQ(fcbus_core_heartbeat(&c, PPU_COUNT_VAL_V5), FCBUS_ARM);
    CHECK_EQ(c.front, 1 - front);
    CHECK(fcbus_core_back_is_free(&c));
    CHECK(g_hook_swapped);
    tail = (const uint8_t *)fcbus_core_stream_front(&c) + VRAM_MAILBOX_OFF_V5;
    CHECK_EQ(tail[MBX_FLAGS] & (MBX_FLAG_ATTR_VALID | MBX_FLAG_PAL_VALID),
             MBX_FLAG_ATTR_VALID | MBX_FLAG_PAL_VALID);
    CHECK_MEM(tail + MBX_ATTR, attr, MBX_ATTR_LEN);
    CHECK_MEM(tail + MBX_PAL, pal, MBX_PAL_LEN);

    /* An ungated publish keeps the v4 swap-at-next-heartbeat behaviour. */
    fcbus_core_publish(&c);
    c.pad2 = 0;
    CHECK_EQ(fcbus_core_heartbeat(&c, PPU_COUNT_VAL_V5), FCBUS_ARM);
    CHECK_EQ(c.front, front);
}

int main(void) {
    test_reset_v1();
    test_reset_v2();
    test_ui_snapshot_v3_only();
    test_ui_snapshot_v4_text_offset();
    test_cmd_append_and_overflow();
    test_cmd_vram_bytes_and_masking();
    test_cmd_vram_needs_three_bytes();
    test_apu_write_pairs_and_cap(FCBUS_PROTO_V1, APU_PAIRS_MAX_V1);
    test_apu_write_pairs_and_cap(FCBUS_PROTO_V2, APU_PAIRS_MAX_V2);
    test_attr_table_v2();
    test_attr_table_v1_diff_and_carry_over();
    test_palette_v2_and_v1();
    test_world_publish_gate_v5();
    return ctest_lite_result();
}
