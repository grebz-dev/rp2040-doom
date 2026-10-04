/* SPDX-License-Identifier: GPL-2.0-or-later */
/* Host evidence seam in the real PD renderer. No host buffers in device builds. */
#include "world_capture.h"
#include "doom/doomstat.h"
#include "doom/info.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <errno.h>

uint8_t fcpico_world_owner;
static const char *capture_directory;
static unsigned capture_frame;
static uint8_t owners[320 * 200], unlit[320 * 200], weapon[320 * 200], fuzz[320 * 200];
static uint16_t projected_spans[65536][4];
static unsigned span_count;
static uint8_t admitted[FCPICO_WORLD_MAX + 1];
static unsigned actor_count;
static FILE *admission_file;
static unsigned next_frame, next_actor;
static int has_next;
struct actor {
    int sprite, frame, patch, flip, flags, scale, light, x1, x2, x, y, z;
    unsigned projected, visible, weapon_overlap;
};
static struct actor actors[FCPICO_WORLD_MAX];

static void fail(const char *message) {
    fprintf(stderr, "world capture: %s\n", message);
    exit(2);
}

static void next_admission(void) {
    unsigned previous_frame = next_frame, previous_actor = next_actor;
    int result = fscanf(admission_file, "%u,%u", &next_frame, &next_actor);
    if (result == EOF) { has_next = 0; return; }
    if (result != 2 || !next_actor || next_actor > FCPICO_WORLD_MAX ||
        (has_next && (next_frame < previous_frame ||
         (next_frame == previous_frame && next_actor <= previous_actor))))
        fail("admission rows must be sorted unique frame,actor pairs");
    has_next = 1;
}

int fcpico_world_configure(const char *directory, const char *admission) {
    capture_directory = directory;
    if (directory && mkdir(directory, 0777) && errno != EEXIST) return 0;
    if (admission) {
        if (!directory) return 0;
        admission_file = fopen(admission, "r");
        if (!admission_file) return 0;
        next_admission();
    }
    return 1;
}

void fcpico_world_begin(void) {
    fcpico_world_owner = 0;
    actor_count = 0;
    if (!capture_directory) return;
    memset(owners, 0, sizeof owners);
    memset(unlit, 0, sizeof unlit);
    memset(weapon, 0, sizeof weapon);
    memset(fuzz, 0, sizeof fuzz);
    span_count = 0;
    memset(actors, 0, sizeof actors);
    memset(admitted, 0, sizeof admitted);
    while (has_next && next_frame == capture_frame) {
        admitted[next_actor] = 1;
        next_admission();
    }
    if (has_next && next_frame < capture_frame) fail("stale admission frame");
}

uint8_t fcpico_world_actor(int sprite, int frame, int patch, int flip,
                         int flags, int scale, int light, int x1, int x2,
                         int x, int y, int z) {
    if (!capture_directory || actor_count == FCPICO_WORLD_MAX) return 0;
    struct actor *a = &actors[actor_count++];
    a->sprite = sprite; a->frame = frame; a->patch = patch; a->flip = flip;
    a->flags = flags; a->scale = scale; a->light = light; a->x1 = x1; a->x2 = x2;
    a->x = x; a->y = y; a->z = z;
    return (uint8_t)actor_count;
}

int fcpico_world_suppressed(uint8_t owner) {
    return owner && owner <= FCPICO_WORLD_MAX && admitted[owner];
}

void fcpico_world_projected(uint8_t owner, int x, int yl, int yh) {
    if (!capture_directory || !owner || x < 0 || x >= 320) return;
    if (span_count == 65536) fail("projected span capture capacity exceeded");
    projected_spans[span_count][0] = owner;
    projected_spans[span_count][1] = (uint16_t)x;
    projected_spans[span_count][2] = (uint16_t)yl;
    projected_spans[span_count++][3] = (uint16_t)yh;
    for (int y = yl; y <= yh; ++y) {
        if (y < 0 || y >= 200) continue;
        unsigned at = y * 320 + x;
        if (owner == FCPICO_WORLD_WEAPON) weapon[at] = 1;
        else if (owner <= actor_count) {
            if (actors[owner - 1].light < 0) fuzz[at] = 1;
            ++actors[owner - 1].projected;
            actors[owner - 1].weapon_overlap += weapon[at] != 0;
        }
    }
}

void fcpico_world_visible(uint8_t owner, int x, int yl, int yh) {
    if (!capture_directory || !owner || x < 0 || x >= 320) return;
    for (int y = yl; y <= yh; ++y) {
        if (y < 0 || y >= 200) continue;
        owners[y * 320 + x] = owner;
        if (owner <= actor_count) ++actors[owner - 1].visible;
    }
}

void fcpico_world_pixels(uint8_t owner, int x, int yl, int count,
                         const uint8_t *pixels, int frac, int step) {
    if (!capture_directory || !owner || owner == FCPICO_WORLD_WEAPON) return;
    for (int i = 0; i <= count; ++i) {
        int y = yl + i;
        if (x >= 0 && x < 320 && y >= 0 && y < 200)
            unlit[y * 320 + x] = pixels[(frac >> 16) & 127];
        frac += step;
    }
}

static FILE *open_frame(const char *suffix) {
    char path[1024];
    if (snprintf(path, sizeof path, "%s/frame%06u.%s", capture_directory,
                 capture_frame, suffix) >= (int)sizeof path) fail("output path too long");
    FILE *file = fopen(path, "wb");
    if (!file) fail("cannot open frame");
    return file;
}

static void dump(const char *suffix, const void *data, size_t size) {
    FILE *file = open_frame(suffix);
    if (fwrite(data, 1, size, file) != size || fclose(file)) fail("cannot write frame");
}

void fcpico_world_end(unsigned frame, int video_type, int palette) {
    if (capture_directory) {
        if (frame != capture_frame) fail("render/output generation mismatch");
        dump("owners", owners, sizeof owners);
        dump("unlit", unlit, sizeof unlit);
        dump("weapon", weapon, sizeof weapon);
        dump("fuzz", fuzz, sizeof fuzz);
        dump("spans", projected_spans, span_count * sizeof projected_spans[0]);
        FILE *file = open_frame("actors.csv");
        fprintf(file, "id,sprite,frame,patch,flip,flags,scale,light,x1,x2,x,y,z,projected,visible,weapon_overlap,suppressed\n");
        for (unsigned i = 0; i < actor_count; ++i) {
            const struct actor *a = &actors[i];
            fprintf(file, "%u,%d,%d,%d,%d,%u,%d,%d,%d,%d,%d,%d,%d,%u,%u,%u,%u\n",
                    i + 1, a->sprite, a->frame, a->patch, a->flip,
                    (unsigned)a->flags, a->scale, a->light, a->x1, a->x2,
                    a->x, a->y, a->z, a->projected, a->visible, a->weapon_overlap,
                    admitted[i + 1]);
        }
        if (fclose(file)) fail("cannot write actors");
        file = open_frame("state");
        fprintf(file, "%d,%d,%d,%d,%d,%d\n", gamestate, video_type, palette,
                menuactive, automapactive, gametic);
        if (fclose(file)) fail("cannot write state");
    }
    ++capture_frame;
}
