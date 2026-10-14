# SPDX-License-Identifier: BSD-3-Clause
"""Tests for tools/respack.py: the pure-Python replacement for binlink.exe.

Verified byte-for-byte against tutorial_project's real archives -- see
fcpico/tools/respack.py's module docstring for the two archive details this
required (4-byte padding between entries; the ``:LABEL`` form's semantics).
"""

from __future__ import annotations

import os
import struct
from pathlib import Path

import pytest

import respack

def _read_text_exact(path: Path) -> str:
    """Read text without universal-newline translation, so files
    checked into this tree with CRLF line endings compare correctly
    against strings built with literal "\\r\\n"."""
    return path.read_bytes().decode()


REPO_ROOT = Path(__file__).resolve().parents[3]
TUTORIAL = REPO_ROOT / "fcpico/external/fc-pico/tutorial_project"

pytestmark = pytest.mark.skipif(
    not TUTORIAL.is_dir(), reason="tutorial_project reference fixtures not present"
)


def _write_relative_list(tmp_path: Path, list_name: str, tutorial_entries: list[str]) -> Path:
    """Write a .lst file at ``tmp_path/list_name`` that reproduces
    ``tutorial_entries`` (paths relative to ``TUTORIAL``, or ``:LABEL``
    lines passed through unchanged) but with each file path recomputed --
    "corrected" -- as a path relative to ``tmp_path`` instead, backslash
    separated the way every real ``.lst`` in this tree is written."""
    lines = []
    for entry in tutorial_entries:
        if entry.startswith(":"):
            lines.append(entry)
            continue
        rel = os.path.relpath(TUTORIAL / entry, tmp_path)
        lines.append(rel.replace(os.sep, "\\"))
    list_path = tmp_path / list_name
    list_path.write_text("\n".join(lines) + "\n")
    return list_path


# ---------------------------------------------------------------------------
# tuto1_hw/res/binlink.lst -> res.bin / res_id.h, byte for byte
# ---------------------------------------------------------------------------


def test_reproduces_tuto1_hw_res_bin_from_a_relocated_copy_of_the_list(tmp_path):
    list_path = _write_relative_list(
        tmp_path,
        "binlink.lst",
        [
            "BOOTROM/rom.NES",
            "mml/sound.nsf",
            "tuto1_hw/res/OBJ.chr",
            "tuto1_hw/res/font.chr",
        ],
    )

    archive, defines = respack.build_archive(list_path, id_base=0)

    real_bin = (TUTORIAL / "tuto1_hw/res/res.bin").read_bytes()
    assert archive == real_bin

    header_text = respack.render_header(defines, "res_id_h")
    real_header = _read_text_exact(TUTORIAL / "tuto1_hw/res/res_id.h")
    assert header_text == real_header


def test_reproduces_tuto1_hw_res_bin_directly_from_the_real_list_file():
    list_path = TUTORIAL / "tuto1_hw/res/binlink.lst"
    archive, defines = respack.build_archive(list_path, id_base=0)

    real_bin = (TUTORIAL / "tuto1_hw/res/res.bin").read_bytes()
    assert archive == real_bin

    header_text = respack.render_header(defines, "res_id_h")
    real_header = _read_text_exact(TUTORIAL / "tuto1_hw/res/res_id.h")
    assert header_text == real_header


def test_tuto1_hw_index_table_entries_match_generated_resources_doc():
    # fc-pico docs/pages/generated-resources.md's table, and _resdata's layout.
    list_path = TUTORIAL / "tuto1_hw/res/binlink.lst"
    archive, defines = respack.build_archive(list_path, id_base=0)

    expected = [
        ("NES_ROM", 32, 32784),
        ("NSF_SOUND", 32816, 7332),
        ("CHR_OBJ", 40148, 4096),
        ("CHR_FONT", 44244, 4096),
    ]
    for i, (name, offset, size) in enumerate(expected):
        off, sz = struct.unpack_from("<ii", archive, i * 8)
        assert (off, sz) == (offset, size)
    assert defines == [(name, i) for i, (name, _, _) in enumerate(expected)]
    assert len(archive) == 48340


def test_cli_reproduces_tuto1_hw_res_bin(tmp_path):
    list_path = _write_relative_list(
        tmp_path,
        "binlink.lst",
        [
            "BOOTROM/rom.NES",
            "mml/sound.nsf",
            "tuto1_hw/res/OBJ.chr",
            "tuto1_hw/res/font.chr",
        ],
    )
    out_bin = tmp_path / "res.bin"
    out_hdr = tmp_path / "res_id.h"

    rc = respack.main([str(list_path), str(out_hdr), str(out_bin), "0"])

    assert rc == 0
    assert out_bin.read_bytes() == (TUTORIAL / "tuto1_hw/res/res.bin").read_bytes()
    assert _read_text_exact(out_hdr) == _read_text_exact(TUTORIAL / "tuto1_hw/res/res_id.h")


# ---------------------------------------------------------------------------
# sample_game/res/binlink.lst (plain, no labels) -> res.bin / res_id.h
# ---------------------------------------------------------------------------


def test_reproduces_sample_game_res_bin_with_seven_entries(tmp_path):
    list_path = _write_relative_list(
        tmp_path,
        "binlink.lst",
        [
            "BOOTROM/rom.NES",
            "sample_game/GAMEROM/map0demo.NES",
            "mml/sound.nsf",
            "sample_game/res/OBJ.chr",
            "sample_game/res/font.chr",
            "sample_game/res/NamLicense0.bpe",
            "sample_game/res/NamLicense1.bpe",
        ],
    )

    archive, defines = respack.build_archive(list_path, id_base=0)

    assert archive == (TUTORIAL / "sample_game/res/res.bin").read_bytes()
    header_text = respack.render_header(defines, "res_id_h")
    assert header_text == _read_text_exact(TUTORIAL / "sample_game/res/res_id.h")


def test_sample_game_res_bin_pads_each_entry_to_a_4_byte_boundary():
    # NamLicense1.bpe is 666 bytes (not a multiple of 4): this is where the
    # "no padding at all" reading of the archives breaks down, and only
    # checking against tuto1_hw/res.bin (whose four sizes happen to already
    # sum to a multiple of 4) would miss it.
    list_path = TUTORIAL / "sample_game/res/binlink.lst"
    archive, _ = respack.build_archive(list_path, id_base=0)

    off5, sz5 = struct.unpack_from("<ii", archive, 5 * 8)
    off6, sz6 = struct.unpack_from("<ii", archive, 6 * 8)
    assert sz5 == 512
    assert sz6 == 666  # recorded size is exact, not rounded up
    assert off6 == off5 + 512  # 512 is already a multiple of 4: no gap here
    assert len(archive) == off6 + 666 + 2  # padded up to the next multiple of 4


# ---------------------------------------------------------------------------
# sample_game/res/binlink2.lst: the ":LABEL" form
# ---------------------------------------------------------------------------


def test_reproduces_sample_game_res_id2_labels_byte_for_byte(tmp_path):
    list_path = _write_relative_list(
        tmp_path,
        "binlink2.lst",
        [
            ":MP3_RES_ID",
            "mp3/007_mono.mp3",
            "mp3/005_mono.mp3",
            "mp3/013_mono.mp3",
            "mp3/014_mono.mp3",
            ":MP3_RES_ID_MAX",
        ],
    )

    archive, defines = respack.build_archive(list_path, id_base=10000)

    assert archive == (TUTORIAL / "sample_game/res/res2.bin").read_bytes()
    header_text = respack.render_header(defines, "res_id2_h")
    assert header_text == _read_text_exact(TUTORIAL / "sample_game/res/res_id2.h")


def test_label_value_is_the_file_count_so_far_not_incremented_by_the_label(tmp_path):
    (tmp_path / "a.bin").write_bytes(b"\x00")
    (tmp_path / "b.bin").write_bytes(b"\x00")
    list_path = tmp_path / "l.lst"
    list_path.write_text(":START\na.bin\n:MID\nb.bin\n:END\n")

    _, defines = respack.build_archive(list_path, id_base=100)

    assert dict(defines) == {
        "START": 100,  # before any file: id_base + 0
        "BIN_A": 100,
        "MID": 101,  # after one file: id_base + 1
        "BIN_B": 101,
        "END": 102,  # after both files: id_base + 2
    }
    # and in list order, interleaved with the file defines:
    assert [name for name, _ in defines] == ["START", "BIN_A", "MID", "BIN_B", "END"]


# ---------------------------------------------------------------------------
# Unit-level coverage of the list parser and path resolution
# ---------------------------------------------------------------------------


def test_parse_list_file_skips_blank_lines_and_detects_labels(tmp_path):
    lst = tmp_path / "x.lst"
    lst.write_text("a.bin\n\n:MYLABEL\n   \nb.bin\n")
    entries = respack.parse_list_file(lst)
    assert entries == [
        ("file", "a.bin"),
        ("label", "MYLABEL"),
        ("file", "b.bin"),
    ]


def test_resolve_list_path_converts_backslashes_and_is_relative_to_the_list_dir(tmp_path):
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "leaf.bin").write_bytes(b"x")

    resolved = respack.resolve_list_path(tmp_path, "sub\\leaf.bin")

    assert resolved == (sub / "leaf.bin").resolve()


def test_identifier_for_matches_the_ext_basename_convention():
    assert respack.identifier_for(Path("rom.NES")) == "NES_ROM"
    assert respack.identifier_for(Path("sound.nsf")) == "NSF_SOUND"
    assert respack.identifier_for(Path("OBJ.chr")) == "CHR_OBJ"
    assert respack.identifier_for(Path("map0demo.NES")) == "NES_MAP0DEMO"


def test_guard_for_matches_the_real_header_guards():
    assert respack.guard_for(Path("res_id.h")) == "res_id_h"
    assert respack.guard_for(Path("res_id2.h")) == "res_id2_h"


# ---------------------------------------------------------------------------
# CLI on small synthetic files (fast, exercises argument parsing precisely)
# ---------------------------------------------------------------------------


def test_cli_end_to_end_with_synthetic_files_and_padding(tmp_path):
    (tmp_path / "one.bin").write_bytes(b"\x01\x02\x03")  # 3 bytes -> 1 pad byte
    (tmp_path / "two.dat").write_bytes(b"\xAA\xBB\xCC\xDD")  # 4 bytes -> no pad
    lst = tmp_path / "list.lst"
    lst.write_text("one.bin\ntwo.dat\n")
    out_bin = tmp_path / "out.bin"
    out_hdr = tmp_path / "res_id.h"

    rc = respack.main([str(lst), str(out_hdr), str(out_bin), "0"])

    assert rc == 0
    data = out_bin.read_bytes()
    assert struct.unpack_from("<ii", data, 0) == (16, 3)  # index table is 2*8=16 bytes
    assert struct.unpack_from("<ii", data, 8) == (20, 4)  # 16+3 padded up to 20
    assert data[16:19] == b"\x01\x02\x03"
    assert data[19] == 0
    assert data[20:24] == b"\xAA\xBB\xCC\xDD"
    assert len(data) == 24

    assert _read_text_exact(out_hdr) == (
        "#ifndef res_id_h\r\n"
        "#define res_id_h\r\n"
        "#define BIN_ONE 0\r\n"
        "#define DAT_TWO 1\r\n"
        "#endif\r\n"
    )


def test_cli_accepts_hex_id_base(tmp_path):
    (tmp_path / "a.bin").write_bytes(b"\x00")
    lst = tmp_path / "list.lst"
    lst.write_text("a.bin\n")
    out_bin = tmp_path / "out.bin"
    out_hdr = tmp_path / "id.h"

    rc = respack.main([str(lst), str(out_hdr), str(out_bin), "0x10"])

    assert rc == 0
    assert "#define BIN_A 16" in _read_text_exact(out_hdr)


def test_cli_requires_exactly_four_arguments(capsys):
    rc = respack.main(["only-one-arg"])
    assert rc == 1
    assert "usage" in capsys.readouterr().err
