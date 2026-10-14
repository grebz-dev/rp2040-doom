/* SPDX-License-Identifier: BSD-3-Clause */
#ifndef FCPICO_TRACE_H
#define FCPICO_TRACE_H

#include <stdbool.h>
#include <stdint.h>

/* About 2 ms / 31 NTSC scanlines.  This is long enough to contain complete lines
 * while keeping the USB serial dump below common 256 KiB terminal limits. */
#define FCPICO_TRACE_WORDS 8192u
#define FCPICO_TRACE_SAMPLES_PER_WORD 6u
#define FCPICO_TRACE_PERIOD_NS 40u

bool trace_init(void);
bool trace_capture(void);
void trace_dump(void);

#endif
