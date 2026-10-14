/*
 * SPDX-License-Identifier: BSD-3-Clause
 *
 * Host replacements for the pico-sdk alarm/repeating-timer convenience functions that
 * PICO_PLATFORM=host does not provide:
 *
 *   - pico-sdk's src/common/pico_time/include/pico/time.h guards add_alarm_in_us(),
 *     add_alarm_in_ms(), cancel_alarm(), add_repeating_timer_us() and add_repeating_timer_ms()
 *     behind `#if !PICO_TIME_DEFAULT_ALARM_POOL_DISABLED`, and
 *     src/host/hardware_timer/CMakeLists.txt sets PICO_TIME_DEFAULT_ALARM_POOL_DISABLED=1 for
 *     the host platform. So on host these five are not merely unimplemented, they are not even
 *     *declared* -- callers get an implicit-declaration warning (an error under -Werror) and
 *     then an undefined reference at link time. This header re-declares four of them
 *     (add_alarm_in_ms() is a trivial wrapper the caller can write in terms of
 *     add_alarm_in_us(), so it is not reproduced here) with alarm_host.c backing them with a
 *     single background pthread.
 *
 *   - cancel_repeating_timer() is the odd one out: pico/time.h declares it unconditionally and
 *     src/common/pico_time/time.c *defines* it unconditionally too, so it links fine even on
 *     host. But its only functioning path (a match) ends in a call to the alarm-pool timer
 *     adapter's ta_force_irq(), and src/host/pico_time_adapter/time_adapter.c stubs every ta_*
 *     hook to panic_unsupported(). In other words the symbol exists, but successfully
 *     cancelling anything through it panics the process -- worse than a link error, since it
 *     would build fine and only fail at runtime. We cannot fix or shadow it (it is a normal,
 *     non-weak symbol already linked in from pico_time), so repeating timers created through
 *     add_repeating_timer_us()/_ms() below must be cancelled with host_cancel_repeating_timer()
 *     instead -- never with the SDK's cancel_repeating_timer().
 */

#ifndef _HOST_SHIM_ALARM_HOST_H
#define _HOST_SHIM_ALARM_HOST_H

#include "pico/time.h"

#ifdef __cplusplus
extern "C" {
#endif

/* Same contract as the SDK's own add_alarm_in_us() (src/common/pico_time/include/pico/time.h):
 * returns >0 (the alarm id) on success, <0 if no alarm slot was available. fire_if_past has no
 * observable effect here since `us` is always a non-negative delay from now. */
alarm_id_t add_alarm_in_us(uint64_t us, alarm_callback_t callback, void *user_data, bool fire_if_past);

/* Same contract as the SDK's own cancel_alarm(): true if an armed alarm with this id was
 * found and cancelled, false otherwise. Safe to call on an id that has already fired. */
bool cancel_alarm(alarm_id_t alarm_id);

/* Same contract as the SDK's own add_repeating_timer_us()/_ms(): populate *out and return true,
 * or return false if no timer slot was available. `out` must outlive the timer, exactly as the
 * SDK documents -- do not use a stack-local repeating_timer_t that goes out of scope before the
 * timer is cancelled. `out->pool` is left NULL and `out->alarm_id` holds this shim's internal
 * id, not a real pico-sdk alarm_id_t -- do not pass either to alarm_pool_* functions. */
bool add_repeating_timer_us(int64_t delay_us, repeating_timer_callback_t callback, void *user_data,
                             repeating_timer_t *out);
bool add_repeating_timer_ms(int32_t delay_ms, repeating_timer_callback_t callback, void *user_data,
                             repeating_timer_t *out);

/* Use this in place of the SDK's cancel_repeating_timer() for any timer obtained from
 * add_repeating_timer_us()/_ms() above -- see the file header comment for why. Same contract:
 * true if the timer was found and cancelled, false otherwise. */
bool host_cancel_repeating_timer(repeating_timer_t *timer);

#ifdef __cplusplus
}
#endif

#endif
