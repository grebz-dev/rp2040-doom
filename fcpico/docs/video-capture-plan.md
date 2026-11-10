# Video capture correlation plan

Goal: for any frame captured from the console's composite output, know exactly
what the firmware sent: which streamed picture, which native world-sprite
generation, whether the frame waited for or timed out on the console, and what
the host engine renders for the same game tic. This turns "maybe some frame
drop and colour mismatch" into measured, per-frame evidence.

## Why: findings from the first hardware capture

From a serial capture of attract-mode demo playback on the
`perf/world-sprite-switches` firmware (`FCPICO_GRAPHICS=world`):

| Window | fps |
| --- | --- |
| No native generations submitted | ~27 |
| Heavy native traffic | 12-14 |
| After the demo (no gameplay) | 60 |

| Frame-end stage | Average | Worst |
| --- | --- | --- |
| `world_wait` for the console to finish presenting the previous generation | ~23 ms | 50 ms (`WORLD_WAIT_US` timeout) |
| Stream conversion | 7.4 ms | 8.0 ms |
| Native raster (`world_us resolve`) | 0.5 ms | 1.9 ms |
| Native tile build (`world_us build`) | 1.0 ms | 3.3 ms |

- A generation takes up to 10 heartbeats to present (`busy_max=10`). Waiting
  for it, not per-actor work, bounds the frame rate.
- `world_wait` timed out 100 times, matching `errors=100`. Each timeout
  publishes the picture with the *previous* generation's sprites, so a hitch
  and briefly misplaced native sprites are expected. Video should confirm this.
- Likely colour mismatch: the native path never reads Doom's `colormap`
  (sector and distance lighting) or `palette_num` (damage, pickup and suit
  flashes). Streamed sprites are lit and tinted; native sprites always use
  atlas colours. Expected: native sprites too bright in dark areas and missing
  the flashes. This comes from reading the code and has not yet been seen on
  screen.

## Overview

| Step | Where | Output |
| --- | --- | --- |
| 1. Visible frame ID (barcode) | firmware, behind a build flag | ID readable in every captured frame |
| 2. Per-frame serial trace | firmware, same flag | one line per published picture |
| 3. Capture session | test machine (serial + composite video) | video file + timestamped serial log |
| 4. Analysis tools | host scripts | per-frame timeline, hitch/timeout correlation, colour report |
| 5. Fixes, re-measured | firmware | before/after reports from the same pipeline |

Build flag: `FCPICO_FRAME_TRACE=ON` (CMake option, off by default; device and
host). It enables both the barcode and the trace, so a capture always has both.

## 1. Visible frame ID for composite video

Composite has poor horizontal resolution and chroma bleed, so the code uses
large, luma-only blocks.

- **Colours:** NES `$0F` (black) and `$30` (white) only. No chroma, so no
  colour bleed between cells; the largest luma difference gives a clean
  threshold.
- **Cell size:** 16 x 16 NES pixels (2 x 2 tiles). At 256 px across, a
  640-720 px capture gives at least 40 capture pixels per cell horizontally.
- **Layout:** one band of 16 cells, 32 tiles wide and 2 tiles tall, inside the
  title-safe area (not the top or bottom 16 lines, which capture devices and
  TVs often crop). Candidate: the top two tile rows of the streamed 3D view.
  The status bar is drawn natively in the world profile, so the band must be
  in the stream.
- **Encoding (16 cells):**

  | Cells | Meaning |
  | --- | --- |
  | 0 | Start marker: always white |
  | 1 | Calibration: always black |
  | 2-11 | Picture ID, 10 bits (wraps every 1024 pictures, ~17 s at 60 fps) |
  | 12-13 | Low 2 bits of the native generation number |
  | 14 | Timeout flag: this picture reused the previous generation |
  | 15 | Even parity over cells 2-14 |

  Cells 0 and 1 give per-frame black and white levels, so decoding adapts to
  brightness, contrast and capture gain. Parity rejects frames torn mid-field.
- **Native sprite mark (optional, phase 2):** one reserved OAM entry draws an
  8 x 8 white block at an x position chosen by the generation's low 2 bits.
  If the barcode's generation bits disagree with the block's position, the
  console is showing a picture and sprites from different generations. That
  is the timeout case, visible directly.
- **First task:** read the stream format in `fcvideo_convert_staged` /
  `fcvideo_compact_native_text` and the console ROM's nametable layout to find
  where the band can be written after conversion, without breaking the v4
  compaction or the mailbox. Stamp the band into the back buffer just before
  `fcbus_device_publish` in `fcvideo_frame_end`, so it labels exactly what was
  published.

## 2. Per-frame serial trace

One line per published picture, printed after publication so it never delays
the bus:

```
T id=513 tic=1432 us=48211903 wait=22140 tmo=0 gen=37 shown=36 tiles=5 packets=3 adm=3/0/2/1 pal=0 drops=1293
```

| Field | Meaning |
| --- | --- |
| `id` | Picture ID (the barcode's 10 bits, unwrapped) |
| `tic` | `gametic`, to match host replay |
| `us` | `time_us_64()` at publish |
| `wait`, `tmo` | `world_wait` duration and whether it hit `WORLD_WAIT_US` |
| `gen`, `shown` | Generation submitted, and the generation the console was showing |
| `tiles`, `packets` | New tiles and mailbox packets for this generation |
| `adm` | Admitted actors: enemy/dying/pickup/scenery |
| `pal` | Doom palette number (flashes) |
| `drops` | Running count of dropped engine frames |

At 60 lines per second the volume is fine over USB CDC. The `printf` cost
perturbs timing slightly; measure it once by comparing fps with the flag on
and off.

Keep the existing 60-frame summary lines (`video`, `world`, `world_role`,
`world_us`, `world_wait`). They remain the quick overview.

## 3. Capture session (test machine)

### Hardware
- Console composite output into a USB capture device.
- Cartridge USB into the same machine, for serial.

### Video settings
- The NES outputs 240p at about 60.1 Hz, which many capture devices treat as
  480i. Each *field* is then a complete NES frame. Process with field
  separation (`ffmpeg -vf separatefields` or `yadif=1` bob), not a blend
  deinterlace, so every console frame stays a separate image.
- Record at the device's native resolution (typically 720 x 480) and 59.94
  fps or more. Use a lossless or near-lossless codec (FFV1, UTVideo, or
  high-bitrate MJPEG); heavy H.264 compression smears the cell edges.
- Disable the capture software's noise reduction, sharpening and auto gain if
  it has them.

### Serial logging
- Log with a host timestamp on every line, for example a small Python
  `pyserial` script that writes `time.monotonic()` and the line. The barcode
  is the exact link; the host timestamps are a rough cross-check and help
  find the start of each run.
- Start the serial logger first, then video, then power on the console.

### Scenes
1. **Attract-mode demo 1**, full run, untouched (frame rate and hitch
   correlation).
2. **Dark-room pickup:** stand in a dark sector with a pickup and a lit
   neighbour in view; take damage once and collect an item (lighting and
   flash colours, native versus streamed).
3. **Control:** the same demo on `fc-pico` firmware (no barcode needed; for
   fps and hitch comparison only).

### Deliverables
For each scene: the video file, the serial log, the UF2 SHA256, and the
`build-info.txt` from the build.

## 4. Analysis tools (host)

Put scripts in `fcpico/tools/capture/`, run them with the repository venv, and
test them against synthetic barcode frames generated by the same code.

1. **`decode_barcode.py`:** extract frames (fields) with ffmpeg, locate the
   band, threshold each cell against the calibration cells, check parity, and
   output `capture_frame -> picture id, gen bits, timeout bit`.
2. **`align_trace.py`:** join decoded frames with the serial trace on picture
   ID (unwrapped using timestamps). Output a per-capture-frame timeline:
   picture ID, how many fields it stayed on screen, wait, timeout, generation.
3. **Reports:**
   - How long each picture stays on screen (fields per picture) and the
     effective fps over time.
   - Hitches (pictures held for 3 or more fields) and how many coincide with
     `tmo=1`, long `wait`, or high `tiles`/`packets`.
   - Mismatches between generation bits and the native sprite mark (phase 2).
4. **Reference frames:** run `fcpico_doom_host --demo N --lockstep
   --dump-stream --world-dump` and render the stream and world records through
   MesenCE (`run_doom_frame.py`) to get the expected NES picture for each tic.
   Comparing those against captured frames separates "the firmware sent the
   wrong thing" from "composite or capture changed it".
5. **Colour report:** for admitted actors (from the trace and world dumps),
   compare the native sprite's mean luma in the capture with the streamed
   reference and with the sector light level. Confirm or rule out the
   lighting and flash hypothesis.

## 5. Fix candidates (decided by the evidence)

Each candidate is re-measured with the same capture and reports:

- **Generation transport:** fewer new tiles per generation (better reuse,
  smaller budgets on busy frames) and fewer packets, so presenting a
  generation takes fewer heartbeats.
- **Stop blocking the frame on `world_wait`:** publish the picture and let
  native sprites follow (the existing `FCPICO_WORLD_LOOSE` path), trading
  sprite lag for frame rate. Video shows whether the lag is acceptable.
- **Lighting- and flash-aware native palettes:** choose generation palettes
  using the actor's light level and the current Doom palette, within the
  console's sprite palette limits.

## Open questions

- Which capture device and software are on the test machine? Their field
  handling decides the exact ffmpeg settings in step 3.
- Does the band in the top two rows of the 3D view cost anything with the v4
  stream compaction? Resolve this in step 1's first task before choosing the
  final position.
