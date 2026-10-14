from __future__ import annotations

from pathlib import Path

import pytest

from trace_decode import decode_dump, pack_samples, parse_dump


FIXTURES = Path(__file__).parents[1] / "fixtures" / "hw_trace_ntsc"


def synthetic_dump() -> str:
    """Build a two-line trace with known read widths and a vblank gap."""
    idle = 0x1F
    samples = [idle] * 20
    for line in range(2):
        for read in range(4):
            active_read = idle & ~(1 << 3) & ~1
            samples.extend([idle, active_read, active_read, idle])
        if line == 0:
            samples.extend([idle] * 12)
    samples.extend([idle] * 40)
    for _ in range(3):
        samples.extend([idle, idle & ~(1 << 4), idle])
    words = pack_samples(samples)
    body = "\n".join(" ".join(f"{word:08x}" for word in words[start : start + 8])
                     for start in range(0, len(words), 8))
    return (f"TRACE period_ns=40 samples={len(samples)} words={len(words)}\n"
            f"{body}\nEND TRACE\n")


def test_synthetic_round_trip() -> None:
    report = decode_dump(synthetic_dump())
    assert report.period_ns == 40
    assert report.qualifying_reads == 8
    assert report.line_counts == [4, 4]
    assert report.rd_low_width_histogram == {2: 8}
    assert report.vblank_gap_samples == 16
    assert report.write_bursts == [3]


def test_hardware_trace_reports_complete_visible_scanlines() -> None:
    report = decode_dump((FIXTURES / "trace_6.hex").read_text(encoding="ascii"))

    assert report.prerender_reads == 66
    assert len(report.line_counts) >= 12
    assert set(report.line_counts) == {64}


def test_parser_round_trip_preserves_samples() -> None:
    text = synthetic_dump()
    period, samples = parse_dump(text)
    assert period == 40
    assert pack_samples(samples)


@pytest.mark.parametrize(
    "text, message",
    [
        ("", "empty"),
        ("garbage", "TRACE header"),
        ("TRACE period_ns=40 samples=1 words=1\n00000000", "END TRACE"),
        ("TRACE period_ns=40 samples=7 words=1\n00000000\nEND TRACE", "capacity"),
    ],
)
def test_rejects_malformed_dump(text: str, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        parse_dump(text)
