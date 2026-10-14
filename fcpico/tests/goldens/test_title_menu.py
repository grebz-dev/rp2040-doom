#!/usr/bin/env python3
"""Exercise the title and menu overlay through the composed-frame host sink."""

import hashlib
import pathlib
import subprocess
import sys
import tempfile


EXPECTED = {
    "title": "23ab4a99836f8ded24877ade251243f27491ff3f9f724e7d1a9ceba5982357ef",
    "menu": "9528567f8758d5cb5a070f91c520d990b8664b6b0932bf064014addff7036eff",
}


def main() -> int:
    executable = pathlib.Path(sys.argv[1])
    whx = pathlib.Path(sys.argv[2])
    with tempfile.TemporaryDirectory(prefix="fcpico-title-menu-") as work:
        for name in ("title", "menu"):
            output = pathlib.Path(work) / name
            command = [str(executable), "--whx", str(whx), "--frames", "150",
                       "--lockstep", "--dump-8bit", str(output)]
            if name == "menu":
                command += ["--menu-at", "60"]
            subprocess.run(command, capture_output=True, text=True, timeout=30, check=True)
            frame = output / "frame000100.raw"
            digest = hashlib.sha256(frame.read_bytes()).hexdigest()
            if digest != EXPECTED[name]:
                raise AssertionError(f"{name} frame changed: {digest}")
            if not (output / "frame000100.png").is_file():
                raise AssertionError(f"{name} PNG preview missing")
    print("title and menu frame 100 goldens verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
