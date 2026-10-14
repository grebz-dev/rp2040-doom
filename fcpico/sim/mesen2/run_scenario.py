#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-3-Clause
"""Run tutorial-ROM S0; diagnostics do not imply calibrated hardware correctness."""

import argparse
import csv
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
STARTUP_FRAMES = 150
sys.path.insert(0, str(REPO / "fcpico/tools"))
from fcpico.protocol import PPU_COUNT_VAL_V1  # noqa: E402
from nes.set_mapper import set_mapper  # noqa: E402


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def steady_heartbeat_rows(writes):
    """Return post-startup v1 heartbeat rows from the mixed mapper trace.

    The trace includes ordinary CPU writes as well as heartbeat writes.  A
    steady v1 heartbeat has one mailbox dummy/read sequence (65 CPU reads)
    and a nonzero rendering-read count; startup transitions can report
    partial or cumulative values and are excluded from the histogram.
    """
    return [
        row for row in writes
        if int(row["ppu_frame"]) > STARTUP_FRAMES
        and int(row["cpu_reads"]) == 65
        and int(row["render_reads"]) > 10000
    ]


def summarize_fetch_trace(rows, frame, cs1_mask):
    """Check Mesen's selected fetch order against the calibrated hardware cadence."""
    expected_lines = range(-1, 240)
    by_line = {line: [] for line in expected_lines}
    for row in rows:
        if int(row["ppu_frame"]) != frame:
            raise ValueError("fetch trace contains another PPU frame")
        line = int(row["scanline"])
        if line not in by_line:
            raise ValueError(f"unexpected rendering scanline {line}")
        by_line[line].append(row)

    total = 0
    open_bus = 0
    sprite_reads = 0
    for line, fetches in by_line.items():
        selected_tiles = (31 if line == -1 else 30) if cs1_mask == "0xf800" else 32
        expected_bg_cycles = [
            cycle for tile in range(selected_tiles)
            for cycle in (8 * tile + 5, 8 * tile + 7)
        ] + [325, 327, 333, 335]
        bg = [r for r in fetches if int(r["cycle"]) <= 255 or int(r["cycle"]) >= 321]
        cycles = [int(r["cycle"]) for r in bg]
        if cycles != expected_bg_cycles:
            raise ValueError(f"scanline {line}: background fetch cycles differ from hardware")
        for low, high in zip(bg[::2], bg[1::2]):
            if int(high["address"]) != int(low["address"]) + 8:
                raise ValueError(f"scanline {line}: low/high pattern fetch address pair differs")
            if cs1_mask == "0xf800" and (int(low["address"]) & 0xF800) != 0x0800:
                raise ValueError(f"scanline {line}: background fetch misses the stream address range")
        sprites = [r for r in fetches if 255 < int(r["cycle"]) < 321]
        expected_sprites = 16 if cs1_mask == "0xe000" else 0
        if len(sprites) != expected_sprites:
            raise ValueError(f"scanline {line}: expected {expected_sprites} sprite fetches, got {len(sprites)}")
        if len(fetches) != selected_tiles * 2 + 4 + expected_sprites:
            raise ValueError(f"scanline {line}: wrong number of selected reads")
        sprite_reads += len(sprites)
        total += len(fetches)
        open_bus += sum(int(r["value"]) == 0xff for r in fetches)
    return {"frame": frame, "scanlines": len(by_line), "selected_reads": total,
            "background_reads": total - sprite_reads,
            "sprite_reads": sprite_reads, "ff_values": open_bus}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("scenario", choices=["S0"])
    parser.add_argument("--diagnostic", action="store_true", help="check the bridge and determinism, not S0 correctness")
    parser.add_argument("--frames", type=int, default=300)
    parser.add_argument("--cs1-mask", choices=["0xf800", "0xf000", "0xe000"], default="0xf800")
    parser.add_argument("--mesen", type=Path, default=HERE / "Mesen2/bin/linux-x64/Release/linux-x64/publish/Mesen")
    parser.add_argument("--output", type=Path, default=HERE / "results/S0")
    parser.add_argument("--golden", type=Path, default=HERE / "goldens/S0.argb.sha256",
                        help="reviewed final ARGB SHA-256 text file")
    parser.add_argument("--fetch-frame", type=int, default=160,
                        help="capture one post-startup Mesen PPU rendering frame (default: 160)")
    args = parser.parse_args()
    if args.cs1_mask != "0xf800" and not args.diagnostic:
        parser.error("wider CS1 masks require --diagnostic; the stream is at $0800-$0FFF")
    if args.frames <= STARTUP_FRAMES:
        parser.error(f"--frames must exceed the {STARTUP_FRAMES}-frame startup window")
    if not (0 <= args.fetch_frame <= args.frames):
        parser.error("--fetch-frame must be within the run")
    binary = args.mesen.resolve()
    if not binary.is_file():
        parser.error(f"build MesenCE first; missing {binary}")
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    # Isolate configuration and extracted native libraries from any desktop Mesen install.
    staging = output / "emulator"
    shutil.copytree(binary.parent, staging, dirs_exist_ok=True)
    config = {
        "Nes": {"Region": "Ntsc", "DisableGameDatabase": True, "RamPowerOnState": "AllZeros",
                "RandomizeCpuPpuAlignment": False, "RandomizeMapperPowerOnState": False,
                "RemoveSpriteLimit": False},
        "Preferences": {"AutomaticallyCheckForUpdates": False, "EnableRewind": False,
                        "EnableAutoSaveState": False, "DisableOsd": True},
        "Debug": {"ScriptWindow": {"AllowIoOsAccess": True}},
    }
    (staging / "settings.json").write_text(json.dumps(config))
    source = (REPO / "fcpico/external/fc-pico/tutorial_project/BOOTROM/rom.NES").read_bytes()
    rom = output / "tutorial-cosim.nes"
    rom.write_bytes(set_mapper(source))
    runs = []
    for number in range(2):
        run = output / f"run{number + 1}"
        run.mkdir(exist_ok=True)
        # Prevent an interrupted earlier run's completion marker from being reused.
        for filename in ("lua_result.json", "trace.csv", "fetch.csv", "final.png", "final.argb", "mailbox.csv"):
            (run / filename).unlink(missing_ok=True)
        env = dict(os.environ, FCPICO_RESULTS=str(run), FCPICO_TRACE=str(run / "trace.csv"),
                   FCPICO_FRAMES=str(args.frames), FCPICO_CS1_MASK=args.cs1_mask,
                   FCPICO_STARTUP_FRAMES=str(STARTUP_FRAMES),
                   FCPICO_FETCH_TRACE=str(run / "fetch.csv"), FCPICO_FETCH_FRAME=str(args.fetch_frame),
                   FCPICO_DEBUG_PEEKS=str(number), SDL_AUDIODRIVER="dummy")
        with (run / "console.log").open("w") as log:
            process = subprocess.run([str(staging / binary.name), "--testRunner", str(HERE / "lua/S0.lua"),
                                      str(rom), "--timeout=60"], cwd=run, env=env,
                                     stdout=log, stderr=subprocess.STDOUT, timeout=90)
        if process.returncode or not (run / "lua_result.json").exists():
            print(f"Mesen run failed/incomplete: {run / 'console.log'}", file=sys.stderr)
            return 1
        result = json.loads((run / "lua_result.json").read_text())
        with (run / "trace.csv").open() as trace:
            rows = list(csv.DictReader(trace))
        with (run / "fetch.csv").open() as trace:
            fetches = list(csv.DictReader(trace))
        try:
            fetch_profile = summarize_fetch_trace(fetches, args.fetch_frame, args.cs1_mask)
        except ValueError as error:
            print(f"PPU fetch trace failed: {error}; inspect {run / 'fetch.csv'}", file=sys.stderr)
            return 1
        writes = [row for row in rows if row["event"] == "write"]
        if not writes or not any(int(row["heartbeats"]) > 10 and int(row["init_actions"]) > 0 for row in writes):
            print(f"Cartridge bridge did not receive heartbeats; inspect {run}", file=sys.stderr)
            return 1
        # The mapper logs every CPU-side write, including protocol payload bytes and
        # ordinary controller traffic.  A steady v1 heartbeat consumes exactly the
        # mailbox plus its buffered-read dummy (65 CPU reads).  Startup transitions
        # can report partial or cumulative values, so keep those out of the steady
        # state histogram as well.
        stable = steady_heartbeat_rows(writes)
        counts = sorted({int(row["last_count"]) for row in stable})
        result.update(counts_after_startup=counts, final_argb_sha256=digest(run / "final.argb"),
                      trace_sha256=digest(run / "trace.csv"), mailbox_sha256=digest(run / "mailbox.csv"),
                      fetch_sha256=digest(run / "fetch.csv"), fetch_profile=fetch_profile,
                      stops_after_startup=(int(stable[-1]["dma_stops"]) - int(stable[0]["dma_stops"])) if stable else -1,
                      final_stats=writes[-1])
        runs.append(result)
    deterministic = all(runs[0][key] == runs[1][key] for key in
                        ("trace_sha256", "mailbox_sha256", "final_argb_sha256", "fetch_sha256"))
    strict_errors = []
    if runs[0]["counts_after_startup"] != [PPU_COUNT_VAL_V1]:
        strict_errors.append(f"observed counts {runs[0]['counts_after_startup']}, expected {PPU_COUNT_VAL_V1}")
    if runs[0]["valid_mailboxes_after_startup"] != args.frames - STARTUP_FRAMES:
        strict_errors.append("mailbox magic is not valid on every post-startup frame")
    if runs[0]["stops_after_startup"] != 0:
        strict_errors.append("DMA stops during post-startup frames")
    if not args.golden.is_file():
        strict_errors.append("no reviewed screenshot golden supplied")
    elif args.golden.read_text().strip() != runs[0]["final_argb_sha256"]:
        strict_errors.append("screenshot does not match the reviewed golden")
    report = dict(scenario="S0", calibrated=args.cs1_mask == "0xf800", cs1_mask=args.cs1_mask,
                  debug_peeks_do_not_change_results=deterministic, runs=runs,
                  strict_s0_pass=deterministic and not strict_errors, strict_errors=strict_errors)
    (output / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))
    return 0 if deterministic and (args.diagnostic or not strict_errors) else 1


if __name__ == "__main__":
    raise SystemExit(main())
