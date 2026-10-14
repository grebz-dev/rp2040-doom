/* SPDX-License-Identifier: BSD-3-Clause */
#include "trace.h"

#include <stdio.h>

#include "hardware/clocks.h"
#include "hardware/dma.h"
#include "hardware/pio.h"

static uint32_t trace_words[FCPICO_TRACE_WORDS];
static int trace_dma = -1;
static uint trace_offset;

/* in pins, 5; right shifting leaves each 30-bit payload in DMA bits 31:2. */
static const uint16_t trace_instructions[] = {0x4005};
static const struct pio_program trace_program = {
    .instructions = trace_instructions,
    .length = 1,
    .origin = -1,
};

bool trace_init(void) {
    if (!pio_can_add_program(pio1, &trace_program)) {
        return false;
    }
    trace_offset = pio_add_program(pio1, &trace_program);
    pio_sm_config config = pio_get_default_sm_config();
    sm_config_set_wrap(&config, trace_offset, trace_offset);
    sm_config_set_in_pins(&config, 17);
    sm_config_set_in_shift(&config, true, true, 30);
    float divider = (float)clock_get_hz(clk_sys) * (float)FCPICO_TRACE_PERIOD_NS / 1.0e9f;
    sm_config_set_clkdiv(&config, divider);
    pio_sm_init(pio1, 0, trace_offset, &config);

    trace_dma = dma_claim_unused_channel(true);
    dma_channel_config dma_config = dma_channel_get_default_config((uint)trace_dma);
    channel_config_set_transfer_data_size(&dma_config, DMA_SIZE_32);
    channel_config_set_read_increment(&dma_config, false);
    channel_config_set_write_increment(&dma_config, true);
    channel_config_set_dreq(&dma_config, pio_get_dreq(pio1, 0, false));
    dma_channel_configure((uint)trace_dma, &dma_config, trace_words, &pio1_hw->rxf[0],
                          FCPICO_TRACE_WORDS, false);
    return true;
}

bool trace_capture(void) {
    if (trace_dma < 0) {
        return false;
    }
    pio_sm_set_enabled(pio1, 0, false);
    pio_sm_clear_fifos(pio1, 0);
    pio_sm_restart(pio1, 0);
    dma_channel_set_write_addr((uint)trace_dma, trace_words, false);
    dma_channel_set_trans_count((uint)trace_dma, FCPICO_TRACE_WORDS, true);
    pio_sm_set_enabled(pio1, 0, true);
    dma_channel_wait_for_finish_blocking((uint)trace_dma);
    pio_sm_set_enabled(pio1, 0, false);
    return true;
}

void trace_dump(void) {
    printf("TRACE period_ns=%u samples=%u words=%u\n", FCPICO_TRACE_PERIOD_NS,
           FCPICO_TRACE_WORDS * FCPICO_TRACE_SAMPLES_PER_WORD, FCPICO_TRACE_WORDS);
    for (uint32_t index = 0; index < FCPICO_TRACE_WORDS; ++index) {
        /* Normalize to the decoder's low-aligned, oldest-sample-first format. */
        printf("%08lx%c", (unsigned long)(trace_words[index] >> 2),
               (index & 7u) == 7u ? '\n' : ' ');
    }
    if ((FCPICO_TRACE_WORDS & 7u) != 0u) {
        putchar('\n');
    }
    puts("END TRACE");
    fflush(stdout);
}
