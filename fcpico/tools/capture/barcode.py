# SPDX-License-Identifier: BSD-3-Clause
"""The FCPICO_FRAME_TRACE frame-ID barcode: encode, read and locate.

Layout (keep in step with FCVIDEO_TRACE_* in port/video/fcvideo.h): 28 cells,
8 x 16 NES pixels each, from x=16 on lines 16..31, black ($0F) or white ($30).

  cell 0 start (white), 1 calibration (black), 2..13 picture ID bits 11..0,
  14..17 world generation bits 3..0, 18 world wait timed out,
  19..25 leveltime bits 6..0, 26 even parity over 2..25, 27 end (white).

``tic`` below is Doom's ``leveltime``: tics since the level (or demo) began,
so a console attract-mode demo and a host ``--demo`` run agree on it.

Captures map NES pixels to capture pixels with a per-setup affine geometry
(x0 + sx * x, y0 + sy * y), found by :func:`calibrate` from the captures
themselves: the start, calibration and end cells never change, so over many
frames they stand out from the averaging data cells and the scene.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict

import numpy as np

CELLS = 28
LEFT = 16
TOP = 16
CELL_WIDTH = 8
HEIGHT = 16
ID_BITS = 12
GEN_BITS = 4
TIC_BITS = 7
MIN_CONTRAST = 24.0  # luma units (0..255) between calibrated black and white


@dataclass
class Code:
    id: int
    generation: int
    timeout: bool
    tic: int


@dataclass
class Geometry:
    """Capture pixel = (x0 + sx * nes_x, y0 + sy * nes_y)."""
    x0: float
    sx: float
    y0: float
    sy: float

    def as_dict(self) -> dict:
        return asdict(self)


def _bits(value: int, count: int) -> list[int]:
    return [(value >> (count - 1 - i)) & 1 for i in range(count)]


def encode(code: Code) -> list[int]:
    cells = [1, 0] + _bits(code.id, ID_BITS) + _bits(code.generation, GEN_BITS)
    cells += [1 if code.timeout else 0] + _bits(code.tic, TIC_BITS)
    cells.append(sum(cells[2:26]) & 1)
    return cells + [1]


def parse(cells) -> Code | None:
    """Cells to a code, or None when the markers or parity are wrong."""
    cells = [int(c) for c in cells]
    if len(cells) != CELLS or cells[0] != 1 or cells[1] != 0 or cells[27] != 1:
        return None
    if sum(cells[2:27]) & 1:
        return None
    value = lambda bits: int("".join(map(str, bits)), 2)
    return Code(value(cells[2:14]), value(cells[14:18]), bool(cells[18]), value(cells[19:26]))


def read_nes(pixels: np.ndarray) -> Code | None:
    """Read the band from a 256x240 array of 2-bit colours (a decoded stream)."""
    band = pixels[TOP:TOP + HEIGHT, LEFT:LEFT + CELLS * CELL_WIDTH]
    cells = band.reshape(HEIGHT, CELLS, CELL_WIDTH).mean(axis=(0, 2))
    return parse(np.rint(cells / 2).astype(int))


def _cell_boxes(geometry: Geometry):
    """Inner sampling box of every cell: the middle half across, middle 8 lines."""
    g = geometry
    xs = LEFT + np.arange(CELLS) * CELL_WIDTH
    x_lo = g.x0 + g.sx * (xs + CELL_WIDTH * 0.25)
    x_hi = g.x0 + g.sx * (xs + CELL_WIDTH * 0.75)
    y_lo = g.y0 + g.sy * (TOP + HEIGHT * 0.25)
    y_hi = g.y0 + g.sy * (TOP + HEIGHT * 0.75)
    return x_lo, x_hi, y_lo, y_hi


def cell_levels(luma: np.ndarray, geometry: Geometry) -> np.ndarray | None:
    """Mean luma of each cell's sampling box, or None when it leaves the image."""
    x_lo, x_hi, y_lo, y_hi = _cell_boxes(geometry)
    h, w = luma.shape
    top, bottom = int(round(y_lo)), max(int(round(y_hi)), int(round(y_lo)) + 1)
    if top < 0 or bottom > h or x_lo[0] < 0 or x_hi[-1] > w:
        return None
    rows = luma[top:bottom].astype(np.float64).mean(axis=0)
    out = np.empty(CELLS)
    for i in range(CELLS):
        a, b = int(round(x_lo[i])), max(int(round(x_hi[i])), int(round(x_lo[i])) + 1)
        out[i] = rows[a:b].mean()
    return out


@dataclass
class Reading:
    code: Code | None
    black: float
    white: float
    margin: float  # smallest distance of a data cell from the threshold, luma units


def read(luma: np.ndarray, geometry: Geometry) -> Reading:
    """Read one captured field (2-D luma, 0..255)."""
    levels = cell_levels(luma, geometry)
    if levels is None:
        return Reading(None, 0.0, 0.0, 0.0)
    black, white = levels[1], (levels[0] + levels[27]) / 2
    if white - black < MIN_CONTRAST:
        return Reading(None, black, white, 0.0)
    threshold = (black + white) / 2
    cells = (levels > threshold).astype(int)
    margin = float(np.abs(levels[2:27] - threshold).min())
    return Reading(parse(cells), float(black), float(white), margin)


def _score_profiles(profile: np.ndarray, x0s: np.ndarray, sx: float) -> np.ndarray:
    """Marker contrast of the averaged band profile at many left edges."""
    csum = np.concatenate([[0.0], np.cumsum(profile)])
    w = len(profile)

    def box(cell):
        lo = x0s + sx * (LEFT + cell * CELL_WIDTH + CELL_WIDTH * 0.25)
        hi = x0s + sx * (LEFT + cell * CELL_WIDTH + CELL_WIDTH * 0.75)
        a = np.clip(np.rint(lo).astype(int), 0, w)
        b = np.clip(np.maximum(np.rint(hi).astype(int), a + 1), 0, w)
        return (csum[b] - csum[a]) / np.maximum(b - a, 1)

    start, calibration, end = box(0), box(1), box(27)
    # Just outside the band: the scene, which must not look like a marker.
    before, after = box(-1), box(28)
    score = np.minimum(start, end) - calibration - np.abs(start - end)
    score -= 0.5 * np.maximum(0, np.minimum(before, after) - calibration)
    valid = (x0s + sx * (LEFT - CELL_WIDTH) >= 0) & (x0s + sx * (LEFT + 29 * CELL_WIDTH) <= w)
    return np.where(valid, score, -np.inf)


def calibrate(fields: list[np.ndarray], candidates: int = 24) -> tuple[Geometry, float]:
    """Find the band in a set of captured fields (at least ~30, spanning
    changing pictures). Returns the geometry and the fraction of fields that
    decode with it."""
    stack = np.stack([f.astype(np.float64) for f in fields])
    mean = stack.mean(axis=0)
    h, w = mean.shape
    ranked = []
    for sy in np.linspace(h / 240 * 0.94, h / 240 * 1.06, 7):
        for y0 in np.arange(-0.12 * h, 0.12 * h + 1, max(1.0, sy / 2)):
            top = int(round(y0 + sy * (TOP + HEIGHT * 0.25)))
            bottom = int(round(y0 + sy * (TOP + HEIGHT * 0.75)))
            if top < 0 or bottom <= top or bottom > h:
                continue
            profile = mean[top:bottom].mean(axis=0)
            for sx in np.linspace(w / 256 * 0.70, w / 256 * 1.0, 61):
                x0s = np.arange(0, w - 200 * sx, 0.5)
                if not len(x0s):
                    continue
                scores = _score_profiles(profile, x0s, sx)
                best = int(np.argmax(scores))
                ranked.append((float(scores[best]), Geometry(float(x0s[best]), float(sx), float(y0), float(sy))))
    ranked.sort(key=lambda r: -r[0])
    sample = fields[:: max(1, len(fields) // 60)]
    shortlist = sorted(ranked[:candidates], key=lambda r: -_rate(sample, r[1]))[:3]
    best, best_rate = None, -1.0
    for _, geometry in shortlist:
        refined, rate = _refine(sample, geometry)
        if rate > best_rate:
            best, best_rate = refined, rate
    if best is None:
        raise ValueError("no barcode band found in the capture")
    return best, best_rate


def _rate(fields, geometry: Geometry) -> float:
    return sum(read(f, geometry).code is not None for f in fields) / len(fields)


def _refine(fields, geometry: Geometry) -> tuple[Geometry, float]:
    """Local search on the decode rate, then centre within the plateau."""
    g, rate = geometry, _rate(fields, geometry)
    steps = {"x0": 0.5, "sx": 0.002 * geometry.sx, "y0": 0.5, "sy": 0.01 * geometry.sy}
    for _ in range(3):
        for name, step in steps.items():
            for direction in (-1, 1):
                for k in range(1, 6):
                    trial = Geometry(**{**g.as_dict(), name: getattr(g, name) + direction * k * step})
                    r = _rate(fields, trial)
                    if r > rate:
                        g, rate = trial, r
    return g, rate
