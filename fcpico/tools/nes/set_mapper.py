#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-3-Clause
"""Create an emulator-only NES 2.0 copy of the tutorial ROM, preserving all PRG bytes."""

import argparse
from pathlib import Path


def set_mapper(data: bytes, mapper: int = 4093) -> bytes:
    if not 0 <= mapper <= 4095:
        raise ValueError("NES 2.0 mapper must be in 0..4095")
    if len(data) != 16 + 32768 or data[:4] != b"NES\x1a":
        raise ValueError("expected the 32 KiB tutorial iNES ROM without CHR-ROM")
    if data[4] != 2 or data[5] != 0 or data[6] & 0x04 or data[7] & 0x0f:
        raise ValueError("expected plain iNES, no trainer or special console")
    header = bytearray(data[:16])
    header[6] = (header[6] & 0x0f) | ((mapper & 0x0f) << 4)
    header[7] = (mapper & 0xf0) | 0x08  # NES 2.0
    header[8:] = bytes(8)
    header[8] = mapper >> 8
    header[11] = 7  # 64 << 7 = 8 KiB CHR RAM; no CHR-ROM on FC PICO.
    return bytes(header) + data[16:]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--mapper", type=int, default=4093)
    args = parser.parse_args()
    if args.source.resolve() == args.output.resolve():
        parser.error("output must be a separate emulator-only ROM")
    try:
        converted = set_mapper(args.source.read_bytes(), args.mapper)
        args.output.write_bytes(converted)
    except (OSError, ValueError) as error:
        parser.error(str(error))


if __name__ == "__main__":
    main()
