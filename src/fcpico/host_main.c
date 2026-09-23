/* SPDX-License-Identifier: GPL-2.0-or-later */
/* SDL-free entry point for the FC PICO engine host build. */

#include "i_system.h"
#include "m_argv.h"
#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>

extern void D_DoomMain(void);

const unsigned char *fcpico_host_whx;
static const char *dump_8bit_dir;
static unsigned frame_limit;
static unsigned frame_count;

void fcpico_host_frame(const unsigned char *pixels, size_t length)
{
    extern volatile unsigned char wipe_min;
    /* No scanline consumer exists on host; complete a wipe at frame boundaries. */
    wipe_min = 200;
    if (dump_8bit_dir != NULL) {
        char path[1024];
        FILE *output;
        if (snprintf(path, sizeof(path), "%s/frame%06u.raw", dump_8bit_dir,
                     frame_count) >= (int)sizeof(path)) {
            fputs("frame path too long\n", stderr);
            exit(1);
        }
        output = fopen(path, "wb");
        if (output == NULL || fwrite(pixels, 1, length, output) != length ||
            fclose(output) != 0) {
            fprintf(stderr, "cannot write %s\n", path);
            exit(1);
        }
    }
    ++frame_count;
    if (frame_count == frame_limit) {
        printf("host frames=%u\n", frame_count);
        exit(0);
    }
}

static void usage(const char *program)
{
    fprintf(stderr, "usage: %s --whx FILE [--demo N] [--frames N] [--lockstep] "
                    "[--dump-8bit DIR] [--dump-stream DIR] [--pads FILE]\n", program);
}

int main(int argc, char **argv)
{
    const char *whx_path = NULL;
    FILE *file;
    long length;
    unsigned char *data;
    int i;
    int demo = 0;
    char *number_end;
    unsigned long parsed_frames;
    char demo_name[8];
    char *engine_argv[4];
    int engine_argc = 1;

    for (i = 1; i < argc; ++i) {
        if (strcmp(argv[i], "--whx") == 0 && i + 1 < argc) {
            whx_path = argv[++i];
        } else if (strcmp(argv[i], "--demo") == 0 && i + 1 < argc) {
            demo = atoi(argv[++i]);
            if (demo < 1 || demo > 3) { usage(argv[0]); return 2; }
        } else if (strcmp(argv[i], "--frames") == 0 && i + 1 < argc) {
            errno = 0;
            parsed_frames = strtoul(argv[++i], &number_end, 10);
            if (errno != 0 || argv[i][0] == '-' || *number_end != '\0' ||
                parsed_frames == 0 || parsed_frames > 1000000) {
                usage(argv[0]);
                return 2;
            }
            frame_limit = (unsigned)parsed_frames;
        } else if (strcmp(argv[i], "--dump-8bit") == 0 && i + 1 < argc) {
            dump_8bit_dir = argv[++i];
        } else if ((strcmp(argv[i], "--dump-stream") == 0 ||
                    strcmp(argv[i], "--pads") == 0) && i + 1 < argc) {
            fprintf(stderr, "%s is not wired into the host runner yet\n", argv[i]);
            return 2;
        } else if (strcmp(argv[i], "--lockstep") != 0) {
            usage(argv[0]);
            return 2;
        }
    }
    if (whx_path == NULL || frame_limit == 0) {
        usage(argv[0]);
        return 2;
    }
    if (dump_8bit_dir != NULL && mkdir(dump_8bit_dir, 0777) != 0 && errno != EEXIST) {
        fprintf(stderr, "cannot create %s: %s\n", dump_8bit_dir, strerror(errno));
        return 1;
    }
    file = fopen(whx_path, "rb");
    if (file == NULL || fseek(file, 0, SEEK_END) != 0 || (length = ftell(file)) < 4 ||
        fseek(file, 0, SEEK_SET) != 0) {
        fprintf(stderr, "cannot read WHX %s: %s\n", whx_path, strerror(errno));
        if (file != NULL) fclose(file);
        return 1;
    }
    data = malloc((size_t)length);
    if (data == NULL || fread(data, 1, (size_t)length, file) != (size_t)length) {
        fprintf(stderr, "cannot load WHX %s\n", whx_path);
        fclose(file);
        free(data);
        return 1;
    }
    fclose(file);
    if (memcmp(data, "IWHX", 4) != 0) {
        fprintf(stderr, "invalid WHX magic in %s\n", whx_path);
        free(data);
        return 1;
    }
    fcpico_host_whx = data;
    engine_argv[0] = argv[0];
    if (demo != 0) {
        snprintf(demo_name, sizeof(demo_name), "DEMO%d", demo);
        engine_argv[engine_argc++] = "-playdemo";
        engine_argv[engine_argc++] = demo_name;
    }
    myargc = engine_argc;
    myargv = engine_argv;
    extern int singletics;
    singletics = 1;
    I_Init();
    D_DoomMain();
    return 0;
}
