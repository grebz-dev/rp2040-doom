#!/usr/bin/env bash
# SPDX-License-Identifier: BSD-3-Clause
set -euo pipefail

repo=$(cd "$(dirname "$0")/../../.." && pwd)
assembler=${NESASM_BIN:-nesasm}
scratch=$(mktemp -d)
trap 'rm -rf -- "$scratch"' EXIT
cp -a "$repo/fcpico/external/fc-pico/tutorial_project/BOOTROM/." "$scratch/"
(
    cd "$scratch"
    "$assembler" -s -o "$scratch/tutorial.raw.nes" PG_main.asm
)
python3 "$repo/fcpico/tools/nes/normalize_ines.py" \
    "$scratch/tutorial.raw.nes" "$scratch/tutorial.nes"
actual=$(md5sum "$scratch/tutorial.nes" | cut -d' ' -f1)
expected=b6cd675342b6c8ad79e537e2c9860579
if [[ "$actual" != "$expected" ]]; then
    echo "tutorial ROM MD5 mismatch: got $actual, expected $expected" >&2
    exit 1
fi
echo "tutorial ROM MD5 gate passed: $actual"
