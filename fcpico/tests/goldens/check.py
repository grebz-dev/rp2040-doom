#!/usr/bin/env python3
"""Check the first 600 DEMO1 FC PICO composed frames against reviewed hashes."""

import hashlib
import pathlib
import struct
import sys


def main() -> int:
    if len(sys.argv) != 2:
        raise SystemExit("usage: check.py FRAME_DIRECTORY")
    directory = pathlib.Path(sys.argv[1])
    frames = sorted(directory.glob("frame*.raw"))
    if len(frames) != 600:
        raise AssertionError(f"expected 600 raw frames, got {len(frames)}")
    for index, frame in enumerate(frames):
        if frame.name != f"frame{index:06d}.raw" or frame.stat().st_size != 320 * 200:
            raise AssertionError(f"invalid composed frame: {frame}")

    manifest = pathlib.Path(__file__).with_name("doom_demo1_600.sha256")
    expected = {name: digest for digest, name in
                (line.split() for line in manifest.read_text().splitlines())}
    if len(expected) != 60:
        raise AssertionError("expected 60 golden hashes")
    for index in range(0, 600, 10):
        name = f"frame{index:06d}.raw"
        digest = hashlib.sha256((directory / name).read_bytes()).hexdigest()
        if expected.get(name) != digest:
            raise AssertionError(f"golden mismatch: {name}: {digest}")

    pngs = sorted(directory.glob("frame*.png"))
    if [png.name for png in pngs] != [f"frame{index:06d}.png" for index in range(0, 600, 100)]:
        raise AssertionError("expected PNG previews every 100th frame")
    for png in pngs:
        header = png.read_bytes()[:26]
        if header[:8] != b"\x89PNG\r\n\x1a\n" or struct.unpack(">II", header[16:24]) != (320, 200):
            raise AssertionError(f"invalid PNG preview: {png}")
    print("600 composed 320x200 frames; 60 golden hashes and 6 PNG previews verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
