/*
 * fcbus_host.h -- host backend for fcbus_core: a byte-stream model of the PPU bus.
 *
 * SPDX-License-Identifier: BSD-3-Clause
 *
 * Simulates both sides of the wire fcbus_core.h talks about, without any PIO or DMA:
 * fcbus_host_ppu_read() stands in for a PPU pattern fetch (what rp_system's DMA/PIO
 * combination would present on the data bus), fcbus_host_ppu_write() stands in for the
 * 6502 writing a byte to `$2007`. Used by fcpico/tests/fcbus/, and by the higher
 * simulation levels (PPU-bus model, Mesen cartridge model) that need a cartridge model
 * without device hardware.
 *
 * One global core instance, matching "core 1 owns the one and only rp_system::sys
 * instance" in the original -- a host process only ever needs one bus link.
 */
#ifndef FCBUS_HOST_H
#define FCBUS_HOST_H

#include <stddef.h>
#include <stdint.h>

#include "fcbus_core.h"

#ifdef __cplusplus
extern "C" {
#endif

/** One entry in the inspectable action log (see fcbus_host_action_log()). Actions that
 *  drive the serving state machine directly (STREAM_RESPONSE, HEARTBEAT, STOP_DMA) are
 *  consumed internally and never appear here -- only the ones a test or a caller needs
 *  to observe after the fact: INIT, RESET, LOG, PROTO_ERROR. */
typedef struct {
    fcbus_action_kind_t kind;
    uint32_t arg;
    uint8_t data[FCBUS_LOG_MAX];
    uint16_t len;
} fcbus_host_log_entry_t;

/** Resets the host backend and the core it wraps. Mirrors rp_system::init(). */
void fcbus_host_init(const fcbus_config_t *cfg);

/** Enable the one stale PIO read before each bulk DLD payload when driving
 * the NES $2007 read path in Mesen. Direct host-byte tests leave this off. */
void fcbus_host_set_bulk_lead_byte(bool enabled);

/**
 * @brief Returns the next byte the PPU would receive, or -1 while the DMA is stopped.
 *
 * While a FCBUS_ACT_STREAM_RESPONSE is pending, serves its bytes in order, then 0xFF
 * ("late" reads with no answer queued -- there is no flow control on real hardware
 * either) until the next heartbeat re-arms streaming. After a heartbeat resolves to one
 * of the ARM variants, serves the front stream buffer from byte 0. ARM_NUDGE1/2 consume
 * one or two buffer bytes first (see fcbus_core.h).
 *
 * Every served byte -- response bytes included -- increments the
 * qualifying-read counter that becomes `count` at the next heartbeat, exactly as the
 * real PIO counter (`fcppu_rna`) counts every CS1-qualified read regardless of which PIO
 * program is actually driving the bus at that moment. Reads taken while the DMA is
 * stopped are counted too -- they return -1 here because no valid data is on the bus, but
 * the counter must still advance or the link could never recover phase (a stopped frame
 * would report a count of zero for ever).
 */
int fcbus_host_ppu_read(void);

/** A `$2007` write. Runs fcbus_core_rx_byte() and immediately updates the serving state
 *  from whatever actions that produced (STREAM_RESPONSE / HEARTBEAT / STOP_DMA); other
 *  actions are appended to the inspectable log. */
void fcbus_host_ppu_write(uint8_t b);

/** Writes a whole burst (e.g. an FP_COM_LOG payload shorter than FCBUS_LOG_MAX bytes)
 *  and then signals fcbus_core_rx_idle(), exactly as a burst ending on a real RX FIFO
 *  going empty would. */
void fcbus_host_write_burst(const uint8_t *data, size_t len);

/** Advances the host backend's clock, for the heartbeat-timeout watchdog
 *  (fcbus_core_tick_ms()). */
void fcbus_host_tick_ms(uint32_t now_ms);

/**
 * @brief The core instance this backend wraps.
 *
 * fcbus_host.h only wraps the "wire" side of fcbus_core.h (rx_byte / heartbeat / tick_ms,
 * driven here by ppu_read()/ppu_write()). The converter-side API -- fcbus_core_stream_back(),
 * fcbus_core_publish(), fcbus_core_cmd()/cmd_vram()/apu_write()/attr_table()/palette(),
 * fcbus_core_request_data_mode(), fcbus_core_set_step() -- is not re-wrapped here; callers
 * (tests, and anything else standing in for the converter) use this pointer to call those
 * directly on the same instance ppu_read()/ppu_write() serve from, exactly as a real
 * converter on core 1 would act on the one shared rp_system::sys-equivalent. Reading
 * `core->state` / `core->stats` etc. directly works too.
 */
fcbus_core_t *fcbus_host_core(void);

/** The inspectable action log (INIT/RESET/LOG/PROTO_ERROR), oldest first. */
const fcbus_host_log_entry_t *fcbus_host_action_log(void);

/** Number of valid entries at the front of fcbus_host_action_log(). */
size_t fcbus_host_action_log_count(void);

/** Empties the action log without touching the core or the serving state. */
void fcbus_host_action_log_clear(void);

#ifdef __cplusplus
}
#endif

#endif /* FCBUS_HOST_H */
