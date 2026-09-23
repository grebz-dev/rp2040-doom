/* SPDX-License-Identifier: GPL-2.0-or-later */
/* Minimal cartridge entry point until the full board bring-up is wired in. */

#include "pico/stdlib.h"
#include "i_system.h"
#include <stdio.h>

extern void D_DoomMain(void);

int main(void)
{
    stdio_init_all();
    sleep_ms(1000);
    puts("FC PICO DOOM engine skeleton");
    I_Init();
    D_DoomMain();
    for (;;) {
        tight_loop_contents();
    }
}
