#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-3-Clause
"""sfx2dpcm.py -- encode a Doom sound effect to a DMC (DPCM) byte stream.

    sfx2dpcm.py <lump.lmp|wav> --rate 9 -o out.dmc

Reads either a raw Doom sound lump (the DMX PC-speaker/8-bit format used in
the WAD: ``u16`` format number (3), ``u16`` sample rate, ``u32`` length,
then that many unsigned 8-bit samples -- some lumps carry 16 padding bytes
at the start and end of the sample data, which is why the samples are taken
as exactly ``data[8:8 + length]`` rather than "the rest of the file") or a
``.wav`` file (read with the stdlib ``wave`` module; 8-bit WAV is used as
unsigned PCM directly, 16-bit signed WAV is downmixed to unsigned 8-bit,
and multi-channel WAV is downmixed to mono by averaging channels), encodes
it with :mod:`dpcm` at the chosen ``$4010`` rate index, writes the result,
and prints the round-trip RMS error and the output size.

Any input whose extension is not ``.wav``/``.wave`` (case-insensitive) is
read as a Doom sound lump, so ``.lmp`` and extension-less lump dumps both
work.
"""

from __future__ import annotations

import argparse
import struct
import sys
import wave
from pathlib import Path

import numpy as np

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from dpcm import RATE_TABLE_HZ, decode_dpcm, encode_dpcm, rms_error, to_target_levels
else:
    from .dpcm import RATE_TABLE_HZ, decode_dpcm, encode_dpcm, rms_error, to_target_levels

_LUMP_HEADER = struct.Struct("<HHI")  # format number, sample rate, length
_LUMP_FORMAT_NUMBER = 3


def read_doom_lump(path) -> tuple[np.ndarray, int]:
    """Read a Doom sound lump: returns ``(samples_u8, sample_rate)``."""
    data = Path(path).read_bytes()
    if len(data) < _LUMP_HEADER.size:
        raise ValueError(
            f"{path}: {len(data)} bytes, shorter than the "
            f"{_LUMP_HEADER.size}-byte sound-lump header"
        )
    fmt_number, rate, length = _LUMP_HEADER.unpack_from(data, 0)
    if fmt_number != _LUMP_FORMAT_NUMBER:
        raise ValueError(
            f"{path}: sound-lump format number {fmt_number}, expected "
            f"{_LUMP_FORMAT_NUMBER}"
        )
    samples = data[_LUMP_HEADER.size : _LUMP_HEADER.size + length]
    if len(samples) != length:
        raise ValueError(
            f"{path}: header declares {length} samples but only "
            f"{len(samples)} bytes follow"
        )
    return np.frombuffer(samples, dtype=np.uint8), rate


def read_wav(path) -> tuple[np.ndarray, int]:
    """Read a WAV file: returns ``(samples_u8, sample_rate)``.

    8-bit WAV is unsigned PCM already and is used as-is. 16-bit WAV is
    signed PCM and is rescaled to unsigned 8-bit. Either is downmixed to
    mono by averaging channels if the file is not already mono.
    """
    with wave.open(str(path), "rb") as w:
        n_channels = w.getnchannels()
        samp_width = w.getsampwidth()
        rate = w.getframerate()
        raw = w.readframes(w.getnframes())

    if samp_width == 1:
        samples = np.frombuffer(raw, dtype=np.uint8).astype(np.float64)
    elif samp_width == 2:
        signed = np.frombuffer(raw, dtype="<i2").astype(np.float64)
        samples = signed / 256.0 + 128.0
    else:
        raise ValueError(
            f"{path}: unsupported WAV sample width {samp_width * 8} bits "
            "(need 8 or 16)"
        )

    if n_channels > 1:
        samples = samples.reshape(-1, n_channels).mean(axis=1)

    samples_u8 = np.clip(np.round(samples), 0, 255).astype(np.uint8)
    return samples_u8, rate


def load_samples(path) -> tuple[np.ndarray, int]:
    """Dispatch on extension: ``.wav``/``.wave`` -> :func:`read_wav`, else
    :func:`read_doom_lump`."""
    if Path(path).suffix.lower() in (".wav", ".wave"):
        return read_wav(path)
    return read_doom_lump(path)


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Encode a Doom sound lump or WAV file to a DMC (DPCM) byte stream."
    )
    parser.add_argument("input", help="Doom sound lump (.lmp) or .wav file")
    parser.add_argument(
        "--rate",
        type=int,
        default=9,
        metavar="0..15",
        help="$4010 DMC rate index (default 9, ~11.2 kHz)",
    )
    parser.add_argument(
        "-o",
        "--output",
        help="output .dmc path (default: the input path with a .dmc suffix)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    in_path = Path(args.input)

    if not (0 <= args.rate < len(RATE_TABLE_HZ)):
        print(
            f"error: --rate {args.rate} out of range 0..{len(RATE_TABLE_HZ) - 1}",
            file=sys.stderr,
        )
        return 1

    samples, src_rate = load_samples(in_path)
    dst_rate = RATE_TABLE_HZ[args.rate]
    dmc = encode_dpcm(samples, src_rate, args.rate)

    target = to_target_levels(samples, src_rate, dst_rate)
    decoded = decode_dpcm(dmc)
    error = rms_error(target, decoded)

    out_path = Path(args.output) if args.output else in_path.with_suffix(".dmc")
    out_path.write_bytes(dmc)

    print(
        f"{in_path.name}: {len(samples)} samples @ {src_rate} Hz -> "
        f"{len(dmc)} bytes @ {dst_rate:.2f} Hz (rate index {args.rate})"
    )
    print(f"RMS error: {error:.3f} (7-bit levels, 0..127)")
    print(f"wrote {out_path} ({len(dmc)} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
