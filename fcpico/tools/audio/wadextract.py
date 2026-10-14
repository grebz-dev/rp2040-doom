#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-3-Clause
"""Extract named IWAD/PWAD lumps, with the last directory entry winning.

    python3 fcpico/tools/audio/wadextract.py doom1.wad D_E1M1 'DS*' -o /tmp/lumps

Music is written as .mus, other lumps as .lmp. No WAD assets are checked in.
"""

from __future__ import annotations

import argparse
import fnmatch
import struct
from pathlib import Path


def read_wad(data: bytes) -> dict[str, bytes]:
    """Validate the complete directory before returning named lump bytes."""
    if len(data) < 12:
        raise ValueError("truncated WAD header")
    magic, count, directory = struct.unpack_from("<4sii", data)
    if magic not in (b"IWAD", b"PWAD"):
        raise ValueError("expected IWAD or PWAD magic")
    if count < 0 or directory < 12 or directory > len(data) - count * 16:
        raise ValueError("WAD directory is outside the file")
    lumps = {}
    for index in range(count):
        offset, size, raw_name = struct.unpack_from("<ii8s", data, directory + index * 16)
        # Empty marker lumps sometimes use zero as their file position.
        if offset < 0 or size < 0 or offset > len(data) - size:
            raise ValueError(f"WAD lump {index} is outside the file")
        try:
            name = raw_name.split(b"\0", 1)[0].decode("ascii").upper()
        except UnicodeDecodeError as exc:
            raise ValueError(f"WAD lump {index} has a non-ASCII name") from exc
        # These names become filenames: disallow paths and glob metacharacters.
        if not name or any(c not in "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-[]" for c in name):
            raise ValueError(f"WAD lump {index} has an unsafe name: {name!r}")
        lumps[name] = data[offset : offset + size]
    return lumps


def extract_lumps(data: bytes, patterns: list[str], output: Path) -> list[Path]:
    """Extract case-insensitive patterns, rejecting unmatched selections up front."""
    lumps = read_wad(data)
    selected = set()
    for pattern in patterns:
        matches = [name for name in lumps if fnmatch.fnmatchcase(name, pattern.upper())]
        if not matches:
            raise ValueError(f"no WAD lumps match {pattern!r}")
        selected.update(matches)
    output.mkdir(parents=True, exist_ok=True)
    paths = []
    for name in sorted(selected):
        suffix = ".mus" if name.startswith("D_") else ".lmp"
        path = output / (name + suffix)
        path.write_bytes(lumps[name])
        paths.append(path)
    return paths


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("names", nargs="+", help="lump names or quoted glob patterns")
    parser.add_argument("-o", "--output", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        paths = extract_lumps(args.input.read_bytes(), args.names, args.output)
    except (OSError, ValueError) as exc:
        parser.exit(1, f"wadextract: {exc}\n")
    for path in paths:
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
