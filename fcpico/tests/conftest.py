# SPDX-License-Identifier: BSD-3-Clause
"""Shared pytest configuration for fcpico/tests.

Puts fcpico/tools on sys.path so any test module under fcpico/tests/ can import
tools packages and scripts directly (e.g. `from fcpico import protocol`,
`import gen_protocol`) without installing anything. Kept intentionally small
and generic: other suites (tests/tools/, tests/bootrom/, ...) share this file
and should not need anything more specific than this from it.
"""

from __future__ import annotations

import sys
from pathlib import Path

_TOOLS_DIR = Path(__file__).resolve().parent.parent / "tools"
if _TOOLS_DIR.is_dir() and str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))
