#!/usr/bin/env python3
"""Check real composed Doom frames against the independent Python NES converter."""

from __future__ import annotations

import pathlib
import subprocess
import sys
import tempfile

import numpy as np
from PIL import Image

ROOT = pathlib.Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "fcpico/tools"))

import fcvideo_ref  # noqa: E402
from fcpico import protocol, stream  # noqa: E402

PRESET = {
    "backdrop": 0x0F,
    "subpalettes": ((0x1D, 0x00, 0x30), (0x08, 0x18, 0x30),
                    (0x06, 0x16, 0x30), (0x09, 0x19, 0x30)),
    "shadow_lift": True,
}


def main() -> int:
    executable, whx = map(pathlib.Path, sys.argv[1:3])
    with tempfile.TemporaryDirectory(prefix="fcpico-real-stream-") as directory:
        root = pathlib.Path(directory)
        raw, encoded = root / "raw", root / "encoded"
        subprocess.run(
            [str(executable), "--whx", str(whx), "--demo", "1", "--frames", "102",
             "--lockstep", "--dump-8bit", str(raw), "--dump-stream", str(encoded)],
            check=True, capture_output=True, text=True, timeout=30,
        )
        with Image.open(raw / "frame000000.png") as preview:
            playpal = np.asarray(preview.getpalette()[:768], dtype=np.uint8).reshape(256, 3)
        err, lut = fcvideo_ref.build_err_and_lut(
            playpal, PRESET["subpalettes"], PRESET["backdrop"], PRESET["shadow_lift"]
        )
        previous = None
        checked = (0, 100, 101)
        for number in range(102):
            frame = np.frombuffer((raw / f"frame{number:06d}.raw").read_bytes(),
                                  dtype=np.uint8).reshape(200, 320)
            image = fcvideo_ref.place_letterbox(fcvideo_ref.decimate_320_to_256(frame))
            attr = fcvideo_ref.choose_block_palettes(image, err, prev_attr=previous)
            previous = attr
            if number not in checked:
                continue
            pixels = fcvideo_ref.quantize(image, lut, attr)
            pixels[:fcvideo_ref.LETTERBOX_TOP] = 0
            pixels[stream.VRAM_LINES - fcvideo_ref.LETTERBOX_BOTTOM:] = 0
            mailbox = bytearray(protocol.FC_COM_BUF_SIZE_V2)
            mailbox[protocol.MBX_FLAGS] = (
                protocol.MBX_FLAG_V2 | protocol.MBX_FLAG_ATTR_VALID |
                protocol.MBX_FLAG_PAL_VALID
            )
            mailbox[protocol.MBX_MAGIC] = protocol.PF_MAGIC_NO
            mailbox[protocol.MBX_ATTR:protocol.MBX_ATTR + protocol.MBX_ATTR_LEN] = attr
            for p, entries in enumerate(PRESET["subpalettes"]):
                mailbox[protocol.MBX_PAL + 4 * p:protocol.MBX_PAL + 4 * p + 4] = bytes(
                    (PRESET["backdrop"], *entries)
                )
            expected = bytes(stream.encode_frame(pixels, mailbox))
            actual = (encoded / f"frame{number:06d}.bin").read_bytes()
            if actual != expected:
                first = next(i for i, (a, b) in enumerate(zip(actual, expected)) if a != b)
                raise AssertionError(f"frame {number}: stream differs at byte {first}: "
                                     f"C={actual[first]:02x}, Python={expected[first]:02x}")
    print("3/3 real Doom v2 streams (frames 0, 100, 101) match Python oracle byte-for-byte")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
