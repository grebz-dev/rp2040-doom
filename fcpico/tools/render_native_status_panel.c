/* SPDX-License-Identifier: BSD-3-Clause */
/* Host-only pixel dump of the exact device status-panel painter. */
#include "fcvideo.h"

#include <stdio.h>
#include <stdlib.h>

int main(int argc, char **argv) {
    if (argc < 5 || argc > 9) return 2;
    static fcvideo_t video;
    fcui_status_t status = {0};
    status.flags = FCUI_FLAG_STATUS_VISIBLE;
    status.health = (uint16_t)atoi(argv[2]);
    status.armor = (uint16_t)atoi(argv[3]);
    status.ready_weapon = (uint8_t)(argc > 5 ? atoi(argv[5]) : 2);
    status.ammo[0] = 60;
    status.maxammo[0] = 200;
    status.ammo[1] = 20;
    status.maxammo[1] = 50;
    status.ammo[2] = 100;
    status.maxammo[2] = 300;
    status.ammo[3] = 4;
    status.maxammo[3] = 50;
    status.ammo[1] = (uint16_t)atoi(argv[4]);
    if (argc > 6) status.ammo[0] = (uint16_t)atoi(argv[6]);
    if (argc > 7) status.ammo[2] = (uint16_t)atoi(argv[7]);
    if (argc > 8) status.ammo[3] = (uint16_t)atoi(argv[8]);
    fcvideo_set_native_status(&video, true);
    fcvideo_set_status_snapshot(&video, &status);
    fcvideo_blank_status(&video);
    FILE *out = fopen(argv[1], "wb");
    if (!out) return 3;
    int ok = fwrite(video.frame, 1, FCVIDEO_FRAME_BYTES, out) == FCVIDEO_FRAME_BYTES;
    fclose(out);
    return ok ? 0 : 4;
}
