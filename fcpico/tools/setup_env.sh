#!/usr/bin/env bash
# SPDX-License-Identifier: BSD-3-Clause
#
# setup_env.sh -- Ubuntu 24.04 host setup for the FC PICO Doom toolchain.
#
# Installs the toolchain pins listed in ../README.md:
# apt packages, the Python tool/test dependencies, pico-sdk, arm-none-eabi-gcc
# and picotool. Idempotent: each step checks for existing work first, so
# re-running after a partial or failed run is safe.
#
# This script needs network access (apt, git clone, one toolchain download)
# that is not available in every environment that edits this repository, so
# it is not run automatically. Run it by hand on a real Ubuntu 24.04 host or
# CI runner:
#
#   fcpico/tools/setup_env.sh
#
set -euo pipefail

log() { printf '>>> %s\n' "$*"; }

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd)"

# ---------------------------------------------------------------------------
# Versions -- keep in sync with fcpico/README.md "Toolchain pins" and
# .github/workflows/fcpico-*.yml. Bump deliberately, in one commit, with a note there.
# ---------------------------------------------------------------------------
PICO_SDK_TAG="2.1.1"
PICOTOOL_TAG="2.1.1"
ARM_GCC_VERSION="13.2.Rel1"
ARM_GCC_DIRNAME="arm-gnu-toolchain-13.2.rel1-x86_64-arm-none-eabi"
# Official Arm developer download for this exact release, x86_64 Linux.
ARM_GCC_URL="https://developer.arm.com/-/media/Files/downloads/gnu/13.2.rel1/binrel/${ARM_GCC_DIRNAME}.tar.xz"

export PICO_SDK_PATH="${PICO_SDK_PATH:-$HOME/pico-sdk}"
TOOLCHAINS_DIR="${TOOLCHAINS_DIR:-$HOME/toolchains}"
ARM_GCC_PATH="$TOOLCHAINS_DIR/$ARM_GCC_DIRNAME"
PICOTOOL_DIR="$HOME/picotool"

# ---------------------------------------------------------------------------
# 1. APT packages (cmake/ninja/gcc for the host and device builds, SDL2 +
#    libpng for chocolate-doom-style host tools, wine32 for nesasm.exe).
# ---------------------------------------------------------------------------
if dpkg --print-foreign-architectures 2>/dev/null | grep -qx i386; then
    log "i386 architecture already enabled."
else
    log "Adding i386 architecture (needed for wine32) ..."
    sudo dpkg --add-architecture i386
fi

log "apt-get update ..."
sudo apt-get update

log "Installing apt packages ..."
sudo apt-get install -y \
    cmake \
    ninja-build \
    build-essential \
    python3-pip \
    libsdl2-dev \
    libsdl2-mixer-dev \
    libsdl2-net-dev \
    libpng-dev \
    git \
    wine32:i386

# ---------------------------------------------------------------------------
# 2. Python dependencies (tools/requirements.txt).
# ---------------------------------------------------------------------------
log "Installing Python dependencies from requirements.txt ..."
# Ubuntu 24.04's system pip refuses a plain install (PEP 668,
# "externally-managed-environment"); --break-system-packages is Debian/
# Ubuntu's own escape hatch for exactly this kind of host tooling setup.
pip3 install --break-system-packages -r "$SCRIPT_DIR/requirements.txt"

# ---------------------------------------------------------------------------
# 3. pico-sdk (tag 2.1.1, with submodules).
# ---------------------------------------------------------------------------
if [ -d "$PICO_SDK_PATH/.git" ]; then
    log "pico-sdk already present at $PICO_SDK_PATH, skipping clone."
else
    log "Cloning pico-sdk ${PICO_SDK_TAG} into $PICO_SDK_PATH ..."
    git clone --branch "$PICO_SDK_TAG" --depth 1 \
        --recurse-submodules --shallow-submodules \
        https://github.com/raspberrypi/pico-sdk.git "$PICO_SDK_PATH"
fi

# ---------------------------------------------------------------------------
# 4. arm-none-eabi-gcc 13.2.Rel1 (prebuilt, from the official Arm download).
# ---------------------------------------------------------------------------
mkdir -p "$TOOLCHAINS_DIR"
if [ -x "$ARM_GCC_PATH/bin/arm-none-eabi-gcc" ]; then
    log "arm-none-eabi-gcc ${ARM_GCC_VERSION} already present at $ARM_GCC_PATH, skipping download."
else
    log "Downloading arm-none-eabi-gcc ${ARM_GCC_VERSION} from $ARM_GCC_URL ..."
    tmp_tar="$(mktemp --tmpdir "arm-gnu-toolchain-XXXXXX.tar.xz")"
    curl -fL "$ARM_GCC_URL" -o "$tmp_tar"
    log "Extracting into $TOOLCHAINS_DIR ..."
    tar -xJf "$tmp_tar" -C "$TOOLCHAINS_DIR"
    rm -f "$tmp_tar"
fi

# ---------------------------------------------------------------------------
# 5. picotool 2.1.1, built from source against the pico-sdk above.
# ---------------------------------------------------------------------------
if [ -x "$PICOTOOL_DIR/build/picotool" ]; then
    log "picotool already built at $PICOTOOL_DIR/build/picotool, skipping."
else
    if [ -d "$PICOTOOL_DIR/.git" ]; then
        log "picotool source already present at $PICOTOOL_DIR."
    else
        log "Cloning picotool ${PICOTOOL_TAG} into $PICOTOOL_DIR ..."
        git clone --branch "$PICOTOOL_TAG" --depth 1 \
            https://github.com/raspberrypi/picotool.git "$PICOTOOL_DIR"
    fi
    log "Building picotool (PICO_SDK_PATH=$PICO_SDK_PATH) ..."
    cmake -S "$PICOTOOL_DIR" -B "$PICOTOOL_DIR/build" -G Ninja \
        "-DPICO_SDK_PATH=$PICO_SDK_PATH"
    cmake --build "$PICOTOOL_DIR/build"
fi

# ---------------------------------------------------------------------------
# 6. Summary.
# ---------------------------------------------------------------------------
log "Setup complete. Add these to your shell profile:"
cat <<EOF

export PICO_SDK_PATH="$PICO_SDK_PATH"
export PATH="$ARM_GCC_PATH/bin:\$PATH"
export PATH="$PICOTOOL_DIR/build:\$PATH"

EOF
