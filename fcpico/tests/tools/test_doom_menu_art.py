# SPDX-License-Identifier: BSD-3-Clause
"""Resident menu art fits its sprite and CHR budgets."""

from __future__ import annotations

import json
import re

import numpy as np
from PIL import Image

from build_doom_menu_art import (ROOT, SMALL_LOGO_SIZE, WHDATA, Whx,
                                 encode_tiles, episode_tiles, logo_pixels, patch_pixels)
from build_native_status_probe import (MENU_GLYPHS, MENU_LAYOUTS,
                                       episode_menu_oam, logo_oam,
                                       main_menu_oam, menu_oam, small_logo_oam)
from edit_sprite_sheets import SHEETS


def test_logo_sheet_is_reproducible_and_eight_sprites_wide():
    whx = Whx(ROOT / "doom1.whx")
    pixels = logo_pixels(whx)
    assert len(encode_tiles(pixels)) == 32 * 16
    labels = re.findall(r"VPATCH_NAME\(([^)]+)\)",
                        WHDATA.read_text().split("#define VPATCH_LIST \\", 1)[1]
                        .split("\n\nenum", 1)[0])
    lookup = np.frombuffer(whx.named("P_START"), dtype="<u2")
    source = patch_pixels(whx.lump(int(lookup[labels.index("M_DOOM") + 1])))
    sampled = np.asarray(Image.fromarray(source).resize((64, 32), Image.Resampling.NEAREST))
    assert np.array_equal(pixels != 0, sampled >= 0), "sprite outline must match M_DOOM"
    small = logo_pixels(whx, SMALL_LOGO_SIZE)
    assert len(encode_tiles(small)) == 21 * 16
    assert len(small_logo_oam()) == 21 * 4
    oam = logo_oam()
    assert len(oam) == 32 * 4
    assert max(oam[index * 4 + 3] for index in range(32)) == 152
    assert all(sum(oam[index * 4] == y for index in range(32)) == 8
               for y in (15, 23, 31, 39))


def test_episode_labels_yield_to_centered_background_text():
    art, lookup = episode_tiles()
    assert len(art) == 19 * 16
    manifest = json.loads((ROOT / "fcpico/assets/doom_menu_art.json").read_text())
    assert manifest["episode_lines"] == [["KNEE DEEP IN", "THE DEAD"],
                                         ["SHORES OF HELL"], ["INFERNO"]]
    oam = episode_menu_oam()
    assert len(oam) == MENU_GLYPHS * 4
    assert all(oam[index * 4] == 0xEF for index in range(MENU_GLYPHS))
    main = main_menu_oam()
    assert sum(main[index * 4] < 0xEF for index in range(MENU_GLYPHS)) == 28
    assert 24 + 1 <= 64


def test_options_labels_follow_the_compiled_mouse_free_menu():
    labels = MENU_LAYOUTS[3][0]
    assert labels == ("END GAME", "MESSAGE", "SOUND")
    oam = menu_oam(MENU_LAYOUTS[3])
    assert {oam[index * 4] for index in range(MENU_GLYPHS)
            if oam[index * 4] != 0xEF} == {58, 74, 90}


def test_editable_png_sheets_round_trip_to_the_rom_chr(tmp_path, monkeypatch):
    import shutil
    import edit_sprite_sheets

    assets = tmp_path / "assets"
    shutil.copytree(ROOT / "fcpico/assets", assets)
    monkeypatch.setattr(edit_sprite_sheets, "ASSETS", assets)
    sources = {path.name: path.read_bytes() for path in assets.glob("*.chr")}
    sources.update({path.name: path.read_bytes() for path in assets.glob("*.bin")})
    for name, (stem, *_rest) in SHEETS.items():
        edit_sprite_sheets.process(name, "export")
        assert (assets / f"{stem}_edit.png").is_file(), name
        edit_sprite_sheets.process(name, "import")
    for filename, data in sources.items():
        assert (assets / filename).read_bytes() == data, filename