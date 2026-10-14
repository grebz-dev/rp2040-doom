/* SPDX-License-Identifier: BSD-3-Clause */
#ifndef AUDIO_TEST_SYNC_H
#define AUDIO_TEST_SYNC_H
#include <stdint.h>
uint32_t save_and_disable_interrupts(void);
void restore_interrupts(uint32_t saved);
#endif
