# src/fcpico -- FC PICO platform layer

See `../../FCPICO-PORT.md` for the file list and the changes to shared files, and
`fc-pico/doom/plan/` (02, 04, 05, 06, 08) for the specifications. Files derived from
`src/pico/` keep the GPLv2 headers of their origin; new files are BSD-3-Clause.

The RP2350 build now initializes the cartridge bus and publishes converted
Doom frames from the scanline sink. It still serves the tutorial v1 boot ROM;
the v2 Doom ROM and its NMI are separate work. Input and sound remain stubbed.
The firmware UF2 contains code and the tutorial boot ROM image, but **not**
`doom1.whx`, which must occupy flash address `0x10080000`. Do not treat the
current build as a hardware-validated release.

Task order: P0-T2 (hardware boot check), P0-T3 (host build), P1-T1 (8-bit composition),
P1-T4 (input subset), P1-T5 (sound stubs), P3-T1 (full input), P4-T2 (sound modules).
