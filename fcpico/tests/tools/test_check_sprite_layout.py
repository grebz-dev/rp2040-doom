# SPDX-License-Identifier: BSD-3-Clause
"""Capacity boundary checks for proposed NES sprite layouts."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
from check_sprite_layout import check_screen  # noqa: E402


class SpriteLayoutBudgetTests(unittest.TestCase):
    def test_scanline_boundary_and_face_cost(self):
        screen = {"name": "status", "groups": [
            {"name": "face", "x": 100, "y": 208, "width": 16, "height": 16},
            {"name": "packed values", "x": 8, "y": 208,
             "width": 48, "height": 8},
        ]}
        result = check_screen(screen)
        self.assertEqual(result["entries"], 10)
        self.assertEqual(result["max_scanline"], 8)
        self.assertEqual(result["scanlines"][208:216], [8] * 8)
        self.assertEqual(result["scanlines"][216:224], [2] * 8)
        self.assertEqual(result["errors"], [])

        screen["groups"].append({"name": "key", "x": 180, "y": 208,
                                 "width": 8, "height": 8})
        result = check_screen(screen)
        self.assertEqual(result["max_scanline"], 9)
        self.assertIn("scanlines", result["errors"][0])

    def test_oam_boundary_across_nonoverlapping_lines(self):
        groups = [{"name": f"row {i}", "x": 8, "y": (i + 1) * 8,
                   "width": 64, "height": 8} for i in range(8)]
        screen = {"name": "menu", "groups": groups}
        self.assertEqual(check_screen(screen)["errors"], [])
        screen["groups"].append({"name": "extra", "x": 8, "y": 80,
                                 "width": 8, "height": 8})
        self.assertIn("OAM: 65", check_screen(screen)["errors"][0])

    def test_rejects_off_screen_group(self):
        screen = {"name": "menu", "groups": [
            {"name": "long text", "x": 248, "y": 10,
             "width": 16, "height": 8},
        ]}
        self.assertIn("outside", check_screen(screen)["errors"][0])

    def test_partial_tile_still_occupies_full_scanline_height(self):
        screen = {"name": "overlap", "groups": [
            {"name": "short glyph", "x": 8, "y": 8, "width": 64, "height": 2},
            {"name": "next row", "x": 8, "y": 10, "width": 8, "height": 8},
        ]}
        result = check_screen(screen)
        self.assertEqual(result["scanlines"][10], 9)
        self.assertIn("scanlines", result["errors"][0])

    def test_first_scanline_cannot_display_a_sprite(self):
        screen = {"name": "top", "groups": [
            {"name": "glyph", "x": 8, "y": 0, "width": 8, "height": 8},
        ]}
        self.assertIn("sprite-visible lines", check_screen(screen)["errors"][0])

    def test_background_text_cells_are_budgeted_without_oam(self):
        screen = {"name": "status", "groups": [], "background_text_regions": [
            {"name": "ammo pairs", "x": 16, "y": 224, "width": 224,
             "height": 8, "characters": 28},
        ]}
        result = check_screen(screen)
        self.assertEqual(result["entries"], 0)
        self.assertEqual(result["background_text_tiles"], 28)
        self.assertEqual(result["errors"], [])
        screen["background_text_regions"][0]["characters"] = 29
        self.assertIn("do not fit", check_screen(screen)["errors"][0])


if __name__ == "__main__":
    unittest.main()
