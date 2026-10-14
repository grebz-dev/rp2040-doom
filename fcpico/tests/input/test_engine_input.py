#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-3-Clause
"""Check that scripted NES pad input moves the real host Doom player."""

from pathlib import Path
import re
import subprocess
import sys
import tempfile


def position(engine: str, whx: str, pads: Path) -> tuple[int, int]:
    result = subprocess.run(
        [engine, "--whx", whx, "--warp", "1", "1", "--frames", "120",
         "--lockstep", "--pads", str(pads)],
        check=True, capture_output=True, text=True,
    )
    match = re.search(r"player x=(-?\d+) y=(-?\d+)", result.stdout)
    if match is None:
        raise AssertionError("host runner did not report a player position")
    return int(match[1]), int(match[2])


def use_frames(engine: str, whx: str, pads: Path) -> int:
    result = subprocess.run(
        [engine, "--whx", whx, "--warp", "1", "1", "--frames", "120",
         "--lockstep", "--pads", str(pads)],
        check=True, capture_output=True, text=True,
    )
    match = re.search(r"use frames=(\d+)", result.stdout)
    if match is None:
        raise AssertionError("host runner did not report use commands")
    return int(match[1])


def main() -> None:
    engine, whx = sys.argv[1:]
    with tempfile.TemporaryDirectory() as directory:
        idle = Path(directory) / "idle.pads"
        moving = Path(directory) / "moving.pads"
        idle.write_text("0\n" * 130)
        moving.write_text("0\n" * 10 + "8\n" * 110 + "0\n" * 10)
        assert position(engine, whx, idle) != position(engine, whx, moving)
        using = Path(directory) / "using.pads"
        using.write_text("0\n" * 20 + "64\n" + "0\n" * 109)
        assert use_frames(engine, whx, idle) == 0
        assert use_frames(engine, whx, using) > 0


if __name__ == "__main__":
    main()
