#!/usr/bin/env python3
"""Run the SDL-free Doom host renderer twice and compare indexed and NES frames."""

import pathlib
import subprocess
import sys
import tempfile


def main() -> int:
    executable = pathlib.Path(sys.argv[1])
    whx = pathlib.Path(sys.argv[2])
    with tempfile.TemporaryDirectory(prefix="fcpico-host-determinism-") as work:
        root = pathlib.Path(work)
        runs = []
        for name in ("one", "two"):
            output = root / name
            streams = root / f"{name}-streams"
            result = subprocess.run(
                [str(executable), "--whx", str(whx), "--demo", "1",
                 "--frames", "600", "--lockstep", "--dump-8bit", str(output),
                 "--dump-stream", str(streams)],
                text=True, capture_output=True, timeout=120, check=True,
            )
            if "host frames=600" not in result.stdout:
                raise AssertionError(result.stdout + result.stderr)
            checker = pathlib.Path(__file__).resolve().parents[2] / "tests/goldens/check.py"
            subprocess.run([sys.executable, str(checker), str(output)],
                           check=True, capture_output=True, text=True, timeout=30)
            runs.append((
                [path.read_bytes() for path in sorted(output.glob("*.raw"))],
                [path.read_bytes() for path in sorted(streams.glob("*.bin"))],
            ))
        if len(runs[0][0]) != 600 or runs[0][0] != runs[1][0]:
            raise AssertionError("600-frame indexed output differs between runs")
        if len(runs[0][1]) != 600 or runs[0][1] != runs[1][1]:
            raise AssertionError("600-frame NES stream differs between runs")
        if any(len(frame) != 17408 for frame in runs[0][1]):
            raise AssertionError("wrong v2 stream size")
        if len(set(runs[0][0])) < 2 or len(set(runs[0][1])) < 2:
            raise AssertionError("renderer did not produce changing frames")
    print("600/600 indexed and NES frames identical across two runs")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
