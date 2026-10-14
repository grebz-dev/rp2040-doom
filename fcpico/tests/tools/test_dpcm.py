# SPDX-License-Identifier: BSD-3-Clause
"""Tests for tools/audio/dpcm.py (the NTSC DMC rate table, linear resampling
and the 1-bit delta encoder/decoder) and tools/audio/dpcm_pack.py (the DPCM
bank layout for $C000-$ECFF).
"""

from __future__ import annotations

import json

import numpy as np
import pytest

from audio import dpcm, dpcm_pack

# ---------------------------------------------------------------------------
# RATE_TABLE_HZ
# ---------------------------------------------------------------------------

EXPECTED_RATES_HZ = (
    4181.71,
    4709.93,
    5264.04,
    5593.04,
    6257.95,
    7046.35,
    7919.35,
    8363.42,
    9419.86,
    11186.1,
    12604.0,
    13982.6,
    16884.6,
    21306.8,
    24858.0,
    33143.9,
)


def test_rate_table_has_sixteen_ntsc_entries():
    assert dpcm.RATE_TABLE_HZ == EXPECTED_RATES_HZ
    assert len(dpcm.RATE_TABLE_HZ) == 16


# ---------------------------------------------------------------------------
# resample_linear
# ---------------------------------------------------------------------------


def test_resample_linear_identity_at_matching_rates():
    src = np.array([10.0, 20.0, 30.0, 40.0, 50.0])
    out = dpcm.resample_linear(src, 8000, 8000)
    assert np.allclose(out, src)


def test_resample_linear_output_length_follows_the_rate_ratio():
    out = dpcm.resample_linear(np.zeros(1000), 22050, 11025)
    assert len(out) == 500


def test_resample_linear_upsamples_and_interpolates_midpoints():
    src = np.array([0.0, 10.0])
    out = dpcm.resample_linear(src, 1, 2)
    # n_dst = round(2 * 2/1) == 4; source indices sampled are 0, 0.5, 1, 1.5
    # (the last clamped to the final source sample).
    assert len(out) == 4
    assert out[0] == pytest.approx(0.0)
    assert out[1] == pytest.approx(5.0)
    assert out[2] == pytest.approx(10.0)
    assert out[3] == pytest.approx(10.0)


def test_resample_linear_clamps_beyond_source_edges():
    src = np.array([5.0, 5.0, 100.0])
    out = dpcm.resample_linear(src, 3, 30)
    assert out[0] == pytest.approx(5.0)
    assert out[-1] == pytest.approx(100.0)


def test_resample_linear_empty_input_is_empty_output():
    out = dpcm.resample_linear(np.zeros(0), 1000, 2000)
    assert len(out) == 0


def test_resample_linear_rejects_nonpositive_rates():
    with pytest.raises(ValueError):
        dpcm.resample_linear(np.zeros(4), 0, 100)
    with pytest.raises(ValueError):
        dpcm.resample_linear(np.zeros(4), 100, -1)


# ---------------------------------------------------------------------------
# to_target_levels
# ---------------------------------------------------------------------------


def test_to_target_levels_maps_full_scale_0_255_to_0_127():
    src = np.array([0, 255], dtype=np.uint8)
    out = dpcm.to_target_levels(src, 1000, 1000)
    assert list(out) == [0, 127]


def test_to_target_levels_rounds_and_clips():
    src = np.array([128], dtype=np.uint8)
    out = dpcm.to_target_levels(src, 1000, 1000)
    assert out[0] == round(128 * 127 / 255)
    assert 0 <= out[0] <= 127


# ---------------------------------------------------------------------------
# encode_dpcm: the $4013 "16n + 1" length rule
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("n_samples", [0, 1, 5, 16, 17, 100, 257, 4000])
def test_encode_dpcm_length_is_always_16n_plus_1(n_samples):
    rng = np.random.default_rng(1)
    samples = rng.integers(0, 256, size=n_samples, dtype=np.uint8)
    data = dpcm.encode_dpcm(samples, 11025, rate_index=9)
    assert len(data) >= 1
    assert (len(data) - 1) % 16 == 0


def test_encode_dpcm_empty_input_is_the_minimal_single_byte():
    data = dpcm.encode_dpcm(np.zeros(0, dtype=np.uint8), 11025, rate_index=9)
    assert len(data) == 1


def test_encode_dpcm_rejects_out_of_range_rate_index():
    with pytest.raises(ValueError):
        dpcm.encode_dpcm(np.zeros(4, dtype=np.uint8), 11025, rate_index=16)
    with pytest.raises(ValueError):
        dpcm.encode_dpcm(np.zeros(4, dtype=np.uint8), 11025, rate_index=-1)


# ---------------------------------------------------------------------------
# decode_dpcm: LSB-first bit order and +/-2 clamped level tracking
# ---------------------------------------------------------------------------


def test_decode_dpcm_reads_bits_lsb_first():
    data = bytes([0b00000001])  # only bit 0 set
    out = dpcm.decode_dpcm(data, start_level=64)
    # bit 0 (read first) is 1: one step up, then seven steps down as the
    # remaining (higher) bits, all 0, are read in order.
    assert list(out) == [66, 64, 62, 60, 58, 56, 54, 52]


def test_decode_dpcm_clamps_at_127_and_0():
    up = dpcm.decode_dpcm(bytes([0xFF] * 4), start_level=120)
    assert up.max() <= 127
    assert up[-1] == 127

    down = dpcm.decode_dpcm(bytes([0x00] * 4), start_level=5)
    assert down.min() >= 0
    assert down[-1] == 0


def test_decode_dpcm_length_is_eight_times_the_byte_count():
    out = dpcm.decode_dpcm(bytes([0xAA, 0x55, 0x00]))
    assert len(out) == 24


def test_decode_dpcm_step_size_is_exactly_two():
    out = dpcm.decode_dpcm(bytes([0b00000011]), start_level=64)  # bits 0,1 == 1
    assert list(out[:2]) == [66, 68]


# ---------------------------------------------------------------------------
# encode -> decode round trip and RMS error bound
# ---------------------------------------------------------------------------


def test_rms_error_is_small_for_a_slow_signal_at_matching_rate():
    rate_index = 9
    sr = dpcm.RATE_TABLE_HZ[rate_index]
    t = np.arange(2000)
    sig = (128 + 100 * np.sin(2 * np.pi * 80 * t / sr)).astype(np.uint8)

    dmc = dpcm.encode_dpcm(sig, sr, rate_index)
    target = dpcm.to_target_levels(sig, sr, sr)
    decoded = dpcm.decode_dpcm(dmc)
    err = dpcm.rms_error(target, decoded)

    assert err < 10.0


def test_rms_error_degrades_for_a_much_faster_signal():
    rate_index = 9
    sr = dpcm.RATE_TABLE_HZ[rate_index]
    t = np.arange(2000)

    slow = (128 + 100 * np.sin(2 * np.pi * 80 * t / sr)).astype(np.uint8)
    fast = (128 + 100 * np.sin(2 * np.pi * 2000 * t / sr)).astype(np.uint8)

    def rms_for(sig):
        dmc = dpcm.encode_dpcm(sig, sr, rate_index)
        target = dpcm.to_target_levels(sig, sr, sr)
        decoded = dpcm.decode_dpcm(dmc)
        return dpcm.rms_error(target, decoded)

    assert rms_for(fast) > rms_for(slow)


def test_rms_error_near_zero_for_silence():
    sig = np.full(500, 128, dtype=np.uint8)
    rate_index = 9
    sr = dpcm.RATE_TABLE_HZ[rate_index]
    dmc = dpcm.encode_dpcm(sig, sr, rate_index)
    target = dpcm.to_target_levels(sig, sr, sr)
    decoded = dpcm.decode_dpcm(dmc)
    assert dpcm.rms_error(target, decoded) < 3.0


def test_rms_error_empty_overlap_is_zero():
    assert dpcm.rms_error(np.zeros(0), np.zeros(0)) == 0.0


def test_rms_error_compares_only_the_overlap():
    a = np.array([1.0, 2.0, 3.0])
    b = np.array([1.0, 2.0])
    assert dpcm.rms_error(a, b) == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# dpcm_pack: bank layout addresses
# ---------------------------------------------------------------------------


def test_layout_first_sample_starts_at_c000():
    packed = dpcm_pack.layout_samples([dpcm_pack.DpcmSample("A", b"\x00" * 17, 9)])
    assert packed[0].addr == dpcm_pack.BASE_ADDR == 0xC000
    assert packed[0].offset == 0
    assert packed[0].addr_field == 0x00
    assert packed[0].len_field == 0x01  # (17 - 1) >> 4
    assert packed[0].rate == 9


def test_layout_aligns_each_sample_to_64_bytes():
    samples = [
        dpcm_pack.DpcmSample("A", b"\x00" * 17, 9),  # ends at 17, not 64-aligned
        dpcm_pack.DpcmSample("B", b"\x00" * 33, 5),  # must start at 64
    ]
    packed = dpcm_pack.layout_samples(samples)
    assert packed[1].offset == 64
    assert packed[1].addr == 0xC000 + 64
    assert packed[1].addr_field == 1
    assert packed[1].len_field == (33 - 1) >> 4


def test_layout_rejects_rate_out_of_range():
    with pytest.raises(ValueError):
        dpcm_pack.layout_samples([dpcm_pack.DpcmSample("A", b"\x00" * 17, 16)])
    with pytest.raises(ValueError):
        dpcm_pack.layout_samples([dpcm_pack.DpcmSample("A", b"\x00" * 17, -1)])


def test_layout_rejects_length_not_of_16n_plus_1_form():
    with pytest.raises(ValueError):
        dpcm_pack.layout_samples([dpcm_pack.DpcmSample("A", b"\x00" * 16, 9)])
    with pytest.raises(ValueError):
        dpcm_pack.layout_samples([dpcm_pack.DpcmSample("A", b"", 9)])


def test_layout_budget_overflow_names_the_offending_sample():
    # BUDGET + 1 bytes, kept in 16n+1 form (BUDGET is a multiple of 16).
    assert dpcm_pack.BUDGET % 16 == 0
    huge = dpcm_pack.DpcmSample("HUGE", bytes(dpcm_pack.BUDGET + 1), 9)
    with pytest.raises(dpcm_pack.DpcmBudgetError, match="HUGE"):
        dpcm_pack.layout_samples([huge])


def test_layout_budget_overflow_triggered_purely_by_64_byte_alignment():
    # First sample ends with 31 bytes of raw budget left -- enough for a
    # 17-byte second sample *without* alignment -- but 64-byte alignment
    # pushes the second sample's start exactly to the limit, leaving no
    # room at all.
    first = dpcm_pack.DpcmSample("FIRST", bytes(11489), 9)  # 16*718 + 1
    second = dpcm_pack.DpcmSample("SECOND", bytes(17), 9)  # 16*1 + 1
    assert (len(first.data) - 1) % 16 == 0
    assert dpcm_pack.BUDGET - len(first.data) == 31  # would fit unaligned

    with pytest.raises(dpcm_pack.DpcmBudgetError, match="SECOND"):
        dpcm_pack.layout_samples([first, second])


def test_layout_exactly_at_the_budget_boundary_is_accepted():
    # BUDGET (11520) - 1 is not itself a multiple of 16, so build the
    # largest valid 16n+1 length that still fits exactly inside the budget.
    n = (dpcm_pack.BUDGET - 1) // 16
    largest_fit = n * 16 + 1
    assert largest_fit <= dpcm_pack.BUDGET
    sample = dpcm_pack.DpcmSample("FIT", bytes(largest_fit), 9)
    packed = dpcm_pack.layout_samples([sample])
    assert packed[0].offset + packed[0].length <= dpcm_pack.BUDGET


# ---------------------------------------------------------------------------
# dpcm_pack: bank image, tables, and identifier sanitising
# ---------------------------------------------------------------------------


def test_build_bank_zero_fills_alignment_gaps_and_places_data():
    samples = [
        dpcm_pack.DpcmSample("A", b"\x01" * 17, 9),
        dpcm_pack.DpcmSample("B", b"\x02" * 17, 9),
    ]
    packed = dpcm_pack.layout_samples(samples)
    bank = dpcm_pack.build_bank(samples, packed)

    assert len(bank) == packed[-1].offset + packed[-1].length
    assert bank[:17] == b"\x01" * 17
    assert bank[17:64] == b"\x00" * (64 - 17)
    assert bank[64:81] == b"\x02" * 17


def test_build_bank_empty_sample_list():
    assert dpcm_pack.build_bank([], []) == b""


def test_render_inc_and_header_formatting():
    packed = dpcm_pack.layout_samples([dpcm_pack.DpcmSample("FOO", b"\x00" * 17, 3)])

    inc_text = dpcm_pack.render_inc(packed)
    assert "DPCM_FOO_ADDR EQU $00" in inc_text
    assert "DPCM_FOO_LEN EQU $01" in inc_text
    assert "DPCM_FOO_RATE EQU $3" in inc_text

    header_text = dpcm_pack.render_header(packed)
    assert "SPDX-License-Identifier: BSD-3-Clause" in header_text
    assert "#define DPCM_FOO_ADDR 0x00" in header_text
    assert "#define DPCM_FOO_LEN 0x01" in header_text
    assert "#define DPCM_FOO_RATE 0x3" in header_text
    assert "DPCM_TABLE_COUNT 1" in header_text
    assert "{ DPCM_FOO_ADDR, DPCM_FOO_LEN, DPCM_FOO_RATE }, /* FOO */" in header_text


def test_render_functions_handle_no_samples():
    assert dpcm_pack.render_inc([]) == ""
    header_text = dpcm_pack.render_header([])
    assert "DPCM_TABLE_COUNT 0" in header_text


def test_sanitize_name_uppercases_and_replaces_non_identifier_chars():
    assert dpcm_pack.sanitize_name("door-open 1") == "DOOR_OPEN_1"
    assert dpcm_pack.sanitize_name("pistol") == "PISTOL"


# ---------------------------------------------------------------------------
# dpcm_pack: manifest loading and CLI
# ---------------------------------------------------------------------------


def test_load_manifest_resolves_file_paths_relative_to_the_manifest(tmp_path):
    sample_dir = tmp_path / "samples"
    sample_dir.mkdir()
    (sample_dir / "kick.dmc").write_bytes(b"\x00" * 17)
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps([{"name": "Kick", "file": "samples/kick.dmc", "rate": 4}])
    )

    samples = dpcm_pack.load_manifest(manifest)
    assert len(samples) == 1
    assert samples[0].name == "KICK"
    assert samples[0].rate == 4
    assert samples[0].data == b"\x00" * 17


def test_load_positional_uses_uppercased_filename_stem_as_name(tmp_path):
    p = tmp_path / "door-open.dmc"
    p.write_bytes(b"\x00" * 17)
    samples = dpcm_pack.load_positional([str(p)], rate=7)
    assert samples[0].name == "DOOR_OPEN"
    assert samples[0].rate == 7


def test_cli_writes_bank_and_tables(tmp_path):
    f1 = tmp_path / "a.dmc"
    f1.write_bytes(b"\x00" * 17)
    outdir = tmp_path / "out"

    rc = dpcm_pack.main([str(f1), "--rate", "9", "--outdir", str(outdir)])

    assert rc == 0
    assert (outdir / "dpcm_bank.bin").read_bytes() == b"\x00" * 17
    assert "DPCM_A_ADDR" in (outdir / "dpcm_table.inc").read_text()
    assert "DPCM_TABLE_H" in (outdir / "dpcm_table.h").read_text()


def test_cli_reports_budget_overflow_and_exits_nonzero(tmp_path, capsys):
    big = tmp_path / "big.dmc"
    big.write_bytes(bytes(dpcm_pack.BUDGET + 1))

    rc = dpcm_pack.main([str(big), "--outdir", str(tmp_path / "out")])

    assert rc == 1
    assert "BIG" in capsys.readouterr().err


def test_cli_requires_files_or_manifest(capsys):
    rc = dpcm_pack.main([])
    assert rc == 1
    assert "error" in capsys.readouterr().err
