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
#if FCPICO_DIAGNOSTIC_NO_ENGINE
    gpio_init(PICO_DEFAULT_LED_PIN);
    gpio_set_dir(PICO_DEFAULT_LED_PIN, GPIO_OUT);
    for (unsigned seconds = 0;; ++seconds) {
        fcpico_video_device_poll_bootsel();
        gpio_put(PICO_DEFAULT_LED_PIN, seconds & 1u);
        printf("FC PICO diagnostic: USB and bus initialized, uptime=%u s\n", seconds);
        sleep_ms(1000);
    }
#else
    puts("FC PICO DOOM video bring-up");
    /* Starting Doom at power-on breaks the console's boot-ROM version check
     * (PICO NOT FOUND) and USB enumeration on hardware, even with Doom's bus
     * output held back. Idling here fixes both; 3 s is the tested value. */
    for (unsigned waited_ms = 0; waited_ms < 3000; waited_ms += 100) {
        fcpico_video_device_poll_bootsel();
        sleep_ms(100);
    }
#if FCPICO_DIAGNOSTIC_ENGINE_DELAY
    puts("FC PICO diagnostic: entering D_DoomMain");
#endif
    I_Init();
    D_DoomMain();
    for (;;) {
        tight_loop_contents();
    }
#endif
}
