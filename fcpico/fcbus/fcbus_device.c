/* SPDX-License-Identifier: BSD-3-Clause */
#include "fcbus_device.h"

#include <string.h>

#include "fcppu.pio.h"
#include "hardware/dma.h"
#include "hardware/gpio.h"
#include "hardware/irq.h"
#include "hardware/pio.h"
#include "hardware/sync.h"
#include "pico/platform.h"
#include "pico/time.h"

enum { SM_RECV = 0, SM_TRAN = 1, SM_BUSDIR = 2, SM_TRCNT = 3 };

static fcbus_device_t *irq_device;

static void __not_in_flash_func(stop_dma)(fcbus_device_t *device) {
    dma_channel_abort((uint)device->dma_channel);
    pio_sm_clear_fifos(pio0, SM_TRAN);
    pio_sm_restart(pio0, SM_TRAN);
}

static void __not_in_flash_func(start_words)(fcbus_device_t *device,
                                               const void *data, uint32_t bytes) {
    /* A heartbeat can replace a stream before DMA has exhausted the old one. */
    dma_channel_abort((uint)device->dma_channel);
    pio_sm_clear_fifos(pio0, SM_TRAN);
    pio_sm_restart(pio0, SM_TRAN);
    dma_channel_set_read_addr((uint)device->dma_channel, data, false);
    dma_channel_set_trans_count((uint)device->dma_channel, (bytes + 3u) / 4u, true);
}

static uint32_t __not_in_flash_func(sample_read_count)(void) {
    /* Mirrors rp_system.cpp:354-357: push !X, reset X, restart, then read. */
    pio_sm_exec(pio0, SM_TRCNT, pio_encode_push(false, false));
    pio_sm_exec(pio0, SM_TRCNT, pio_encode_mov_not(pio_x, pio_null));
    pio_sm_restart(pio0, SM_TRCNT);
    return pio_sm_get(pio0, SM_TRCNT);
}

#if FCPICO_SPRITE_FETCH_PROBE
#define SM_PA12 1
#define SM_PA12_CS1 2
static uint32_t probe_sample(uint sm) {
    pio_sm_exec(pio1, sm, pio_encode_mov_not(pio_isr, pio_x));
    pio_sm_exec(pio1, sm, pio_encode_push(false, false));
    return pio_sm_get(pio1, sm);
}

void fcbus_device_probe_counts(uint32_t *pa12, uint32_t *pa12_cs1) {
    *pa12 = probe_sample(SM_PA12);
    *pa12_cs1 = probe_sample(SM_PA12_CS1);
}

static void probe_init(void) {
    gpio_init(PI_PA12_BIT);
    gpio_set_dir(PI_PA12_BIT, GPIO_IN);
    uint offset = pio_add_program(pio1, &fcppu_pa12_count_program);
    pio_sm_config config = fcppu_pa12_count_program_get_default_config(offset);
    sm_config_set_jmp_pin(&config, PI_PA12_BIT);
    pio_sm_init(pio1, SM_PA12, offset, &config);
    offset = pio_add_program(pio1, &fcppu_pa12_cs1_count_program);
    config = fcppu_pa12_cs1_count_program_get_default_config(offset);
    sm_config_set_in_pins(&config, PI_CS1_BIT);
    sm_config_set_in_shift(&config, false, false, 32);
    pio_sm_init(pio1, SM_PA12_CS1, offset, &config);
    pio_sm_set_enabled(pio1, SM_PA12, true);
    pio_sm_set_enabled(pio1, SM_PA12_CS1, true);
}
#else
void fcbus_device_probe_counts(uint32_t *pa12, uint32_t *pa12_cs1) { *pa12 = *pa12_cs1 = 0; }
#endif

#if FCPICO_DIAGNOSTIC_ENGINE_DELAY
static uint32_t __not_in_flash_func(sample_raw_read_count)(void) {
    pio_sm_exec(pio1, 0, pio_encode_push(false, false));
    pio_sm_exec(pio1, 0, pio_encode_mov_not(pio_x, pio_null));
    pio_sm_restart(pio1, 0);
    return pio_sm_get(pio1, 0);
}
#endif

static void __not_in_flash_func(handle_actions)(fcbus_device_t *device) {
    fcbus_action_t action;
    while (fcbus_core_pop_action(&device->core, &action)) {
        if (action.kind == FCBUS_ACT_HEARTBEAT) {
            /* Mirrors rp_system.cpp:350-366: restart, DMA the front buffer, then nudge. */
            uint32_t bytes = device->core.proto >= FCBUS_PROTO_V3 ?
                VRAM_BUF_BYTES_V3 : VRAM_BUF_BYTES_V2;
            start_words(device, fcbus_core_stream_front(&device->core), bytes);
            if (action.arg == FCBUS_ARM_NUDGE2) {
                pio_sm_exec(pio0, SM_TRAN, pio_encode_out(pio_pins, 8));
                pio_sm_exec(pio0, SM_TRAN, pio_encode_out(pio_pins, 8));
            } else if (action.arg == FCBUS_ARM_NUDGE1) {
                pio_sm_exec(pio0, SM_TRAN, pio_encode_out(pio_pins, 8));
            }
            if (device->heartbeat_callback) {
                device->heartbeat_callback(device->heartbeat_user, device->core.frame_no);
            }
        } else if (action.kind == FCBUS_ACT_STOP_DMA) {
            stop_dma(device);
        } else if (action.kind == FCBUS_ACT_STREAM_RESPONSE) {
            memset(device->response_words, 0xff, sizeof device->response_words);
            memcpy(device->response_words, action.data, action.len);
            device->response_word_count = (action.len + 3u) / 4u;
            start_words(device, device->response_words, action.len);
        }
    }
}

static void __not_in_flash_func(pio0_rx_irq)(void) {
    fcbus_device_t *device = irq_device;
    while (!pio_sm_is_rx_fifo_empty(pio0, SM_RECV)) {
        /* Mirrors getRcvCom()/jobRcvCom(): low byte from each RX FIFO word. */
        uint8_t byte = (uint8_t)pio_sm_get(pio0, SM_RECV);
        if (fcbus_core_rx_will_heartbeat(&device->core, byte)) {
#if FCPICO_DIAGNOSTIC_ENGINE_DELAY
            device->diag_raw_read_count = sample_raw_read_count();
#endif
            fcbus_core_set_read_count(&device->core, sample_read_count());
        }
        uint32_t previous_frame = device->core.frame_no;
        fcbus_core_rx_byte(&device->core, byte);
        if (device->core.frame_no != previous_frame) {
            uint8_t next = (uint8_t)((device->pad_write + 1u) & 31u);
            if (next == device->pad_read) {
                device->pad_read = (uint8_t)((device->pad_read + 1u) & 31u);
            }
            device->pad_frames[device->pad_write] = device->core.pad1;
            device->pad_write = next;
        }
        handle_actions(device);
    }
    fcbus_core_rx_idle(&device->core);
    handle_actions(device);
}

bool fcbus_device_init(fcbus_device_t *device, const fcbus_config_t *config) {
    memset(device, 0, sizeof *device);
    fcbus_core_init(&device->core, config);

    /* Mirrors rp_system.cpp:56-164: the four PIO0 state-machine configurations. */
    gpio_init_mask((1u << PI_WR_BIT) | (1u << PI_RD_BIT) | (1u << PI_CS1_BIT) |
                   (0xffu << PI_D0_BIT));
    gpio_set_dir_in_masked((1u << PI_WR_BIT) | (1u << PI_RD_BIT) | (1u << PI_CS1_BIT));
    for (uint pin = PI_D0_BIT; pin <= PI_D7_BIT; ++pin) {
        gpio_pull_up(pin);
        pio_gpio_init(pio0, pin);
    }

    uint offset = pio_add_program(pio0, &fcppu_w_program);
    pio_sm_config config_w = fcppu_w_program_get_default_config(offset);
    sm_config_set_in_pins(&config_w, PI_D0_BIT);
    sm_config_set_jmp_pin(&config_w, PI_CS1_BIT);
    sm_config_set_fifo_join(&config_w, PIO_FIFO_JOIN_RX);
    sm_config_set_in_shift(&config_w, true, true, 32);
    pio_sm_init(pio0, SM_RECV, offset, &config_w);

    offset = pio_add_program(pio0, &fcppu_r_program);
    pio_sm_config config_r = fcppu_r_program_get_default_config(offset);
    sm_config_set_out_pins(&config_r, PI_D0_BIT, 8);
    sm_config_set_jmp_pin(&config_r, PI_CS1_BIT);
    sm_config_set_fifo_join(&config_r, PIO_FIFO_JOIN_TX);
    sm_config_set_out_shift(&config_r, true, true, 32);
    pio_sm_init(pio0, SM_TRAN, offset, &config_r);

    offset = pio_add_program(pio0, &fcppu_dir_program);
    pio_sm_config config_dir = fcppu_dir_program_get_default_config(offset);
    sm_config_set_out_pins(&config_dir, PI_D0_BIT, 8);
    sm_config_set_in_pins(&config_dir, PI_D0_BIT);
    sm_config_set_jmp_pin(&config_dir, PI_RD_BIT);
    pio_sm_init(pio0, SM_BUSDIR, offset, &config_dir);

    offset = pio_add_program(pio0, &fcppu_rna_program);
    pio_sm_config config_count = fcppu_rna_program_get_default_config(offset);
    sm_config_set_jmp_pin(&config_count, PI_CS1_BIT);
    sm_config_set_fifo_join(&config_count, PIO_FIFO_JOIN_RX);
    sm_config_set_in_shift(&config_count, true, true, 32);
    pio_sm_init(pio0, SM_TRCNT, offset, &config_count);

#if FCPICO_DIAGNOSTIC_ENGINE_DELAY
    offset = pio_add_program(pio1, &fcppu_raw_count_program);
    pio_sm_config raw_config = fcppu_raw_count_program_get_default_config(offset);
    sm_config_set_fifo_join(&raw_config, PIO_FIFO_JOIN_RX);
    sm_config_set_in_shift(&raw_config, true, true, 32);
    pio_sm_init(pio1, 0, offset, &raw_config);
#endif

    /* Mirrors rp_dma.cpp:263-286: one 32-bit, read-incrementing, TX-DREQ channel. */
    device->dma_channel = dma_claim_unused_channel(true);
    dma_channel_config dma_config = dma_channel_get_default_config((uint)device->dma_channel);
    channel_config_set_transfer_data_size(&dma_config, DMA_SIZE_32);
    channel_config_set_read_increment(&dma_config, true);
    channel_config_set_write_increment(&dma_config, false);
    channel_config_set_dreq(&dma_config, pio_get_dreq(pio0, SM_TRAN, true));
    dma_channel_configure((uint)device->dma_channel, &dma_config, &pio0_hw->txf[SM_TRAN],
                          NULL, 0, false);

    irq_device = device;
    pio_set_irq0_source_enabled(pio0, pis_sm0_rx_fifo_not_empty, true);
    irq_set_exclusive_handler(PIO0_IRQ_0, pio0_rx_irq);
    irq_set_enabled(PIO0_IRQ_0, true);
    pio_enable_sm_mask_in_sync(pio0, 0x0fu);
#if FCPICO_DIAGNOSTIC_ENGINE_DELAY
    pio_sm_set_enabled(pio1, 0, true);
#endif
#if FCPICO_SPRITE_FETCH_PROBE
    probe_init();
#endif

    /* Mirrors rp_system.cpp:171-173: make the version stream available at boot. */
    fcbus_core_rx_byte(&device->core, FP_COM_VER);
    handle_actions(device);
    return true;
}

void fcbus_device_set_heartbeat_callback(fcbus_device_t *device,
    void (*callback)(void *user, uint32_t frame), void *user) {
    uint32_t saved = save_and_disable_interrupts();
    device->heartbeat_user = user;
    device->heartbeat_callback = callback;
    restore_interrupts(saved);
}

void fcbus_device_deinit(fcbus_device_t *device) {
    irq_set_enabled(PIO0_IRQ_0, false);
    dma_channel_unclaim((uint)device->dma_channel);
    pio_set_sm_mask_enabled(pio0, 0x0fu, false);
#if FCPICO_DIAGNOSTIC_ENGINE_DELAY
    pio_sm_set_enabled(pio1, 0, false);
#endif
    irq_device = NULL;
}

void fcbus_device_poll(fcbus_device_t *device) {
    fcbus_core_tick_ms(&device->core, to_ms_since_boot(get_absolute_time()));
    handle_actions(device);
}

uint16_t *fcbus_device_stream_back(fcbus_device_t *device) {
    return fcbus_core_stream_back(&device->core);
}

bool fcbus_device_back_is_free(const fcbus_device_t *device) {
    return fcbus_core_back_is_free(&device->core);
}

void fcbus_device_publish(fcbus_device_t *device) { fcbus_core_publish(&device->core); }
const fcbus_stats_t *fcbus_device_stats(const fcbus_device_t *device) {
    return fcbus_core_stats(&device->core);
}
const uint8_t *fcbus_device_attributes(const fcbus_device_t *device) {
    return device->core.attr_want;
}
const uint8_t *fcbus_device_palette(const fcbus_device_t *device) {
    return device->core.pal_want;
}
const uint8_t *fcbus_device_mailbox(const fcbus_device_t *device) {
    return device->core.mailbox_next;
}

bool fcbus_device_pop_pad_frame(fcbus_device_t *device, uint8_t *pad1) {
    uint32_t saved = save_and_disable_interrupts();
    bool available = device->pad_read != device->pad_write;
    if (available) {
        *pad1 = device->pad_frames[device->pad_read];
        device->pad_read = (uint8_t)((device->pad_read + 1u) & 31u);
    }
    restore_interrupts(saved);
    return available;
}
