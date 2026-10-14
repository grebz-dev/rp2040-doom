#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-3-Clause
"""bin2c.py -- pure-Python replacement for Bin2C.exe.

    bin2c.py <in.bin> <out.c> <symbol>

Produces a 4-byte-aligned C byte array and its length, e.g. for
``bin2c.py res.bin resdata.c _resdata``::

    const unsigned char _resdata[48340] __attribute__((aligned(4))) = {
    	0x20, 0x00, 0x00, ...
    	...
    };

    const int _resdata_length = 48340;

Byte-for-byte reproduction of ``tutorial_project/tuto1_hw/res/resdata.c``
(the real Bin2C.exe output for that same ``res.bin``) fixed the exact
layout: 11 hex bytes per line, each formatted ``0x%02x``, tab-indented,
comma-and-space separated, with a trailing ", " after every line except
the last (so the very last byte in the array has no trailing comma), and
CRLF line endings throughout, including one blank line between the closing
``};`` and the ``_length`` line.

Line-ending note: ``sample_game/res/resdata.c`` -- otherwise byte-for-byte
the same layout (11 values/line, same separators, same no-trailing-comma
rule on the last line) -- is checked into this tree with bare LF line
endings instead of CRLF. Since both are claimed to be genuine Bin2C.exe
output, and Bin2C.exe is a native Windows tool, this is almost certainly
line-ending normalisation from a Unix checkout/`git` config rather than a
second real behaviour of the tool; ``tuto1_hw/res/resdata.c`` (CRLF) is
treated here as authoritative, since it is the one explicitly named for
this tool to match.
"""

from __future__ import annotations

import sys
from pathlib import Path

VALUES_PER_LINE = 11


def bin2c_text(data: bytes, symbol: str) -> str:
    n = len(data)
    hex_values = [f"0x{b:02x}" for b in data]

    lines = []
    for i in range(0, len(hex_values), VALUES_PER_LINE):
        group = hex_values[i : i + VALUES_PER_LINE]
        line = "\t" + ", ".join(group)
        if i + VALUES_PER_LINE < len(hex_values):
            line += ", "
        lines.append(line)

    pieces = [f"const unsigned char {symbol}[{n}] __attribute__((aligned(4))) = {{"]
    pieces.extend(lines)
    pieces.append("};")
    pieces.append("")
    pieces.append(f"const int {symbol}_length = {n};")
    return "\r\n".join(pieces) + "\r\n"


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 3:
        print("usage: bin2c.py <in.bin> <out.c> <symbol>", file=sys.stderr)
        return 1
    in_path, out_path, symbol = argv

    data = Path(in_path).read_bytes()
    text = bin2c_text(data, symbol)
    Path(out_path).write_text(text, newline="")

    print(f"wrote {out_path} ({len(data)} bytes as {symbol})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
