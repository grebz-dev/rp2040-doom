# SPDX-License-Identifier: BSD-3-Clause
"""Per-map background palettes: map geometry, choice and the generated header."""
import struct

import numpy as np

import map_palettes as mp
from test_audio_convert import make_wad
from test_world_atlas import patch


def name8(text):
    return text.encode("ascii").ljust(8, b"\0")


def room_map(wall, floor, light=255):
    """One 128x128 square sector, walls of texture `wall`, floor and ceiling `floor`."""
    verts = [(0, 0), (0, 128), (128, 128), (128, 0)]
    vertexes = b"".join(struct.pack("<hh", *v) for v in verts)
    linedefs = b"".join(struct.pack("<hhhhhHH", i, (i + 1) % 4, 1, 0, 0, i, 0xFFFF) for i in range(4))
    sidedefs = b"".join(struct.pack("<hh8s8s8sh", 0, 0, name8("-"), name8("-"), name8(wall), 0)
                        for _ in range(4))
    sectors = struct.pack("<hh8s8shhh", 0, 128, name8(floor), name8(floor), light, 0, 0)
    return [("THINGS", b""), ("LINEDEFS", linedefs), ("SIDEDEFS", sidedefs),
            ("VERTEXES", vertexes), ("SEGS", b""), ("SSECTORS", b""), ("NODES", b""),
            ("SECTORS", sectors), ("REJECT", b""), ("BLOCKMAP", b"")]


def fixture():
    playpal = bytearray(768)
    playpal[3:6] = bytes((200, 30, 30))    # 1: red
    playpal[6:9] = bytes((40, 60, 200))    # 2: blue
    colormap = bytes(range(256)) * 34
    texture1 = struct.pack("<ii", 1, 8) + struct.pack("<8sihhih", name8("REDWALL"), 0, 64, 64, 0, 1)
    texture1 += struct.pack("<hhhhh", 0, 0, 0, 1, 0)
    lumps = [("PLAYPAL", bytes(playpal)), ("COLORMAP", colormap),
             ("PNAMES", struct.pack("<i", 1) + name8("REDPATCH")),
             ("TEXTURE1", texture1), ("REDPATCH", patch(64, 64, 0, 0, 1))]
    lumps += [("E1M1", b"")] + room_map("REDWALL", "BLUFLAT")
    lumps += [("E1M2", b"")] + room_map("REDWALL", "REDFLAT")
    lumps += [("F_START", b""), ("BLUFLAT", bytes([2]) * 4096), ("REDFLAT", bytes([1]) * 4096),
              ("F_END", b"")]
    return make_wad(lumps)


def test_surfaces_weigh_walls_by_area_and_flats_by_sector_area():
    wad = mp.Wad(fixture())
    walls, flats = mp.map_surfaces(wad.maps()["E1M1"])
    assert walls == {("REDWALL", 255): 4 * 128 * 128}
    assert flats == {("BLUFLAT", 255): 2 * 128 * 128}


def test_choice_follows_the_colours_a_map_uses():
    choices = mp.choose(fixture())
    hue = lambda p: {c & 0x0F for c in p if c not in (0x0F, 0x2D, 0x30, 0x3D)}
    for name in ("E1M1", "E1M2"):
        assert len(choices[name]) == 3
        assert any(hue(p) & {0x05, 0x06, 0x07} for p in choices[name]), name
    assert any(hue(p) & {0x01, 0x02, 0x03} for p in choices["E1M1"])
    assert choices["E1M1"] != choices["E1M2"]


def test_best_set_prefers_columns_that_cover_every_tile():
    costs = np.array([[0, 9, 9, 9, 1], [9, 0, 9, 9, 1], [9, 9, 9, 0, 1], [9, 9, 9, 0, 1]], float)
    hud = np.full(4, 100.0)
    assert mp.best_set(costs, np.ones(4), hud) == [0, 1, 3]


def test_header_lists_maps_in_engine_order():
    choices = {"E1M2": [(1, 2, 3)] * 3, "E1M1": [(4, 5, 6)] * 3, "MAP10": [(7, 8, 9)] * 3}
    text = mp.header(choices)
    assert text.index("/* E1M1 */") < text.index("/* E1M2 */") < text.index("/* MAP10 */")
    assert "{1, 10, {{0x07, 0x08, 0x09}" in text
