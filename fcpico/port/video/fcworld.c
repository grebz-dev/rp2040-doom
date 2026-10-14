/*
 * fcworld.c -- native NES sprites for Doom world actors.
 *
 * SPDX-License-Identifier: BSD-3-Clause
 */
#include "fcworld.h"

#include <string.h>

/* The heartbeat path runs in the cartridge bus IRQ, which must not execute from
 * flash while saves program it. Device builds place it in RAM and avoid libc. */
#if FCWORLD_RAM_FUNCTIONS
#define FCWORLD_RAM(name) __attribute__((section(".time_critical.fcworld_" #name))) name
#else
#define FCWORLD_RAM(name) name
#endif

static void FCWORLD_RAM(copy)(uint8_t *dst, const uint8_t *src, unsigned n) {
    while (n--) *dst++ = *src++;
}

static void FCWORLD_RAM(fill)(uint8_t *dst, uint8_t value, unsigned n) {
    while (n--) *dst++ = value;
}

static bool FCWORLD_RAM(same)(const uint8_t *a, const uint8_t *b, unsigned n) {
    while (n--) if (*a++ != *b++) return false;
    return true;
}

/* ------------------------------------------------------------------------ */
/* Atlas                                                                      */
/* ------------------------------------------------------------------------ */
/* Format v2 (tools/build_world_atlas.py): 48-byte header; sprite records
 * {u16 first entry, u8 frames, u8 family}; u16 entries (patch+1 | flip<<15);
 * family records {6 candidate colours, flags, count, 8 bytes of 4-bit weights,
 * [15][64] select errors, [15][64] map errors}; 12-byte patch records; 4bpp
 * texel rows. */
enum {
    ATLAS_HEADER = 48,
    SPRITE_RECORD = 4,
    PATCH_RECORD = 12,
    FAMILY_RECORD = 16 + 2 * FCWORLD_LOCAL_COLORS * 64,
    ROTATIONS = 8,
};

static uint16_t rd16(const uint8_t *p) { return (uint16_t)(p[0] | p[1] << 8); }
static uint32_t rd32(const uint8_t *p) {
    return (uint32_t)p[0] | (uint32_t)p[1] << 8 | (uint32_t)p[2] << 16 | (uint32_t)p[3] << 24;
}

typedef struct {
    uint32_t sprites, entries, entry_count, patches, families;
} atlas_tables_t;

static atlas_tables_t atlas_tables(const fcworld_atlas_t *atlas) {
    const uint8_t *d = atlas->data;
    atlas_tables_t t = {rd32(d + 20), rd32(d + 24), rd32(d + 28), rd32(d + 32), rd32(d + 36)};
    return t;
}

static bool range_ok(uint32_t offset, uint32_t length, uint32_t size) {
    return offset <= size && length <= size - offset;
}

bool fcworld_atlas_open(fcworld_atlas_t *atlas, const uint8_t *data, uint32_t size) {
    memset(atlas, 0, sizeof *atlas);
    if (data == NULL || size < ATLAS_HEADER || rd32(data) != FCWORLD_ATLAS_MAGIC ||
        rd16(data + 4) != FCWORLD_ATLAS_VERSION || rd32(data + 8) != size) {
        return false;
    }
    fcworld_atlas_t candidate = {data, size, rd16(data + 6), rd16(data + 18), rd16(data + 16),
                                 rd32(data + 12)};
    atlas_tables_t t = atlas_tables(&candidate);
    if (!range_ok(t.sprites, (uint32_t)candidate.sprite_count * SPRITE_RECORD, size) ||
        !range_ok(t.entries, t.entry_count * 2u, size) ||
        !range_ok(t.patches, (uint32_t)candidate.patch_count * PATCH_RECORD, size) ||
        !range_ok(t.families, (uint32_t)candidate.family_count * FAMILY_RECORD, size)) {
        return false;
    }
    for (uint32_t i = 0; i < candidate.sprite_count; ++i) {
        const uint8_t *s = data + t.sprites + i * SPRITE_RECORD;
        uint16_t first = rd16(s);
        if (first != 0xFFFF && ((uint32_t)first + (uint32_t)s[2] * ROTATIONS > t.entry_count ||
                                s[3] >= candidate.family_count)) {
            return false;
        }
    }
    for (uint32_t i = 0; i < t.entry_count; ++i) {
        uint16_t e = rd16(data + t.entries + i * 2);
        if ((e & 0x7FFF) > candidate.patch_count) return false;
    }
    for (uint32_t i = 0; i < candidate.family_count; ++i) {
        const uint8_t *f = data + t.families + i * FAMILY_RECORD;
        if (f[7] == 0 || f[7] > FCWORLD_LOCAL_COLORS) return false;
        for (int c = 0; c < 6; ++c) if (f[c] > 0x3F) return false;
    }
    for (uint32_t i = 0; i < candidate.patch_count; ++i) {
        const uint8_t *p = data + t.patches + i * PATCH_RECORD;
        uint32_t stride = ((uint32_t)p[0] + 1u) / 2u;
        if (p[0] == 0 || p[1] == 0 || p[6] >= candidate.family_count ||
            !range_ok(rd32(p + 8), stride * p[1], size)) {
            return false;
        }
    }
    *atlas = candidate;
    return true;
}

bool fcworld_atlas_family(const fcworld_atlas_t *atlas, unsigned index, fcworld_family_t *family) {
    if (atlas->data == NULL || index >= atlas->family_count) return false;
    const uint8_t *f = atlas->data + atlas_tables(atlas).families + index * FAMILY_RECORD;
    family->candidates = f;
    family->flags = f[6];
    family->count = f[7];
    family->weight[0] = 0;
    for (int v = 1; v < 16; ++v) family->weight[v] = (uint8_t)((f[8 + v / 2] >> ((v & 1) * 4)) & 15);
    family->select = f + 16;
    family->map = f + 16 + FCWORLD_LOCAL_COLORS * 64;
    return true;
}

bool fcworld_atlas_lookup(const fcworld_atlas_t *atlas, int sprite, int frame, int rotation,
                          bool *flip, fcworld_patch_t *patch) {
    if (atlas->data == NULL || sprite < 0 || sprite >= atlas->sprite_count ||
        frame < 0 || rotation < 0 || rotation >= ROTATIONS) {
        return false;
    }
    atlas_tables_t t = atlas_tables(atlas);
    const uint8_t *s = atlas->data + t.sprites + (uint32_t)sprite * SPRITE_RECORD;
    uint16_t first = rd16(s);
    if (first == 0xFFFF || frame >= s[2]) return false;
    uint16_t e = rd16(atlas->data + t.entries + ((uint32_t)first + (uint32_t)frame * ROTATIONS +
                                                 (uint32_t)rotation) * 2u);
    if ((e & 0x7FFF) == 0) return false;
    const uint8_t *p = atlas->data + t.patches + (uint32_t)((e & 0x7FFF) - 1) * PATCH_RECORD;
    patch->width = p[0];
    patch->height = p[1];
    patch->left = (int16_t)rd16(p + 2);
    patch->top = (int16_t)rd16(p + 4);
    patch->family = p[6];
    patch->stride = (uint8_t)((p[0] + 1) / 2);
    patch->pixels = atlas->data + rd32(p + 8);
    *flip = (e & 0x8000) != 0;
    return true;
}

/* ------------------------------------------------------------------------ */
/* Raster                                                                     */
/* ------------------------------------------------------------------------ */
static int64_t ceil_div(int64_t a, int64_t b) {
    int64_t q = a / b;
    return (a % b != 0 && (a > 0) == (b > 0)) ? q + 1 : q;
}

static int64_t floor_div(int64_t a, int64_t b) {
    int64_t q = a / b;
    return (a % b != 0 && (a < 0) != (b < 0)) ? q - 1 : q;
}

/* 65536 * 2^(j/96): scale steps are every 96/FCWORLD_SCALE_STEPS entries and
 * the rounding midpoints halfway between them. */
static const uint32_t octave96[97] = {
    65536, 66011, 66489, 66971, 67456, 67945, 68438, 68933,
    69433, 69936, 70443, 70953, 71468, 71985, 72507, 73032,
    73562, 74095, 74632, 75172, 75717, 76266, 76819, 77375,
    77936, 78501, 79069, 79642, 80220, 80801, 81386, 81976,
    82570, 83169, 83771, 84378, 84990, 85606, 86226, 86851,
    87480, 88114, 88752, 89396, 90043, 90696, 91353, 92015,
    92682, 93354, 94030, 94711, 95398, 96089, 96785, 97487,
    98193, 98905, 99621, 100343, 101070, 101803, 102540, 103283,
    104032, 104786, 105545, 106310, 107080, 107856, 108638, 109425,
    110218, 111017, 111821, 112631, 113448, 114270, 115098, 115932,
    116772, 117618, 118470, 119329, 120194, 121065, 121942, 122825,
    123715, 124612, 125515, 126425, 127341, 128263, 129193, 130129,
    131072
};
#if 48 % FCWORLD_SCALE_STEPS != 0
#error "FCWORLD_SCALE_STEPS must divide 48"
#endif

/* Scale snapped to FCWORLD_SCALE_STEPS geometric steps per octave. */
static uint32_t quantise_scale(uint32_t v) {
    if (v == 0) return 0;
    int e = 0;
    while ((v >> e) > 1) ++e;
    uint64_t m = ((uint64_t)v << 16) >> e;   /* [65536, 131072) */
    const int stride = 96 / FCWORLD_SCALE_STEPS;
    int i = 0;
    while (i < FCWORLD_SCALE_STEPS && m >= octave96[i * stride + stride / 2]) ++i;
    return (uint32_t)(((uint64_t)octave96[i * stride] << e) >> 16);
}

uint32_t fcworld_scale_step(uint32_t iscale, uint32_t previous) {
    uint32_t q = quantise_scale(iscale);
    if (previous == 0 || q == previous) return q;
    /* Keep the previous step while iscale stays within one step of it. */
    uint64_t step = octave96[96 / FCWORLD_SCALE_STEPS];
    uint64_t a = (uint64_t)iscale << 16, b = (uint64_t)previous << 16;
    if (a < previous * step && (uint64_t)iscale * step > b) return previous;
    return q;
}

bool fcworld_actor_fits(unsigned tiles, bool was_native) {
    return tiles <= (was_native ? FCWORLD_ACTOR_STAY_TILES : FCWORLD_ACTOR_ENTER_TILES);
}

/* Native-space sampling relative to the patch origin. Texels depend only on
 * (patch, scale step, flip), never on screen position, so moving actors keep
 * their tiles and the scheduler finds them resident. */
typedef struct {
    int64_t step_x, step_y;    /* texels per native column / line, 16.16 */
    int64_t anchor_u, anchor_v;
    int ax, ay;                /* native column / line of the patch origin */
    int col_lo, col_hi, row_lo, row_hi;   /* relative to ax / ay, inclusive */
    bool flip;
} layout_t;

static bool layout(const fcworld_projection_t *p, layout_t *l) {
    if (p->iscale == 0 || p->xiscale == 0 || p->patch.width == 0 || p->patch.height == 0)
        return false;
    uint32_t q = p->scale_step ? p->scale_step : quantise_scale(p->iscale);
    l->step_x = (int64_t)q * 5 / 4;
    l->step_y = (int64_t)q * 25 / 28;
    l->flip = p->xiscale < 0;
    l->anchor_u = (int64_t)p->patch.left * 65536;
    l->anchor_v = (int64_t)p->patch.top * 65536;
    /* Origins and clipped/flipped texture offsets can be negative. */
    int64_t xa = (int64_t)(p->x1 + p->view_x) * 65536 +
                 (l->anchor_u - p->startfrac) * 65536 / p->xiscale;
    int64_t ya = (int64_t)(p->centery + p->view_y) * 65536 +
                 (l->anchor_v - p->texturemid) * 65536 / (int64_t)p->iscale;
    l->ax = (int)floor_div(xa * 4 / 5 + 32768, 65536);
    l->ay = FCWORLD_TOP_MARGIN + (int)floor_div(ya * 224 / 200 + 32768, 65536);
    int64_t w = (int64_t)p->patch.width << 16, h = (int64_t)p->patch.height << 16;
    int lo = (int)ceil_div(-l->anchor_u - l->step_x / 2, l->step_x);
    int hi = (int)ceil_div(w - l->anchor_u - l->step_x / 2, l->step_x) - 1;
    l->col_lo = l->flip ? -hi - 1 : lo;
    l->col_hi = l->flip ? -lo - 1 : hi;
    l->row_lo = (int)ceil_div(-l->anchor_v - l->step_y / 2, l->step_y);
    l->row_hi = (int)ceil_div(h - l->anchor_v - l->step_y / 2, l->step_y) - 1;
    return l->col_lo <= l->col_hi && l->row_lo <= l->row_hi;
}

static int layout_column(const layout_t *l, int n) {
    int64_t r = n - l->ax;
    if (l->flip) r = -r - 1;
    return (int)((l->anchor_u + (2 * r + 1) * l->step_x / 2) >> 16);
}

static int layout_row(const layout_t *l, int line) {
    int64_t r = line - l->ay;
    return (int)((l->anchor_v + (2 * r + 1) * l->step_y / 2) >> 16);
}

bool fcworld_bounds(const fcworld_projection_t *p, fcworld_box_t *box) {
    layout_t l;
    if (!layout(p, &l)) return false;
    /* Native columns / lines whose source sample lies inside the view. */
    int s1 = p->view_x, s2 = p->view_x + p->view_width - 1;
    if (s1 < 0) s1 = 0;
    if (s2 > 319) s2 = 319;
    int n_lo = (s1 / 5) * 4 + (s1 % 5 == 4 ? 4 : s1 % 5);
    int n_hi = (s2 / 5) * 4 + (s2 % 5 > 3 ? 3 : s2 % 5);
    if (n_hi > 255) n_hi = 255;
    int sy_lo = p->view_y, sy_hi = p->view_y + p->view_height - 1;
    int d_lo = (sy_lo * 28 + 24) / 25 + FCWORLD_TOP_MARGIN;
    int d_hi = ((sy_hi + 1) * 28 - 1) / 25;
    if (d_hi > 223) d_hi = 223;
    d_hi += FCWORLD_TOP_MARGIN;
    if (sy_hi < 199 && d_hi >= FCWORLD_STATUS_START) d_hi = FCWORLD_STATUS_START - 1;
    if (n_lo < l.ax + l.col_lo) n_lo = l.ax + l.col_lo;
    if (n_hi > l.ax + l.col_hi) n_hi = l.ax + l.col_hi;
    if (d_lo < l.ay + l.row_lo) d_lo = l.ay + l.row_lo;
    if (d_hi > l.ay + l.row_hi) d_hi = l.ay + l.row_hi;
    if (n_lo > n_hi || d_lo > d_hi) return false;
    int w = n_hi - n_lo + 1, h = d_hi - d_lo + 1;
    if (w > 248 || h > 216) return false;
    box->x = (int16_t)n_lo;
    box->y = (int16_t)d_lo;
    box->w = (uint8_t)w;
    box->h = (uint8_t)h;
    return true;
}

void fcworld_raster_reset(fcworld_raster_t *raster) {
    raster->count = 0;
    raster->used = 0;
}

bool fcworld_raster_actor(fcworld_raster_t *raster, const fcworld_projection_t *p,
                          const fcworld_box_t *box, fcworld_hidden_fn hidden, void *context) {
    unsigned area = (unsigned)box->w * box->h;
    layout_t l;
    if (raster->count >= FCWORLD_MAX_ACTORS || area > (unsigned)FCWORLD_PIXEL_ARENA - raster->used ||
        !layout(p, &l)) {
        return false;
    }
    uint8_t *out = raster->pixels + raster->used;
    /* A column samples the same texel in every row. Keep the 64-bit layout
     * arithmetic outside the pixel loop; 255 is outside every u8 patch width. */
    uint8_t columns[256];
    for (int col = 0; col < box->w; ++col) {
        int tx = layout_column(&l, box->x + col);
        columns[col] = tx >= 0 && tx < p->patch.width ? (uint8_t)tx : 255;
    }
    bool occluded = false;
    for (int row = 0; row < box->h; ++row) {
        int vy = fcworld_native_to_source_y(box->y + row) - p->view_y;
        int ty = layout_row(&l, box->y + row);
        for (int col = 0; col < box->w; ++col) {
            int tx = columns[col];
            uint8_t texel = 0;
            if (ty >= 0 && ty < p->patch.height && tx >= 0 && tx < p->patch.width) {
                texel = fcworld_patch_texel(&p->patch, tx, ty);
                if (texel != 0 && hidden != NULL &&
                    hidden(context, fcworld_native_to_source_x(box->x + col) - p->view_x, vy)) {
                    texel = 0;
                    occluded = true;
                }
            }
            out[row * box->w + col] = texel;
        }
    }
    raster->actor[raster->count].box = *box;
    raster->actor[raster->count].family = p->patch.family;
    raster->actor[raster->count].offset = raster->used;
    raster->actor[raster->count].id = 0;
    raster->actor[raster->count].occluded = occluded;
    raster->actor[raster->count].ax = (int16_t)l.ax;
    raster->actor[raster->count].ay = (int16_t)l.ay;
    raster->count++;
    raster->used = (uint16_t)(raster->used + area);
    return true;
}

/* ------------------------------------------------------------------------ */
/* Tiles and frames                                                           */
/* ------------------------------------------------------------------------ */
void fcworld_encode_tile(uint8_t tile[16], const uint8_t *pixels, int stride, int w, int h) {
    for (int row = 0; row < 8; ++row) {
        uint8_t lo = 0, hi = 0;
        for (int col = 0; col < 8; ++col) {
            uint8_t v = (row < h && col < w) ? pixels[row * stride + col] : 0;
            lo = (uint8_t)(lo | ((v & 1) << (7 - col)));
            hi = (uint8_t)(hi | (((v >> 1) & 1) << (7 - col)));
        }
        tile[row] = lo;
        tile[8 + row] = hi;
    }
}

static uint8_t reverse8(uint8_t v) {
    v = (uint8_t)((v & 0xF0) >> 4 | (v & 0x0F) << 4);
    v = (uint8_t)((v & 0xCC) >> 2 | (v & 0x33) << 2);
    return (uint8_t)((v & 0xAA) >> 1 | (v & 0x55) << 1);
}

/** Attribute flip bits that display `stored` as `wanted`, or -1. */
static int flip_match(const uint8_t stored[16], const uint8_t wanted[16]) {
    static const uint8_t flips[4] = {0x00, 0x40, 0x80, 0xC0};
    for (int f = 0; f < 4; ++f) {
        bool h = (flips[f] & 0x40) != 0, v = (flips[f] & 0x80) != 0;
        bool same = true;
        for (int plane = 0; plane < 2 && same; ++plane) {
            for (int row = 0; row < 8; ++row) {
                uint8_t s = stored[plane * 8 + (v ? 7 - row : row)];
                if ((h ? reverse8(s) : s) != wanted[plane * 8 + row]) { same = false; break; }
            }
        }
        if (same) return flips[f];
    }
    return -1;
}

void fcworld_frame_clear(fcworld_frame_t *frame, const uint8_t palettes[MBX_WORLD_PAL_LEN]) {
    frame->tile_count = 0;
    frame->oam_count = 0;
    frame->peak_line = 0;
    frame->actor_count = 0;
    memcpy(frame->palettes, palettes, MBX_WORLD_PAL_LEN);
}

static uint8_t fixed_palette1[3] = FCWORLD_FIXED_PALETTE1_INIT;
static uint8_t fixed_palette3[3] = FCWORLD_FIXED_PALETTE3_INIT;

static void loaded_palettes(const uint8_t world[MBX_WORLD_PAL_LEN], const uint8_t *out[4]) {
    out[0] = world;
    out[1] = fixed_palette1;
    out[2] = world + 3;
    out[3] = fixed_palette3;
}

static uint32_t family_error(const fcworld_family_t *f, const uint8_t palette[3]) {
    uint32_t total = 0;
    for (unsigned v = 1; v <= f->count; ++v) {
        const uint8_t *row = f->select + (v - 1) * 64;
        uint8_t best = row[palette[0]];
        if (row[palette[1]] < best) best = row[palette[1]];
        if (row[palette[2]] < best) best = row[palette[2]];
        total += (uint32_t)f->weight[v] * best;
    }
    return total;
}

void fcworld_plan_palettes(const fcworld_atlas_t *atlas, const fcworld_raster_t *raster,
                           const fcworld_palette_state_t *shown,
                           uint8_t palettes[MBX_WORLD_PAL_LEN]) {
    memcpy(palettes, shown->palettes, MBX_WORLD_PAL_LEN);
    bool open[2] = {!(shown->busy & 1), !(shown->busy & 2)};
    uint8_t seen[FCWORLD_MAX_ACTORS];
    unsigned families = 0;
    for (unsigned a = 0; a < raster->count; ++a) {
        bool dup = false;
        for (unsigned i = 0; i < families; ++i) dup = dup || seen[i] == raster->actor[a].family;
        if (!dup) seen[families++] = raster->actor[a].family;
    }
    /* Every family's first candidate before any second one, nearest first. */
    for (unsigned pass = 0; pass < 2; ++pass) {
        for (unsigned i = 0; i < families && (open[0] || open[1]); ++i) {
            fcworld_family_t f;
            if (!fcworld_atlas_family(atlas, seen[i], &f)) continue;
            const uint8_t *candidate = f.candidates + pass * 3;
            const uint8_t *loaded[4];
            loaded_palettes(palettes, loaded);
            uint32_t want = family_error(&f, candidate), have = UINT32_MAX;
            for (int p = 0; p < 4; ++p) {
                uint32_t e = family_error(&f, loaded[p]);
                if (e < have) have = e;
            }
            if ((uint64_t)have * 10u <= (uint64_t)want * 11u) continue;
            int slot = open[0] ? 0 : 1;
            memcpy(palettes + slot * 3, candidate, 3);
            open[slot] = false;
        }
    }
}

void fcworld_frame_state(const fcworld_frame_t *frame, fcworld_palette_state_t *state) {
    memcpy(state->palettes, frame->palettes, MBX_WORLD_PAL_LEN);
    state->busy = 0;
    for (unsigned n = 0; n < frame->oam_count; ++n) {
        uint8_t palette = frame->oam[n][2] & 3;
        if (palette == 0) state->busy |= 1;
        if (palette == 2) state->busy |= 2;
    }
}

bool fcworld_frame_build(fcworld_frame_t *frame, const fcworld_raster_t *raster,
                         const fcworld_atlas_t *atlas, const fcworld_palette_state_t *shown) {
    uint8_t palettes[MBX_WORLD_PAL_LEN];
    fcworld_plan_palettes(atlas, raster, shown, palettes);
    uint8_t lines[240];
    memset(lines, 0, sizeof lines);
    fcworld_frame_clear(frame, palettes);
    const uint8_t *loaded[4];
    loaded_palettes(palettes, loaded);
    int cached_family = -1;
    uint16_t planes[4][16], cost[4][16];
    bool allowed[4] = {true, true, true, true};
    for (unsigned a = 0; a < raster->count; ++a) {
        const fcworld_box_t *box = &raster->actor[a].box;
        const uint8_t *pixels = raster->pixels + raster->actor[a].offset;
        /* Per loaded palette: both NES bitplanes at column zero, and weighted
         * cost of each local colour. Consecutive actors of the same family
         * share these tables; loaded palettes stay fixed for the frame. */
        if (cached_family != raster->actor[a].family) {
            fcworld_family_t f;
            if (!fcworld_atlas_family(atlas, raster->actor[a].family, &f)) goto overflow;
            cached_family = raster->actor[a].family;
            for (int p = 0; p < 4; ++p) allowed[p] = true;
            if (f.flags & FCWORLD_FAMILY_EXCLUSIVE) {
                bool any = false;
                for (int p = 0; p < 4; ++p) {
                    allowed[p] = memcmp(loaded[p], f.candidates, 3) == 0 ||
                                 memcmp(loaded[p], f.candidates + 3, 3) == 0;
                    any = any || allowed[p];
                }
                if (!any) for (int p = 0; p < 4; ++p) allowed[p] = true;
            }
            for (int p = 0; p < 4; ++p) {
                planes[p][0] = 0;
                cost[p][0] = 0;
                for (unsigned v = 1; v < 16; ++v) {
                    unsigned row = (v <= f.count ? v : 1) - 1;
                    const uint8_t *sel = f.select + row * 64, *map = f.map + row * 64;
                    uint8_t best_sel = 255, best_map = 255, k = 0;
                    for (uint8_t c = 0; c < 3; ++c) {
                        if (sel[loaded[p][c]] < best_sel) best_sel = sel[loaded[p][c]];
                        if (map[loaded[p][c]] < best_map) { best_map = map[loaded[p][c]]; k = c; }
                    }
                    unsigned colour = k + 1;
                    planes[p][v] = (uint16_t)(((colour & 1) << 7) | ((colour & 2) << 14));
                    cost[p][v] = (uint16_t)(f.weight[v] * best_sel);
                }
            }
        }
        uint8_t first_entry = frame->oam_count;
        for (int ty = 0; ty < box->h; ty += 8) {
            for (int tx = 0; tx < box->w; tx += 8) {
                int w = box->w - tx < 8 ? box->w - tx : 8;
                int h = box->h - ty < 8 ? box->h - ty : 8;
                uint32_t errors[4] = {0, 0, 0, 0};
                bool empty = true;
                for (int y = 0; y < h; ++y) {
                    for (int x = 0; x < w; ++x) {
                        uint8_t v = pixels[(ty + y) * box->w + tx + x];
                        empty = empty && v == 0;
                        errors[0] += cost[0][v];
                        errors[1] += cost[1][v];
                        errors[2] += cost[2][v];
                        errors[3] += cost[3][v];
                    }
                }
                if (empty) continue;
                int chosen = -1;
                for (int p = 0; p < 4; ++p) {
                    if (allowed[p] && (chosen < 0 || errors[p] < errors[chosen])) chosen = p;
                }
                uint8_t tile[16] = {0};
                for (int y = 0; y < h; ++y) {
                    const uint8_t *row = pixels + (ty + y) * box->w + tx;
                    uint16_t packed = 0;
                    for (int x = 0; x < w; ++x)
                        packed |= planes[chosen][row[x]] >> x;
                    tile[y] = (uint8_t)packed;
                    tile[8 + y] = (uint8_t)(packed >> 8);
                }
                int slot = -1, flip = 0;
                for (int t = 0; t < frame->tile_count && slot < 0; ++t) {
                    int m = flip_match(frame->tiles[t], tile);
                    if (m >= 0) { slot = t; flip = m; }
                }
                if (slot < 0) {
                    if (frame->tile_count >= FCWORLD_MAX_TILES) goto overflow;
                    slot = frame->tile_count++;
                    memcpy(frame->tiles[slot], tile, 16);
                }
                if (frame->oam_count >= WORLD_OAM_ENTRIES) goto overflow;
                int y = box->y + ty, x = box->x + tx;
                for (int line = y; line < y + 8 && line < 240; ++line) {
                    if (++lines[line] > FCWORLD_MAX_LINE_SPRITES) goto overflow;
                    if (lines[line] > frame->peak_line) frame->peak_line = lines[line];
                }
                uint8_t *entry = frame->oam[frame->oam_count++];
                entry[0] = (uint8_t)(y - 1);
                entry[1] = (uint8_t)slot;
                entry[2] = (uint8_t)(chosen | flip);
                entry[3] = (uint8_t)x;
            }
        }
        if (frame->oam_count > first_entry) {
            unsigned n = frame->actor_count++;
            frame->actor[n].id = raster->actor[a].id;
            frame->actor[n].ax = raster->actor[a].ax;
            frame->actor[n].ay = raster->actor[a].ay;
            frame->actor[n].occluded = raster->actor[a].occluded;
            frame->actor[n].first = first_entry;
            frame->actor[n].count = (uint8_t)(frame->oam_count - first_entry);
            frame->actor[n].stale = 0;
        }
    }
    return true;
overflow:
    fcworld_frame_clear(frame, palettes);
    return false;
}

/* ------------------------------------------------------------------------ */
/* Scheduler                                                                  */
/* ------------------------------------------------------------------------ */
static int find_tile(const fcworld_frame_t *frame, const uint8_t tile[16], int *flip) {
    for (int t = 0; t < frame->tile_count; ++t) {
        int m = flip_match(frame->tiles[t], tile);
        if (m >= 0) {
            *flip = m;
            return t;
        }
    }
    return -1;
}

static bool palettes_kept(const fcworld_frame_t *frame, const fcworld_frame_t *previous,
                          unsigned first, unsigned count) {
    for (unsigned n = first; n < first + count; ++n) {
        unsigned p = previous->oam[n][2] & 3u;
        if (p == 0 && memcmp(frame->palettes, previous->palettes, 3) != 0) return false;
        if (p == 2 && memcmp(frame->palettes + 3, previous->palettes + 3, 3) != 0) return false;
    }
    return true;
}

/* Append one actor's entries from `source` moved by (dx, dy). False (and
 * `out` unchanged) if its tiles do not fit. */
static bool append_actor(fcworld_frame_t *out, const fcworld_frame_t *source,
                         unsigned first, unsigned count, int dx, int dy) {
    uint8_t index[WORLD_OAM_ENTRIES], flips[WORLD_OAM_ENTRIES];
    uint8_t added = 0;
    for (unsigned n = 0; n < count; ++n) {
        const uint8_t *tile = source->tiles[source->oam[first + n][1]];
        int flip = 0;
        int t = find_tile(out, tile, &flip);
        if (t < 0) {
            /* also look among tiles this actor already added */
            for (int k = 0; k < added && t < 0; ++k) {
                int m = flip_match(out->tiles[out->tile_count + k], tile);
                if (m >= 0) { t = out->tile_count + k; flip = m; }
            }
        }
        if (t < 0) {
            if (out->tile_count + added >= FCWORLD_MAX_TILES) return false;
            t = out->tile_count + added++;
            memcpy(out->tiles[t], tile, 16);
            flip = 0;
        }
        index[n] = (uint8_t)t;
        flips[n] = (uint8_t)flip;
    }
    if (out->oam_count + count > WORLD_OAM_ENTRIES) return false;
    out->tile_count = (uint8_t)(out->tile_count + added);
    for (unsigned n = 0; n < count; ++n) {
        const uint8_t *e = source->oam[first + n];
        uint8_t *o = out->oam[out->oam_count++];
        o[0] = (uint8_t)(e[0] + dy);
        o[1] = index[n];
        o[2] = (uint8_t)(e[2] ^ flips[n]);
        o[3] = (uint8_t)(e[3] + dx);
    }
    return true;
}

static int lowest_line(const fcworld_frame_t *frame) {
    int bottom = FCWORLD_STATUS_START - 1;
    for (int n = 0; n < frame->oam_count; ++n)
        if (frame->oam[n][0] + 8 > bottom) bottom = frame->oam[n][0] + 8;
    return bottom;
}

unsigned fcworld_frame_limit(fcworld_frame_t *frame, const fcworld_frame_t *previous,
                             unsigned budget) {
    if (previous == NULL || previous->actor_count == 0 || frame->actor_count == 0) return 0;
    static fcworld_frame_t out;
    fcworld_frame_clear(&out, frame->palettes);
    uint8_t lines[240];
    memset(lines, 0, sizeof lines);
    for (int n = 0; n < frame->oam_count; ++n)
        for (int line = frame->oam[n][0] + 1; line < frame->oam[n][0] + 9 && line < 240; ++line)
            lines[line]++;
    int bottom = lowest_line(frame);
    unsigned fresh_total = 0, held = 0;
    for (unsigned a = 0; a < frame->actor_count; ++a) {
        const unsigned first = frame->actor[a].first, count = frame->actor[a].count;
        /* Tiles this actor would upload: absent from the shown generation. */
        unsigned fresh = 0;
        for (unsigned n = first; n < first + count; ++n) {
            int flip;
            const uint8_t *tile = frame->tiles[frame->oam[n][1]];
            if (find_tile(previous, tile, &flip) >= 0) continue;
            bool seen = false;
            for (unsigned k = first; k < n && !seen; ++k)
                seen = flip_match(frame->tiles[frame->oam[k][1]], tile) >= 0;
            if (!seen) fresh++;
        }
        int reuse = -1;
        if (fresh > 0 && fresh_total + fresh > budget && frame->actor[a].id != 0 &&
            !frame->actor[a].occluded) {
            for (unsigned b = 0; b < previous->actor_count && reuse < 0; ++b) {
                if (previous->actor[b].id == frame->actor[a].id) reuse = (int)b;
            }
        }
        int dx = 0, dy = 0;
        if (reuse >= 0) {
            unsigned pf = previous->actor[reuse].first, pc = previous->actor[reuse].count;
            dx = frame->actor[a].ax - previous->actor[reuse].ax;
            dy = frame->actor[a].ay - previous->actor[reuse].ay;
            bool ok = previous->actor[reuse].stale == 0 && !previous->actor[reuse].occluded &&
                      palettes_kept(frame, previous, pf, pc);
            /* Stay on screen and inside the per-line sprite limit. */
            uint8_t trial[240];
            memcpy(trial, lines, sizeof trial);
            for (unsigned n = first; ok && n < first + count; ++n)
                for (int line = frame->oam[n][0] + 1; line < frame->oam[n][0] + 9 && line < 240; ++line)
                    trial[line]--;
            for (unsigned n = pf; ok && n < pf + pc; ++n) {
                int y = previous->oam[n][0] + dy, x = previous->oam[n][3] + dx;
                if (x < 0 || x > 248 || y + 1 < FCWORLD_TOP_MARGIN || y + 8 > bottom) ok = false;
                for (int line = y + 1; ok && line < y + 9; ++line)
                    if (++trial[line] > FCWORLD_MAX_LINE_SPRITES) ok = false;
            }
            uint8_t mark_tiles = out.tile_count, mark_oam = out.oam_count;
            if (ok && append_actor(&out, previous, pf, pc, dx, dy)) {
                memcpy(lines, trial, sizeof lines);
                unsigned n = out.actor_count++;
                out.actor[n] = frame->actor[a];
                out.actor[n].first = mark_oam;
                out.actor[n].count = (uint8_t)(out.oam_count - mark_oam);
                out.actor[n].stale = (uint8_t)(previous->actor[reuse].stale + 1);
                held++;
                continue;
            }
            out.tile_count = mark_tiles;
            out.oam_count = mark_oam;
        }
        uint8_t mark_oam = out.oam_count;
        if (!append_actor(&out, frame, first, count, 0, 0)) return 0;  /* frame unchanged */
        unsigned n = out.actor_count++;
        out.actor[n] = frame->actor[a];
        out.actor[n].first = mark_oam;
        fresh_total += fresh;
    }
    if (held == 0) return 0;
    uint8_t peak = 0;
    for (int line = 0; line < 240; ++line) if (lines[line] > peak) peak = lines[line];
    out.peak_line = peak;
    *frame = out;
    return held;
}

/* Non-const: .data is RAM, which the IRQ-time reset path may read. */
static uint8_t pool_tiles[FCWORLD_POOL_TILES] = FCWORLD_TILE_POOL_INIT;
static uint8_t menu_palettes[MBX_WORLD_PAL_LEN] = FCWORLD_MENU_PALETTES_INIT;

void FCWORLD_RAM(fcworld_reset)(fcworld_t *world) {
    fill((uint8_t *)world, 0, sizeof *world);
    for (int i = 0; i < FCWORLD_POOL_TILES; ++i) world->slots[i].tile = pool_tiles[i];
    copy(world->shown_pal, menu_palettes, sizeof menu_palettes);
}

bool fcworld_idle(const fcworld_t *world) {
    return __atomic_load_n(&world->stage, __ATOMIC_ACQUIRE) == FCWORLD_IDLE;
}

bool fcworld_reserve(fcworld_t *world) {
    if (!fcworld_idle(world)) return false;
    __atomic_store_n(&world->stage, FCWORLD_PREPARING, __ATOMIC_RELEASE);
    return true;
}

bool fcworld_quiescent(const fcworld_t *world, const uint8_t palettes[MBX_WORLD_PAL_LEN]) {
    return world->stage == FCWORLD_IDLE && world->shown_gen == 0 &&
           memcmp(world->shown_pal, palettes, MBX_WORLD_PAL_LEN) == 0;
}

static int find_slot(fcworld_t *world, const uint8_t tile[16], int *flip) {
    for (int i = 0; i < FCWORLD_POOL_TILES; ++i) {
        if (!world->slots[i].valid) continue;
        int f = flip_match(world->slots[i].data, tile);
        if (f >= 0) { *flip = f; return i; }
    }
    return -1;
}

static int allocate_slot(fcworld_t *world) {
    int best = -1;
    for (int i = 0; i < FCWORLD_POOL_TILES; ++i) {
        const fcworld_slot_t *s = &world->slots[i];
        if (s->shown || s->target) continue;
        if (!s->valid) return i;
        if (best < 0 || s->last_used < world->slots[best].last_used) best = i;
    }
    return best;
}

static uint8_t next_generation(fcworld_t *world) {
    uint8_t gen = world->last_gen;
    do {
        gen = (uint8_t)(gen % WORLD_GEN_MASK + 1);
    } while (gen == world->shown_gen);
    world->last_gen = gen;
    return gen;
}

static uint8_t submit(fcworld_t *world, const fcworld_frame_t *frame, bool held) {
    if (world->stage != FCWORLD_PREPARING && !fcworld_reserve(world)) return 0xFF;
    /* Publish hold before stage, including the all-cached commit-only path. */
    world->hold_commit = held;
    world->stats.submits++;
    world->clock++;
    for (int i = 0; i < FCWORLD_POOL_TILES; ++i) world->slots[i].target = false;
    bool same_pal = memcmp(world->shown_pal, frame->palettes, MBX_WORLD_PAL_LEN) == 0;
    memcpy(world->pal, frame->palettes, MBX_WORLD_PAL_LEN);
    world->upload_count = world->upload_next = 0;
    world->oam_half = 0;
    world->busy_heartbeats = 0;

    if (frame->oam_count == 0) {
        if (world->shown_gen == 0 && same_pal && world->shown_known) {
            world->stats.unchanged++;
            world->hold_commit = false;
            __atomic_store_n(&world->stage, FCWORLD_IDLE, __ATOMIC_RELEASE);
            return 0;
        }
        world->clear = true;
        world->target_gen = 0;
        world->stats.clears++;
        __atomic_store_n(&world->stage, FCWORLD_OAM, __ATOMIC_RELEASE);
        return 0;
    }

    uint8_t slot_of[FCWORLD_MAX_TILES];
    for (int t = 0; t < frame->tile_count; ++t) {
        int flip = 0;
        /* A resident tile in any orientation is reused with OAM flip bits. */
        int slot = find_slot(world, frame->tiles[t], &flip);
        if (slot >= 0) {
            world->stats.cache_hits++;
        } else {
            slot = allocate_slot(world);
            if (slot < 0) {
                world->hold_commit = false;
                __atomic_store_n(&world->stage, FCWORLD_IDLE, __ATOMIC_RELEASE);
                return 0xFF;
            }
            fcworld_slot_t *s = &world->slots[slot];
            s->valid = false;
            memcpy(s->data, frame->tiles[t], 16);
            world->upload[world->upload_count++] = (uint8_t)slot;
        }
        world->slots[slot].target = true;
        world->slots[slot].last_used = world->clock;
        slot_of[t] = (uint8_t)slot;
    }

    memset(world->oam, 0, sizeof world->oam);
    for (int n = 0; n < WORLD_OAM_ENTRIES; ++n) world->oam[n * 4] = 0xEF;
    for (int n = 0; n < frame->oam_count; ++n) {
        const uint8_t *e = frame->oam[n];
        const fcworld_slot_t *s = &world->slots[slot_of[e[1]]];
        /* A pending upload holds the wanted orientation; a cached slot may
         * need flip bits to present it. */
        int flip = s->valid ? flip_match(s->data, frame->tiles[e[1]]) : 0;
        world->oam[n * 4 + 0] = e[0];
        world->oam[n * 4 + 1] = s->tile;
        world->oam[n * 4 + 2] = (uint8_t)(e[2] ^ (uint8_t)flip);
        world->oam[n * 4 + 3] = e[3];
    }

    if (world->upload_count == 0 && same_pal && world->shown_known && world->shown_gen != 0 &&
        memcmp(world->oam, world->shown_oam, WORLD_OAM_ENTRIES * 4) == 0) {
        world->stats.unchanged++;
        for (int i = 0; i < FCWORLD_POOL_TILES; ++i) world->slots[i].target = false;
        world->hold_commit = false;
        __atomic_store_n(&world->stage, FCWORLD_IDLE, __ATOMIC_RELEASE);
        return world->shown_gen;
    }
    world->clear = false;
    world->target_gen = next_generation(world);
    world->stats.generations++;
    __atomic_store_n(&world->stage, world->upload_count ? FCWORLD_CHR : FCWORLD_OAM,
                     __ATOMIC_RELEASE);
    return world->target_gen;
}

uint8_t fcworld_submit(fcworld_t *world, const fcworld_frame_t *frame) {
    return submit(world, frame, false);
}

uint8_t fcworld_submit_held(fcworld_t *world, const fcworld_frame_t *frame) {
    return submit(world, frame, true);
}

void fcworld_release(fcworld_t *world) { world->hold_commit = false; }

static void FCWORLD_RAM(finish_packet)(fcworld_t *world) {
    if (world->packet_kind == WORLD_OP_CHR) {
        for (int i = 0; i < world->packet_tile_count; ++i) {
            world->slots[world->packet_tiles[i]].valid = true;
        }
        world->stats.chr_tiles += world->packet_tile_count;
        return;
    }
    uint8_t arg = world->packet_cmd[MBX_WORLD_ARG - MBX_CMD];
    uint8_t offset = (uint8_t)(arg & 0x7F);
    if (offset != WORLD_OAM_NONE) {
        int half = offset == 0 ? 0 : 1;
        copy(world->staged + half * 64, world->packet_payload, 64);
        world->staged_known[half] = true;
    }
    if (arg & WORLD_OAM_COMMIT) {
        world->stage = FCWORLD_WAIT_SHOWN;
        world->stats.commits++;
    }
}

static void FCWORLD_RAM(finalize_generation)(fcworld_t *world) {
    if (world->clear) {
        fill(world->shown_oam, 0, sizeof world->shown_oam);
        for (int n = 0; n < WORLD_OAM_ENTRIES; ++n) world->shown_oam[n * 4] = 0xEF;
    } else {
        copy(world->shown_oam, world->oam, WORLD_OAM_ENTRIES * 4);
    }
    copy(world->shown_pal, world->pal, MBX_WORLD_PAL_LEN);
    world->shown_known = true;
    for (int i = 0; i < FCWORLD_POOL_TILES; ++i) {
        world->slots[i].shown = !world->clear && world->slots[i].target;
        world->slots[i].target = false;
    }
    world->stats.shown++;
    if (world->busy_heartbeats > world->stats.busy_heartbeats_max) {
        world->stats.busy_heartbeats_max = world->busy_heartbeats;
    }
    __atomic_store_n(&world->stage, FCWORLD_IDLE, __ATOMIC_RELEASE);
}

static bool FCWORLD_RAM(half_needed)(const fcworld_t *world, int half) {
    return !world->staged_known[half] ||
           !same(world->staged + half * 64, world->oam + half * 64, 64);
}

/** Prepare the next unsent packet; false if nothing remains in this stage. */
static bool FCWORLD_RAM(next_packet)(fcworld_t *world) {
    fill(world->packet_cmd, 0, sizeof world->packet_cmd);
    fill(world->packet_payload, 0, sizeof world->packet_payload);
    if (world->stage == FCWORLD_CHR) {
        if (world->upload_next < world->upload_count) {
            world->packet_kind = WORLD_OP_CHR;
            world->packet_tile_count = 0;
            for (int i = 0; i < WORLD_CHR_TILES; ++i) {
                int index = world->upload_next < world->upload_count ?
                    world->upload_next++ : world->upload_next - 1;
                uint8_t slot = world->upload[index];
                uint16_t address = (uint16_t)(0x1000 + world->slots[slot].tile * 16);
                world->packet_cmd[MBX_WORLD_ARG - MBX_CMD + i * 2] = (uint8_t)(address >> 8);
                world->packet_cmd[MBX_WORLD_ARG - MBX_CMD + i * 2 + 1] = (uint8_t)address;
                copy(world->packet_payload + i * 16, world->slots[slot].data, 16);
                if (i == 0 || world->packet_tiles[world->packet_tile_count - 1] != slot) {
                    world->packet_tiles[world->packet_tile_count++] = slot;
                }
            }
            return true;
        }
        world->stage = FCWORLD_OAM;
    }
    if (world->stage != FCWORLD_OAM) return false;
    world->packet_kind = WORLD_OP_OAM;
    uint8_t arg = WORLD_OAM_NONE;
    bool commit = true;
    int send = -1;
    if (!world->clear) {
        for (int half = world->oam_half; half < 2 && send < 0; ++half) {
            if (half_needed(world, half)) send = half;
        }
        commit = send < 0 || send == 1 || !half_needed(world, 1);
    }
    /* Held: the last packet waits so that it still carries the commit. */
    if (commit && world->hold_commit) return false;
    if (send >= 0) {
        arg = (uint8_t)(send * WORLD_OAM_PACKET);
        copy(world->packet_payload, world->oam + send * 64, 64);
        world->oam_half = (uint8_t)(send + 1);
    }
    if (commit) {
        arg |= WORLD_OAM_COMMIT;
        copy(world->packet_cmd + (MBX_WORLD_PAL - MBX_CMD), world->pal, MBX_WORLD_PAL_LEN);
        world->oam_half = 2;
    }
    world->packet_cmd[MBX_WORLD_ARG - MBX_CMD] = arg;
    return true;
}

static void FCWORLD_RAM(stall)(fcworld_t *world) {
    fcworld_stats_t stats = world->stats;
    uint8_t seq = world->seq;
    fcworld_reset(world);
    world->stats = stats;
    world->stats.stalls++;
    world->seq = seq;
    world->stalled = true;
}

void FCWORLD_RAM(fcworld_heartbeat)(fcworld_t *world, uint8_t *mailbox, uint8_t pad2) {
    if (__atomic_load_n(&world->stage, __ATOMIC_ACQUIRE) == FCWORLD_PREPARING) return;
    uint8_t ack = (uint8_t)(pad2 & WORLD_SEQ_MASK);
    uint8_t shown = (uint8_t)((pad2 >> WORLD_HB_GEN_SHIFT) & WORLD_GEN_MASK);
    if (world->outstanding != 0 && ack == world->outstanding) {
        world->outstanding = 0;
        finish_packet(world);
    }
    world->shown_gen = shown;
    if (world->stage == FCWORLD_WAIT_SHOWN && shown == world->target_gen) {
        finalize_generation(world);
    }
    if (world->stage == FCWORLD_IDLE) return;
    if (++world->busy_heartbeats > FCWORLD_STALL_HEARTBEATS) {
        stall(world);
        return;
    }
    if (world->stage == FCWORLD_WAIT_SHOWN) return;
    bool free = mailbox[MBX_CMD] == PF_COM_NONE &&
        (mailbox[MBX_FLAGS] & (MBX_FLAG_ATTR_VALID | MBX_FLAG_PAL_VALID)) == 0;
    if (!free) {
        world->stats.deferred_heartbeats++;
        return;
    }
    if (world->outstanding == 0) {
        if (!next_packet(world)) return;
        world->seq = (uint8_t)(world->seq % WORLD_SEQ_MASK + 1);
        world->outstanding = world->seq;
        world->packet_cmd[0] = world->packet_kind;
        world->packet_cmd[MBX_WORLD_SEQ - MBX_CMD] = world->seq;
        world->packet_cmd[MBX_WORLD_GEN - MBX_CMD] = world->target_gen;
    } else {
        world->stats.resends++;
    }
    world->stats.packets++;
    mailbox[MBX_FLAGS] |= MBX_FLAG_WORLD;
    copy(mailbox + MBX_CMD, world->packet_cmd, MBX_CMD_LEN);
    copy(mailbox + MBX_WORLD_PAYLOAD, world->packet_payload, MBX_ATTR_LEN);
}

bool fcworld_take_stall(fcworld_t *world) {
    bool stalled = world->stalled;
    world->stalled = false;
    return stalled;
}
