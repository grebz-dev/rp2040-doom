/*
 * SPDX-License-Identifier: BSD-3-Clause
 *
 * See alarm_host.h for why this file exists and what it deliberately does not reuse from the
 * SDK's own alarm-pool machinery (src/common/pico_time/time.c): that machinery is driven by a
 * hardware timer IRQ, and every hook it needs from the platform adapter
 * (src/host/pico_time_adapter/time_adapter.c: ta_set_timeout, ta_enable_irq_handler,
 * ta_force_irq, ...) is stubbed to panic_unsupported() on the host. So instead of trying to
 * make an alarm_pool_t work, this is a small, independent scheduler: one background pthread
 * that sleeps (via a CLOCK_MONOTONIC condvar, so its deadlines line up with time_us_64(), which
 * the host's src/host/hardware_timer/timer.c also derives from CLOCK_MONOTONIC) until the next
 * due alarm or repeating timer, then runs callbacks outside the lock so they may themselves
 * call back into add_alarm_in_us()/cancel_alarm() without deadlocking.
 */

#include <pthread.h>
#include <stddef.h>
#include <stdint.h>
#include <time.h>

#include "alarm_host.h"
#include "pico/error.h"

#define HOST_SHIM_MAX_ALARMS 32

typedef enum {
    SLOT_FREE = 0,
    SLOT_ARMED,
    SLOT_FIRING,
} host_alarm_state_t;

typedef struct {
    host_alarm_state_t state;
    bool is_repeating;
    bool cancel_requested;
    uint32_t generation; /* 0 only for a never-used slot; real ids start at 1 */
    int64_t target_us;   /* absolute, same epoch as time_us_64() */
    int64_t repeat_delay_us;
    alarm_callback_t alarm_cb;
    repeating_timer_callback_t repeat_cb;
    repeating_timer_t *rt;
    void *user_data;
} host_alarm_slot_t;

/* Zero-initialised: every slot starts SLOT_FREE (0) with generation 0, which alloc_slot() and
 * the cancel functions below both treat correctly without any separate init step. */
static host_alarm_slot_t g_slots[HOST_SHIM_MAX_ALARMS];
static pthread_mutex_t g_lock = PTHREAD_MUTEX_INITIALIZER;
static pthread_cond_t g_wake;
static uint32_t g_next_generation = 1;

static pthread_once_t g_thread_once = PTHREAD_ONCE_INIT;

static void *alarm_thread_main(void *arg);

static void alarm_thread_start(void) {
    pthread_condattr_t attr;
    pthread_condattr_init(&attr);
    pthread_condattr_setclock(&attr, CLOCK_MONOTONIC);
    pthread_cond_init(&g_wake, &attr);
    pthread_condattr_destroy(&attr);

    pthread_t thread;
    pthread_create(&thread, NULL, alarm_thread_main, NULL);
    pthread_detach(thread);
}

static void ensure_alarm_thread(void) {
    pthread_once(&g_thread_once, alarm_thread_start);
}

/* Caller must hold g_lock. */
static bool alloc_slot(int *out_index, uint32_t *out_generation) {
    for (int i = 0; i < HOST_SHIM_MAX_ALARMS; i++) {
        if (g_slots[i].state == SLOT_FREE) {
            uint32_t generation = g_next_generation++;
            if (g_next_generation == 0) {
                g_next_generation = 1; /* never hand out generation 0 */
            }
            g_slots[i].generation = generation;
            *out_index = i;
            *out_generation = generation;
            return true;
        }
    }
    return false;
}

static alarm_id_t make_id(int index, uint32_t generation) {
    return (alarm_id_t) (((uint32_t) index << 16) | (generation & 0xFFFFu));
}

static int id_index(uint32_t bits) {
    return (int) (bits >> 16);
}

static uint32_t id_generation(uint32_t bits) {
    return bits & 0xFFFFu;
}

typedef struct {
    int index;
    uint32_t generation;
    bool is_repeating;
    int64_t fired_target_us;
    int64_t repeat_delay_us;
    alarm_callback_t alarm_cb;
    repeating_timer_callback_t repeat_cb;
    repeating_timer_t *rt;
    void *user_data;
} due_alarm_t;

static void *alarm_thread_main(void *arg) {
    (void) arg;

    for (;;) {
        pthread_mutex_lock(&g_lock);

        bool have_target = false;
        int64_t earliest = 0;
        for (int i = 0; i < HOST_SHIM_MAX_ALARMS; i++) {
            if (g_slots[i].state == SLOT_ARMED && (!have_target || g_slots[i].target_us < earliest)) {
                earliest = g_slots[i].target_us;
                have_target = true;
            }
        }

        if (!have_target) {
            pthread_cond_wait(&g_wake, &g_lock);
            pthread_mutex_unlock(&g_lock);
            continue;
        }

        int64_t now = (int64_t) time_us_64();
        if (earliest > now) {
            struct timespec deadline;
            deadline.tv_sec = (time_t) (earliest / 1000000);
            deadline.tv_nsec = (long) ((earliest % 1000000) * 1000);
            pthread_cond_timedwait(&g_wake, &g_lock, &deadline);
            pthread_mutex_unlock(&g_lock);
            continue;
        }

        /* At least one slot is due; still holding g_lock from the scan above. Snapshot every
         * due slot and flip it to SLOT_FIRING so a concurrent cancel_alarm() /
         * host_cancel_repeating_timer() can see it is mid-callback rather than racing to reuse
         * the slot. */
        due_alarm_t due[HOST_SHIM_MAX_ALARMS];
        int due_count = 0;
        for (int i = 0; i < HOST_SHIM_MAX_ALARMS; i++) {
            host_alarm_slot_t *s = &g_slots[i];
            if (s->state == SLOT_ARMED && s->target_us <= now) {
                s->state = SLOT_FIRING;
                s->cancel_requested = false;

                due[due_count].index = i;
                due[due_count].generation = s->generation;
                due[due_count].is_repeating = s->is_repeating;
                due[due_count].fired_target_us = s->target_us;
                due[due_count].repeat_delay_us = s->repeat_delay_us;
                due[due_count].alarm_cb = s->alarm_cb;
                due[due_count].repeat_cb = s->repeat_cb;
                due[due_count].rt = s->rt;
                due[due_count].user_data = s->user_data;
                due_count++;
            }
        }

        pthread_mutex_unlock(&g_lock);

        for (int i = 0; i < due_count; i++) {
            bool keep = false;
            int64_t next_target = 0;

            if (due[i].is_repeating) {
                bool cont = due[i].repeat_cb(due[i].rt);
                if (cont) {
                    int64_t interval = due[i].repeat_delay_us;
                    if (interval < 0) interval = -interval;
                    if (interval == 0) interval = 1;
                    next_target = due[i].fired_target_us + interval;
                    keep = true;
                }
            } else {
                int64_t delta = due[i].alarm_cb(make_id(due[i].index, due[i].generation), due[i].user_data);
                if (delta != 0) {
                    next_target = (delta < 0) ? (due[i].fired_target_us - delta) : ((int64_t) time_us_64() + delta);
                    keep = true;
                }
            }

            pthread_mutex_lock(&g_lock);
            host_alarm_slot_t *s = &g_slots[due[i].index];
            if (s->state == SLOT_FIRING && s->generation == due[i].generation) {
                if (keep && !s->cancel_requested) {
                    s->target_us = next_target;
                    s->state = SLOT_ARMED;
                } else {
                    s->state = SLOT_FREE;
                }
            }
            pthread_mutex_unlock(&g_lock);
        }
        /* Loop back round: due alarms may have rescheduled themselves, or new ones may have
         * been armed by a callback above. */
    }
    return NULL;
}

alarm_id_t add_alarm_in_us(uint64_t us, alarm_callback_t callback, void *user_data, bool fire_if_past) {
    /* `us` is always a non-negative delay from now, so there is no "the time already passed"
     * case for this shim to special-case the way alarm_pool_add_alarm_at() does. */
    (void) fire_if_past;
    ensure_alarm_thread();

    pthread_mutex_lock(&g_lock);
    int index;
    uint32_t generation;
    if (!alloc_slot(&index, &generation)) {
        pthread_mutex_unlock(&g_lock);
        return PICO_ERROR_INSUFFICIENT_RESOURCES;
    }

    host_alarm_slot_t *s = &g_slots[index];
    s->is_repeating = false;
    s->cancel_requested = false;
    s->target_us = (int64_t) time_us_64() + (int64_t) us;
    s->repeat_delay_us = 0;
    s->alarm_cb = callback;
    s->repeat_cb = NULL;
    s->rt = NULL;
    s->user_data = user_data;
    s->state = SLOT_ARMED;

    alarm_id_t id = make_id(index, generation);
    pthread_cond_signal(&g_wake);
    pthread_mutex_unlock(&g_lock);
    return id;
}

bool cancel_alarm(alarm_id_t alarm_id) {
    uint32_t bits = (uint32_t) alarm_id;
    int index = id_index(bits);
    uint32_t generation = id_generation(bits);
    if (index < 0 || index >= HOST_SHIM_MAX_ALARMS) {
        return false;
    }

    pthread_mutex_lock(&g_lock);
    host_alarm_slot_t *s = &g_slots[index];
    bool cancelled = false;
    if (!s->is_repeating && s->generation == generation) {
        if (s->state == SLOT_ARMED) {
            s->state = SLOT_FREE;
            cancelled = true;
        } else if (s->state == SLOT_FIRING) {
            s->cancel_requested = true;
            cancelled = true;
        }
    }
    pthread_mutex_unlock(&g_lock);
    return cancelled;
}

bool add_repeating_timer_us(int64_t delay_us, repeating_timer_callback_t callback, void *user_data,
                             repeating_timer_t *out) {
    ensure_alarm_thread();

    int64_t interval = delay_us < 0 ? -delay_us : delay_us;
    if (interval == 0) interval = 1;

    pthread_mutex_lock(&g_lock);
    int index;
    uint32_t generation;
    if (!alloc_slot(&index, &generation)) {
        pthread_mutex_unlock(&g_lock);
        return false;
    }

    host_alarm_slot_t *s = &g_slots[index];
    s->is_repeating = true;
    s->cancel_requested = false;
    s->target_us = (int64_t) time_us_64() + interval;
    s->repeat_delay_us = delay_us;
    s->alarm_cb = NULL;
    s->repeat_cb = callback;
    s->rt = out;
    s->user_data = user_data;
    s->state = SLOT_ARMED;
    pthread_cond_signal(&g_wake);
    pthread_mutex_unlock(&g_lock);

    out->delay_us = delay_us;
    out->pool = NULL; /* not a real pico-sdk alarm_pool_t -- see alarm_host.h */
    out->alarm_id = make_id(index, generation);
    out->callback = callback;
    out->user_data = user_data;
    return true;
}

bool add_repeating_timer_ms(int32_t delay_ms, repeating_timer_callback_t callback, void *user_data,
                             repeating_timer_t *out) {
    return add_repeating_timer_us((int64_t) delay_ms * 1000, callback, user_data, out);
}

bool host_cancel_repeating_timer(repeating_timer_t *timer) {
    if (!timer) {
        return false;
    }

    uint32_t bits = (uint32_t) timer->alarm_id;
    int index = id_index(bits);
    uint32_t generation = id_generation(bits);
    if (index < 0 || index >= HOST_SHIM_MAX_ALARMS) {
        return false;
    }

    pthread_mutex_lock(&g_lock);
    host_alarm_slot_t *s = &g_slots[index];
    bool cancelled = false;
    if (s->is_repeating && s->generation == generation && s->rt == timer) {
        if (s->state == SLOT_ARMED) {
            s->state = SLOT_FREE;
            cancelled = true;
        } else if (s->state == SLOT_FIRING) {
            s->cancel_requested = true;
            cancelled = true;
        }
    }
    pthread_mutex_unlock(&g_lock);

    timer->alarm_id = 0;
    return cancelled;
}
