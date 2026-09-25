# src/fcpico -- FC PICO platform layer

See `../../FCPICO-PORT.md` for the file list and the changes to shared files, and
`fc-pico/doom/plan/` (02, 04, 05, 06, 08) for the specifications. Files derived from
`src/pico/` keep the GPLv2 headers of their origin; new files are BSD-3-Clause.

The RP2350 build now initializes the cartridge bus and publishes converted
Doom frames from the scanline sink. It embeds the Doom v2 console ROM and
serves it to the permanent fix bank's reflash path. The current ROM sends
pad 1 and a zero pad 2 byte. Pad 1 drives the Doom key-event mapper at each
engine tic; pad 2 and sound remain unimplemented. USB serial accepts `bootsel`
followed by Enter to start the next UF2 update with the cartridge installed.
The firmware UF2 contains code and the Doom boot ROM image, but **not**
`doom1.whx`, which must occupy flash address `0x10080000`; merge it with
`doom/tools/whx2uf2.py doom1.whx out.uf2 --firmware fcpico_doom.uf2`. Do not treat the
current build as a hardware-validated release: a version mismatch will make
the console erase and replace its `$8000`-`$EFFF` bank on first boot.

The host runner accepts `--pads FILE` with one numeric pad-1 byte per tic
(for example `0x08` for Up), plus `--warp 1 1` to start E1M1 directly.
The NES bit order is A=`0x80`, B=`0x40`, Select=`0x20`, Start=`0x10`,
Up=`0x08`, Down=`0x04`, Left=`0x02`, Right=`0x01`.

Task order: P0-T2 (hardware boot check), P0-T3 (host build), P1-T1 (8-bit composition),
P1-T4 (input subset), P1-T5 (sound stubs), P3-T1 (full input), P4-T2 (sound modules).
