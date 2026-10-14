#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-3-Clause
"""Check proposed 8x8 NES sprite layouts against OAM and scanline limits.

Input JSON contains ``screens``; each screen has named rectangular ``groups``
with native-pixel x, y, width and height. A group consumes a full 8x8 sprite
grid, even when edge tiles are partly transparent. Coordinates describe the
visible top left; the eventual OAM Y encoding is a separate renderer concern.
The first scanline is excluded because PPU sprite evaluation cannot populate it.
This is a conservative capacity check, not an image or hardware simulation.
Optional ``background_text_regions`` reserve aligned 8x8 cells and validate
character capacity; they consume no OAM and require separate stream-count proof.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

SCREEN_WIDTH = 256
SCREEN_HEIGHT = 240
SPRITE_SIZE = 8
OAM_LIMIT = 64
SCANLINE_LIMIT = 8


def check_screen(screen: dict) -> dict:
    """Return occupancy and errors for one proposed native-pixel layout."""
    name = screen["name"]
    counts = [0] * SCREEN_HEIGHT
    entries = 0
    errors = []
    for group in screen["groups"]:
        label = group["name"]
        x, y = group["x"], group["y"]
        width, height = group["width"], group["height"]
        if not all(isinstance(v, int) and not isinstance(v, bool)
                   for v in (x, y, width, height)):
            errors.append(f"{label}: coordinates and dimensions must be integers")
            continue
        if width <= 0 or height <= 0 or x < 0 or y < 1 or \
                x + width > SCREEN_WIDTH or y + height > SCREEN_HEIGHT:
            errors.append(f"{label}: rectangle is outside sprite-visible lines 1..239 or 256-pixel width")
            continue

        columns = (width + SPRITE_SIZE - 1) // SPRITE_SIZE
        rows = (height + SPRITE_SIZE - 1) // SPRITE_SIZE
        entries += columns * rows
        for row in range(rows):
            top = y + row * SPRITE_SIZE
            # The PPU evaluates every pixel row of the allocated 8x8 tile,
            # including rows transparent in the source rectangle.
            bottom = min(top + SPRITE_SIZE, SCREEN_HEIGHT)
            for line in range(top, bottom):
                counts[line] += columns

    text_tiles = 0
    for region in screen.get("background_text_regions", []):
        label = region["name"]
        x, y = region["x"], region["y"]
        width, height = region["width"], region["height"]
        characters = region["characters"]
        if not all(isinstance(v, int) and not isinstance(v, bool)
                   for v in (x, y, width, height, characters)):
            errors.append(f"{label}: text geometry must be integers")
            continue
        if x < 0 or y < 0 or width <= 0 or height <= 0 or \
                x + width > SCREEN_WIDTH or y + height > SCREEN_HEIGHT or \
                x % SPRITE_SIZE or y % SPRITE_SIZE or \
                width % SPRITE_SIZE or height % SPRITE_SIZE:
            errors.append(f"{label}: text region must fit aligned 8x8 cells")
            continue
        if characters < 0 or characters * SPRITE_SIZE > width:
            errors.append(f"{label}: {characters} characters do not fit {width} pixels")
            continue
        text_tiles += width * height // (SPRITE_SIZE * SPRITE_SIZE)

    if entries > OAM_LIMIT:
        errors.append(f"OAM: {entries} entries exceed {OAM_LIMIT}")
    overfull = [line for line, count in enumerate(counts)
                if count > SCANLINE_LIMIT]
    if overfull:
        errors.append(f"scanlines {overfull}: exceed {SCANLINE_LIMIT} sprites")
    return {
        "name": name,
        "entries": entries,
        "max_scanline": max(counts),
        "max_scanlines": [line for line, count in enumerate(counts)
                          if count == max(counts) and count > 0],
        "scanlines": counts,
        "background_text_tiles": text_tiles,
        "errors": errors,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("layout", type=Path, help="JSON file with a screens array")
    parser.add_argument("--lines", action="store_true",
                        help="print occupancy for every used scanline")
    args = parser.parse_args()
    data = json.loads(args.layout.read_text())
    if not isinstance(data, dict) or not isinstance(data.get("screens"), list) \
            or not data["screens"]:
        parser.error("layout must contain a nonempty screens array")
    failed = False
    for screen in data["screens"]:
        result = check_screen(screen)
        print(f"{result['name']}: {result['entries']}/64 OAM, "
              f"{result['max_scanline']}/8 peak scanline sprites, "
              f"{result['background_text_tiles']} reserved BG text cells")
        if args.lines:
            for line, count in enumerate(result["scanlines"]):
                if count:
                    print(f"  line {line}: {count}/8")
        for error in result["errors"]:
            print(f"  {error}")
        failed |= bool(result["errors"])
    return int(failed)


if __name__ == "__main__":
    raise SystemExit(main())
