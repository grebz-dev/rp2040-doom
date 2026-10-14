# SPDX-License-Identifier: BSD-3-Clause
"""Tests for tools/whx2uf2.py."""

from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
import whx2uf2  # noqa: E402

FAMILY = 0xE48BFF59


def firmware(length: int) -> bytes:
    data = bytes(i & 0xFF for i in range(length))
    return whx2uf2.write_blocks(whx2uf2.binary_blocks(data, 0x10000000, FAMILY))


def test_merged_image_places_whx_and_renumbers() -> None:
    whx = bytes(range(256)) * 3 + b"tail"
    merged = whx2uf2.build(whx, firmware(600))
    blocks = whx2uf2.read_blocks(merged)
    assert len(blocks) == 3 + 4
    for number in range(len(blocks)):
        header = merged[number * 512:number * 512 + 32]
        assert int.from_bytes(header[20:24], "little") == number
        assert int.from_bytes(header[24:28], "little") == len(blocks)
    assert {family for _, _, family, _ in blocks} == {FAMILY}
    placed = b"".join(p for _, addr, _, p in blocks if addr >= whx2uf2.WHX_ADDR)
    assert placed == whx
    assert blocks[3][1] == whx2uf2.WHX_ADDR


def test_rejects_firmware_overlapping_whx() -> None:
    with pytest.raises(ValueError, match="overlapping"):
        whx2uf2.build(b"x", firmware(0x80001))


def test_rejects_whx_past_end_of_flash() -> None:
    with pytest.raises(ValueError, match="fit"):
        whx2uf2.build(bytes(0x380001), None)
