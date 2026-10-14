/*
 * test_host_roundtrip.c -- the host backend driven like a console: frames, resync, data mode.
 *
 * SPDX-License-Identifier: BSD-3-Clause
 *
 * Plays the part of the PPU (pattern fetches, via fcbus_host_ppu_read()) and of the 6502
 * (bytes written to `$2007`, via fcbus_host_ppu_write()) against the same core a real
 * converter would fill, and checks that a frame comes out of the wire the way
 * rp_system::ppu_dma() arranges it.
 *
 * The hardware trace resolves the arithmetic: 66 pre-render reads, 240*64 visible reads,
 * and 65 NMI reads are 15491 physical strobes. fcppu_rna reports N-1 because it copies
 * !X before decrementing X, so firmware observes PPU_COUNT_VAL_V1 = 15490. A frame re-arm
 * starts at buffer byte zero; manual phase nudges consume one or two bytes before reads resume.
 */
#include "fcbus_host.h"
#include "ctest_lite.h"

static uint8_t g_rom[FCBUS_ROM_PRG_BYTES];

/* Reads taken since the last heartbeat. The console's read count is what decides the
 * sync outcome, so a test that reads part of a frame by hand must have the rest of that
 * frame's budget made up before it beats -- finish_frame() does that. */
static int g_frame_reads;

static void init_host(fcbus_proto_t proto) {
    fcbus_config_t cfg = {.rom_image = g_rom, .proto_default = proto};
    fcbus_host_init(&cfg);
    g_frame_reads = 0;
}

/** One PPU read, counted against the current frame. */
static int rd(void) {
    g_frame_reads++;
    return fcbus_host_ppu_read();
}

/** `n` PPU reads, discarding the data; returns how many returned real bytes. */
static int skipn(int n) {
    int valid = 0;
    for (int i = 0; i < n; i++) {
        if (rd() >= 0) {
            valid++;
        }
    }
    return valid;
}

static uint32_t expected_count(fcbus_proto_t proto) {
    return (proto == FCBUS_PROTO_V2) ? PPU_COUNT_VAL_V2 : PPU_COUNT_VAL_V1;
}

static uint32_t expected_physical_reads(fcbus_proto_t proto) {
    return expected_count(proto) + PPU_COUNTER_REPORT_BIAS;
}

/** Writes the frame heartbeat the way a console of protocol `proto` would. */
static void write_heartbeat(fcbus_proto_t proto, uint8_t pad1, uint8_t pad2) {
    if (proto == FCBUS_PROTO_V2) {
        fcbus_host_ppu_write(FP_COM_KEY);
        fcbus_host_ppu_write(pad1);
        fcbus_host_ppu_write(pad2);
    } else {
        fcbus_host_ppu_write(pad1);
        (void)pad2;
    }
    g_frame_reads = 0;
}

/** Pads the frame to exactly `total_reads` reads, then beats. The count is what the
 *  sync decision is made on, so tests state the count they want rather than tracking
 *  how many reads they have already taken by hand. */
static void beat_at(fcbus_proto_t proto, int total_reads, uint8_t pad1, uint8_t pad2) {
    CHECK(g_frame_reads <= total_reads);
    skipn(total_reads - g_frame_reads);
    write_heartbeat(proto, pad1, pad2);
}

/** Pads the frame to exactly `expected` reads, then beats: an in-phase frame. */
static void finish_frame(fcbus_proto_t proto, uint8_t pad1, uint8_t pad2) {
    beat_at(proto, (int)expected_physical_reads(proto), pad1, pad2);
}

/** An in-phase frame that reads nothing interesting. */
static void plain_frame(fcbus_proto_t proto) {
    finish_frame(proto, 0, 0);
}

/* ---------------------------------------------------------------------- */

static void test_cold_start_then_resync(void) {
    /* Nothing is in phase yet, so the first frame's count is wrong and the DMA stays
     * stopped -- what a console powering up mid-frame looks like. Reads while stopped
     * yield no data but are still counted, which is how phase is recovered. */
    init_host(FCBUS_PROTO_V1);
    CHECK_EQ(rd(), -1);

    write_heartbeat(FCBUS_PROTO_V1, 0, 0);
    CHECK_EQ(fcbus_core_stats(fcbus_host_core())->dma_stops, 1);
    CHECK_EQ(fcbus_core_stats(fcbus_host_core())->resyncs, 0);

    /* A full frame's worth of (dataless) reads puts the count back in phase. */
    CHECK_EQ(skipn((int)PPU_COUNT_VAL_V1 + PPU_COUNTER_REPORT_BIAS), 0);
    write_heartbeat(FCBUS_PROTO_V1, 0, 0);
    CHECK_EQ(fcbus_core_stats(fcbus_host_core())->resyncs, 1);
    CHECK_EQ(fcbus_core_stats(fcbus_host_core())->dma_stops, 1);

    /* Now the bus carries data again from buffer byte zero. */
    CHECK(rd() >= 0);
}

static void test_nudges_advance_the_buffer(void) {
    struct {
        int count_delta;
        int first_byte;
    } cases[] = {
        {0, 0xA5},  /* ARM: starts at buffer byte zero. */
        {-1, 0xB6}, /* ARM_NUDGE1 consumes one byte with manual OUT. */
        {-2, 0xC7}, /* ARM_NUDGE2 consumes two bytes. */
    };

    for (size_t i = 0; i < sizeof cases / sizeof cases[0]; i++) {
        init_host(FCBUS_PROTO_V1);
        /* Mark the buffer that will be streamed so its first byte is recognisable. */
        uint8_t *back = (uint8_t *)fcbus_core_stream_back(fcbus_host_core());
        back[0] = 0xA5;
        back[1] = 0xB6;
        back[2] = 0xC7;
        fcbus_core_publish(fcbus_host_core());

        skipn((int)PPU_COUNT_VAL_V1 + PPU_COUNTER_REPORT_BIAS + cases[i].count_delta);
        write_heartbeat(FCBUS_PROTO_V1, 0, 0);

        CHECK_EQ(rd(), cases[i].first_byte);
    }
}

static void test_mailbox_placement_on_the_wire(void) {
    /* The placement contract: whatever the console's read count turns out to be, the
     * mailbox is at VRAM_MAILBOX_OFF of the streamed buffer and comes off the wire
     * there. See the header comment for why this is checked apart from the sync
     * contract. */
    for (int v2 = 0; v2 < 2; v2++) {
        fcbus_proto_t proto = v2 ? FCBUS_PROTO_V2 : FCBUS_PROTO_V1;
        size_t mbx_off = v2 ? VRAM_MAILBOX_OFF_V2 : VRAM_MAILBOX_OFF_V1;
        size_t mbx_len = v2 ? FC_COM_BUF_SIZE_V2 : FC_COM_BUF_SIZE_V1;

        init_host(proto);
        fcbus_core_t *core = fcbus_host_core();
        plain_frame(proto); /* cold start: gets the count in phase */

        /* Build the mailbox, then take the frame that carries it. */
        CHECK(fcbus_core_cmd(core, PF_COM_FDIN));
        CHECK(fcbus_core_apu_write(core, 0x04, 0x7F));
        plain_frame(proto); /* the heartbeat copies it onto the streamed buffer */

        /* Skip the picture, then read the mailbox off the bus. */
        skipn((int)mbx_off);
        uint8_t wire[FC_COM_BUF_SIZE_V2];
        for (size_t i = 0; i < mbx_len; i++) {
            int b = rd();
            CHECK(b >= 0);
            wire[i] = (uint8_t)b;
        }

        CHECK_EQ(wire[MBX_MAGIC], PF_MAGIC_NO);
        CHECK_EQ(wire[MBX_FLAGS], v2 ? MBX_FLAG_V2 | MBX_FLAG_APU_VALID : 0);
        CHECK_EQ(wire[MBX_CMD], PF_COM_FDIN);
        CHECK_EQ(wire[MBX_CMD + 1], PF_COM_NONE);
        CHECK_EQ(wire[MBX_APU + 0], 0x04);
        CHECK_EQ(wire[MBX_APU + 1], 0x7F);
        CHECK_EQ(wire[MBX_APU + 2], 0xFF);

        /* The same bytes the core says it is streaming. */
        const uint8_t *front = (const uint8_t *)fcbus_core_stream_front(core);
        CHECK_MEM(wire, front + mbx_off, mbx_len);
    }
}

static void test_published_frame_reaches_the_wire(void) {
    init_host(FCBUS_PROTO_V1);
    fcbus_core_t *core = fcbus_host_core();
    plain_frame(FCBUS_PROTO_V1); /* get in phase */

    /* Draw a recognisable picture into the back buffer and publish it. */
    uint16_t *back = fcbus_core_stream_back(core);
    *fcbus_stream_word(back, 0, 0) = 0x1234;
    CHECK(fcbus_core_back_is_free(core));
    fcbus_core_publish(core);
    CHECK(!fcbus_core_back_is_free(core));

    /* The swap happens at the heartbeat, so this frame's reads are the new picture. */
    plain_frame(FCBUS_PROTO_V1);
    CHECK(fcbus_core_back_is_free(core));

    skipn(VRAM_HEAD_WORDS * 2); /* words 0..30 precede line 0, tile 0 */
    CHECK_EQ(rd(), 0x34);       /* little-endian: bitplane 0 first */
    CHECK_EQ(rd(), 0x12);

    /* Publishing again alternates the buffers. */
    const uint16_t *front_a = fcbus_core_stream_front(core);
    fcbus_core_publish(core);
    plain_frame(FCBUS_PROTO_V1);
    CHECK(fcbus_core_stream_front(core) != front_a);
}

static void test_out_of_phase_frame_stops_the_dma(void) {
    /* Three reads short: outside the +/-2 window, so the DMA stops and the bus goes
     * quiet rather than tearing the picture. */
    init_host(FCBUS_PROTO_V1);
    plain_frame(FCBUS_PROTO_V1);
    CHECK(rd() >= 0); /* streaming; this read counts towards the next frame */

    beat_at(FCBUS_PROTO_V1,
            (int)PPU_COUNT_VAL_V1 + PPU_COUNTER_REPORT_BIAS - 3, 0, 0);
    CHECK_EQ(fcbus_core_stats(fcbus_host_core())->dma_stops, 1);
    CHECK_EQ(rd(), -1);

    /* One good frame recovers. */
    beat_at(FCBUS_PROTO_V1,
            (int)PPU_COUNT_VAL_V1 + PPU_COUNTER_REPORT_BIAS, 0, 0);
    CHECK_EQ(fcbus_core_stats(fcbus_host_core())->resyncs, 1);
    CHECK_EQ(rd(), 0x00); /* first buffer byte after re-arm */

    /* Three reads long: also outside the window. */
    init_host(FCBUS_PROTO_V1);
    plain_frame(FCBUS_PROTO_V1);
    beat_at(FCBUS_PROTO_V1,
            (int)PPU_COUNT_VAL_V1 + PPU_COUNTER_REPORT_BIAS + 3, 0, 0);
    CHECK_EQ(fcbus_core_stats(fcbus_host_core())->dma_stops, 1);
    CHECK_EQ(rd(), -1);
}

static void test_pads_and_action_log(void) {
    init_host(FCBUS_PROTO_V2);
    finish_frame(FCBUS_PROTO_V2, KEY_LEFT | KEY_A, KEY_RUN);
    CHECK_EQ(fcbus_core_pads(fcbus_host_core()),
             (uint16_t)((KEY_RUN << 8) | (KEY_LEFT | KEY_A)));

    /* Actions the backend does not consume itself are observable afterwards. */
    fcbus_host_action_log_clear();
    fcbus_host_ppu_write(FP_COM_INI);
    fcbus_host_ppu_write(3);
    CHECK_EQ(fcbus_host_action_log_count(), 1);
    CHECK_EQ(fcbus_host_action_log()[0].kind, FCBUS_ACT_INIT);
    CHECK_EQ(fcbus_host_action_log()[0].arg, 3);

    /* A log burst arrives as one entry once the write burst ends. */
    fcbus_host_action_log_clear();
    static const uint8_t burst[] = {FP_COM_LOG, 0xEA, 0x01};
    fcbus_host_write_burst(burst, sizeof burst);
    CHECK_EQ(fcbus_host_action_log_count(), 1);
    CHECK_EQ(fcbus_host_action_log()[0].kind, FCBUS_ACT_LOG);
    CHECK_EQ(fcbus_host_action_log()[0].len, 2);
    CHECK_EQ(fcbus_host_action_log()[0].data[0], 0xEA);
}

static void test_version_and_rom_replies_on_the_wire(void) {
    /* The boot path: the fix bank asks for the build stamp, then pulls the ROM image
     * page by page (docs/pages/boot-and-reflash.md). */
    memcpy(g_rom + FCBUS_ROM_STAMP_OFF, "DOOM-02-000001", 14);
    for (int i = 0; i < 256; i++) {
        g_rom[0x1200 + i] = (uint8_t)i;
    }
    init_host(FCBUS_PROTO_V1);

    fcbus_host_ppu_write(FP_COM_VER);
    /* The 6502 discards bytes until it sees 'C', then takes 14 (CHK_ROMVER). */
    int b;
    do {
        b = rd();
        CHECK(b >= 0);
    } while (b != 'C');
    char stamp[15] = {0};
    for (int i = 0; i < 14; i++) {
        stamp[i] = (char)rd();
    }
    CHECK_EQ(strcmp(stamp, "DOOM-02-000001"), 0);

    fcbus_host_ppu_write(FP_COM_ROM);
    fcbus_host_ppu_write(0x92); /* CPU $9200 -> PRG offset $1200 */
    for (int i = 0; i < 256; i++) {
        CHECK_EQ(rd(), i);
    }
    /* Past the reply the bus carries filler until the next heartbeat re-arms it. */
    CHECK_EQ(rd(), 0xFF);
}

static void test_data_mode_sequence(void) {
    init_host(FCBUS_PROTO_V1);
    fcbus_core_t *core = fcbus_host_core();

    /* Ask for a table the console does not have, then for data mode. In v1 only four
     * bytes per frame could trickle through as pokes, which is exactly why a bulk
     * upload exists -- and why it must send the wanted table, not the trickle. */
    uint8_t attr[MBX_ATTR_LEN], pal[MBX_PAL_LEN];
    for (int i = 0; i < MBX_ATTR_LEN; i++) {
        attr[i] = (uint8_t)(i ^ 0x5A);
    }
    for (int i = 0; i < MBX_PAL_LEN; i++) {
        pal[i] = (uint8_t)(0x0F + i);
    }
    fcbus_core_attr_table(core, attr);
    fcbus_core_palette(core, pal);
    fcbus_core_request_data_mode(core);

    /* PF_COM_DMOD reaches the console in the mailbox, and is re-queued every frame until
     * the console answers (rp_system::startDataMode()'s retry loop). */
    plain_frame(FCBUS_PROTO_V1);
    const uint8_t *front = (const uint8_t *)fcbus_core_stream_front(core);
    bool saw_dmod = false;
    for (int i = MBX_CMD; i < FC_COM_BUF_SIZE16; i++) {
        if (front[VRAM_MAILBOX_OFF_V1 + i] == PF_COM_DMOD) {
            saw_dmod = true;
        }
    }
    CHECK(saw_dmod);
    CHECK_EQ(fcbus_core_mailbox_next(core)[MBX_CMD], PF_COM_DMOD); /* re-queued */

    /* The console enters data mode and asks for the first block. The header layout is
     * rp_system::drq_ret()'s two little-endian words. */
    fcbus_host_ppu_write(FP_COM_DRQ);
    CHECK_EQ(fcbus_host_core()->state, FCBUS_ST_DATA);
    uint8_t hdr[8];
    for (int i = 0; i < 8; i++) {
        hdr[i] = (uint8_t)rd();
    }
    CHECK_EQ(hdr[0], PF_DAT_VRAM); /* com */
    CHECK_EQ(hdr[1], PF_MAGIC_NO); /* magic */
    CHECK_EQ(hdr[2], 0x00);        /* adrL of $3F00 */
    CHECK_EQ(hdr[3], 0x3F);        /* adrH */
    CHECK_EQ(hdr[4], 32);          /* sizeL: the 32-byte palette */
    CHECK_EQ(hdr[5], 0);           /* sizeH */

    fcbus_host_ppu_write(FP_COM_DLD);
    fcbus_host_ppu_write(0);
    for (int i = 0; i < MBX_PAL_LEN; i++) {
        CHECK_EQ(rd(), pal[i]); /* the wanted palette, not the four trickled bytes */
    }
    skipn(256 - MBX_PAL_LEN);

    /* Then the attribute table. */
    fcbus_host_ppu_write(FP_COM_DRQ);
    for (int i = 0; i < 8; i++) {
        hdr[i] = (uint8_t)rd();
    }
    CHECK_EQ(hdr[0], PF_DAT_VRAM);
    CHECK_EQ(hdr[2], 0xC0); /* $23C0 */
    CHECK_EQ(hdr[3], 0x23);
    CHECK_EQ(hdr[4], MBX_ATTR_LEN);

    fcbus_host_ppu_write(FP_COM_DLD);
    fcbus_host_ppu_write(0);
    for (int i = 0; i < MBX_ATTR_LEN; i++) {
        CHECK_EQ(rd(), attr[i]);
    }
    skipn(256 - MBX_ATTR_LEN);

    /* Nothing left: the mode ends and the link returns to streaming frames. */
    fcbus_host_ppu_write(FP_COM_DRQ);
    for (int i = 0; i < 8; i++) {
        hdr[i] = (uint8_t)rd();
    }
    CHECK_EQ(hdr[0], PF_COM_NONE);
    CHECK_EQ(hdr[1], PF_MAGIC_NO);
    CHECK_EQ(fcbus_host_core()->state, FCBUS_ST_RUN);

    /* Data mode read an unbounded number of bytes with no heartbeats, so the first beat
     * after it is out of phase by construction; the frame after that is clean, and its
     * mailbox no longer asks for data mode. */
    write_heartbeat(FCBUS_PROTO_V1, 0, 0);
    plain_frame(FCBUS_PROTO_V1);
    CHECK_EQ(fcbus_core_mailbox_next(core)[MBX_CMD], PF_COM_NONE);
    /* The bulk upload synced the tables, so nothing trickles any more either. */
    fcbus_core_attr_table(core, attr);
    CHECK_EQ(fcbus_core_mailbox_next(core)[MBX_CMD], PF_COM_NONE);
}

static void test_data_mode_step_reply(void) {
    init_host(FCBUS_PROTO_V1);
    fcbus_core_t *core = fcbus_host_core();
    fcbus_core_request_data_mode(core);
    fcbus_core_set_step(core, 3);

    /* Palette, attributes, then the step: the tutorial's fixed priority order. */
    fcbus_host_ppu_write(FP_COM_DRQ);
    skipn(8);
    fcbus_host_ppu_write(FP_COM_DRQ);
    skipn(8);
    fcbus_host_ppu_write(FP_COM_DRQ);
    uint8_t hdr[8];
    for (int i = 0; i < 8; i++) {
        hdr[i] = (uint8_t)rd();
    }
    CHECK_EQ(hdr[0], PF_DAT_STEP);
    CHECK_EQ(hdr[1], PF_MAGIC_NO);
    CHECK_EQ(hdr[2], 3); /* the step code rides in the address field */
    CHECK_EQ(fcbus_host_core()->state, FCBUS_ST_RUN);
}

static void test_heartbeat_timeout_stops_the_bus(void) {
    init_host(FCBUS_PROTO_V1);
    plain_frame(FCBUS_PROTO_V1);
    CHECK(rd() >= 0);

    fcbus_host_tick_ms(1000);
    fcbus_host_tick_ms(3001); /* the console went away */
    CHECK_EQ(fcbus_core_stats(fcbus_host_core())->hb_timeouts, 1);
    CHECK_EQ(rd(), -1);
}

int main(void) {
    test_cold_start_then_resync();
    test_nudges_advance_the_buffer();
    test_mailbox_placement_on_the_wire();
    test_published_frame_reaches_the_wire();
    test_out_of_phase_frame_stops_the_dma();
    test_pads_and_action_log();
    test_version_and_rom_replies_on_the_wire();
    test_data_mode_sequence();
    test_data_mode_step_reply();
    test_heartbeat_timeout_stops_the_bus();
    return ctest_lite_result();
}
