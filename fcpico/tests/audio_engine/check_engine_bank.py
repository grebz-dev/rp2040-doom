#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-3-Clause
"""Exercise automatic bank installation through real Doom music/SFX calls."""
from collections import Counter
from pathlib import Path
import re
import subprocess
import sys
import tempfile


def main():
    binary, whx = sys.argv[1:]
    with tempfile.TemporaryDirectory(prefix='fcpico-engine-audio-') as directory:
        captures = []
        for run in range(2):
            path = Path(directory) / f'{run}.csv'
            result = subprocess.run([binary, '--whx', whx, '--demo', '1',
                                     '--frames', '600', '--lockstep', '--dump-apu', str(path)],
                                    check=True, text=True, capture_output=True)
            assert re.search(r'apu frames=\d+ pairs_max=\d+ drops=0\b', result.stdout), result.stdout
            capture = path.read_bytes()
            rows = [tuple(line.split(',')) for line in capture.decode().splitlines()]
            assert len(rows) > 100, 'engine did not advance the configured music bank'
            counts = Counter(frame for frame, _, _ in rows)
            assert max(counts.values()) <= 15
            assert any(int(frame) > 100 and int(reg, 16) in (2, 3, 10, 11)
                       for frame, reg, _ in rows), 'music stopped advancing'
            captures.append(capture)
        assert captures[0] == captures[1], 'APU output differs between identical demo runs'
    print('engine audio bank: deterministic, advancing, cap <=15, no drops')


if __name__ == '__main__':
    main()
