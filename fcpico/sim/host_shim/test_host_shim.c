/*
 * SPDX-License-Identifier: BSD-3-Clause
 *
 * Exercises the host_shim library: multicore_launch_core1() plus the inter-core FIFO (a 1000
 * word round trip in each direction, verified with checksums so the test also catches data
 * corruption, not just deadlocks), pico_sync's semaphore_t (single-threaded first, then a real
 * cross-thread block/release with core 1), time_us_64()/sleep_ms(), and -- since alarm_host.c
 * provides them -- a repeating timer.
 *
 * The FIFO round trip is done as 1000 individual push-then-pop-the-echo steps rather than 1000
 * pushes followed by 1000 pops: each direction only has 8 slots (see multicore_host.c), and
 * core1 both pops core0's queue and pushes its own from a single thread, so pushing all 1000
 * words up front before popping any would fill core1's outbound queue solid (it would block
 * trying to push its 9th reply because nobody is draining it yet) while core0's own outbound
 * queue also fills up waiting on a core1 that has stopped popping -- a real deadlock, and
 * exactly the kind naive protocols hit on the real SIO FIFO too, not a shim bug.
 *
 * Exits nonzero if any check fails.
 */

#include <stdatomic.h>
#include <stdint.h>
#include <stdio.h>

#include "pico/multicore.h"
#include "pico/sem.h"
#include "pico/stdlib.h"

#include "alarm_host.h"

#define WORD_COUNT 1000u
#define XOR_MASK 0xA5A5A5A5u

static semaphore_t g_cross_sem;

static void core1_entry(void) {
    /* Phase 1: pop WORD_COUNT words core0 sends, push each back transformed, and report both
     * checksums so core0 can verify the data (not just the count) made the round trip intact. */
    uint32_t recv_checksum = 0;
    uint32_t sent_checksum = 0;
    for (uint32_t i = 0; i < WORD_COUNT; i++) {
        uint32_t v = multicore_fifo_pop_blocking();
        recv_checksum += v;
        uint32_t out_v = v ^ XOR_MASK;
        sent_checksum += out_v;
        multicore_fifo_push_blocking(out_v);
    }
    multicore_fifo_push_blocking(recv_checksum);
    multicore_fifo_push_blocking(sent_checksum);

    /* Phase 2: a real cross-thread semaphore_t exchange with core0. First try to acquire before
     * core0 has released -- this must time out. Then block for real until core0 does release. */
    bool ok = true;
    if (sem_acquire_timeout_ms(&g_cross_sem, 50)) {
        ok = false; /* should not have acquired anything yet */
    }
    sem_acquire_blocking(&g_cross_sem);

    multicore_fifo_push_blocking(ok ? 1u : 0u);
}

static atomic_int g_timer_fire_count = 0;

static bool repeating_timer_cb(repeating_timer_t *rt) {
    (void) rt;
    atomic_fetch_add(&g_timer_fire_count, 1);
    return true;
}

int main(void) {
    int failures = 0;

    /* time_us_64() advances across sleep_ms(10). */
    uint64_t t0 = time_us_64();
    sleep_ms(10);
    uint64_t t1 = time_us_64();
    if (!(t1 > t0)) {
        fprintf(stderr, "FAIL: time_us_64() did not advance across sleep_ms(10) (%llu -> %llu)\n",
                (unsigned long long) t0, (unsigned long long) t1);
        failures++;
    }

    /* Single-threaded semaphore_t exercise: sem_init, sem_available, sem_release,
     * sem_acquire_timeout_ms (success and timeout paths), sem_acquire_blocking. Kept
     * single-threaded so it is fully deterministic. */
    semaphore_t local_sem;
    sem_init(&local_sem, 0, 4);
    if (sem_available(&local_sem) != 0) {
        fprintf(stderr, "FAIL: local_sem available != 0 initially\n");
        failures++;
    }
    sem_release(&local_sem);
    if (sem_available(&local_sem) != 1) {
        fprintf(stderr, "FAIL: local_sem available != 1 after one release\n");
        failures++;
    }
    sem_release(&local_sem);
    sem_release(&local_sem);
    if (sem_available(&local_sem) != 3) {
        fprintf(stderr, "FAIL: local_sem available != 3 after three releases\n");
        failures++;
    }
    if (!sem_acquire_timeout_ms(&local_sem, 10)) {
        fprintf(stderr, "FAIL: local_sem acquire timed out despite permits being available\n");
        failures++;
    }
    if (sem_available(&local_sem) != 2) {
        fprintf(stderr, "FAIL: local_sem available != 2 after one acquire\n");
        failures++;
    }
    sem_acquire_blocking(&local_sem);
    sem_acquire_blocking(&local_sem);
    if (sem_available(&local_sem) != 0) {
        fprintf(stderr, "FAIL: local_sem available != 0 after draining\n");
        failures++;
    }
    if (sem_acquire_timeout_ms(&local_sem, 30)) {
        fprintf(stderr, "FAIL: local_sem acquire on an empty semaphore did not time out\n");
        failures++;
    }

    /* Cross-thread FIFO + semaphore exercise with core 1. */
    sem_init(&g_cross_sem, 0, 1);

    if (multicore_fifo_rvalid()) {
        fprintf(stderr, "FAIL: inbound FIFO reports data available before core1 has sent any\n");
        failures++;
    }
    if (!multicore_fifo_wready()) {
        fprintf(stderr, "FAIL: outbound FIFO reports no room before anything has been pushed\n");
        failures++;
    }

    multicore_launch_core1(core1_entry);

    uint32_t expected_recv_by_core1 = 0;
    uint32_t expected_sent_by_core1 = 0;
    uint32_t recv_checksum_main = 0;
    for (uint32_t i = 0; i < WORD_COUNT; i++) {
        uint32_t v = i * 2654435761u + 1u; /* arbitrary deterministic, non-trivial pattern */
        expected_recv_by_core1 += v;
        expected_sent_by_core1 += (v ^ XOR_MASK);
        multicore_fifo_push_blocking(v);
        recv_checksum_main += multicore_fifo_pop_blocking();
    }
    uint32_t core1_recv_checksum = multicore_fifo_pop_blocking();
    uint32_t core1_sent_checksum = multicore_fifo_pop_blocking();

    if (core1_recv_checksum != expected_recv_by_core1) {
        fprintf(stderr, "FAIL: core1's received checksum 0x%08x != expected 0x%08x\n",
                core1_recv_checksum, expected_recv_by_core1);
        failures++;
    }
    if (core1_sent_checksum != expected_sent_by_core1) {
        fprintf(stderr, "FAIL: core1's sent checksum 0x%08x != expected 0x%08x\n",
                core1_sent_checksum, expected_sent_by_core1);
        failures++;
    }
    if (recv_checksum_main != expected_sent_by_core1) {
        fprintf(stderr, "FAIL: core0's received checksum 0x%08x != expected 0x%08x\n",
                recv_checksum_main, expected_sent_by_core1);
        failures++;
    }

    /* Give core1 time to run its 50ms timeout-then-block sequence before we release it. */
    sleep_ms(80);
    if (sem_available(&g_cross_sem) != 0) {
        fprintf(stderr, "FAIL: g_cross_sem available != 0 before core0 releases it\n");
        failures++;
    }
    sem_release(&g_cross_sem);

    uint32_t core1_sem_ok = multicore_fifo_pop_blocking();
    if (!core1_sem_ok) {
        fprintf(stderr, "FAIL: core1's semaphore timeout/acquire sequence did not behave as expected\n");
        failures++;
    }

    /* Repeating timer: alarm_host.c backs add_repeating_timer_ms() since the pico-sdk host
     * platform does not declare it (PICO_TIME_DEFAULT_ALARM_POOL_DISABLED=1 on host) -- see
     * fcpico/README.md. Expect at least 3 fires in 50ms from a 10ms period. */
    repeating_timer_t rt;
    if (!add_repeating_timer_ms(10, repeating_timer_cb, NULL, &rt)) {
        fprintf(stderr, "FAIL: add_repeating_timer_ms could not allocate a timer\n");
        failures++;
    } else {
        sleep_ms(50);
        host_cancel_repeating_timer(&rt);
        int fire_count = atomic_load(&g_timer_fire_count);
        if (fire_count < 3) {
            fprintf(stderr, "FAIL: repeating timer fired %d times in 50ms, expected >= 3\n", fire_count);
            failures++;
        }
    }

    if (failures > 0) {
        fprintf(stderr, "host_shim_test: %d check(s) failed\n", failures);
        return 1;
    }

    printf("host_shim_test: all checks passed\n");
    return 0;
}
