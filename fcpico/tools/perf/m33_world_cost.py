#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-3-Clause
"""Count M33 sprite instructions and compare exact results with a host reference.

Requires the project Arm GCC toolchain on PATH and Unicorn. --reference-source
may point to a saved pre-change fcworld.c directory for differential validation.
Instruction counts are deterministic work estimates, not cycles or hardware FPS.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile

from unicorn import Uc, UC_ARCH_ARM, UC_MODE_THUMB, UC_MODE_MCLASS, UC_HOOK_CODE
from unicorn.arm_const import UC_ARM_REG_SP, UC_ARM_REG_LR

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
MEASURE = ("fcworld_raster_actor", "fcworld_frame_build", "fcworld_frame_limit", "fcworld_submit_held", "fcworld_reserve")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--atlas", type=Path, required=True)
    parser.add_argument("--source", type=Path, default=ROOT / "port/video")
    parser.add_argument("--reference-source", type=Path)
    parser.add_argument("--max-instructions", type=int)
    parser.add_argument("--max-irq-masked-instructions", type=int)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    atlas = args.atlas.read_bytes()
    if len(atlas) >= 1024 * 1024:
        parser.error("atlas exceeds driver capacity")
    inc = ["-I", str(ROOT / "port/video"), "-I", str(ROOT / "fcbus")]
    driver = str(HERE / "m33_world_driver.c")
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        host = work / "host"
        ref = args.reference_source or args.source
        subprocess.run(["gcc", "-O2", "-ffunction-sections", "-fdata-sections", "-I", str(ref), *inc,
                        driver, str(ref / "fcworld.c"), "-Wl,--gc-sections", "-o", str(host)],
                       check=True)
        expected = subprocess.check_output([str(host), str(args.atlas)])
        elf = work / "world.elf"
        subprocess.run(["arm-none-eabi-gcc", "-mcpu=cortex-m33", "-mthumb", "-Os",
                        "-march=armv8-m.main+fp+dsp", "-mfloat-abi=softfp", "-mcmse",
                        "-std=gnu11", "-DNDEBUG", "-nostdlib", "-fstack-usage",
                        "-DWORLD_BARE_METAL=1", "-I", str(args.source), *inc,
                        "-ffunction-sections", "-fdata-sections", "-Wl,--gc-sections",
                        "-Wl,-Ttext=0x10000000", "-Wl,-Tdata=0x20000000", "-Wl,-e,entry",
                        driver, str(args.source / "fcworld.c"), "-lc", "-lgcc",
                        "-o", str(elf)], check=True)
        syms = {}
        for line in subprocess.check_output(["arm-none-eabi-nm", "-S", str(elf)], text=True).splitlines():
            parts = line.split()
            if len(parts) == 4:
                syms[parts[3]] = (int(parts[0], 16), int(parts[1], 16))
        binary = work / "text.bin"
        subprocess.run(["arm-none-eabi-objcopy", "-O", "binary", "-j", ".text", "-j", ".rodata",
                        str(elf), str(binary)], check=True)
        code = binary.read_bytes()
        data_binary = work / "data.bin"
        subprocess.run(["arm-none-eabi-objcopy", "-O", "binary", "-j", ".data",
                        str(elf), str(data_binary)], check=True)
        data = data_binary.read_bytes()
        stack = {}
        for usage in work.glob("*.su"):
            for line in usage.read_text().splitlines():
                location, size, _kind = line.split("\t")
                name = location.rsplit(":", 1)[-1]
                if name in MEASURE:
                    stack[name] = int(size)
    mu = Uc(UC_ARCH_ARM, UC_MODE_THUMB | UC_MODE_MCLASS)
    mu.mem_map(0x10000000, 0x100000)
    mu.mem_map(0x20000000, 0x400000)
    mu.mem_write(0x10000000, code)
    if data:
        mu.mem_write(0x20000000, data)
    mu.mem_write(syms["atlas_data"][0], atlas)
    mu.mem_write(syms["atlas_size"][0], len(atlas).to_bytes(4, "little"))
    counts = {name: 0 for name in MEASURE}
    starts = {syms[name][0] & ~1: name for name in MEASURE if name in syms}
    active, completed = [], False

    def hook(uc, address, _size, _user):
        nonlocal completed
        if address == syms["finished"][0] & ~1:
            completed = True
            uc.emu_stop()
            return
        while active and address == active[-1][1]:
            active.pop()
        if address in starts:
            active.append((starts[address], uc.reg_read(UC_ARM_REG_LR) & ~1))
        for name, _return_to in active:
            counts[name] += 1

    mu.hook_add(UC_HOOK_CODE, hook)
    mu.reg_write(UC_ARM_REG_SP, 0x203F0000)
    mu.emu_start(syms["entry"][0] | 1, 0, count=50_000_000)

    def scalar(name):
        return int.from_bytes(mu.mem_read(syms[name][0], 4), "little")

    actual = bytes(mu.mem_read(syms["results"][0], scalar("result_size")))
    exact = completed and not scalar("failed") and actual == expected
    report = {"cases": scalar("cases"), "instructions": counts,
              "stack_bytes_excluding_callees": stack,
              "total_instructions": sum(v for k, v in counts.items() if k != "fcworld_reserve"),
              "irq_masked_instructions": counts["fcworld_reserve"] if "fcworld_reserve" in syms
                                         else counts["fcworld_submit_held"],
              "exact": exact,
              "completed": completed, "failed": scalar("failed"),
              "bytes": len(actual), "expected_bytes": len(expected),
              "compiler": subprocess.check_output(["arm-none-eabi-gcc", "-dumpfullversion"],
                                                  text=True).strip(),
              "source_sha256": hashlib.sha256((args.source / "fcworld.c").read_bytes()).hexdigest(),
              "reference_sha256": hashlib.sha256((ref / "fcworld.c").read_bytes()).hexdigest(),
              "output_sha256": hashlib.sha256(actual).hexdigest(),
              "atlas_sha256": hashlib.sha256(atlas).hexdigest()}
    if actual != expected:
        report["first_difference"] = next((i for i, (a, b) in enumerate(zip(actual, expected))
                                           if a != b), min(len(actual), len(expected)))
    if args.max_instructions is not None:
        report["within_budget"] = report["total_instructions"] <= args.max_instructions
    if args.max_irq_masked_instructions is not None:
        report["irq_masked_within_budget"] = report["irq_masked_instructions"] <= args.max_irq_masked_instructions
    print(json.dumps(report, indent=2))
    if args.output:
        args.output.write_text(json.dumps(report, indent=2) + "\n")
    return 0 if exact and report.get("within_budget", True) and report.get("irq_masked_within_budget", True) else 1


if __name__ == "__main__":
    raise SystemExit(main())
