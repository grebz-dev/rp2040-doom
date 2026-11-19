#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-3-Clause
"""Read the frame-ID barcode from every field of a console capture.

    decode_video.py capture.mkv -o fields.csv [--calibration cal.json]
    decode_video.py fields_dir/ -o fields.csv        (PNG/BMP fields, sorted)

A capture device usually records the NES's 240p signal as 480i, so each field
is one console frame: video input is split into fields (ffmpeg
``separatefields``) unless ``--frames`` is given. The band geometry is found
from the first ``--calibrate`` fields and saved with ``--save-calibration``;
pass it back with ``--calibration`` for later captures from the same setup.
Needs ffmpeg/ffprobe on PATH for video files.
"""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
import subprocess
from typing import Iterator

import numpy as np

try:
    from . import barcode
except ImportError:  # run as a script
    import barcode  # type: ignore

IMAGE_SUFFIXES = {".png", ".bmp", ".tif", ".tiff", ".jpg", ".jpeg"}


def iter_fields(source: Path, fields: bool = True) -> Iterator[np.ndarray]:
    """2-D uint8 luma images, one per console frame."""
    if source.is_dir():
        from PIL import Image
        for path in sorted(p for p in source.iterdir() if p.suffix.lower() in IMAGE_SUFFIXES):
            yield np.asarray(Image.open(path).convert("L"))
        return
    probe = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                            "stream=width,height", "-of", "csv=p=0", str(source)],
                           check=True, capture_output=True, text=True)
    width, height = (int(v) for v in probe.stdout.strip().split(",")[:2])
    command = ["ffmpeg", "-v", "error", "-i", str(source)]
    if fields:
        command += ["-vf", "separatefields"]
        height //= 2
    command += ["-f", "rawvideo", "-pix_fmt", "gray", "-"]
    size = width * height
    with subprocess.Popen(command, stdout=subprocess.PIPE) as process:
        assert process.stdout is not None
        while True:
            data = process.stdout.read(size)
            if len(data) < size:
                break
            yield np.frombuffer(data, np.uint8).reshape(height, width)


def decode(source: Path, output: Path, fields: bool, calibration: Path | None,
           save_calibration: Path | None, calibrate_count: int) -> dict:
    stream = iter_fields(source, fields)
    head = []
    if calibration:
        geometry = barcode.Geometry(**json.loads(calibration.read_text()))
        rate = None
    else:
        for image in stream:
            head.append(image)
            if len(head) >= calibrate_count:
                break
        geometry, rate = barcode.calibrate(head)
        if save_calibration:
            save_calibration.write_text(json.dumps(geometry.as_dict(), indent=1) + "\n")
    decoded = total = 0
    with output.open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["field", "id", "generation", "timeout", "tic", "black", "white", "margin"])

        def emit(index, image):
            nonlocal decoded, total
            reading = barcode.read(image, geometry)
            code = reading.code
            total += 1
            decoded += code is not None
            writer.writerow([index, *(("", "", "", "") if code is None else
                                      (code.id, code.generation, int(code.timeout), code.tic)),
                             f"{reading.black:.1f}", f"{reading.white:.1f}", f"{reading.margin:.1f}"])

        index = 0
        for image in head:
            emit(index, image)
            index += 1
        for image in stream:
            emit(index, image)
            index += 1
    return {"geometry": geometry.as_dict(), "calibration_rate": rate,
            "fields": total, "decoded": decoded}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("source", type=Path, help="video file or directory of field images")
    parser.add_argument("-o", "--output", type=Path, required=True, help="CSV, one row per field")
    parser.add_argument("--frames", action="store_true",
                        help="the video is progressive: do not split it into fields")
    parser.add_argument("--calibration", type=Path, help="geometry JSON from an earlier run")
    parser.add_argument("--save-calibration", type=Path)
    parser.add_argument("--calibrate", type=int, default=600,
                        help="fields used to find the band (default 600, about 10 s)")
    args = parser.parse_args(argv)
    result = decode(args.source, args.output, not args.frames, args.calibration,
                    args.save_calibration, args.calibrate)
    print(json.dumps(result, indent=1))
    return 0 if result["decoded"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
