/* SPDX-License-Identifier: BSD-3-Clause */
#ifndef FCBUS_DEVICE_H
#define FCBUS_DEVICE_H

#include <stdbool.h>
#include <stdint.h>

#include "fcbus_core.h"

typedef struct {
    fcbus_core_t core;
    int dma_channel;
    uint32_t response_words[66];
    uint32_t response_word_count;
    uint8_t pad_frames[32];
    uint8_t pad_read;
    uint8_t pad_write;
    void (*heartbeat_callback)(void *user, uint32_t frame);
    void *heartbeat_user;
#if FCPICO_DIAGNOSTIC_ENGINE_DELAY
    uint32_t diag_raw_read_count;
#endif
} fcbus_device_t;

bool fcbus_device_init(fcbus_device_t *device, const fcbus_config_t *config);
void fcbus_device_deinit(fcbus_device_t *device);
void fcbus_device_poll(fcbus_device_t *device);
uint16_t *fcbus_device_stream_back(fcbus_device_t *device);
bool fcbus_device_back_is_free(const fcbus_device_t *device);
void fcbus_device_publish(fcbus_device_t *device);
/** Passive sprite-fetch probe (FCPICO_SPRITE_FETCH_PROBE): cumulative PPU reads
 *  with A12 high, and with A12 high while CS1 selects the cartridge. Zero when
 *  the probe is not built. */
void fcbus_device_probe_counts(uint32_t *pa12, uint32_t *pa12_cs1);
const fcbus_stats_t *fcbus_device_stats(const fcbus_device_t *device);
const uint8_t *fcbus_device_attributes(const fcbus_device_t *device);
const uint8_t *fcbus_device_palette(const fcbus_device_t *device);
const uint8_t *fcbus_device_mailbox(const fcbus_device_t *device);
/** Pop one pad-1 snapshot captured at a console heartbeat. */
bool fcbus_device_pop_pad_frame(fcbus_device_t *device, uint8_t *pad1);
/** Runs on core 0's bus IRQ after DMA re-arm, with a fresh next mailbox.
 * Keep bounded and protect shared application state against IRQ interruption. */
void fcbus_device_set_heartbeat_callback(fcbus_device_t *device,
    void (*callback)(void *user, uint32_t frame), void *user);

#endif
