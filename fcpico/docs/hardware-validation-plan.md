# Hardware validation plan: serial trace and video capture

Goal: validate the current firmware on the NES with per-picture evidence,
instead of 60-frame serial summaries alone. For every field the console shows,
the session establishes which picture the firmware sent, what the firmware
recorded for it, and what the host engine renders at the same moment of the
demo.

This plan updates `plan/video-capture-correlation`
(`fcpico/docs/video-capture-plan.md` on that branch) for the firmware on
`integration/claude-2026-11-17` and later. It includes the generation queue
and abandon, lit native sprites, per-map background palettes and automatic
sprite scope. Steps 1–4 are implemented on `feat/hw-validation-trace`. Step 5
needs the hardware.

## Baseline: the last serial capture

From `Capture.log` (attract-mode demos, `perf/world-sprite-switches`
firmware, before this work):

| Measure | Value |
| --- | --- |
| Published fps during demo play (60-frame windows) | 12.4–21.3, 27 with few actors |
| Stream conversion | 7.4 ms average, 8.0 ms worst |
| `world_wait` for the console to present the previous generation | 22.8 ms average, 50 ms worst (the timeout) |
| `world_wait` timeouts (= `world errors`) | 100 in 1,328 waits |
| Heartbeats to present one generation (`busy_max`) | 10 |

On hardware, conversion is 7.4 ms, not the 38 ms in `KNOWN-ISSUES.md`. The
frame rate is bounded by waiting for the console to present the generation,
which matches the cosimulation finding.

## What changed since that capture, and what to check

| Change | Hypothesis | Evidence that confirms it |
| --- | --- | --- |
| Abandon (generation queue) | Each wait timeout showed a dropped frame's sprites over the previous picture, which looked like flashes and desyncs | No shown-out-of-order pictures, and hitch sheets show no sprites from a different picture |
| Lit native sprites (atlas v3) | Native sprites no longer look too bright in dark sectors | Reference sheets in dark areas: native sprites match the brightness of streamed neighbours |
| Per-map palettes | Brown and red walls look closer to Doom, and a switch costs one hold at level start | `palette_key_changes` lines up with level starts. One hold of about 11 fields (180 ms) per switch, and no other holds |
| Integer table builder | Boot and level start are faster | `[palette] build_us` is about 150,000–190,000 |

## Implemented

### 1. Frame-ID barcode (firmware, `FCPICO_FRAME_TRACE=ON`)

`fcvideo_stamp_trace` (`port/video/fcvideo.c`) overwrites lines 16–31,
x=16–239, with 28 cells of 8 x 16 NES pixels. The cells are black (`$0F`) or
white (`$30`), in the HUD sub-palette, which is the same on every map:

| Cells | Meaning |
| --- | --- |
| 0 | Start marker: white |
| 1 | Calibration: black |
| 2–13 | Picture ID, 12 bits (the publish counter; wraps every 4,096) |
| 14–17 | World generation submitted with the picture, low 4 bits |
| 18 | The world wait timed out for this picture |
| 19–25 | `leveltime`, low 7 bits (tics since the level or demo began) |
| 26 | Even parity over cells 2–25 |
| 27 | End marker: white |

- **Why this layout:**
  - Luma-only, 8-pixel cells survive composite chroma bleed and blur.
  - The two white markers and the black one give a black and white level on
    every field, and they fix the scale.
  - The band sits below the 8 lines that TVs and capture devices often crop,
    and above the v4-compacted text row.
- **`leveltime` rather than `gametic`:** the console reaches a demo through
  the title screen, so `gametic` differs from a host `--demo` run.
  `leveltime` is the same for the same moment of a demo.
- **Damage flashes:** during heavy red flashes the whole palette, including
  the HUD sub-palette, tints towards red. Then white and black can come out the
  same, and those fields read as undecodable rather than wrong.

### 2. Per-picture serial line (same flag)

The firmware prints one line after each publish, so the line never delays the
bus:

```
T id=513 tic=1432 lt=320 us=48211903 conv=7437 wait=22140 tmo=0 drops=1293 gen=37 shown=36 oam=9 pkt=3 tiles=5 q=0 ab=0 pal=0 key=5 err=0
```

| Field | Meaning |
| --- | --- |
| `id` | Picture ID (the barcode carries the low 12 bits) |
| `tic`, `lt` | `gametic`, `leveltime` |
| `us` | `time_us_64()` after publish |
| `conv` | Conversion and stamp time for this picture |
| `wait`, `tmo` | World wait for this picture, and whether it timed out |
| `drops` | Running count of rendered frames dropped |
| `gen`, `shown` | Generation submitted with this picture, and the one the console showed at publish |
| `oam` | OAM entries in the submitted generation |
| `pkt`, `tiles` | World packets and new CHR tiles since the previous picture |
| `q`, `ab` | Generations queued and abandoned since the previous picture |
| `pal`, `key` | Doom palette (flashes), and the per-map palette set (0 = fixed) |
| `err` | Running world errors (stalls and failed submits) |

The 60-frame summaries (`video`, `world`, `world_wait`, `[palette]`, …) are
unchanged.

### 3. Host tools (`fcpico/tools/capture/`, tested in `tests/tools/test_capture.py`)

| Tool | Does |
| --- | --- |
| `trace_log.py record [--port P] -o session.log` | Logs serial with host timestamps. It waits for the cartridge to enumerate, reconnects after resets, and never writes to the port |
| `decode_video.py CAPTURE -o fields.csv` | Splits video into fields with ffmpeg, finds the band by itself (`--save-calibration` / `--calibration`), and reads every field |
| `analyze.py fields.csv session.log -o report.json` | Joins fields to trace lines. Reports effective fps, fields per picture, hitches and what the replacing picture recorded, unseen and out-of-order pictures, barcode/trace mismatches, and palette switches |
| `reference.py CAPTURE fields.csv HOST_DIR --log session.log [--report report.json] -o sheet.png` | Sheets of captured fields next to the host picture at the same `leveltime` |

The tests cover the following:
- The C encoder matches the Python decoder.
- A C-stamped stream reads back in the HUD palette.
- A simulated composite capture (scaled 2.58x, offset, blurred, noisy,
  gain-shifted) self-calibrates and decodes without errors.
- The analyzer attributes a hitch to its timeout across the 4,096 wrap.
- The parser reads the firmware's exact line format.
- A host trace build decodes all 400 pictures of demo 1, and their IDs are
  consecutive.

### 4. Builds

```sh
# Hardware candidate with tracing (separate build directory):
FCPICO_AUDIO_WAD=... FCPICO_WORLD_WAD=... BUILD_DIR=$PWD/.build/fcpico-device-trace \
  FCPICO_EXTRA_CMAKE_ARGS=-DFCPICO_FRAME_TRACE=ON fcpico/tools/build_device.sh
# Host reference streams with the same barcode:
cmake -S fcpico -B build-trace -G Ninja -DPICO_PLATFORM=host -DFCPICO_WORLD_WAD=... \
  -DFCPICO_FRAME_TRACE=ON && cmake --build build-trace --target doom_tiny_fcpico
build-trace/rp2040-doom/src/fcpico_doom_host --whx doom1.whx --demo 1 --frames 2000 \
  --lockstep --dump-stream host-d1/stream --world-dump host-d1/world
```

The flag is off by default. The default image, without the barcode, is the
release candidate.

## 5. Session procedure (hardware)

### Capture machine setup

```sh
git clone -b integration/claude-2026-11-17 https://github.com/grebz-dev/rp2040-doom.git
cd rp2040-doom
python3 -m venv fcpico/.venv && fcpico/.venv/bin/pip install -r fcpico/tools/requirements.txt
ffmpeg -version && ffprobe -version     # install ffmpeg if missing
```

- **Not in git:** the firmware images, the WAD and the host reference
  streams. Copy the two UF2s (trace and default) from the build machine, and
  check them against the SHA-256s from the build.
  `reference.py` also needs a host trace build's `--dump-stream` directory.
  Copy it over, or build it on the capture machine (needs pico-sdk and the
  WAD; see step 4).
- **Running the tools:** run them from the clone, for example
  `fcpico/.venv/bin/python fcpico/tools/capture/trace_log.py record -o session.log`.
- **Serial on Linux:** the user needs access to `/dev/ttyACM*` (the
  `dialout` group). On Windows, pass `--port COMn` if auto-detection finds
  nothing.

### Equipment and settings

- **Connections:**
  - The console's composite output goes into a USB capture device.
  - The cartridge's USB goes into the same machine.
  - Install ffmpeg and use the repository venv (`pyserial` is already in it).
- **Capture:**
  - Record at native resolution (typically 720 x 480), 59.94 fps or more.
  - Use a lossless or near-lossless codec (FFV1, UTVideo, or high-bitrate
    MJPEG).
  - Turn off noise reduction, sharpening and auto gain.
  - Most devices treat 240p as 480i; `decode_video.py` splits fields by
    default. Use `--frames` if the device delivers progressive 240p.
- **Start order:** start the serial recorder, then the video, then power on
  the console.

### Runs

1. **Calibration (2 min):** run the trace image in the attract loop.
   - Run `decode_video.py --save-calibration cal.json`.
   - Proceed when at least 95% of non-flash fields decode.
2. **Attract loop, 10 minutes, untouched:** demos 1–3 on E1M5, E1M3 and E1M7,
   which covers palette switches at each level.
   - Run `analyze.py`, then `reference.py --report`, to get the hitch sheets.
3. **Trace overhead control:** run the default image (no barcode) for the same
   10 minutes, with the serial summaries only. Compare the `video fps` lines
   with run 2. If the trace build is more than about 5% slower, the per-picture
   `printf` costs too much and run 2's fps is pessimistic.
4. **New game on E1M1:** start from the menu.
   - Find the dark-sector enemies near the start: lit sprites.
   - Take damage and collect an item: flashes. Native sprites are known not
     to tint; record how this looks.
   - Exit the level: palette switch at the intermission and at E1M2.
5. **Soak:** an attract loop of 30 minutes or more on the trace image. Look for
   out-of-order pictures, mismatches and the hold distribution.
   `KNOWN-ISSUES.md` notes that no long soak has been done.

Keep for each run:
- The video file and `session.log`.
- `fields.csv` and `report.json`.
- The UF2 SHA-256 and `build-info.txt`.

### Pass criteria

- `shown_out_of_order` is empty and `barcode_trace_mismatches` is empty.
  - Out-of-order pictures would mean the console showed an older picture
    again.
  - Mismatches would mean decoding errors or a lost serial line.
- In hitch sheets, no native sprites appear from a different picture than the
  background, especially for hitches with `tmo=1` (the abandon fix).
- Effective fps is at least the baseline's 12.4–21.3 range per window.
  `world_wait` timeouts are no more than 100 per 1,328 waits.
- Each `palette_key_changes` entry is at a level start or end. The hold there
  matches `[palette] build_us`, and there are no other holds of that length.
- Native sprites in dark areas are no brighter than the streamed picture
  around them.

## Not implemented (decide after the first session)

- **Native sprite generation mark:** a reserved OAM entry whose position
  encodes the shown generation. It would detect sprite/picture mismatches
  without visual review, but costs one of the 64 sprites and a ROM-side
  agreement. Add it only if the hitch sheets are ambiguous.
- **Automatic colour report** (mean luma of native sprites against the
  reference): composite gain and black level make absolute comparisons weak.
  Visual sheets come first.

## Open questions

- The capture device and its field handling decide `--frames` and the ffmpeg
  filter.
- Is the USB serial `printf` per picture cheap enough? Run 3 answers this.
