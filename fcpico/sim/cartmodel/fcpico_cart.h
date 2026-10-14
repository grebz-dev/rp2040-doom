/*
 * fcpico_cart.h -- bounded host cartridge adapter for Mesen co-simulation.
 *
 * SPDX-License-Identifier: BSD-3-Clause
 */
#ifndef FCPICO_CART_H
#define FCPICO_CART_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include "fcbus_core.h"

#ifdef __cplusplus
extern "C" {
#endif

/**
 * Extra counters owned by the adapter, alongside the protocol counters returned
 * by fcpico_cart_stats().  They let the mapper and its scenario log distinguish
 * a stopped host bus (which maps to open bus) from a real 0xFF stream byte.
 */
typedef struct {
    uint64_t ppu_reads;
    uint64_t ppu_writes;
    uint64_t open_bus_reads;
    uint32_t init_actions;
    uint32_t pattern_frames;
    uint8_t last_init_stage;
} fcpico_cart_metrics_t;

/**
 * Starts the one process-global host cartridge.
 *
 * `prg` is the console's 32 KiB headerless PRG.  The cartridge copies it and
 * serves that copy for FP_COM_VER / FP_COM_ROM, unless the environment variable
 * FCPICO_SERVE_ROM names an iNES image whose PRG the firmware should serve
 * instead (the S1 reflash scenario).  Returns false for a null pointer, a
 * length other than FCBUS_ROM_PRG_BYTES or an unreadable FCPICO_SERVE_ROM.
 */
bool fcpico_cart_init(const uint8_t *prg, size_t prg_len);

/** Stops this adapter's public interface.  The underlying host backend has no
 * external resources, so this is safe to call repeatedly. */
void fcpico_cart_shutdown(void);

/**
 * One CS1-qualified PPU read.  fcbus_host reports -1 while its DMA model is
 * stopped; cartridges have no driven byte in that state, so the adapter exposes
 * the defined open-bus value 0xFF to an emulator mapper instead.
 */
uint8_t fcpico_cart_ppu_read(void);

/**
 * One CS1-qualified PPU write, normally a 6502 `$2007` write.  FP_COM_RST
 * recreates the host link from the retained PRG image and resets both protocol
 * statistics and fcpico_cart_metrics(), matching a firmware restart.
 */
void fcpico_cart_ppu_write(uint8_t value);

/** Advances the host backend's monotonic heartbeat watchdog clock. */
void fcpico_cart_tick_ms(uint32_t now_ms);

/** Protocol statistics from the real fcbus host backend, or NULL before init. */
const fcbus_stats_t *fcpico_cart_stats(void);

/** Adapter-owned diagnostics for scenario logging, or NULL before init. */
const fcpico_cart_metrics_t *fcpico_cart_metrics(void);

#ifdef __cplusplus
}
#endif

#endif /* FCPICO_CART_H */
