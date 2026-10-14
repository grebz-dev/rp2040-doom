/*
 * SPDX-License-Identifier: BSD-3-Clause
 *
 * pthread-backed implementation of every function declared in pico-sdk's host
 * "pico/multicore.h" (src/host/pico_multicore/include/pico/multicore.h). That header ships
 * for PICO_PLATFORM=host, but src/host/pico_multicore/CMakeLists.txt adds no source file for
 * it -- the SDK's host README says the real implementation lives in pico-host-sdl, which pulls
 * in SDL2. This file is the SDL-free replacement: one real OS thread stands in for "core 1",
 * and the inter-core SIO FIFO is modelled as two independent 8-entry mutex+condvar queues
 * (one per direction), matched to the hardware semantics: a full push blocks, an empty pop
 * blocks.
 *
 * get_core_num() override
 * ------------------------
 * src/host/pico_platform/platform_base.c defines get_core_num() through the
 * PICO_WEAK_FUNCTION_DEF/IMPL_NAME pair, which expands (on GCC/Clang) to
 * `#pragma weak get_core_num` immediately before the definition -- i.e. the SDK marks its own
 * `return 0;` stub weak specifically so a strong definition elsewhere in the final link can
 * replace it. That is the "hook" the task description asks us to look for, and it does exist,
 * so this file provides a strong get_core_num() that reads a thread-local set by the core 1
 * trampoline, rather than leaving every thread reporting core 0. (Verified empirically: a
 * strong get_core_num() in another translation unit wins the link over the SDK's weak one.)
 *
 * Without this override, code running on the "core 1" pthread would observe get_core_num()==0
 * just like core 0, and anything gated on `get_core_num() == 1` (the engine's
 * disallow_core1_malloc-style assertions among them) could never fire on the host build --
 * see the README for the fuller version of this caveat.
 */

#include <pthread.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

#include "pico/multicore.h"
#include "pico/platform.h"

/* ------------------------------------------------------------------------------------------
 * get_core_num() override
 * ---------------------------------------------------------------------------------------- */

static __thread uint host_shim_core_num = 0;

uint get_core_num(void) {
    return host_shim_core_num;
}

/* ------------------------------------------------------------------------------------------
 * Inter-core FIFO: two independent 8-entry queues, one per direction.
 *
 * On real hardware each core has its own push (WOFIFO) and pop (ROFIFO) register aliased to
 * the same two physical FIFOs, so "push" from core 0 and "pop" from core 1 touch the same
 * queue. We model that directly instead of a single shared queue, keyed on get_core_num() of
 * the caller.
 * ---------------------------------------------------------------------------------------- */

#define HOST_SHIM_FIFO_DEPTH 8

typedef struct {
    uint32_t data[HOST_SHIM_FIFO_DEPTH];
    int head;
    int count;
    pthread_mutex_t lock;
    pthread_cond_t not_empty;
    pthread_cond_t not_full;
} host_fifo_t;

static void host_fifo_init(host_fifo_t *f) {
    memset(f->data, 0, sizeof(f->data));
    f->head = 0;
    f->count = 0;
    pthread_mutex_init(&f->lock, NULL);

    pthread_condattr_t attr;
    pthread_condattr_init(&attr);
    pthread_condattr_setclock(&attr, CLOCK_MONOTONIC);
    pthread_cond_init(&f->not_empty, &attr);
    pthread_cond_init(&f->not_full, &attr);
    pthread_condattr_destroy(&attr);
}

/* fifo[0] carries core0->core1 traffic, fifo[1] carries core1->core0 traffic. */
static host_fifo_t g_fifo[2];
static pthread_once_t g_fifo_once = PTHREAD_ONCE_INIT;

static void host_fifo_init_all(void) {
    host_fifo_init(&g_fifo[0]);
    host_fifo_init(&g_fifo[1]);
}

static void host_fifo_ensure_init(void) {
    pthread_once(&g_fifo_once, host_fifo_init_all);
}

static host_fifo_t *host_out_fifo(void) {
    host_fifo_ensure_init();
    return &g_fifo[get_core_num() == 0 ? 0 : 1];
}

static host_fifo_t *host_in_fifo(void) {
    host_fifo_ensure_init();
    return &g_fifo[get_core_num() == 0 ? 1 : 0];
}

static void host_timespec_from_now_us(struct timespec *ts, uint64_t timeout_us) {
    clock_gettime(CLOCK_MONOTONIC, ts);
    ts->tv_sec += (time_t) (timeout_us / 1000000ull);
    uint64_t extra_ns = (timeout_us % 1000000ull) * 1000ull;
    ts->tv_nsec += (long) extra_ns;
    if (ts->tv_nsec >= 1000000000L) {
        ts->tv_nsec -= 1000000000L;
        ts->tv_sec += 1;
    }
}

bool multicore_fifo_rvalid(void) {
    host_fifo_t *f = host_in_fifo();
    pthread_mutex_lock(&f->lock);
    bool valid = f->count > 0;
    pthread_mutex_unlock(&f->lock);
    return valid;
}

bool multicore_fifo_wready(void) {
    host_fifo_t *f = host_out_fifo();
    pthread_mutex_lock(&f->lock);
    bool ready = f->count < HOST_SHIM_FIFO_DEPTH;
    pthread_mutex_unlock(&f->lock);
    return ready;
}

static void host_fifo_push_locked(host_fifo_t *f, uint32_t data) {
    int tail = (f->head + f->count) % HOST_SHIM_FIFO_DEPTH;
    f->data[tail] = data;
    f->count++;
}

static uint32_t host_fifo_pop_locked(host_fifo_t *f) {
    uint32_t v = f->data[f->head];
    f->head = (f->head + 1) % HOST_SHIM_FIFO_DEPTH;
    f->count--;
    return v;
}

void multicore_fifo_push_blocking(uint32_t data) {
    host_fifo_t *f = host_out_fifo();
    pthread_mutex_lock(&f->lock);
    while (f->count == HOST_SHIM_FIFO_DEPTH) {
        pthread_cond_wait(&f->not_full, &f->lock);
    }
    host_fifo_push_locked(f, data);
    pthread_cond_signal(&f->not_empty);
    pthread_mutex_unlock(&f->lock);
}

bool multicore_fifo_push_timeout_us(uint32_t data, uint64_t timeout_us) {
    host_fifo_t *f = host_out_fifo();
    struct timespec deadline;
    host_timespec_from_now_us(&deadline, timeout_us);

    pthread_mutex_lock(&f->lock);
    bool timed_out = false;
    while (f->count == HOST_SHIM_FIFO_DEPTH && !timed_out) {
        timed_out = pthread_cond_timedwait(&f->not_full, &f->lock, &deadline) != 0;
    }
    bool pushed = false;
    if (f->count < HOST_SHIM_FIFO_DEPTH) {
        host_fifo_push_locked(f, data);
        pthread_cond_signal(&f->not_empty);
        pushed = true;
    }
    pthread_mutex_unlock(&f->lock);
    return pushed;
}

uint32_t multicore_fifo_pop_blocking(void) {
    host_fifo_t *f = host_in_fifo();
    pthread_mutex_lock(&f->lock);
    while (f->count == 0) {
        pthread_cond_wait(&f->not_empty, &f->lock);
    }
    uint32_t v = host_fifo_pop_locked(f);
    pthread_cond_signal(&f->not_full);
    pthread_mutex_unlock(&f->lock);
    return v;
}

bool multicore_fifo_pop_timeout_us(uint64_t timeout_us, uint32_t *out) {
    host_fifo_t *f = host_in_fifo();
    struct timespec deadline;
    host_timespec_from_now_us(&deadline, timeout_us);

    pthread_mutex_lock(&f->lock);
    bool timed_out = false;
    while (f->count == 0 && !timed_out) {
        timed_out = pthread_cond_timedwait(&f->not_empty, &f->lock, &deadline) != 0;
    }
    bool popped = false;
    if (f->count > 0) {
        *out = host_fifo_pop_locked(f);
        pthread_cond_signal(&f->not_full);
        popped = true;
    }
    pthread_mutex_unlock(&f->lock);
    return popped;
}

void multicore_fifo_drain(void) {
    host_fifo_t *f = host_in_fifo();
    pthread_mutex_lock(&f->lock);
    f->head = 0;
    f->count = 0;
    pthread_cond_signal(&f->not_full);
    pthread_mutex_unlock(&f->lock);
}

void multicore_fifo_clear_irq(void) {
    /* On real hardware this write-1-to-clears the sticky ROE/WOF bits in FIFO_ST. Our queues
     * never overflow (push blocks instead of overwriting) or underflow (pop blocks instead of
     * reading garbage), so there is no sticky error state to clear; this is a faithful no-op. */
}

uint32_t multicore_fifo_get_status(void) {
    /* SIO FIFO_ST-like bits, from the calling core's point of view: bit0 VLD (data available to
     * pop), bit1 RDY (space available to push). ROE/WOF are intentionally not modelled -- see
     * multicore_fifo_clear_irq() above. */
    uint32_t status = 0;
    if (multicore_fifo_rvalid()) status |= 1u << 0;
    if (multicore_fifo_wready()) status |= 1u << 1;
    return status;
}

/* ------------------------------------------------------------------------------------------
 * Core 1 launch
 * ---------------------------------------------------------------------------------------- */

typedef struct {
    void (*entry)(void);
} host_core1_launch_t;

static pthread_mutex_t g_core1_lock = PTHREAD_MUTEX_INITIALIZER;
static pthread_t g_core1_thread;
static bool g_core1_running = false;

static void *host_core1_trampoline(void *arg) {
    host_core1_launch_t launch = *(host_core1_launch_t *) arg;
    free(arg);

    host_shim_core_num = 1;
    launch.entry();

    /* Real core 1 firmware never returns from its entry point either; if it does here, there
     * is nothing sensible to run next, so just let the thread (and its "core") end. */
    pthread_mutex_lock(&g_core1_lock);
    g_core1_running = false;
    pthread_mutex_unlock(&g_core1_lock);
    return NULL;
}

static void host_launch_core1(void (*entry)(void)) {
    pthread_mutex_lock(&g_core1_lock);
    if (g_core1_running) {
        pthread_mutex_unlock(&g_core1_lock);
        panic("multicore_launch_core1: core 1 is already running");
    }

    host_core1_launch_t *arg = malloc(sizeof(host_core1_launch_t));
    if (!arg) {
        pthread_mutex_unlock(&g_core1_lock);
        panic("multicore_launch_core1: out of memory");
    }
    arg->entry = entry;

    int rc = pthread_create(&g_core1_thread, NULL, host_core1_trampoline, arg);
    if (rc != 0) {
        free(arg);
        pthread_mutex_unlock(&g_core1_lock);
        panic("multicore_launch_core1: pthread_create failed");
    }
    g_core1_running = true;
    pthread_mutex_unlock(&g_core1_lock);
}

void multicore_launch_core1(void (*entry)(void)) {
    host_launch_core1(entry);
}

void multicore_launch_core1_with_stack(void (*entry)(void), uint32_t *stack_bottom, size_t stack_size_bytes) {
    /* Real hardware runs core 1 on caller-supplied stack memory; a host pthread has no
     * equivalent notion worth modelling (its stack is anonymous mmap'd memory the kernel
     * manages), so this delegates to the plain launch and ignores the placement. */
    (void) stack_bottom;
    (void) stack_size_bytes;
    host_launch_core1(entry);
}

void multicore_launch_core1_raw(void (*entry)(void), uint32_t *sp, uint32_t vector_table) {
    /* The "raw" launch bypasses the bootrom core1 launch protocol and hands core 1 its own
     * vector table; neither concept exists on the host, so this too delegates. */
    (void) sp;
    (void) vector_table;
    host_launch_core1(entry);
}

void multicore_reset_core1(void) {
    /* Real hardware can halt and reset core 1 unconditionally via the bootrom. A pthread
     * cannot be forced back to a clean, known state without risking whatever locks or
     * resources it held, so this is best-effort only: if core 1 looks like it is running, ask
     * it to stop and wait for it to go away; if it's already idle, this is a no-op exactly as
     * on real hardware. */
    pthread_mutex_lock(&g_core1_lock);
    bool running = g_core1_running;
    pthread_t thread = g_core1_thread;
    pthread_mutex_unlock(&g_core1_lock);

    if (!running) {
        return;
    }

    pthread_cancel(thread);
    pthread_join(thread, NULL);

    pthread_mutex_lock(&g_core1_lock);
    g_core1_running = false;
    pthread_mutex_unlock(&g_core1_lock);

    /* The FIFOs are shared, addressed state, not per-launch state, so a reset drains them just
     * like power-cycling core 1 would leave nothing of its in-flight traffic behind. */
    multicore_fifo_drain();
}

/* ------------------------------------------------------------------------------------------
 * Lockout: no-ops. There is no second core to actually lock out of anything on the host, and
 * the host build never fires an IRQ into a "victim" thread the way real hardware does, so the
 * only faithful behaviour is to report success without doing anything.
 * ---------------------------------------------------------------------------------------- */

void multicore_lockout_victim_init(void) {
}

bool multicore_lockout_start_timeout_us(uint64_t timeout_us) {
    (void) timeout_us;
    return true;
}

void multicore_lockout_start_blocking(void) {
}

bool multicore_lockout_end_timeout_us(uint64_t timeout_us) {
    (void) timeout_us;
    return true;
}

void multicore_lockout_end_blocking(void) {
}
