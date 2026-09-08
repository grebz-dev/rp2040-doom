# FC PICO port -- engine-side notes

This fork branch (`claude/doom-fc-pico-nes-2bb1bx`, based on `rp2`) is consumed as the git
submodule `doom/rp2040-doom` of `grebz-dev/fc-pico`, where the full development plan lives
(`doom/plan/`). This file lists what changes **in this repository** to support the FC PICO
target: an RP2350 inside a Famicom cartridge that feeds the console's PPU a pattern-table byte
stream (256x240, 2 bits per pixel plus a per-16x16-block palette) and plays sound on the
console's APU. There is no VGA, no I2S, no USB host and no I2C network on that target.

Nothing here is implemented yet. Work items are referenced by the task IDs of
`fc-pico/doom/plan/10-workplan.md`.

## Build integration (P0-T2)

- `CMakeLists.txt` (top level): wrap `include(pico_sdk_import.cmake)`,
  `include(pico_extras_import.cmake)`, `project(...)` and `pico_sdk_init()` in
  `if (NOT FCPICO_SUPERBUILD)`. When set, the parent project (`fc-pico/doom/CMakeLists.txt`)
  has already initialised the SDK; `pico-extras` is not required (only the VGA targets use
  `pico_scanvideo_dpi` / `pico_audio_i2s`).
- `src/CMakeLists.txt`: add `add_doom_tiny(_fcpico render_newhope)` under
  `if (FCPICO_SUPERBUILD)`, linking `common_fcpico`, `fcbus`, `fcapu` and defining:
  `FCPICO=1 NO_USE_ENDDOOM=1 USE_PICO_NET=0 USB_SUPPORT=0 TINY_WAD_ADDR=0x10080000
  PICO_CORE1_STACK_SIZE=0x1000 FCPICO_AUTO_SAVENAME=1`. Start from `doom_tiny`'s flag set and
  keep `tiny_settings` (`WHD_SUPER_TINY`) so `doom1.whx` is the data format; evaluate dropping
  `DEMO1_ONLY` (RP2350 has room).
- `src/pico/CMakeLists.txt` is untouched. `src/fcpico/CMakeLists.txt` defines
  `common_fcpico` from `src/pico/{i_system.c,i_timer.c,i_glob.c,stubs.c,picoflash.c,blit.S}`
  plus the new files below. `pico_scanvideo_dpi`, `hardware_i2c` and `tinyusb_host` are not
  linked.
- Host build: `PICO_PLATFORM=host` must work without `pico_host_sdl`; `src/fcpico/host_main.c`
  provides `main()` with `--whx`, `--demo`, `--frames`, `--lockstep`, `--dump-8bit`,
  `--dump-stream`, `--pads` (P0-T3). The shim for `multicore`/`sem` on the host comes from
  `fc-pico/doom/sim/host_shim/`.

## New platform files under `src/fcpico/` (GPLv2 where derived from `src/pico/`)

| File | Derived from | What changes |
|------|--------------|--------------|
| `i_video_fcpico.c` | `src/pico/i_video.c` | No `scanvideo`. Scanline composition (`scanline_func_*`, `draw_vpatch`, overlays, wipe) writes **8-bit palette indices** into a 320-byte line buffer instead of 16-bit RGB; `palette[]` lookups and the interpolator-based `palette8to16` go away; `shared_pal` becomes `uint8_t`. `new_frame_stuff()` keeps the frame-flip/overlay/wipe logic and records `next_pal` for the console palette instead of rebuilding `palette[]`. `I_InitGraphics` launches `core1()` which installs the `fcbus` ISR and a `LOW_PRIO_IRQ` handler that calls `fcvideo_convert_frame()` (fc-pico side) whenever a heartbeat arrives and a new frame is ready. Text mode (ENDOOM) is not ported (`NO_USE_ENDDOOM=1`). The `stbar` XIP-stream DMA trick is dropped unless measured to matter. Keep `__no_inline_not_in_flash_func` on everything that runs while `VIDEO_TYPE_SAVING` (flash programming) is active. See `fc-pico/doom/plan/04-video.md`. |
| `i_input_fcpico.c` | `src/pico/i_input.c` | Keep the UART "SDL event forwarder" path for bring-up; add `fcinput_poll()` called from `I_StartTic()`: latched controller bytes (from `fcbus`) -> edge detection -> `D_PostEvent` with Doom keys; tap/hold logic for B (use vs strafe) and Select (next weapon vs automap); always-run; cheat sequences. See `plan/05-input.md`. |
| `i_sound_fcpico.c` | `src/pico/i_picosound.c` (interface only) | `sound_fcpico_module` and `music_fcpico_module` forwarding to the `fcapu` APU register sequencer (fc-pico side): `StartSound` -> `fcapu_sfx_start(id, vol)`, `PlaySong` -> `fcapu_music_play(stream)`, `UpdateSound` -> `fcapu_pump()`. No ADPCM, no emu8950, no `pico_audio_i2s`. See `plan/06-audio.md`. |
| `i_main_fcpico.c` | `src/i_main.c` | Clock/voltage setup for the cartridge (150 MHz initially; overclock is a later task), no I2S `bi_decl`, no `piconet_init()`. Alternatively `#if FCPICO` in `i_main.c` if the diff stays small. |
| `host_main.c` | new | host entry point described above. |
| `fcpico_video_sink.h` | new | the seam: `fcvideo_line_sink(int y, const uint8_t *line320)` and `fcvideo_frame_begin/end(next_pal, video_type)`, implemented on the fc-pico side. |

## Small shared-file changes (behind `#if FCPICO`)

- `src/doom/m_menu.c`: `FCPICO_AUTO_SAVENAME` -- accept a generated save name without entering
  text-entry mode (P3-T2).
- `src/doom/d_main.c` / `src/i_sound.c`: register the fcpico sound/music modules when `FCPICO`.
- `src/pico/i_system.c`: `I_Quit` -> `watchdog_reboot` (already `restart()`), and the zone base
  computation must tolerate `fcbus`/`fcvideo` statics; if `__end__` crosses the short-pointer
  window (`SHORTPTR_BASE + 0x40000`), those statics move to a dedicated section above
  `0x20070000` in a copy of `memmap_doom.ld` for rp2350 (P1-T3, risk R9 in the plan).
- `src/pd_render.cpp`: no functional change expected; `USE_CORE1_FOR_*` stays. If the
  converter's IRQ on core 1 measurably slows the visplane pass, revisit (risk R13).

## What the fc-pico side provides to the engine

- `fcbus`: heartbeat ISR, stream buffers, mailbox, controller latch (`fcbus_pads()`), protocol.
- `fcvideo`: `fcvideo_convert_frame()`, `fcvideo_line_sink()`, palette sets, attribute
  selection.
- `fcapu`: the APU sequencer and its data.

## Verification for this repository's changes

- `doom_tiny` (VGA) and `chocolate-doom` must still build exactly as before (`FCPICO_SUPERBUILD`
  unset): CI in fc-pico builds them from the submodule as a regression check.
- `fcpico_doom` links for rp2350 with flash end below `0x10080000`.
- `fcpico_doom_host --demo 1 --frames 600` is deterministic.
