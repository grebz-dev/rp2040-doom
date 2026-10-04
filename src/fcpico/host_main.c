/* SPDX-License-Identifier: GPL-2.0-or-later */
/* SDL-free entry point for the FC PICO engine host build. */

#if FCPICO_WORLD_CAPTURE
#include "world_capture.h"
static const char *world_directory, *world_admission;
#endif
#if FCPICO_WORLD_SPRITES
#include "world_sprites.h"
/* Per composed frame: the v4 picture with admitted actors suppressed plus the
 * native world generation, replayed by the Mesen cartridge model. */
static const char *world_dump;
static uint8_t frame_shown_gen;
static bool world_off;
#endif
#if FCPICO_WORLD_CAPTURE || FCPICO_WORLD_SPRITES
#include "ui_capture.h"
#endif
#include "i_system.h"
#include "m_argv.h"
#include "fcpico_video_sink.h"
#include "i_audio_fcpico.h"
#include "pico/platform.h"
#include "fcvideo.h"
#include "doom/m_menu.h"
#include "doom/doomstat.h"
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
static FILE *apu_file;

bool fcpico_audio_write(void *user, uint8_t reg, uint8_t value)
{
    (void)user;
    if (apu_file && fprintf(apu_file, "%u,%02x,%02x\n", frame_count, reg, value) < 0) {
        fprintf(stderr, "cannot write APU capture\n");
        exit(1);
    }
    return true;
}

void fcpico_audio_update(void)
{
    /* Host rendered frames provide a deterministic synthetic heartbeat. */
    if (get_core_num() != 0) return;
    I_FCPicoAudioPump(frame_count);
}
static unsigned use_frames;
static unsigned menu_at;
static unsigned char composed_frame[320 * 200];
static fcvideo_t stream_video;
static uint8_t stream_err[FCVIDEO_ERR_BYTES];
static uint8_t stream_lut[FCVIDEO_LUT_BYTES];
static uint8_t stream_palettes[FCVIDEO_PALETTE_SET_COUNT][MBX_PAL_LEN];
static uint8_t stream_frame[VRAM_BUF_BYTES_V4];
static uint8_t stream_attr[MBX_ATTR_LEN];
static bool stream_initialized;
static FILE *pads_file;
static bool pad_supplied;

bool fcpico_read_pad_frame(uint8_t *pad1)
{
    if (pads_file == NULL || pad_supplied) return false;
    pad_supplied = true;
    char line[80];
    if (fgets(line, sizeof line, pads_file) == NULL) {
        *pad1 = 0;
        return true;
    }
    char *end;
    unsigned long value = strtoul(line, &end, 0);
    if (end == line || value > 255) {
        fputs("invalid --pads line; expected one numeric pad byte per tic\n", stderr);
        exit(2);
    }
    while (*end == ' ' || *end == '\t' || *end == '\r' || *end == '\n') ++end;
    if (*end != '\0') {
        fputs("invalid --pads line; expected one numeric pad byte per tic\n", stderr);
        exit(2);
    }
    *pad1 = (uint8_t)value;
    return true;
}

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
    if (players[consoleplayer].cmd.buttons & BT_USE) ++use_frames;
    pad_supplied = false;
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
        size_t stream_size = VRAM_BUF_BYTES_V2;
        fcvideo_set_palette(&stream_video, stream_palettes[palette_num]);
#if FCPICO_WORLD_CAPTURE || FCPICO_WORLD_SPRITES
        bool native_stream = false;
#if FCPICO_WORLD_CAPTURE
        native_stream = native_stream || world_directory != NULL;
#endif
#if FCPICO_WORLD_SPRITES
        native_stream = native_stream || world_dump != NULL;
#endif
        if (native_stream) {
            fcui_status_t status;
            fcpico_ui_capture(&status, (uint8_t)(frame_count % 254 + 1));
            bool native = (status.flags & FCUI_FLAG_STATUS_VISIBLE) != 0;
            uint8_t palette[MBX_PAL_LEN];
            memcpy(palette, stream_palettes[palette_num], sizeof palette);
#if FCVIDEO_DEFAULT_PRESET != FCVIDEO_PRESET_SHARED_HUD
            if (native) {
                uint8_t *hud = palette + FCVIDEO_HUD_PALETTE * 4;
                hud[0] = 0x0f; hud[1] = 0; hud[2] = 0x30; hud[3] = 0x16;
            }
#endif
            fcvideo_set_palette(&stream_video, palette);
            fcvideo_set_native_status(&stream_video, native);
            fcvideo_set_status_snapshot(&stream_video, &status);
            fcvideo_frame_begin(&stream_video);
            for (int y = 0; y < 200; ++y)
                fcvideo_push_line(&stream_video, y, composed_frame + y * 320);
            fcvideo_blank_status(&stream_video);
            fcvideo_convert_staged(&stream_video, stream_frame, stream_attr, frame_count == 0);
            uint8_t mailbox[FC_COM_BUF_SIZE_V4] = {0};
            memcpy(mailbox, stream_frame + VRAM_MAILBOX_OFF_V2, FC_COM_BUF_SIZE_V2);
            mailbox[MBX_FLAGS] |= MBX_FLAG_V3 | MBX_FLAG_V4 | MBX_FLAG_UI_VALID;
            fcui_pack_status(mailbox + MBX_UI, &status);
            fcvideo_compact_native_text(stream_frame);
            memcpy(stream_frame + VRAM_MAILBOX_OFF_V4, mailbox, sizeof mailbox);
            stream_size = VRAM_BUF_BYTES_V4;
        } else
#endif
        fcvideo_convert(&stream_video, composed_frame, stream_frame, stream_attr,
                        frame_count == 0);
        if (snprintf(path, sizeof(path), "%s/frame%06u.bin", dump_stream_dir,
                     frame_count) >= (int)sizeof(path)) {
            fputs("stream path too long\n", stderr);
            exit(1);
        }
        write_bytes(path, stream_frame, stream_size);
    }
#if FCPICO_WORLD_CAPTURE
    fcpico_world_end(frame_count, video_type, palette_num);
#endif
#if FCPICO_WORLD_SPRITES
    {
        /* The host has no console; the cartridge model owns shown state. */
        fcworld_frame_t frame;
        /* Replay submits every recorded frame in order, so each one is the
         * shown generation for the next plan. */
        bool used = fcpico_world_prepare(&frame, frame_shown_gen);
        fcpico_world_note_submitted(&frame);
        frame_shown_gen = frame.oam_count ? 1 : 0;
        if (world_dump != NULL) {
            char path[1024];
            if (snprintf(path, sizeof path, "%s/frame%06u.world", world_dump, frame_count) >=
                (int)sizeof path) {
                fputs("world path too long\n", stderr);
                exit(1);
            }
            uint8_t record[16 + FCWORLD_MAX_TILES * 16 + WORLD_OAM_ENTRIES * 4];
            size_t n = 0;
            memcpy(record, "FCWF", 4);
            n = 4;
            record[n++] = used;
            record[n++] = frame.tile_count;
            record[n++] = frame.oam_count;
            record[n++] = frame.peak_line;
            memcpy(record + n, frame.palettes, MBX_WORLD_PAL_LEN);
            n += MBX_WORLD_PAL_LEN;
            memcpy(record + n, frame.tiles, frame.tile_count * 16u);
            n += frame.tile_count * 16u;
            memcpy(record + n, frame.oam, frame.oam_count * 4u);
            n += frame.oam_count * 4u;
            write_bytes(path, record, n);
        }
    }
#endif
    ++frame_count;
    if (frame_count == menu_at) M_StartControlPanel();
    if (frame_count == frame_limit) {
        if (players[consoleplayer].mo != NULL) {
            printf("player x=%ld y=%ld\n", (long)players[consoleplayer].mo->xy.x,
                   (long)players[consoleplayer].mo->xy.y);
        }
        printf("host frames=%u\n", frame_count);
        const fcapu_stats_t *audio_stats = fcapu_stats();
        printf("apu frames=%u pairs_max=%u drops=%u\n", audio_stats->frames,
               audio_stats->pairs_per_frame_max, audio_stats->dropped);
        if (pads_file != NULL) printf("use frames=%u\n", use_frames);
#if FCPICO_WORLD_SPRITES
        const fcpico_world_stats_t *ws = fcpico_world_stats();
        printf("world frames=%u enabled=%u candidates=%u admitted=%u streamed budget=%u "
               "effect=%u lookup=%u mismatch=%u overflow=%u build_failures=%u\n",
               ws->frames, ws->enabled_frames, ws->candidates, ws->admitted,
               ws->streamed_budget, ws->streamed_effect, ws->streamed_lookup,
               ws->streamed_mismatch, ws->streamed_overflow, ws->build_failures);
#endif
        if (apu_file && fclose(apu_file) != 0) {
            fprintf(stderr, "cannot finish APU capture\n");
            exit(1);
        }
        exit(0);
    }
}

static void usage(const char *program)
{
    fprintf(stderr, "usage: %s --whx FILE [--demo N] [--frames N] [--lockstep] "
                    "[--dump-8bit DIR] [--menu-at N] [--dump-stream DIR] "
                    "[--pads FILE] [--warp EPISODE MAP] [--dump-apu FILE]\n", program);
}

int main(int argc, char **argv)
{
    const char *whx_path = NULL;
    FILE *file;
    long length;
    unsigned char *data;
    int i;
    int demo = 0;
    int warp_episode = 0;
    int warp_map = 0;
    char *number_end;
    unsigned long parsed_frames;
    char demo_name[8];
    char warp_episode_arg[4], warp_map_arg[4];
    char *engine_argv[7];
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
        } else if (strcmp(argv[i], "--dump-apu") == 0 && i + 1 < argc) {
            apu_file = fopen(argv[++i], "w");
            if (!apu_file) {
                fprintf(stderr, "cannot create APU capture %s: %s\n", argv[i], strerror(errno));
                return 2;
            }
        } else if (strcmp(argv[i], "--dump-stream") == 0 && i + 1 < argc) {
            dump_stream_dir = argv[++i];
#if FCPICO_WORLD_CAPTURE
        } else if (strcmp(argv[i], "--world-capture") == 0 && i + 1 < argc) {
            world_directory = argv[++i];
        } else if (strcmp(argv[i], "--world-admit") == 0 && i + 1 < argc) {
            world_admission = argv[++i];
#endif
#if FCPICO_WORLD_SPRITES
        } else if (strcmp(argv[i], "--world-dump") == 0 && i + 1 < argc) {
            world_dump = argv[++i];
        } else if (strcmp(argv[i], "--world-off") == 0) {
            /* Same recording format with every actor streamed (baseline). */
            world_off = true;
#endif
        } else if (strcmp(argv[i], "--menu-at") == 0 && i + 1 < argc) {
            menu_at = (unsigned)atoi(argv[++i]);
            if (menu_at == 0) { usage(argv[0]); return 2; }
        } else if (strcmp(argv[i], "--pads") == 0 && i + 1 < argc) {
            pads_file = fopen(argv[++i], "r");
            if (pads_file == NULL) {
                fprintf(stderr, "cannot read pads %s: %s\n", argv[i], strerror(errno));
                return 2;
            }
        } else if (strcmp(argv[i], "--warp") == 0 && i + 2 < argc) {
            warp_episode = atoi(argv[++i]);
            warp_map = atoi(argv[++i]);
            if (warp_episode < 1 || warp_episode > 4 || warp_map < 1 || warp_map > 9) {
                usage(argv[0]); return 2;
            }
        } else if (strcmp(argv[i], "--lockstep") != 0) {
            usage(argv[0]);
            return 2;
        }
    }
    if (whx_path == NULL || frame_limit == 0 || (demo && warp_episode)) {
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
#if FCPICO_WORLD_SPRITES
    if (world_dump != NULL) {
        if (dump_stream_dir == NULL || (mkdir(world_dump, 0777) && errno != EEXIST) ||
            !fcpico_world_init_default()) {
            fprintf(stderr, "--world-dump needs --dump-stream and a matching atlas\n");
            return 2;
        }
        fcpico_world_set_link(!world_off);
    }
#endif
#if FCPICO_WORLD_CAPTURE
    if (!fcpico_world_configure(world_directory, world_admission)) {
        fprintf(stderr, "cannot configure world capture\n");
        return 2;
    }
#endif
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
    if (warp_episode != 0) {
        snprintf(warp_episode_arg, sizeof warp_episode_arg, "%d", warp_episode);
        snprintf(warp_map_arg, sizeof warp_map_arg, "%d", warp_map);
        engine_argv[engine_argc++] = "-warp";
        engine_argv[engine_argc++] = warp_episode_arg;
        engine_argv[engine_argc++] = warp_map_arg;
    }
    myargc = engine_argc;
    myargv = engine_argv;
    singletics = 1;
    I_Init();
    D_DoomMain();
    return 0;
}
