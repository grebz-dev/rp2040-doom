;/// @file version.inc
;/// @brief Build stamp of the Doom boot ROM, assembled at $EFF0 (14 characters + NUL).
;///
;/// Replaces the tutorial's wall-clock dbdate.h so builds are reproducible.
;/// Format: 20DOOM-<protocol version, 2 digits>-<build number, 4 digits>.
;/// The leading "20" is mandatory: the fix bank's CHK_ROMVER ($F2B2) only
;/// reflashes after a mismatch when the cartridge stamp starts with "20";
;/// otherwise it prints PICO NOT FOUND and halts.
;/// Bump the build number on purpose only: the cartridge compares these 14 bytes
;/// with its own copy at every boot and a mismatch makes the console reflash itself
;/// (see docs/pages/boot-and-reflash.md in fc-pico).
;/// SPDX-License-Identifier: BSD-3-Clause
	DB "20DOOM-02-0002",0
