/* SPDX-License-Identifier: BSD-3-Clause */
/* pico-sdk host declares hard_assertion_failure but does not link runtime.c. */
#include <stdio.h>
#include <stdlib.h>

void hard_assertion_failure(void)
{
    fputs("pico-sdk hard assertion failed\n", stderr);
    abort();
}
