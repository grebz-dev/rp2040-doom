/* SPDX-License-Identifier: BSD-3-Clause */
/* Host fixture: apply the production episode overlay to one v4 frame. */
#include "fcvideo.h"

#include <stdio.h>

int main(int argc, char **argv) {
    if (argc != 2) return 2;
    static uint8_t frame[VRAM_BUF_BYTES_V4];
    FILE *file = fopen(argv[1], "rb");
    if (!file) return 3;
    int ok = fread(frame, 1, sizeof frame, file) == sizeof frame;
    fclose(file);
    if (!ok) return 4;
    uint8_t *mailbox = frame + VRAM_MAILBOX_OFF_V4;
    fcvideo_overlay_episode_menu(frame, mailbox + MBX_ATTR, mailbox + MBX_PAL);
    file = fopen(argv[1], "wb");
    if (!file) return 5;
    ok = fwrite(frame, 1, sizeof frame, file) == sizeof frame;
    fclose(file);
    return ok ? 0 : 6;
}
