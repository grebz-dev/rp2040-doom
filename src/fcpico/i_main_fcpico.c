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
#if FCPICO_DIAGNOSTIC_ENGINE_DELAY
    gpio_init(PICO_DEFAULT_LED_PIN);
    gpio_set_dir(PICO_DEFAULT_LED_PIN, GPIO_OUT);
    for (unsigned remaining = 30; remaining != 0; --remaining) {
        fcpico_video_device_poll_bootsel();
        gpio_put(PICO_DEFAULT_LED_PIN, remaining & 1u);
        printf("FC PICO diagnostic: Doom starts in %u s\n", remaining);
        sleep_ms(1000);
    }
    puts("FC PICO diagnostic: entering D_DoomMain");
#endif
    I_Init();
    D_DoomMain();
    for (;;) {
        tight_loop_contents();
    }
#endif
}
