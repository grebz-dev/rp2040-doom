#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-3-Clause
"""Join decoded capture fields with the firmware's per-picture serial trace.

    analyze.py fields.csv session.log [-o report.json]

Answers, per picture the console actually showed: how many fields it stayed
on screen, and what the firmware recorded for the picture that replaced it
(world wait and timeout, packets and new tiles, drops, palette changes). Also
counts pictures the firmware published but the capture never showed, and
pictures shown out of order (an older picture coming back).
"""
from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
import json
from pathlib import Path
import statistics

try:
    from . import barcode, trace_log
except ImportError:  # run as a script
    import barcode  # type: ignore
    import trace_log  # type: ignore

NES_FIELD_HZ = 60.0988
ID_MOD = 1 << barcode.ID_BITS
TIC_MOD = 1 << barcode.TIC_BITS
GEN_MOD = 1 << barcode.GEN_BITS


@dataclass
class Showing:
    id: int          # unwrapped picture ID
    first_field: int
    fields: int      # consecutive fields on screen, undecodable fields included
    tic: int
    generation: int
    timeout: bool


def load_fields(path: Path) -> list[barcode.Code | None]:
    rows = []
    with path.open() as handle:
        for row in csv.DictReader(handle):
            rows.append(None if row["id"] == "" else barcode.Code(
                int(row["id"]), int(row["generation"]), bool(int(row["timeout"])), int(row["tic"])))
    return rows


def showings(fields: list[barcode.Code | None], first_id: int | None = None) -> list[Showing]:
    """Runs of fields showing one picture, with IDs unwrapped past 4096.
    An undecodable field extends the run it interrupts when the same picture
    is on both sides; otherwise it is left out."""
    runs: list[Showing] = []
    previous = first_id
    pending_gap = 0
    for index, code in enumerate(fields):
        if code is None:
            pending_gap += 1
            continue
        if previous is None:
            full = code.id
        else:
            delta = (code.id - previous) % ID_MOD
            full = previous + delta if delta < ID_MOD // 2 else previous - (ID_MOD - delta)
        if runs and runs[-1].id == full:
            runs[-1].fields += 1 + pending_gap
        else:
            runs.append(Showing(full, index, 1, code.tic, code.generation, code.timeout))
        pending_gap = 0
        previous = full
    return runs


def align(runs: list[Showing], traces: list[trace_log.Trace]) -> int:
    """Offset (a multiple of 4096) mapping capture IDs to trace IDs, chosen by
    the most tic and generation agreements."""
    by_id = {t.id: t for t in traces}
    best, best_votes = 0, -1
    lo, hi = min(by_id, default=0), max(by_id, default=0)
    for wrap in range((lo - ID_MOD) // ID_MOD, hi // ID_MOD + 2):
        offset = wrap * ID_MOD
        votes = sum(1 for r in runs if (t := by_id.get(r.id + offset)) is not None
                    and t.lt % TIC_MOD == r.tic and t.gen % GEN_MOD == r.generation)
        if votes > best_votes:
            best, best_votes = offset, votes
    return best


def analyze(fields: list[barcode.Code | None], traces: list[trace_log.Trace],
            hitch_fields: int = 4) -> dict:
    runs = showings(fields)
    offset = align(runs, traces)
    for run in runs:
        run.id += offset
    by_id = {t.id: t for t in traces}
    shown_ids = [r.id for r in runs]
    backwards = [(a.id, b.id) for a, b in zip(runs, runs[1:]) if b.id < a.id]
    mismatched = [r.id for r in runs if (t := by_id.get(r.id)) is not None
                  and (t.lt % TIC_MOD != r.tic or t.gen % GEN_MOD != r.generation
                       or bool(t.tmo) != r.timeout)]
    untraced = [r.id for r in runs if r.id not in by_id]
    span = range(min(shown_ids), max(shown_ids) + 1) if shown_ids else range(0)
    unseen = [i for i in span if i in by_id and i not in set(shown_ids)]

    held = [r.fields for r in runs[1:-1]]  # first and last runs are cut by the capture
    histogram: dict[int, int] = {}
    for count in held:
        histogram[count] = histogram.get(count, 0) + 1

    def replacement(index):
        """Trace of the picture that ended showing ``index``: why it came late."""
        return by_id.get(runs[index + 1].id) if index + 1 < len(runs) else None

    hitches, normal = [], []
    for index, run in enumerate(runs[1:-1], start=1):
        trace = replacement(index)
        if trace is None:
            continue
        (hitches if run.fields >= hitch_fields else normal).append((run, trace))

    def share(items, test):
        return round(sum(1 for _, t in items if test(t)) / len(items), 3) if items else None

    def mean(items, key):
        return round(statistics.fmean(getattr(t, key) for _, t in items), 1) if items else None

    keys = [(t.id, t.key) for a, t in zip(traces, traces[1:]) if t.key != a.key]
    decoded = sum(code is not None for code in fields)
    return {
        "fields": len(fields),
        "decoded_fields": decoded,
        "pictures_shown": len(runs),
        "id_offset": offset,
        "effective_fps": round(NES_FIELD_HZ / statistics.fmean(held), 2) if held else None,
        "fields_per_picture": dict(sorted(histogram.items())),
        "hitch_fields": hitch_fields,
        "hitches": len(hitches),
        "hitch_causes": {
            "timeout": share(hitches, lambda t: t.tmo),
            "waited": share(hitches, lambda t: t.wait > 0),
            "dropped_before": share(hitches, lambda t: t.id in by_id and by_id.get(t.id - 1) is not None
                                    and t.drops > by_id[t.id - 1].drops),
            "palette_key_change": share(hitches, lambda t: by_id.get(t.id - 1) is not None
                                        and t.key != by_id[t.id - 1].key),
        },
        "replacement_means": {
            "hitch": {k: mean(hitches, k) for k in ("wait", "conv", "pkt", "tiles", "oam")},
            "normal": {k: mean(normal, k) for k in ("wait", "conv", "pkt", "tiles", "oam")},
        },
        "hitch_pictures": [{"id": r.id, "fields": r.fields, "first_field": r.first_field,
                            "next": t.fields} for r, t in hitches[:200]],
        "unseen_pictures": len(unseen),
        "unseen_sample": unseen[:50],
        "shown_out_of_order": backwards[:50],
        "barcode_trace_mismatches": mismatched[:50],
        "untraced_pictures": untraced[:50],
        "palette_key_changes": keys,
        "trace_timeouts": sum(t.tmo for t in traces),
        "trace_abandoned": sum(t.ab for t in traces),
    }


def summary(report: dict) -> str:
    lines = [
        f"fields {report['fields']}, decoded {report['decoded_fields']}, "
        f"pictures shown {report['pictures_shown']}, effective {report['effective_fps']} fps",
        f"fields per picture: {report['fields_per_picture']}",
        f"hitches (>= {report['hitch_fields']} fields): {report['hitches']}, "
        f"causes {report['hitch_causes']}",
        f"replacement means: {report['replacement_means']}",
        f"unseen pictures {report['unseen_pictures']}, out of order "
        f"{len(report['shown_out_of_order'])}, barcode/trace mismatches "
        f"{len(report['barcode_trace_mismatches'])}, untraced {len(report['untraced_pictures'])}",
        f"palette key changes: {report['palette_key_changes']}",
    ]
    return "\n".join(lines)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("fields", type=Path, help="decode_video.py CSV")
    parser.add_argument("log", type=Path, help="trace_log.py record output")
    parser.add_argument("--hitch-fields", type=int, default=4,
                        help="a picture held this many fields or more is a hitch (default 4)")
    parser.add_argument("-o", "--output", type=Path, help="report JSON")
    args = parser.parse_args(argv)
    report = analyze(load_fields(args.fields), trace_log.parse(args.log).traces, args.hitch_fields)
    if args.output:
        args.output.write_text(json.dumps(report, indent=1) + "\n")
    print(summary(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
