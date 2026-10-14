#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-3-Clause
"""Estimate device conversion time of fcvideo_convert_staged.

Builds the converter for Cortex-M33 (-Os, as MinSizeRel), runs one native-HUD
conversion of a real Doom frame in Unicorn and counts executed instructions.
The host-built reference state (tables, staged frame, previous attributes) is
loaded into emulated RAM and the emulated picture bytes are checked against
the host result. Calibration: the previous converter executed 4.73M
instructions for the frame the device converted in 38.2 ms, about 1.21 cycles
per instruction at 150 MHz.

Usage: m33_convert_cost.py --playpal PLAYPAL.bin --frame frameNNNNNN.raw
       [--source DIR containing fcvideo.c]
Frames come from `fcpico_doom_host --dump-8bit DIR`. Needs arm-none-eabi-gcc
and `pip install unicorn` in the virtualenv.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import subprocess
import tempfile

from unicorn import UC_ARCH_ARM, UC_HOOK_CODE, UC_MODE_MCLASS, UC_MODE_THUMB, Uc
from unicorn.arm_const import UC_ARM_REG_SP, UC_ARM_REG_LR

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
CYCLES_PER_INSTRUCTION = 38.2e-3 * 150e6 / 4_731_660
FRAME_BYTES = 256 * 240 + 16
ERR_BYTES, LUT_BYTES = 1024, 16384


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--playpal", type=Path, required=True)
    parser.add_argument("--frame", type=Path, required=True)
    parser.add_argument("--source", type=Path, default=ROOT / "port/video")
    args = parser.parse_args()
    inc = ["-I", str(ROOT / "port/video"), "-I", str(ROOT / "fcbus")]
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        subprocess.run(["gcc", "-O2", "-w", *inc, str(HERE / "m33_state.c"),
                        str(args.source / "fcvideo.c"), str(ROOT / "port/video/fcui.c"),
                        "-lm", "-o", str(work / "state")], check=True)
        subprocess.run([str(work / "state"), str(args.playpal), str(args.frame),
                        str(work / "state.bin")], check=True)
        elf = work / "conv.elf"
        subprocess.run(["arm-none-eabi-gcc", "-mcpu=cortex-m33", "-mthumb", "-Os",
                        "-ffreestanding", "-nostdlib", "-w", *inc,
                        "-Wl,-Ttext=0x10000000", "-Wl,-Tdata=0x20000000", "-Wl,-e,entry",
                        "-ffunction-sections", "-fdata-sections", "-Wl,--gc-sections",
                        "-o", str(elf), str(HERE / "m33_driver.c"),
                        str(args.source / "fcvideo.c"), "-lgcc", "-lm", "-lc"], check=True)
        syms = {}
        nm = subprocess.run(["arm-none-eabi-nm", str(elf)], capture_output=True, text=True)
        for line in nm.stdout.splitlines():
            parts = line.split()
            if len(parts) == 3:
                syms[parts[2]] = int(parts[0], 16)
        subprocess.run(["arm-none-eabi-objcopy", "-O", "binary", "-j", ".text", "-j",
                        ".rodata", str(elf), str(work / "text.bin")], check=True)
        state = (work / "state.bin").read_bytes()
        text = (work / "text.bin").read_bytes()
    offset = 0
    err = state[offset:offset + ERR_BYTES]; offset += ERR_BYTES
    lut = state[offset:offset + LUT_BYTES]; offset += LUT_BYTES
    frame = state[offset:offset + FRAME_BYTES]; offset += FRAME_BYTES
    previous = state[offset:offset + 64]; offset += 64
    want_stream, want_attr = state[offset:-64], state[-64:]

    mu = Uc(UC_ARCH_ARM, UC_MODE_THUMB | UC_MODE_MCLASS)
    mu.mem_map(0x10000000, 0x100000)
    mu.mem_map(0x20000000, 0x200000)
    mu.mem_write(0x10000000, text)
    mu.mem_write(syms["err"], err)
    mu.mem_write(syms["lut"], lut)
    mu.mem_write(syms["frame_in"], frame)
    mu.mem_write(syms["prev_in"], previous)
    mu.mem_write(syms["native_flag"], (1).to_bytes(4, "little"))
    count, counting, completed, return_to = 0, False, False, 0
    target = syms["fcvideo_convert_staged"] & ~1

    def hook(uc, address, _size, _user):
        nonlocal count, counting, completed, return_to
        if address == syms["finished"] & ~1:
            completed = True
            uc.emu_stop()
            return
        if counting and address == return_to:
            counting = False
        if address == target:
            counting = True
            return_to = uc.reg_read(UC_ARM_REG_LR) & ~1
        if counting:
            count += 1

    mu.hook_add(UC_HOOK_CODE, hook)
    mu.reg_write(UC_ARM_REG_SP, 0x201F0000)
    mu.emu_start(syms["entry"] | 1, 0, count=50_000_000)
    stream = bytes(mu.mem_read(syms["stream"], len(want_stream)))
    attr = bytes(mu.mem_read(syms["attr"], 64))
    # The driver leaves the mailbox palette unset; compare the picture only.
    picture = 15426
    exact = completed and count > 0 and stream[:picture] == want_stream[:picture] and attr == want_attr
    ms = count * CYCLES_PER_INSTRUCTION / 150e6 * 1e3
    print(f"instructions {count:,}  estimated {ms:.1f} ms at 150 MHz  "
          f"picture {'exact' if exact else 'MISMATCH'}")
    return 0 if exact else 1


if __name__ == "__main__":
    raise SystemExit(main())
