# SPDX-License-Identifier: BSD-3-Clause
"""Pure-Python replacements for the Windows-only NES bank tools.

Modules:

* ``bincut`` -- replacement for ``bin_catcut.exe`` (cut a byte range out of
  a file).
* ``bin2c`` -- replacement for ``Bin2C.exe`` (binary -> aligned C array).
* ``check_fixbank`` -- verifies the fix bank tail of an assembled iNES image
  against the reference ``bootrom_fixr.bin``.
"""
