#!/usr/bin/env bash
# SPDX-License-Identifier: BSD-3-Clause
# Build the cartridge firmware, embedded console ROM and merged flash UF2 with the
# hardware-validated device profile (MinSizeRel, engine startup delay and diagnostics).
# fcpico/tools/build_device.sh wraps this with the current generated console ROM.
set -euo pipefail

repo=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)
bootstrap=0
case "${1:-}" in
    --bootstrap-tools) bootstrap=1 ;;
    --help|-h)
        cat <<'EOF'
Usage: fcpico/tools/build_firmware.sh [--bootstrap-tools]

Builds firmware, embedded console ROM and merged WHX UF2 under .build/fcpico-firmware.
--bootstrap-tools fetches/builds pinned native picotool and NESASM if absent.
Requires the project-local Arm GCC 13.2 toolchain and Pico SDK 2.1.1.

Overrides: BUILD_DIR, TOOLS_DIR, ARM_TOOLCHAIN_PATH, PICO_SDK_PATH,
           PICOTOOL_DIR (CMake package directory), NESASM_BIN, JOBS,
           FCPICO_AUDIO_WAD (optional local shareware WAD; enables audio).
           FCPICO_WORLD_WAD (optional local IWAD; enables native world sprites).
See fcpico/README.md for prerequisites.
EOF
        exit 0 ;;
    '') ;;
    *) echo "Unknown argument: $1 (use --help)" >&2; exit 2 ;;
esac
[[ $# -le 1 ]] || { echo 'Too many arguments' >&2; exit 2; }
fail() { echo "$*" >&2; exit 1; }
build=${BUILD_DIR:-$repo/.build/fcpico-firmware}
tools_dir=${TOOLS_DIR:-$repo/.build/tools}
arm=${ARM_TOOLCHAIN_PATH:-$repo/arm-gnu-toolchain-13.2.Rel1-x86_64-arm-none-eabi}
export PICO_SDK_PATH=${PICO_SDK_PATH:-$HOME/.local/fcpico/pico-sdk}
picotool_dir=${PICOTOOL_DIR:-$tools_dir/picotool-install/lib/cmake/picotool}
export NESASM_BIN=${NESASM_BIN:-$tools_dir/nesasm/nesasm}
jobs=${JOBS:-4}
audio_wad=${FCPICO_AUDIO_WAD:-}
world_wad=${FCPICO_WORLD_WAD:-}
python_args=()
if [[ -n $audio_wad ]]; then
    [[ -f $audio_wad ]] || fail "Missing audio WAD: $audio_wad"
    [[ -x $repo/fcpico/.venv/bin/python ]] || fail 'Audio conversion requires fcpico/.venv with numpy'
    python_args=(-DPython3_EXECUTABLE="$repo/fcpico/.venv/bin/python")
fi
if [[ -n $world_wad ]]; then
    [[ -f $world_wad ]] || fail "Missing world sprite WAD: $world_wad"
    [[ -x $repo/fcpico/.venv/bin/python ]] || fail 'World atlas generation requires fcpico/.venv with numpy'
    python_args=(-DPython3_EXECUTABLE="$repo/fcpico/.venv/bin/python")
fi
for command in cmake ninja python3 git make; do
    command -v "$command" >/dev/null || fail "Missing command: $command"
done
[[ -x $arm/bin/arm-none-eabi-gcc ]] || fail "Missing Arm toolchain: $arm"
[[ $("$arm/bin/arm-none-eabi-gcc" -dumpfullversion) == 13.2.1 ]] || fail 'Requires Arm GCC 13.2.1'
[[ -f $PICO_SDK_PATH/pico_sdk_version.cmake ]] || fail "Missing Pico SDK: $PICO_SDK_PATH"
[[ $(git -C "$PICO_SDK_PATH" describe --tags --exact-match HEAD) == 2.1.1 ]] || fail 'Requires Pico SDK tag 2.1.1'
[[ -f $repo/doom1.whx ]] || fail 'doom1.whx is missing from the repository root'
export PATH="$arm/bin:$PATH"

if [[ ! -f $picotool_dir/picotoolConfig.cmake ]]; then
    [[ $bootstrap == 1 ]] || fail 'Set PICOTOOL_DIR to native picotool 2.1.1, or run --bootstrap-tools'
    mkdir -p "$tools_dir"
    if [[ ! -d $tools_dir/picotool-src ]]; then
        git clone --branch 2.1.1 --depth 1 https://github.com/raspberrypi/picotool.git "$tools_dir/picotool-src"
    fi
    [[ $(git -C "$tools_dir/picotool-src" rev-parse HEAD) == de8ae5ac334e1126993f72a5c67949712fd1e1a4 ]] || fail 'Unexpected picotool source revision'
    cmake -S "$tools_dir/picotool-src" -B "$tools_dir/picotool-build" -G Ninja \
        -DCMAKE_BUILD_TYPE=Release -DCMAKE_POLICY_VERSION_MINIMUM=3.5 \
        -DPICO_SDK_PATH="$PICO_SDK_PATH" -DCMAKE_INSTALL_PREFIX="$tools_dir/picotool-install"
    cmake --build "$tools_dir/picotool-build" -j "$jobs"
    cmake --install "$tools_dir/picotool-build"
    picotool_dir=$tools_dir/picotool-install/lib/cmake/picotool
fi
if ! command -v "$NESASM_BIN" >/dev/null; then
    [[ $bootstrap == 1 ]] || fail 'Set NESASM_BIN to native NESASM CE, or run --bootstrap-tools'
    mkdir -p "$tools_dir"
    if [[ ! -d $tools_dir/nesasm ]]; then
        git clone https://github.com/ClusterM/nesasm.git "$tools_dir/nesasm"
        git -C "$tools_dir/nesasm" checkout --detach 6fc41cda37b934aa29aa2639d0baa74424268e31
    fi
    [[ $(git -C "$tools_dir/nesasm" rev-parse HEAD) == 6fc41cda37b934aa29aa2639d0baa74424268e31 ]] || fail 'Unexpected NESASM source revision'
    make -C "$tools_dir/nesasm/source" -j "$jobs"
    export NESASM_BIN=$tools_dir/nesasm/nesasm
fi
# Refuse reuse of a cache made with the system GCC or another compiler.
if [[ -f $build/device/CMakeCache.txt ]]; then
    cached=$(sed -n 's/^CMAKE_C_COMPILER:FILEPATH=//p;s/^CMAKE_C_COMPILER:STRING=//p' "$build/device/CMakeCache.txt")
    [[ $(realpath "$cached") == $(realpath "$arm/bin/arm-none-eabi-gcc") ]] || fail 'Build cache uses another compiler; choose a fresh BUILD_DIR'
fi
"$repo/fcpico/bootrom/ci/tutorial_md5_gate.sh"
cmake -S "$repo/fcpico" -B "$build/device" -G Ninja \
    -DCMAKE_BUILD_TYPE=MinSizeRel -DPICO_BOARD=fcpico -DPICO_PLATFORM=rp2350-arm-s \
    -DPICO_TOOLCHAIN_PATH="$arm" -DPICO_NO_FLASH=OFF -DPICO_COPY_TO_RAM=OFF \
    -DPICO_SDK_PATH="$PICO_SDK_PATH" -Dpicotool_DIR="$picotool_dir" \
    -DFCPICO_HOST_ONLY=OFF -DFCPICO_BUILD_ENGINE=ON -DFCPICO_DIAGNOSTIC_NO_ENGINE=OFF \
    -DFCPICO_DIAGNOSTIC_ENGINE_DELAY=ON -DPICO_NO_PICOTOOL=OFF -DPICO_NO_UF2=OFF \
    -DFCPICO_AUDIO_WAD="$audio_wad" -DFCPICO_WORLD_WAD="$world_wad" "${python_args[@]}" \
    ${FCPICO_EXTRA_CMAKE_ARGS:-}
cmake --build "$build/device" --target doom_tiny_fcpico -j "$jobs"
firmware=$build/device/rp2040-doom/src
python3 "$repo/fcpico/tools/flash_layout_check.py" "$firmware/fcpico_doom.elf"
output=$build/artifacts
mkdir -p "$output"
cp "$firmware/fcpico_doom.elf" "$firmware/fcpico_doom.uf2" "$output/"
cp "$firmware/fcpico/doom_bootrom/doom.nes" "$output/"
audio_args=()
if [[ -n $audio_wad ]]; then
    cp "$build/device/port/audio/audio.bin" "$build/device/port/audio/audio.json" "$output/"
    audio_args=(--audio "$output/audio.bin")
fi
if [[ -n $world_wad ]]; then
    cp "$build/device/port/world/world_atlas.bin" "$build/device/port/world/world_atlas.json" "$output/"
    audio_args+=(--world "$output/world_atlas.bin")
fi
python3 "$repo/fcpico/tools/whx2uf2.py" "$repo/doom1.whx" \
    "$output/fcpico_doom_whx.uf2" --firmware "$output/fcpico_doom.uf2" "${audio_args[@]}"
{
    printf 'Profile: MinSizeRel, RP2350 ARM Secure, engine delay ON\n'
    printf 'Repository: '; git -C "$repo" rev-parse HEAD
    printf 'SDK: '; git -C "$PICO_SDK_PATH" rev-parse HEAD
    "$arm/bin/arm-none-eabi-gcc" --version
    printf 'picotool_DIR=%s\nNESASM_BIN=%s\n' "$picotool_dir" "$NESASM_BIN"
    printf 'FCPICO_AUDIO_WAD=%s\nFCPICO_WORLD_WAD=%s\n' "$audio_wad" "$world_wad"
    git -C "$repo" status --short
} > "$output/build-info.txt"
cp "$build/device/CMakeCache.txt" "$output/"
git -C "$repo" diff --binary HEAD > "$output/source.patch"
(cd "$output" && sha256sum fcpico_doom.elf fcpico_doom.uf2 doom.nes \
    fcpico_doom_whx.uf2 > SHA256SUMS)
if [[ -n $audio_wad ]]; then
    (cd "$output" && sha256sum audio.bin audio.json >> SHA256SUMS)
fi
if [[ -n $world_wad ]]; then
    (cd "$output" && sha256sum world_atlas.bin world_atlas.json >> SHA256SUMS)
fi
printf '\nFlash image: %s/fcpico_doom_whx.uf2\n' "$output"
cat "$output/SHA256SUMS"
