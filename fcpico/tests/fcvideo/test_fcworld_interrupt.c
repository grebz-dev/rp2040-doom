/* SPDX-License-Identifier: BSD-3-Clause */
/* Inject the actual heartbeat at every instrumented C function boundary. */
#include "ctest_lite.h"
#include "fcworld.h"

static fcworld_t world;
static bool injecting, in_interrupt;
static unsigned interrupts, leaked_commits;
static uint8_t mailbox[FC_COM_BUF_SIZE_V5];

static void inject(void) {
    if (!injecting || in_interrupt) return;
    in_interrupt = true;
    memset(mailbox, 0, sizeof mailbox);
    fcworld_heartbeat(&world, mailbox, 0);
    if ((mailbox[MBX_FLAGS] & MBX_FLAG_WORLD) &&
        mailbox[MBX_WORLD_OP] == WORLD_OP_OAM &&
        (mailbox[MBX_WORLD_ARG] & WORLD_OAM_COMMIT)) leaked_commits++;
    interrupts++;
    in_interrupt = false;
}

void __cyg_profile_func_enter(void *function, void *caller) {
    (void)function; (void)caller; inject();
}
void __cyg_profile_func_exit(void *function, void *caller) {
    (void)function; (void)caller; inject();
}

int main(void) {
    fcworld_reset(&world);
    fcworld_frame_t frame;
    fcworld_frame_clear(&frame, world.shown_pal);
    frame.tile_count = frame.oam_count = FCWORLD_MAX_TILES;
    uint32_t random = 0x1432350;
    for (unsigned t = 0; t < frame.tile_count; ++t) {
        for (unsigned b = 0; b < 16; ++b) {
            random = random * 1664525u + 1013904223u;
            frame.tiles[t][b] = (uint8_t)(random >> 24);
        }
        frame.oam[t][0] = 39 + (t / 4) * 8;
        frame.oam[t][1] = t;
        frame.oam[t][3] = 64 + (t % 4) * 8;
    }
    injecting = true;
    CHECK(fcworld_submit_held(&world, &frame) != 0xFF);
    injecting = false;
    CHECK(interrupts > 100);
    CHECK_EQ(leaked_commits, 0);
    CHECK(world.hold_commit);
    CHECK_EQ(world.upload_count, FCWORLD_MAX_TILES);
    for (unsigned t = 0; t < world.upload_count; ++t)
        CHECK_MEM(world.slots[world.upload[t]].data, frame.tiles[t], 16);

    /* A cached one-entry frame goes directly to a committing OAM packet.
     * An IRQ between preparation and submit_held returning must not commit. */
    fcworld_reset(&world);
    world.slots[0].valid = true;
    memcpy(world.slots[0].data, frame.tiles[0], 16);
    world.staged_known[1] = true;
    for (unsigned n = WORLD_OAM_PACKET; n < WORLD_OAM_ENTRIES; ++n)
        world.staged[n * 4] = 0xEF;
    frame.tile_count = frame.oam_count = 1;
    injecting = true;
    CHECK(fcworld_submit_held(&world, &frame) != 0xFF);
    injecting = false;
    CHECK_EQ(leaked_commits, 0);
    CHECK(world.hold_commit);
    fcworld_release(&world);

    /* Explicit lease: a heartbeat must leave both the mailbox and partial
     * generation alone until the foreground publishes its completed state. */
    fcworld_reset(&world);
    CHECK(fcworld_reserve(&world));
    CHECK(!fcworld_reserve(&world));
    CHECK(!fcworld_idle(&world));
    unsigned before = interrupts;
    injecting = true;
    for (unsigned i = 0; i < FCWORLD_STALL_HEARTBEATS + 1; ++i) inject();
    injecting = false;
    CHECK(interrupts > before);
    CHECK_EQ(world.busy_heartbeats, 0);
    CHECK_EQ(world.stats.stalls, 0);
    CHECK_EQ(mailbox[MBX_FLAGS], 0);
    injecting = true;
    CHECK(fcworld_submit_held(&world, &frame) != 0xFF);
    injecting = false;
    CHECK_EQ(leaked_commits, 0);
    return ctest_lite_result();
}
