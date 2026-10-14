#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-3-Clause
"""Approximate a Doom DMX lump or WAV as a pulse-2/noise APUS effect.

    python3 fcpico/tools/audio/sfx2apu.py dspistol.lmp --auto --kind noise -o pistol.apus
    python3 fcpico/tools/audio/sfx2apu.py item.sfx --kind pulse -o item.apus

Hand-authored .sfx: one frame per non-comment line, hexadecimal reg=value
pairs (e.g. '04=bf 05=08 06=fd 07=08'); '-' is a silent frame. '#' comments
are allowed. Registers are restricted to the chosen voice. Effects do not
loop or touch $4015, so the sequencer can arbitrate channel ownership.
"""

from __future__ import annotations

import argparse
import wave
from pathlib import Path

import numpy as np

if __package__ in (None, ""):
    from apus import thin_register_writes, write_apus
    from sfx2dpcm import load_samples
else:
    from .apus import thin_register_writes, write_apus
    from .sfx2dpcm import load_samples

_REGISTERS = {"pulse": {4, 5, 6, 7}, "noise": {12, 14, 15}}
_NOISE_PERIODS = (4, 8, 16, 32, 64, 96, 128, 160, 202, 254, 380, 508, 762, 1016, 2034, 4068)
_CPU_NTSC = 1789773


def script_from_text(text: str, kind: str) -> bytes:
    """Preserve explicit frame writes; append a muted frame on termination."""
    if kind not in _REGISTERS:
        raise ValueError("effect kind must be pulse or noise")
    allowed = _REGISTERS[kind]
    frames = []
    for line_number, line in enumerate(text.splitlines(), 1):
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        frame = []
        if line != "-":
            seen = set()
            for token in line.split():
                try:
                    register, value = token.split("=")
                    register, value = int(register, 16), int(value, 16)
                except ValueError as exc:
                    raise ValueError(f"line {line_number}: expected hexadecimal reg=value") from exc
                if register not in allowed or not 0 <= value <= 255:
                    raise ValueError(f"line {line_number}: register/value outside {kind} voice")
                if register in seen:
                    raise ValueError(f"line {line_number}: duplicate register")
                seen.add(register)
                frame.append((register, value))
        frames.append(frame)
    if not frames:
        raise ValueError("effect script has no frames")
    frames.append([(4 if kind == "pulse" else 12, 0x30)])
    return write_apus(frames, loop_frame=None)


def _pitch(samples: np.ndarray, rate: int) -> float:
    """First strong autocorrelation peak; a low-quality fallback is 220 Hz."""
    samples = samples - samples.mean()
    correlation = np.correlate(samples, samples, mode="full")[len(samples) - 1 :]
    minimum = max(1, rate // 2000)
    maximum = min(len(samples) - 2, rate // 50)
    if maximum <= minimum or correlation[0] <= 0:
        return 220.0
    candidates = [lag for lag in range(minimum, maximum + 1)
                  if correlation[lag] >= correlation[lag - 1]
                  and correlation[lag] > correlation[lag + 1]]
    if not candidates:
        return 220.0
    best = max(correlation[lag] for lag in candidates)
    if best <= 0:
        return 220.0
    threshold = best * 0.8
    lag = next(lag for lag in candidates if correlation[lag] >= threshold)
    return rate / lag


def synthesize(samples, rate: int, kind: str) -> bytes:
    """Track per-frame RMS envelope and pulse pitch or noise brightness."""
    if kind not in _REGISTERS:
        raise ValueError("effect kind must be pulse or noise")
    samples = np.asarray(samples, dtype=np.float64)
    if samples.ndim != 1 or not len(samples) or rate <= 0:
        raise ValueError("effect needs nonempty mono samples and a positive rate")
    if not np.all(np.isfinite(samples)) or np.any(samples < 0) or np.any(samples > 255):
        raise ValueError("effect samples must be unsigned 8-bit PCM")
    count = (len(samples) * 60 + rate - 1) // rate
    if count > 60 * 60:
        raise ValueError("effect exceeds the one-minute conversion limit")
    centered = samples - 128
    chunks = [centered[index * rate // 60 : min(len(samples), (index + 1) * rate // 60)]
              for index in range(count)]
    envelopes = [float(np.sqrt(np.mean(chunk * chunk))) if len(chunk) else 0 for chunk in chunks]
    maximum = max(1.0, max(envelopes))
    images = []
    for index, (chunk, rms) in enumerate(zip(chunks, envelopes)):
        level = min(15, int(rms / maximum * 15 + 0.5)) if rms >= 1 else 0
        if kind == "pulse":
            # A three-frame analysis window keeps bass periods visible.
            window = centered[max(0, (index - 1) * rate // 60) : min(len(samples), (index + 2) * rate // 60)]
            frequency = _pitch(window, rate)
            period = min(2047, max(8, int(_CPU_NTSC / (16 * frequency) + 0.5) - 1))
            images.append({4: 0xB0 | level, 5: 8, 6: period & 255, 7: 8 | (period >> 8)})
        else:
            crossings = np.count_nonzero(np.diff(chunk >= 0)) if len(chunk) > 1 else 0
            frequency = max(1, crossings * rate / (2 * max(1, len(chunk))))
            period = min(range(16), key=lambda i: abs(_CPU_NTSC / (2 * _NOISE_PERIODS[i]) - frequency))
            images.append({12: 0x30 | level, 14: period, 15: 8})
    images.append({4 if kind == "pulse" else 12: 0x30})
    return write_apus(thin_register_writes(images), loop_frame=None)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("--auto", action="store_true", help="derive envelope and pitch from PCM")
    parser.add_argument("--kind", choices=tuple(_REGISTERS), default="pulse")
    parser.add_argument("-o", "--output", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.input.suffix.lower() == ".sfx":
            result = script_from_text(args.input.read_text(), args.kind)
        else:
            if not args.auto:
                raise ValueError("PCM input needs --auto; use .sfx for hand-authored frames")
            samples, rate = load_samples(args.input)
            if args.input.suffix.lower() not in (".wav", ".wave"):
                # Doom's DMX format includes 16 padding samples at either end.
                if len(samples) <= 32:
                    raise ValueError("DMX effect is too short for its 32 padding samples")
                samples = samples[16:-16]
            result = synthesize(samples, rate, args.kind)
        path = args.output or args.input.with_suffix(".apus")
        path.write_bytes(result)
    except (OSError, ValueError, EOFError, wave.Error) as exc:
        parser.exit(1, f"sfx2apu: {exc}\n")
    print(f"{path}: {len(result)} bytes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
