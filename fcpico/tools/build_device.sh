#!/usr/bin/env bash
# SPDX-License-Identifier: BSD-3-Clause
# Build the flashable FC PICO Doom image: firmware, a console ROM for the chosen
# graphics profile, WHX, and the optional audio bank and world sprite atlas,
# merged into one UF2.
#
# FCPICO_GRAPHICS selects how much of the picture the console draws natively.
# The firmware follows the protocol the console ROM negotiates, so the profile
# is the choice of ROM (and whether the world atlas is embedded):
#   world     (default) 20DOOM-05-9031: native status bar, menus, enemies and pickups
#   hud       20DOOM-04-9016: native status bar and menus; every actor is streamed
#   streamed  20DOOM-02-0002: the whole picture, status bar and menus are streamed
set -euo pipefail

repo=$(cd "$(dirname "$0")/../.." && pwd)
[[ $# == 0 ]] || { echo "usage: FCPICO_GRAPHICS=world|hud|streamed $0 (configure with FCPICO_AUDIO_WAD/FCPICO_WORLD_WAD and the overrides in build_firmware.sh)" >&2; exit 2; }
graphics=${FCPICO_GRAPHICS:-world}
case $graphics in
    world)
        generator='from build_world_sprite_rom import native_world_source as source'
        expected_stamp=20DOOM-05-9031
        default_build=$repo/.build/fcpico-device ;;
    hud)
        generator='from build_native_status_probe import native_status_source as source'
        expected_stamp=20DOOM-04-9016
        default_build=$repo/.build/fcpico-device-hud ;;
    streamed)
        generator=
        expected_stamp=20DOOM-02-0002
        default_build=$repo/.build/fcpico-device-streamed ;;
    *) echo "FCPICO_GRAPHICS must be world, hud or streamed (got: $graphics)" >&2; exit 2 ;;
esac
if [[ $graphics != world && -n ${FCPICO_WORLD_WAD:-} ]]; then
    # A v4/v2 console never negotiates world sprites; do not embed an unused atlas.
    echo "FCPICO_GRAPHICS=$graphics: ignoring FCPICO_WORLD_WAD" >&2
fi
[[ $graphics == world ]] || export FCPICO_WORLD_WAD=

generated="$repo/fcpico/bootrom/src/PG_generated.asm"
if [[ -n $generator ]]; then
    [[ ! -e $generated ]] || { echo "Generated ROM source already exists: $generated" >&2; exit 2; }
    cleanup() { rm -f "$generated"; }
    trap cleanup EXIT
    PYTHONPATH="$repo/fcpico/tools" python3 -c \
      "from pathlib import Path; $generator; import sys; Path(sys.argv[1]).write_text(source())" \
      "$generated"
    export FCPICO_BOOTROM_SOURCE=${generated##*/}
else
    export FCPICO_BOOTROM_SOURCE=PG_main.asm
fi
export BUILD_DIR=${BUILD_DIR:-$default_build}
rm -f "$BUILD_DIR/device/rp2040-doom/src/fcpico/doom_bootrom/doom.nes"
"$repo/fcpico/tools/build_firmware.sh"

rom="$BUILD_DIR/artifacts/doom.nes"
PYTHONPATH="$repo/fcpico/tools" python3 -c \
  'from pathlib import Path; import sys; rom=Path(sys.argv[1]).read_bytes(); fix=Path(sys.argv[2]).read_bytes(); assert rom[16+0x6ff0:16+0x6ffe] == sys.argv[3].encode(); assert rom[16+0x7000:16+0x8000] == fix' \
  "$rom" "$repo/fcpico/external/fc-pico/tutorial_project/BOOTROM/bootrom_fixr.bin" "$expected_stamp"
printf 'FCPICO_GRAPHICS=%s (console ROM %s)\n' "$graphics" "$expected_stamp" >> "$BUILD_DIR/artifacts/build-info.txt"
sha256sum "$BUILD_DIR/artifacts/fcpico_doom_whx.uf2" "$rom"
echo "Flashable image ($graphics graphics): $BUILD_DIR/artifacts/fcpico_doom_whx.uf2"
