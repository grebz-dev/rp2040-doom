#!/usr/bin/env python3
"""Check that an RP2350 Doom ELF leaves usable heap beyond its zone allocation."""

from __future__ import annotations

import argparse
import re
import subprocess


def symbol_value(elf: str, name: str) -> int:
    symbols = subprocess.check_output(["readelf", "-sW", elf], text=True)
    match = re.search(rf"^\s*\d+:\s*([0-9a-fA-F]+)\s+\d+\s+\S+\s+\S+\s+\S+\s+\S+\s+{re.escape(name)}$",
                      symbols, re.MULTILINE)
    if match is None:
        raise ValueError(f"missing ELF symbol {name}")
    return int(match.group(1), 16)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("elf")
    parser.add_argument("--zone-kb", type=int, required=True)
    parser.add_argument("--minimum-free-kb", type=int, default=24)
    args = parser.parse_args()
    bss_end = symbol_value(args.elf, "__bss_end__")
    heap_limit = symbol_value(args.elf, "__HeapLimit")
    available = heap_limit - bss_end
    remaining = available - args.zone_kb * 1024
    print(f"RAM heap={available} zone={args.zone_kb * 1024} "
          f"post-zone={remaining} minimum={args.minimum_free_kb * 1024} bytes")
    if remaining < args.minimum_free_kb * 1024:
        raise SystemExit("insufficient RP2350 post-zone heap margin")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
