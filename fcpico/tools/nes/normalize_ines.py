#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-3-Clause
"""Convert NESASM CE's NES 2.0 header to the vendor's mapper-0 iNES header.

NESASM CE reproduces the tutorial PRG byte-for-byte, but writes two NES 2.0
header bytes. The FC PICO fix bank and cartridge model use the vendor's iNES
header, so fail closed on any other layout rather than silently rewriting it.
"""

from __future__ import annotations

import argparse
from pathlib import Path


def normalize(data: bytes) -> bytes:
    if len(data) != 16 + 32768 or data[:16] != bytes.fromhex(
        "4e45531a020001080000000700000000"
    ):
        raise ValueError("expected NESASM CE's 32 KB mapper-0 NES 2.0 image")
    header = bytearray(data[:16])
    header[7] = 0
    header[11] = 0
    return bytes(header) + data[16:]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    args.output.write_bytes(normalize(args.source.read_bytes()))


if __name__ == "__main__":
    main()
