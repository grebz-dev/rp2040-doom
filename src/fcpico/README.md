# src/fcpico -- FC PICO platform layer

See `../../FCPICO-PORT.md` for the file list and the changes to shared files, and
`fc-pico/doom/plan/` (02, 04, 05, 06, 08) for the specifications. Files derived from
`src/pico/` keep the GPLv2 headers of their origin; new files are BSD-3-Clause.

The P0-T2 RP2350 skeleton links and prints a serial bring-up marker. Its video,
input and sound files supply engine interfaces but do not publish frames, read
controllers or play audio yet. `i_picosound.h` and `picoflash.h` keep platform
headers local without putting the VGA tree on the Pico SDK USB include path.

Task order: P0-T2 (hardware boot check), P0-T3 (host build), P1-T1 (8-bit composition),
P1-T4 (input subset), P1-T5 (sound stubs), P3-T1 (full input), P4-T2 (sound modules).
