/* SPDX-License-Identifier: GPL-2.0-or-later */
/* Minimal cartridge entry point until the full board bring-up is wired in. */

#include "pico/stdlib.h"
#include "i_system.h"
#include "fcpico_video_sink.h"
#include <stdio.h>

extern void D_DoomMain(void);

int main(void)
{
    stdio_init_all();
    fcpico_video_device_init();
    puts("FC PICO DOOM video bring-up");
    I_Init();
    D_DoomMain();
    for (;;) {
        tight_loop_contents();
    }
}
