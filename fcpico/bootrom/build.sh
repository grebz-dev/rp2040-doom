#!/usr/bin/env bash
# SPDX-License-Identifier: BSD-3-Clause
set -euo pipefail

here=$(cd "$(dirname "$0")" && pwd)
repo=$(cd "$here/../.." && pwd)
output_dir=${1:-$here/out}
assembler=${NESASM_BIN:-nesasm}
source_file=${FCPICO_BOOTROM_SOURCE:-PG_main.asm}
if ! command -v "$assembler" >/dev/null 2>&1; then
    echo "NESASM_BIN must point to native NESASM CE (see fcpico/README.md)" >&2
    exit 2
fi
mkdir -p "$output_dir"
output_dir=$(cd "$output_dir" && pwd)
(
    cd "$here/src"
    "$assembler" -s -f -o "$output_dir/doom.raw.nes" "$source_file"
)
python3 "$repo/fcpico/tools/nes/normalize_ines.py" \
    "$output_dir/doom.raw.nes" "$output_dir/doom.nes"
python3 "$repo/fcpico/tools/nes/check_fixbank.py" \
    "$output_dir/doom.nes" "$repo/fcpico/external/fc-pico/tutorial_project/BOOTROM/bootrom_fixr.bin"
