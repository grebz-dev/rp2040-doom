#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-3-Clause
"""Record the cartridge's USB serial output with host timestamps, and parse it.

    trace_log.py record [--port /dev/ttyACM0] -o session.log

Every line is written as ``<host monotonic ns> <line>``. The cartridge only
enumerates after its startup idle window and re-enumerates on reset, so the
recorder waits for the port and reconnects; start it before powering the
console. It never writes to the port (a BOOTSEL request is a serial command).
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass, field
import datetime
from pathlib import Path
import re
import sys
import time

RASPBERRY_PI_VID = 0x2E8A
TRACE = re.compile(r"^T ((?:[a-z_]+=-?\d+ ?)+)$")


@dataclass
class Trace:
    host_ns: int
    fields: dict[str, int]

    def __getattr__(self, name):
        try:
            return self.__dict__["fields"][name]
        except KeyError as exc:
            raise AttributeError(name) from exc


@dataclass
class Log:
    traces: list[Trace] = field(default_factory=list)
    other: list[tuple[int, str]] = field(default_factory=list)


def parse_line(line: str) -> dict[str, int] | None:
    match = TRACE.match(line.strip())
    if not match:
        return None
    return {k: int(v) for k, v in (pair.split("=") for pair in match.group(1).split())}


def parse(path: Path) -> Log:
    """A recorded log (``<ns> <line>``), or a bare serial capture (no stamps)."""
    log = Log()
    for number, raw in enumerate(path.read_text(errors="replace").splitlines()):
        if raw.startswith("#"):
            continue
        stamp, _, rest = raw.partition(" ")
        if stamp.isdigit():
            host_ns, line = int(stamp), rest
        else:
            host_ns, line = number, raw
        fields = parse_line(line)
        if fields is not None:
            log.traces.append(Trace(host_ns, fields))
        else:
            log.other.append((host_ns, line))
    return log


def find_port() -> str | None:
    from serial.tools import list_ports
    for port in list_ports.comports():
        if port.vid == RASPBERRY_PI_VID:
            return port.device
    return None


def record(port: str | None, output: Path, baud: int, seconds: float | None) -> int:
    import serial
    started = time.monotonic()
    lines = 0
    with output.open("a", buffering=1) as out:
        out.write(f"# start {datetime.datetime.now().isoformat()} monotonic_ns={time.monotonic_ns()}\n")
        while seconds is None or time.monotonic() - started < seconds:
            device = port or find_port()
            if device is None:
                time.sleep(0.2)
                continue
            try:
                with serial.Serial(device, baud, timeout=0.5) as link:
                    out.write(f"# open {device} monotonic_ns={time.monotonic_ns()}\n")
                    print(f"recording {device} -> {output}", file=sys.stderr)
                    pending = b""
                    while seconds is None or time.monotonic() - started < seconds:
                        chunk = link.read(4096)
                        if not chunk:
                            continue
                        now = time.monotonic_ns()
                        pending += chunk
                        *complete, pending = pending.split(b"\n")
                        for raw in complete:
                            out.write(f"{now} {raw.decode(errors='replace').rstrip()}\n")
                            lines += 1
            except (OSError, serial.SerialException) as exc:
                out.write(f"# lost {device}: {exc} monotonic_ns={time.monotonic_ns()}\n")
                time.sleep(0.2)
    print(f"{lines} lines", file=sys.stderr)
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    rec = sub.add_parser("record", help="log the serial port with host timestamps")
    rec.add_argument("--port", help="serial device (default: first Raspberry Pi USB device)")
    rec.add_argument("--baud", type=int, default=115200)
    rec.add_argument("--seconds", type=float, help="stop after this long (default: Ctrl-C)")
    rec.add_argument("-o", "--output", type=Path, required=True)
    summary = sub.add_parser("summary", help="count trace lines in a log")
    summary.add_argument("log", type=Path)
    args = parser.parse_args(argv)
    if args.command == "record":
        try:
            return record(args.port, args.output, args.baud, args.seconds)
        except KeyboardInterrupt:
            return 0
    log = parse(args.log)
    print(f"{len(log.traces)} picture lines, {len(log.other)} other lines")
    if log.traces:
        print(f"pictures {log.traces[0].id}..{log.traces[-1].id}, "
              f"timeouts {sum(t.tmo for t in log.traces)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
