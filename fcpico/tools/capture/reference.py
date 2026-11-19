#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-3-Clause
"""Side-by-side sheets of captured fields and the host engine's picture for
the same level tic (Doom's leveltime).

    reference.py fields_src fields.csv HOST_STREAM_DIR -o sheet.png --log session.log
        [--report report.json | --field-list 120,121,...]

HOST_STREAM_DIR is ``fcpico_doom_host --demo N --lockstep --dump-stream DIR``
from an FCPICO_FRAME_TRACE host build, so every host picture carries its tic
in the same barcode. The host picture is the streamed background only (native
sprites and the status bar are drawn by the console), rendered with the
emulator palette: a reference for what the firmware sent, not for composite
colour. Demos are deterministic and start with their level, so equal level
tics are equal game states. Host tics are unwrapped from the barcode's 7 bits,
so the host dump must start at the demo's first frame.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

try:
    from . import barcode
    from .analyze import align, load_fields, showings
    from . import trace_log
    from .decode_video import iter_fields
except ImportError:  # run as a script
    import barcode  # type: ignore
    from analyze import align, load_fields, showings  # type: ignore
    import trace_log  # type: ignore
    from decode_video import iter_fields  # type: ignore

from fcpico import nes_palette, protocol, stream


def render_stream(data: bytes) -> np.ndarray:
    """A dumped stream buffer as a 240x256 RGB picture."""
    if len(data) == protocol.VRAM_BUF_BYTES_V4:
        mailbox = data[protocol.VRAM_MAILBOX_OFF_V4:]
        data = bytes(stream.expand_native_text_stream(data))
    else:
        mailbox = data[protocol.VRAM_MAILBOX_OFF_V2:]
    pixels = stream.decode_frame(data)
    palette = np.frombuffer(mailbox[protocol.MBX_PAL:protocol.MBX_PAL + 16], np.uint8)
    attr = mailbox[protocol.MBX_ATTR:protocol.MBX_ATTR + 64]
    ys, xs = np.indices(pixels.shape)
    sub = np.vectorize(lambda x, y: stream.attr_get(attr, x, y))(xs[::8, ::8], ys[::8, ::8])
    sub = np.repeat(np.repeat(sub, 8, axis=0), 8, axis=1)[:pixels.shape[0], :pixels.shape[1]]
    index = np.where(pixels == 0, palette[0], palette[sub * 4 + pixels])
    return nes_palette.NES_PALETTE_RGB_ARRAY[index]


def host_pictures(directory: Path) -> dict[int, Path]:
    """Unwrapped tic -> host stream file (first picture of each tic)."""
    out: dict[int, Path] = {}
    previous = None
    for path in sorted(directory.glob("frame*.bin")):
        data = path.read_bytes()
        if len(data) == protocol.VRAM_BUF_BYTES_V4:
            data = bytes(stream.expand_native_text_stream(data))
        code = barcode.read_nes(stream.decode_frame(data))
        if code is None:
            continue
        tic = code.tic if previous is None else previous + (code.tic - previous) % (1 << barcode.TIC_BITS)
        out.setdefault(tic, path)
        previous = tic
    return out


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("source", type=Path, help="the capture decode_video.py read")
    parser.add_argument("fields", type=Path, help="decode_video.py CSV")
    parser.add_argument("host", type=Path, help="host --dump-stream directory (trace build)")
    parser.add_argument("-o", "--output", type=Path, required=True)
    parser.add_argument("--log", type=Path,
                        help="trace_log.py record output: exact tics (else matched on 7 bits)")
    parser.add_argument("--report", type=Path, help="analyze.py JSON: sheet its hitch pictures")
    parser.add_argument("--field-list", help="comma-separated capture field numbers")
    parser.add_argument("--frames", action="store_true", help="progressive capture")
    parser.add_argument("--limit", type=int, default=12)
    args = parser.parse_args(argv)

    codes = load_fields(args.fields)
    if args.report:
        wanted = [h["first_field"] for h in json.loads(args.report.read_text())["hitch_pictures"]]
    elif args.field_list:
        wanted = [int(v) for v in args.field_list.split(",")]
    else:
        wanted = [i for i, c in enumerate(codes) if c is not None][:: max(1, len(codes) // args.limit)]
    wanted = sorted(set(wanted))[:args.limit]
    host = host_pictures(args.host)
    exact: dict[int, int] = {}
    if args.log:
        traces = trace_log.parse(args.log).traces
        runs = showings(codes)
        offset = align(runs, traces)
        tics = {t.id: t.lt for t in traces}
        for run in runs:
            for field in range(run.first_field, run.first_field + run.fields):
                if run.id + offset in tics:
                    exact[field] = tics[run.id + offset]
    host_tics = sorted(host)

    rows = []
    wanted_set = set(wanted)
    for index, luma in enumerate(iter_fields(args.source, not args.frames)):
        if index in wanted_set:
            rows.append((index, luma))
        if len(rows) == len(wanted):
            break
    cell_w, cell_h = 512, 480
    sheet = Image.new("RGB", (cell_w * 2, (cell_h + 24) * len(rows)), (24, 24, 24))
    draw = ImageDraw.Draw(sheet)
    previous_tic = None
    for row, (index, luma) in enumerate(rows):
        y = row * (cell_h + 24)
        code = codes[index]
        label = f"field {index}"
        if code is not None:
            tic7 = code.tic
            # Nearest host tic with the same low bits, after the previous row's.
            if index in exact:
                candidates = [t for t in host_tics if t >= exact[index]][:1]
            else:
                candidates = [t for t in host_tics if t % (1 << barcode.TIC_BITS) == tic7
                              and (previous_tic is None or t >= previous_tic)]
            if candidates:
                tic = candidates[0]
                previous_tic = tic
                reference = render_stream(host[tic].read_bytes())
                sheet.paste(Image.fromarray(reference).resize((cell_w, cell_h), Image.NEAREST),
                            (cell_w, y + 24))
                label += f"  picture {code.id}  tic {tic}  host {host[tic].name}"
        sheet.paste(Image.fromarray(luma).convert("RGB").resize((cell_w, cell_h)), (0, y + 24))
        draw.text((8, y + 4), label, fill=(255, 255, 255))
    sheet.save(args.output)
    print(f"{len(rows)} rows -> {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
