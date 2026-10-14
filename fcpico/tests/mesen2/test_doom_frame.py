# SPDX-License-Identifier: BSD-3-Clause
"""The Doom display gate must reject the hardware's mailbox-only failure."""

from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "sim/mesen2"))
from run_doom_frame import check_heartbeats, protocol


def trace(count=protocol.PPU_COUNT_VAL_V2, stop_frame=None):
    rows = []
    stops = 0
    for frame in range(1, 32):
        if frame == stop_frame:
            stops += 1
        row = {"ppu_frame": str(frame + 150), "heartbeats": str(frame),
               "last_count": str(count), "dma_stops": str(stops)}
        rows.extend([row, dict(row)])  # frame/packet logs are not extra heartbeats
    return rows


def test_steady_heartbeat_count():
    assert check_heartbeats(trace(), protocol.PPU_COUNT_VAL_V2, 150) == 31


@pytest.mark.parametrize("count", [128, protocol.PPU_COUNT_VAL_V2 - 4])
def test_rejects_mailbox_only_and_wrong_prerender_phase(count):
    with pytest.raises(AssertionError, match=f"count={count}"):
        check_heartbeats(trace(count), protocol.PPU_COUNT_VAL_V2, 150)


def test_rejects_dma_stop_even_with_correct_final_count():
    with pytest.raises(AssertionError, match="new DMA stops=1"):
        check_heartbeats(trace(stop_frame=18), protocol.PPU_COUNT_VAL_V2, 150)


def test_missing_heartbeats_cannot_pass():
    with pytest.raises(AssertionError, match="only 0"):
        check_heartbeats([], protocol.PPU_COUNT_VAL_V2, 150)


def test_rejects_dma_stop_between_heartbeats():
    rows = trace(stop_frame=18)
    rows[34]["heartbeats"] = "17"  # timeout log before heartbeat 18
    with pytest.raises(AssertionError, match="new DMA stops=1"):
        check_heartbeats(rows, protocol.PPU_COUNT_VAL_V2, 150)
