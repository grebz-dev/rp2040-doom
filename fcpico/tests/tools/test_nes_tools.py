# SPDX-License-Identifier: BSD-3-Clause
"""Tests for tools/nes/bin2c.py, bincut.py and check_fixbank.py: the
pure-Python replacements for Bin2C.exe, bin_catcut.exe's cut mode, and the
fix-bank equality check (see docs/pages/build-pipeline.md in fc-pico).
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

import pytest

from nes import bin2c, bincut, check_fixbank, normalize_ines

REPO_ROOT = Path(__file__).resolve().parents[3]
TUTORIAL = REPO_ROOT / "fcpico/external/fc-pico/tutorial_project"

pytestmark = pytest.mark.skipif(
    not TUTORIAL.is_dir(), reason="tutorial_project reference fixtures not present"
)

_HEX_BYTE_RE = re.compile(rb"0x([0-9a-fA-F]{2})")
BOOTROM_FIXR_MD5 = "1A9A2AC85F1E7A15AF9DF47013432BF9"


def test_native_nesasm_header_normalization_preserves_prg():
    vendor = (TUTORIAL / "BOOTROM/rom.NES").read_bytes()
    ce = bytearray(vendor)
    ce[7] = 8
    ce[11] = 7
    assert normalize_ines.normalize(bytes(ce)) == vendor
    ce[6] = 0x11
    with pytest.raises(ValueError, match="mapper-0"):
        normalize_ines.normalize(bytes(ce))


def _parse_bin2c_bytes(text_bytes: bytes) -> bytes:
    """Reconstruct the original byte array from bin2c's ``0xNN, ...`` text."""
    return bytes(int(m.group(1), 16) for m in _HEX_BYTE_RE.finditer(text_bytes))


# ---------------------------------------------------------------------------
# bin2c.py
# ---------------------------------------------------------------------------


def test_bin2c_round_trips_arbitrary_bytes():
    data = bytes(range(256)) * 3 + b"\x00\x01\x02"
    text = bin2c.bin2c_text(data, "_blob")
    assert _parse_bin2c_bytes(text.encode()) == data


def test_bin2c_array_declaration_and_length_constant():
    text = bin2c.bin2c_text(b"\x00\x01\x02", "_x")
    assert text.startswith(
        "const unsigned char _x[3] __attribute__((aligned(4))) = {\r\n"
    )
    assert text.rstrip("\r\n").endswith("const int _x_length = 3;")
    assert "};\r\n\r\nconst int _x_length = 3;" in text  # one blank line before it


def test_bin2c_no_trailing_comma_before_closing_brace():
    text = bin2c.bin2c_text(bytes(range(11)), "_one_line")  # exactly one line
    assert "0x0a\r\n};" in text
    assert "0x0a,\r\n};" not in text


def test_bin2c_wraps_at_eleven_values_per_line():
    text = bin2c.bin2c_text(bytes(range(12)), "_wrap")
    lines = text.split("\r\n")
    first_data_line = lines[1]
    assert first_data_line.count("0x") == 11
    second_data_line = lines[2]
    assert second_data_line.count("0x") == 1


def test_bin2c_empty_input():
    text = bin2c.bin2c_text(b"", "_empty")
    assert _parse_bin2c_bytes(text.encode()) == b""
    assert "_empty[0]" in text
    assert "_empty_length = 0;" in text


def test_bin2c_reproduces_real_resdata_c_byte_for_byte():
    res_bin = TUTORIAL / "tuto1_hw/res/res.bin"
    resdata_c = TUTORIAL / "tuto1_hw/res/resdata.c"
    text = bin2c.bin2c_text(res_bin.read_bytes(), "_resdata")
    assert text.encode() == resdata_c.read_bytes()


def test_bin2c_cli_reproduces_real_resdata_c_file(tmp_path):
    res_bin = TUTORIAL / "tuto1_hw/res/res.bin"
    out_c = tmp_path / "resdata.c"

    rc = bin2c.main([str(res_bin), str(out_c), "_resdata"])

    assert rc == 0
    assert out_c.read_bytes() == (TUTORIAL / "tuto1_hw/res/resdata.c").read_bytes()


def test_bin2c_cli_requires_three_arguments(capsys):
    rc = bin2c.main(["only", "two"])
    assert rc == 1
    assert "usage" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# bincut.py
# ---------------------------------------------------------------------------


def test_parse_int_accepts_hex_and_decimal():
    assert bincut.parse_int("0x1000") == 0x1000
    assert bincut.parse_int("0X7010") == 0x7010
    assert bincut.parse_int("4096") == 4096


def test_bincut_cuts_the_fix_bank_out_of_pg_main_nes_matching_the_real_m_bat():
    # BOOTROM_FIX/m.BAT: bin_catcut PG_main.NES ..\BOOTROM\bootrom_fixr.bin
    #                     -new -r_offset 0x7010 -size 0x1000
    pg_main = TUTORIAL / "BOOTROM_FIX/PG_main.nes"
    chunk = bincut.cut(pg_main, 0x7010, 0x1000)

    assert len(chunk) == 4096
    assert hashlib.md5(chunk).hexdigest().upper() == BOOTROM_FIXR_MD5
    assert chunk == (TUTORIAL / "BOOTROM/bootrom_fixr.bin").read_bytes()


def test_bincut_rejects_negative_offset_or_size(tmp_path):
    p = tmp_path / "f.bin"
    p.write_bytes(b"\x00" * 10)
    with pytest.raises(ValueError):
        bincut.cut(p, -1, 4)
    with pytest.raises(ValueError):
        bincut.cut(p, 0, -1)


def test_bincut_rejects_range_extending_past_eof(tmp_path):
    p = tmp_path / "small.bin"
    p.write_bytes(b"\x00" * 10)
    with pytest.raises(ValueError):
        bincut.cut(p, 5, 10)  # needs 15 bytes, file has 10


def test_bincut_cli_reproduces_bootrom_fixr_bin(tmp_path):
    pg_main = TUTORIAL / "BOOTROM_FIX/PG_main.nes"
    out = tmp_path / "cut.bin"

    rc = bincut.main([str(pg_main), "0x7010", "0x1000", str(out)])

    assert rc == 0
    assert out.read_bytes() == (TUTORIAL / "BOOTROM/bootrom_fixr.bin").read_bytes()


def test_bincut_cli_accepts_decimal_arguments(tmp_path):
    pg_main = TUTORIAL / "BOOTROM_FIX/PG_main.nes"
    out = tmp_path / "cut.bin"

    rc = bincut.main([str(pg_main), "28688", "4096", str(out)])  # 0x7010, 0x1000

    assert rc == 0
    assert out.read_bytes() == (TUTORIAL / "BOOTROM/bootrom_fixr.bin").read_bytes()


def test_bincut_cli_reports_error_and_exits_nonzero(tmp_path, capsys):
    p = tmp_path / "small.bin"
    p.write_bytes(b"\x00" * 4)
    out = tmp_path / "out.bin"

    rc = bincut.main([str(p), "0", "10", str(out)])

    assert rc == 1
    assert "error" in capsys.readouterr().err
    assert not out.exists()


def test_bincut_cli_requires_four_arguments(capsys):
    rc = bincut.main(["a", "b"])
    assert rc == 1
    assert "usage" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# check_fixbank.py
# ---------------------------------------------------------------------------


def test_check_fixbank_tail_matches_the_real_rom():
    tail = check_fixbank.fix_bank_tail(TUTORIAL / "BOOTROM/rom.NES")
    assert tail == (TUTORIAL / "BOOTROM/bootrom_fixr.bin").read_bytes()


def test_check_fixbank_cli_passes_on_the_real_rom():
    rc = check_fixbank.main(
        [
            str(TUTORIAL / "BOOTROM/rom.NES"),
            str(TUTORIAL / "BOOTROM/bootrom_fixr.bin"),
        ]
    )
    assert rc == 0


def test_check_fixbank_cli_fails_on_a_corrupted_rom_copy(tmp_path):
    real = bytearray((TUTORIAL / "BOOTROM/rom.NES").read_bytes())
    real[-1] ^= 0xFF  # last byte of the file is the fix bank's last byte
    corrupt = tmp_path / "corrupt.NES"
    corrupt.write_bytes(bytes(real))

    rc = check_fixbank.main([str(corrupt), str(TUTORIAL / "BOOTROM/bootrom_fixr.bin")])

    assert rc == 1


def test_check_fixbank_cli_fails_on_a_corrupted_reference(tmp_path):
    real = bytearray((TUTORIAL / "BOOTROM/bootrom_fixr.bin").read_bytes())
    real[0] ^= 0xFF
    corrupt_ref = tmp_path / "bootrom_fixr.bin"
    corrupt_ref.write_bytes(bytes(real))

    rc = check_fixbank.main([str(TUTORIAL / "BOOTROM/rom.NES"), str(corrupt_ref)])

    assert rc == 1


def test_check_fixbank_rejects_bad_ines_magic(tmp_path):
    p = tmp_path / "bad.nes"
    p.write_bytes(b"XXXX" + b"\x00" * 100)
    with pytest.raises(ValueError):
        check_fixbank.fix_bank_tail(p)


def test_check_fixbank_rejects_wrong_prg_size(tmp_path):
    header = b"NES\x1a" + bytes([1, 1]) + b"\x00" * 10  # 1 PRG unit = 16 KB, not 32 KB
    p = tmp_path / "wrong.nes"
    p.write_bytes(header + b"\x00" * 16384)
    with pytest.raises(ValueError):
        check_fixbank.fix_bank_tail(p)


def test_check_fixbank_rejects_truncated_prg(tmp_path):
    header = b"NES\x1a" + bytes([2, 0]) + b"\x00" * 10  # declares 32 KB PRG
    p = tmp_path / "short.nes"
    p.write_bytes(header + b"\x00" * 100)  # but far fewer bytes follow
    with pytest.raises(ValueError):
        check_fixbank.fix_bank_tail(p)


def test_check_fixbank_cli_reports_reference_size_mismatch(tmp_path):
    header = b"NES\x1a" + bytes([2, 0]) + b"\x00" * 10
    p = tmp_path / "synth.nes"
    p.write_bytes(header + bytes(32768))
    short_ref = tmp_path / "short.bin"
    short_ref.write_bytes(b"\x00" * 100)

    rc = check_fixbank.main([str(p), str(short_ref)])

    assert rc == 1


def test_check_fixbank_cli_requires_two_arguments(capsys):
    rc = check_fixbank.main(["only-one"])
    assert rc == 1
    assert "usage" in capsys.readouterr().err
