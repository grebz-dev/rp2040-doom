#!/usr/bin/env bash
# SPDX-License-Identifier: BSD-3-Clause
set -euo pipefail
script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd -- "$script_dir/../../.." && pwd)"
python_bin="${PYTHON:-$repo_root/fcpico/.venv/bin/python}"
if [[ ! -x "$python_bin" ]]; then python_bin=python3; fi
exec "$python_bin" "$script_dir/run_scenario.py" "$@"
