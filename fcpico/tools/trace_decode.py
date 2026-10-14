#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-3-Clause
"""Decode packed FC PICO PPU-strobe trace dumps."""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path

HEADER_RE = re.compile(r"^TRACE period_ns=(\d+) samples=(\d+) words=(\d+)$")
NTSC_SCANLINE_NS = 63_556


@dataclass(frozen=True)
class TraceReport:
    """Measurements extracted from a trace.

    Parameters
    ----------
    period_ns
        Sampling interval in nanoseconds.
    qualifying_reads
        Number of read falling edges with active-low CS1 asserted.
    line_counts
        Qualifying reads in each inferred visible scanline.
    prerender_reads
        Qualifying reads in the complete pre-render line, when the capture is
        frame-anchored; otherwise ``None``.
    rd_low_width_histogram
        Mapping from pulse width in samples to occurrence count.
    vblank_gap_samples
        Largest interval between qualifying reads.
    write_bursts
        Lengths in falling edges of contiguous active-low write bursts.
    """

    period_ns: int
    qualifying_reads: int
    line_counts: list[int]
    prerender_reads: int | None
    rd_low_width_histogram: dict[int, int]
    vblank_gap_samples: int
    write_bursts: list[int]


def pack_samples(samples: list[int]) -> list[int]:
    """Pack 5-bit samples in the format produced by the PIO sampler.

    Parameters
    ----------
    samples
        Sample values, each in the range 0 through 31.

    Returns
    -------
    list[int]
        Thirty-bit words containing six samples each.
    """
    words = []
    for start in range(0, len(samples), 6):
        word = 0
        chunk = samples[start : start + 6]
        for offset, sample in enumerate(chunk):
            word |= (sample & 0x1F) << (offset * 5)
        words.append(word)
    return words


def unpack_words(words: list[int], sample_count: int) -> list[int]:
    """Unpack low-to-high 5-bit samples from 30-bit DMA words."""
    samples = []
    for word in words:
        samples.extend((word >> (offset * 5)) & 0x1F for offset in range(6))
    return samples[:sample_count]


def parse_dump(text: str) -> tuple[int, list[int]]:
    """Parse a serial trace dump and return its period and samples."""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        raise ValueError("empty trace dump")
    match = HEADER_RE.match(lines[0])
    if match is None:
        raise ValueError("missing or malformed TRACE header")
    period_ns, sample_count, word_count = map(int, match.groups())
    tokens = []
    found_end = False
    for line in lines[1:]:
        if line == "END TRACE":
            found_end = True
            break
        tokens.extend(line.split())
    if not found_end:
        raise ValueError("missing END TRACE marker")
    if len(tokens) != word_count:
        raise ValueError(f"header declares {word_count} words, found {len(tokens)}")
    try:
        words = [int(token, 16) for token in tokens]
    except ValueError as error:
        raise ValueError("trace contains a non-hexadecimal word") from error
    if sample_count > word_count * 6:
        raise ValueError("sample count exceeds packed word capacity")
    return period_ns, unpack_words(words, sample_count)


def _falling_edges(samples: list[int], bit: int) -> list[int]:
    return [index for index in range(1, len(samples))
            if samples[index - 1] & bit and not samples[index] & bit]


def _visible_line_counts(
    samples: list[int], period_ns: int, rd_edges: list[int], qualifying: list[int]
) -> tuple[int, list[int]] | None:
    """Count reads in complete visible lines of a vblank-anchored NTSC capture.

    The firmware begins a trace immediately after its frame heartbeat, so a
    hardware capture starts in vblank. The first /RD activity is the pre-render
    line; equal-duration windows after it are visible scanlines. Returning
    ``None`` leaves short synthetic and non-frame-anchored captures to the
    interval-based fallback.
    """
    if not rd_edges:
        return None

    anchor_ns = rd_edges[0] * period_ns
    capture_ns = len(samples) * period_ns
    if anchor_ns < NTSC_SCANLINE_NS * 2:
        return None

    complete_lines = (capture_ns - anchor_ns) // NTSC_SCANLINE_NS
    if complete_lines < 2:
        return None

    counts = [0] * complete_lines
    for edge in qualifying:
        line = (edge * period_ns - anchor_ns) // NTSC_SCANLINE_NS
        if 0 <= line < complete_lines:
            counts[line] += 1

    # Line zero is the pre-render line, not part of the visible result.
    return counts[0], counts[1:]


def _interval_line_counts(qualifying: list[int]) -> list[int]:
    """Infer read groups from gaps for compact synthetic captures."""
    intervals = [right - left for left, right in zip(qualifying, qualifying[1:])]
    nonzero = sorted(interval for interval in intervals if interval > 0)
    median = nonzero[len(nonzero) // 2] if nonzero else 0
    line_threshold = median * 2
    line_counts = []
    if qualifying:
        count = 1
        for interval in intervals:
            if line_threshold and interval > line_threshold:
                line_counts.append(count)
                count = 1
            else:
                count += 1
        line_counts.append(count)
    return line_counts


def decode_samples(samples: list[int], period_ns: int) -> TraceReport:
    """Decode strobe measurements from 5-bit samples.

    GP17 through GP21 map to bits 0 through 4. CS1, RD, and WR are all
    active low. Frame-anchored NTSC hardware captures use the known scanline
    duration and omit the pre-render and incomplete tail lines. Short synthetic
    captures fall back to gap-based grouping. The largest interval between
    qualifying reads is reported as the vblank gap.
    """
    rd_edges = _falling_edges(samples, 1 << 3)
    qualifying = [index for index in rd_edges if not samples[index] & 1]

    widths = []
    for edge in rd_edges:
        end = edge
        while end < len(samples) and not samples[end] & (1 << 3):
            end += 1
        widths.append(end - edge)

    intervals = [right - left for left, right in zip(qualifying, qualifying[1:])]
    frame_counts = _visible_line_counts(samples, period_ns, rd_edges, qualifying)
    if frame_counts is None:
        prerender_reads = None
        line_counts = _interval_line_counts(qualifying)
    else:
        prerender_reads, line_counts = frame_counts

    wr_edges = _falling_edges(samples, 1 << 4)
    write_bursts = []
    if wr_edges:
        burst = 1
        typical = 0
        wr_intervals = [right - left for left, right in zip(wr_edges, wr_edges[1:])]
        if wr_intervals:
            ordered = sorted(wr_intervals)
            typical = ordered[len(ordered) // 2]
        for interval in wr_intervals:
            if typical and interval > typical * 2:
                write_bursts.append(burst)
                burst = 1
            else:
                burst += 1
        write_bursts.append(burst)

    return TraceReport(
        period_ns=period_ns,
        qualifying_reads=len(qualifying),
        line_counts=line_counts,
        prerender_reads=prerender_reads,
        rd_low_width_histogram=dict(sorted(Counter(widths).items())),
        vblank_gap_samples=max(intervals, default=0),
        write_bursts=write_bursts,
    )


def decode_dump(text: str) -> TraceReport:
    """Parse and decode one complete serial trace dump."""
    period_ns, samples = parse_dump(text)
    return decode_samples(samples, period_ns)


def main() -> int:
    """Run the command-line trace decoder."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dump", type=Path, help="serial trace dump")
    parser.add_argument("--json", action="store_true", help="emit JSON")
    args = parser.parse_args()
    report = decode_dump(args.dump.read_text(encoding="ascii"))
    if args.json:
        print(json.dumps(asdict(report), indent=2))
    else:
        print(f"sample period: {report.period_ns} ns")
        print(f"qualifying reads: {report.qualifying_reads}")
        if report.prerender_reads is not None:
            print(f"pre-render reads: {report.prerender_reads}")
        print("per-line reads: " + " ".join(map(str, report.line_counts)))
        print("/RD low widths: " + " ".join(
            f"{width}:{count}" for width, count in report.rd_low_width_histogram.items()))
        print(f"vblank gap: {report.vblank_gap_samples} samples "
              f"({report.vblank_gap_samples * report.period_ns} ns)")
        print("write bursts: " + " ".join(map(str, report.write_bursts)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
