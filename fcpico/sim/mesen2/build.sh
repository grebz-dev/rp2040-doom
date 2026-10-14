#!/usr/bin/env bash
# SPDX-License-Identifier: BSD-3-Clause
set -euo pipefail
script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd -- "$script_dir/../../.." && pwd)"
build_dir="${FCPICO_COSIM_BUILD:-$script_dir/build}"
mkdir -p "$build_dir"
build_dir="$(cd -- "$build_dir" && pwd)"

command -v dotnet >/dev/null || { echo 'Install .NET SDK 10 and add it to PATH.' >&2; exit 1; }
command -v sdl2-config >/dev/null || { echo 'Install libsdl2-dev.' >&2; exit 1; }
dotnet --list-sdks | grep -q '^10\.' || { echo '.NET SDK 10 is required.' >&2; exit 1; }

export DOTNET_CLI_HOME="$build_dir/dotnet-home"
export NUGET_PACKAGES="$build_dir/nuget"
export DOTNET_CLI_TELEMETRY_OPTOUT=1

cmake -S "$repo_root/fcpico" -B "$build_dir/cartmodel" -G Ninja \
    -DFCPICO_HOST_ONLY=ON -DFCPICO_BUILD_COSIM=ON
cmake --build "$build_dir/cartmodel"
ctest --test-dir "$build_dir/cartmodel" --output-on-failure

# The upstream makefile does not track header dependencies; force the mapper TU to
# rebuild when its header or our optional build flags change.
make -C "$script_dir/Mesen2" -j "${JOBS:-4}" USE_GCC=true LTO=false \
    FCPICO_DIR="$repo_root/fcpico" FCPICO_BUILD="$build_dir/cartmodel" \
    -W Core/NES/MapperFactory.cpp

echo "Built MesenCE; run $script_dir/run_scenario.sh S0 --diagnostic"
