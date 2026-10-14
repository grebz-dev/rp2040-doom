#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-3-Clause
"""dpcm_pack.py -- lay out .dmc files into the boot ROM's DPCM bank.

    dpcm_pack.py a.dmc b.dmc --rate 9 --outdir gen/
    dpcm_pack.py --manifest samples.json --outdir gen/

Samples are placed back-to-back starting at $C000, each 64-byte aligned
(bank layout: DPCM samples live in
$C000-$ECFF); packing fails loudly if a sample would end at or past
$ED00, the fix bank's NMI-vector boundary -- an 11,520-byte budget.

Writes, into --outdir:

* dpcm_bank.bin  -- sample bytes from offset 0 ($C000), with the 0-63
  byte gaps introduced by 64-byte alignment zero-filled, sized to the end
  of the last sample. It is *not* padded out to the full 11,520-byte
  budget -- whatever assembles it into the ROM bank is expected to pad
  the remainder itself (e.g. nesasm's own end-of-bank fill).
* dpcm_table.inc -- nesasm EQUs: DPCM_<NAME>_ADDR/_LEN/_RATE per sample,
  in ``($4012, $4013, $4010-rate)`` byte form.
* dpcm_table.h   -- the same three values as C #defines, plus a
  ``const dpcm_sample_t dpcm_table[]`` lookup array.

Manifest JSON is a list of ``{"name": ..., "file": ..., "rate": ...}``
objects; "file" is resolved relative to the manifest's own directory.
Without --manifest, positional arguments are .dmc file paths; each
sample's name is then its filename stem (uppercased) and --rate applies
to all of them uniformly.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

BASE_ADDR = 0xC000
LIMIT_ADDR = 0xED00
BUDGET = LIMIT_ADDR - BASE_ADDR  # 11520 bytes
ALIGN = 64

_IDENT_BAD_CHARS = re.compile(r"[^A-Z0-9_]")


class DpcmBudgetError(ValueError):
    """A sample's placement would end at or past LIMIT_ADDR ($ED00)."""


@dataclass(frozen=True)
class DpcmSample:
    name: str
    data: bytes
    rate: int


@dataclass(frozen=True)
class PackedSample:
    name: str
    offset: int  # relative to BASE_ADDR, 64-byte aligned
    length: int  # byte length of the raw sample data
    rate: int

    @property
    def addr(self) -> int:
        """Absolute CPU address of the sample's first byte."""
        return BASE_ADDR + self.offset

    @property
    def addr_field(self) -> int:
        """The $4012 sample-address byte: (addr - $C000) >> 6."""
        return self.offset >> 6

    @property
    def len_field(self) -> int:
        """The $4013 sample-length byte: (len - 1) >> 4."""
        return (self.length - 1) >> 4


def sanitize_name(name: str) -> str:
    """Upper-case ``name`` and replace anything not [A-Z0-9_] with '_', so
    it is safe to splice into both a nesasm symbol and a C identifier."""
    return _IDENT_BAD_CHARS.sub("_", name.upper())


def layout_samples(samples: list[DpcmSample]) -> list[PackedSample]:
    """Place ``samples`` back-to-back from $C000, 64-byte aligned, in order.

    Raises :class:`DpcmBudgetError` naming the offending sample if placing
    it would end at or past ``LIMIT_ADDR`` ($ED00). Raises ``ValueError``
    if a sample's rate is outside 0..15 or its length is not of the DMC
    ``16n + 1`` form ``encode_dpcm`` produces (required so ``len_field``,
    ``(len - 1) >> 4``, round-trips through ``$4013`` losslessly).
    """
    packed = []
    offset = 0
    for sample in samples:
        if not (0 <= sample.rate <= 15):
            raise ValueError(f"{sample.name}: rate {sample.rate} out of range 0..15")
        if len(sample.data) == 0 or (len(sample.data) - 1) % 16 != 0:
            raise ValueError(
                f"{sample.name}: length {len(sample.data)} is not of the "
                "APU DMC form 16n + 1"
            )
        aligned = (offset + ALIGN - 1) // ALIGN * ALIGN
        end = aligned + len(sample.data)
        if end > BUDGET:
            raise DpcmBudgetError(
                f"{sample.name}: placing {len(sample.data)} bytes at "
                f"${BASE_ADDR + aligned:04X} would end at "
                f"${BASE_ADDR + end:04X}, past the ${LIMIT_ADDR:04X} limit "
                f"({end - BUDGET} byte(s) over the {BUDGET}-byte budget)"
            )
        packed.append(PackedSample(sample.name, aligned, len(sample.data), sample.rate))
        offset = end
    return packed


def build_bank(samples: list[DpcmSample], packed: list[PackedSample]) -> bytes:
    """The DPCM bank image: each sample's bytes at its packed offset,
    64-byte-alignment gaps zero-filled, sized to the end of the last
    sample (``samples`` and ``packed`` must correspond index-for-index,
    as returned by :func:`layout_samples` for the same ``samples`` list)."""
    total = packed[-1].offset + packed[-1].length if packed else 0
    bank = bytearray(total)
    for sample, place in zip(samples, packed):
        bank[place.offset : place.offset + place.length] = sample.data
    return bytes(bank)


def render_inc(packed: list[PackedSample]) -> str:
    """nesasm EQUs for each sample's $4012/$4013/rate byte."""
    lines = []
    for p in packed:
        lines.append(f"DPCM_{p.name}_ADDR EQU ${p.addr_field:02X}")
        lines.append(f"DPCM_{p.name}_LEN EQU ${p.len_field:02X}")
        lines.append(f"DPCM_{p.name}_RATE EQU ${p.rate:X}")
    return "\n".join(lines) + ("\n" if lines else "")


def render_header(packed: list[PackedSample]) -> str:
    """C #defines plus a ``const dpcm_sample_t dpcm_table[]`` array."""
    lines = [
        "/* SPDX-License-Identifier: BSD-3-Clause */",
        "#ifndef DPCM_TABLE_H",
        "#define DPCM_TABLE_H",
        "",
        "#include <stdint.h>",
        "",
    ]
    for p in packed:
        lines.append(f"#define DPCM_{p.name}_ADDR 0x{p.addr_field:02X}")
        lines.append(f"#define DPCM_{p.name}_LEN 0x{p.len_field:02X}")
        lines.append(f"#define DPCM_{p.name}_RATE 0x{p.rate:X}")
    lines += [
        "",
        "typedef struct {",
        "    uint8_t addr;",
        "    uint8_t len;",
        "    uint8_t rate;",
        "} dpcm_sample_t;",
        "",
        "static const dpcm_sample_t dpcm_table[] = {",
    ]
    for p in packed:
        lines.append(
            f"    {{ DPCM_{p.name}_ADDR, DPCM_{p.name}_LEN, DPCM_{p.name}_RATE }}, "
            f"/* {p.name} */"
        )
    lines += [
        "};",
        "",
        f"#define DPCM_TABLE_COUNT {len(packed)}",
        "",
        "#endif",
        "",
    ]
    return "\n".join(lines)


def load_manifest(manifest_path: Path) -> list[DpcmSample]:
    entries = json.loads(manifest_path.read_text())
    base_dir = manifest_path.parent
    samples = []
    for entry in entries:
        file_path = (base_dir / entry["file"]).resolve()
        samples.append(
            DpcmSample(
                name=sanitize_name(entry["name"]),
                data=file_path.read_bytes(),
                rate=int(entry["rate"]),
            )
        )
    return samples


def load_positional(paths: list[str], rate: int) -> list[DpcmSample]:
    samples = []
    for raw_path in paths:
        p = Path(raw_path)
        samples.append(
            DpcmSample(name=sanitize_name(p.stem), data=p.read_bytes(), rate=rate)
        )
    return samples


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Lay out .dmc samples into the boot ROM's DPCM bank."
    )
    parser.add_argument(
        "files", nargs="*", help=".dmc files (ignored if --manifest is given)"
    )
    parser.add_argument("--manifest", help="JSON manifest: [{name, file, rate}, ...]")
    parser.add_argument(
        "--rate",
        type=int,
        default=9,
        help="rate index applied to all positional files (default 9)",
    )
    parser.add_argument(
        "--outdir", default=".", help="directory to write the bank and tables into"
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)

    if args.manifest:
        samples = load_manifest(Path(args.manifest))
    elif args.files:
        samples = load_positional(args.files, args.rate)
    else:
        print("error: give .dmc files or --manifest", file=sys.stderr)
        return 1

    try:
        packed = layout_samples(samples)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    (outdir / "dpcm_bank.bin").write_bytes(build_bank(samples, packed))
    (outdir / "dpcm_table.inc").write_text(render_inc(packed))
    (outdir / "dpcm_table.h").write_text(render_header(packed))

    total = packed[-1].offset + packed[-1].length if packed else 0
    print(f"packed {len(packed)} sample(s), {total}/{BUDGET} bytes of the DPCM budget used")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
