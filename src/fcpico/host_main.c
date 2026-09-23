/* SPDX-License-Identifier: GPL-2.0-or-later */
/* SDL-free entry point for the FC PICO engine host build. */

#include "i_system.h"
#include "m_argv.h"
#include "fcpico_video_sink.h"
#include "fcvideo.h"
#include "doom/m_menu.h"
#include "d_loop.h"
#include "w_wad.h"
#include "z_zone.h"
#include <png.h>
#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>

extern void D_DoomMain(void);

const unsigned char *fcpico_host_whx;
static const char *dump_8bit_dir;
static const char *dump_stream_dir;
static unsigned frame_limit;
static unsigned frame_count;
static unsigned menu_at;
static unsigned char composed_frame[320 * 200];
static fcvideo_t stream_video;
static uint8_t stream_err[FCVIDEO_ERR_BYTES];
static uint8_t stream_lut[FCVIDEO_LUT_BYTES];
static uint8_t stream_palettes[FCVIDEO_PALETTE_SET_COUNT][MBX_PAL_LEN];
static uint8_t stream_frame[VRAM_BUF_BYTES_V2];
static uint8_t stream_attr[MBX_ATTR_LEN];
static bool stream_initialized;

static void write_bytes(const char *path, const void *data, size_t size)
{
    FILE *output = fopen(path, "wb");
    if (output == NULL) {
        fprintf(stderr, "cannot open %s: %s\n", path, strerror(errno));
        exit(1);
    }
    if (fwrite(data, 1, size, output) != size) {
        fprintf(stderr, "cannot write %s\n", path);
        fclose(output);
        exit(1);
    }
    if (fclose(output) != 0) {
        fprintf(stderr, "cannot close %s\n", path);
        exit(1);
    }
}

static void write_png(const char *path)
{
    const uint8_t *playpal = W_CacheLumpNum(W_GetNumForName("PLAYPAL"), PU_STATIC);
    png_color colors[256];
    png_bytep rows[200];
    png_structp png;
    png_infop info;
    FILE *output = fopen(path, "wb");
    if (output == NULL) {
        fprintf(stderr, "cannot open %s\n", path);
        exit(1);
    }
    png = png_create_write_struct(PNG_LIBPNG_VER_STRING, NULL, NULL, NULL);
    info = png != NULL ? png_create_info_struct(png) : NULL;
    if (info == NULL || setjmp(png_jmpbuf(png))) {
        fprintf(stderr, "cannot encode %s\n", path);
        fclose(output);
        exit(1);
    }
    for (int i = 0; i < 256; ++i) {
        colors[i].red = playpal[i * 3];
        colors[i].green = playpal[i * 3 + 1];
        colors[i].blue = playpal[i * 3 + 2];
    }
    for (int y = 0; y < 200; ++y) rows[y] = composed_frame + y * 320;
    png_init_io(png, output);
    png_set_IHDR(png, info, 320, 200, 8, PNG_COLOR_TYPE_PALETTE,
                 PNG_INTERLACE_NONE, PNG_COMPRESSION_TYPE_DEFAULT,
                 PNG_FILTER_TYPE_DEFAULT);
    png_set_PLTE(png, info, colors, 256);
    png_write_info(png, info);
    png_write_image(png, rows);
    png_write_end(png, NULL);
    png_destroy_write_struct(&png, &info);
    if (fclose(output) != 0) {
        fprintf(stderr, "cannot close %s\n", path);
        exit(1);
    }
}

void fcvideo_line_sink(int y, const uint8_t *line320)
{
    memcpy(composed_frame + y * 320, line320, 320);
}

void fcvideo_frame_end(int palette_num, int video_type)
{
    (void)video_type;
    if (dump_8bit_dir != NULL) {
        char path[1024];
        if (snprintf(path, sizeof(path), "%s/frame%06u.raw", dump_8bit_dir,
                     frame_count) >= (int)sizeof(path)) {
            fputs("frame path too long\n", stderr);
            exit(1);
        }
        write_bytes(path, composed_frame, sizeof(composed_frame));
        if (frame_count % 100 == 0) {
            if (snprintf(path, sizeof(path), "%s/frame%06u.png", dump_8bit_dir,
                         frame_count) >= (int)sizeof(path)) {
                fputs("PNG path too long\n", stderr);
                exit(1);
            }
            write_png(path);
        }
    }
    if (dump_stream_dir != NULL) {
        char path[1024];
        if (!stream_initialized) {
            const uint8_t *playpal = W_CacheLumpNum(W_GetNumForName("PLAYPAL"), PU_STATIC);
            fcvideo_tables_t tables = {stream_err, stream_lut, {0}};
            fcvideo_build_tables(playpal, &fcvideo_presets[FCVIDEO_DEFAULT_PRESET],
                                 stream_err, stream_lut, tables.palette);
            fcvideo_build_palette_sets(&fcvideo_presets[FCVIDEO_DEFAULT_PRESET],
                                       stream_palettes);
            fcvideo_init(&stream_video, &tables);
            stream_initialized = true;
        }
        if (palette_num < 0 || palette_num >= FCVIDEO_PALETTE_SET_COUNT) {
            fprintf(stderr, "invalid Doom palette %d\n", palette_num);
            exit(1);
        }
        fcvideo_set_palette(&stream_video, stream_palettes[palette_num]);
        fcvideo_convert(&stream_video, composed_frame, stream_frame, stream_attr,
                        frame_count == 0);
        if (snprintf(path, sizeof(path), "%s/frame%06u.bin", dump_stream_dir,
                     frame_count) >= (int)sizeof(path)) {
            fputs("stream path too long\n", stderr);
            exit(1);
        }
        write_bytes(path, stream_frame, sizeof(stream_frame));
    }
    ++frame_count;
    if (frame_count == menu_at) M_StartControlPanel();
    if (frame_count == frame_limit) {
        printf("host frames=%u\n", frame_count);
        exit(0);
    }
}

static void usage(const char *program)
{
    fprintf(stderr, "usage: %s --whx FILE [--demo N] [--frames N] [--lockstep] "
                    "[--dump-8bit DIR] [--menu-at N] [--dump-stream DIR] "
                    "[--pads FILE]\n", program);
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
        } else if (strcmp(argv[i], "--dump-stream") == 0 && i + 1 < argc) {
            dump_stream_dir = argv[++i];
        } else if (strcmp(argv[i], "--menu-at") == 0 && i + 1 < argc) {
            menu_at = (unsigned)atoi(argv[++i]);
            if (menu_at == 0) { usage(argv[0]); return 2; }
        } else if (strcmp(argv[i], "--pads") == 0 && i + 1 < argc) {
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
    if (dump_stream_dir != NULL && mkdir(dump_stream_dir, 0777) != 0 && errno != EEXIST) {
        fprintf(stderr, "cannot create %s: %s\n", dump_stream_dir, strerror(errno));
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
    singletics = 1;
    I_Init();
    D_DoomMain();
    return 0;
}
