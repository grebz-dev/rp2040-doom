# SPDX-License-Identifier: BSD-3-Clause
"""Regression tests for the Mesen2 scenario report's trace filtering."""

from pathlib import Path
import sys

import pytest

HERE = Path(__file__).resolve()
sys.path.insert(0, str(HERE.parents[2] / "sim" / "mesen2"))

from run_scenario import steady_heartbeat_rows, summarize_fetch_trace


def _row(frame, cpu_reads, render_reads, last_count):
    return {
        "ppu_frame": str(frame),
        "cpu_reads": str(cpu_reads),
        "render_reads": str(render_reads),
        "last_count": str(last_count),
    }


def test_histogram_uses_only_steady_heartbeat_rows():
    rows = [
        _row(119, 65, 16388, 16453),  # startup boundary
        _row(123, 0, 1901828, 0),  # ordinary protocol write
        _row(125, 90, 1934604, 1934694),  # transition/cumulative count
        _row(156, 65, 15426, 15490),  # real calibrated heartbeat
    ]

    assert steady_heartbeat_rows(rows) == [rows[-1]]


def _fetch_rows(mask="0xf800"):
    rows = []
    for line in range(-1, 240):
        selected_tiles = (31 if line == -1 else 30) if mask == "0xf800" else 32
        for tile in range(selected_tiles):
            for cycle, address in ((tile * 8 + 5, 0x800), (tile * 8 + 7, 0x808)):
                rows.append({"ppu_frame": "121", "scanline": str(line),
                             "cycle": str(cycle), "address": str(address), "value": "255"})
        if mask == "0xe000":
            for tile in range(8):
                for cycle, address in ((tile * 8 + 261, 4096), (tile * 8 + 263, 4104)):
                    rows.append({"ppu_frame": "121", "scanline": str(line),
                                 "cycle": str(cycle), "address": str(address), "value": "255"})
        for cycle, address in ((325, 0x800), (327, 0x808), (333, 0x800), (335, 0x808)):
            rows.append({"ppu_frame": "121", "scanline": str(line),
                         "cycle": str(cycle), "address": str(address), "value": "255"})
    return rows


@pytest.mark.parametrize("mask,selected,sprites", [
    ("0xf800", 15426, 0), ("0xf000", 16388, 0), ("0xe000", 20244, 3856)
])
def test_fetch_profile_checks_all_241_lines(mask, selected, sprites):
    profile = summarize_fetch_trace(_fetch_rows(mask), 121, mask)
    assert profile["selected_reads"] == selected
    assert profile["sprite_reads"] == sprites


def test_fetch_profile_rejects_wrong_prefetch_cycle():
    rows = _fetch_rows()
    rows[62]["cycle"] = "323"
    with pytest.raises(ValueError, match="background fetch cycles"):
        summarize_fetch_trace(rows, 121, "0xf800")


def test_fetch_profile_rejects_wrong_bitplane_pair():
    rows = _fetch_rows()
    rows[1]["address"] = str(0x809)
    with pytest.raises(ValueError, match="low/high pattern"):
        summarize_fetch_trace(rows, 121, "0xf800")


def test_fetch_profile_rejects_font_addresses_even_with_correct_count():
    rows = _fetch_rows()
    rows[0]["address"] = "0"
    rows[1]["address"] = "8"
    with pytest.raises(ValueError, match="stream address range"):
        summarize_fetch_trace(rows, 121, "0xf800")
