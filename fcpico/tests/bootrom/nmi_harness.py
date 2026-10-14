"""
SPDX-License-Identifier: BSD-3-Clause

Reusable py65-based harness for measuring 6502 cycle counts of a Famicom
boot ROM's vertical-blank (NMI) handler.

Built and tested against py65 1.2.0 (py65.devices.mpu6502.MPU,
py65.memory.ObservableMemory). See fcpico/README.md for how the
numbers are defined, what is and isn't modelled, and how to point this at
fcpico/bootrom/out/doom.nes once that ROM exists.

This module knows nothing tutorial- or Doom-specific beyond:

  * the fixed NMI trampoline address (ROM_NMI_ENTRY = $ED00, `jmp NMI`) that
    both boot ROMs are contractually required to share with the fix bank; and
  * the FC-to-console mailbox shape documented in docs/pages/protocol.md
    (magic byte at offset 1, APU (register, value) pairs from offset 16,
    terminated by a byte with bit 7 set, capped at 24 pairs).

Point NmiHarness at any 32 KB mapper-0, no-CHR PRG image built on that
contract and it will measure that image's NMI handler the same way.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple, Union

from py65.devices.mpu6502 import MPU
from py65.memory import ObservableMemory

__all__ = [
    "load_ines",
    "build_mailbox",
    "cycle_attribute_name",
    "NmiHarness",
    "NmiResult",
    "cycle_table",
    "DEFAULT_RAM_PRESET",
    "NMI_ENTRY",
    "NTSC_VBLANK_CYCLES",
    "ENTRY_CYCLES",
    "DMA_STALL_CYCLES",
    "SENTINEL_RETURN",
    "PF_MAGIC_CODE",
    "MAILBOX_APU_MAX_PAIRS",
    "NMI_FLG",
    "PALFADE_VAL",
    "PAL_CHG_FG",
    "FLG_2000",
    "FLG_2001",
    "KEY_NEW",
    "PICO_COM",
    "NMI_CALL_ADR_HI",
]

# --------------------------------------------------------------------------
# iNES loading
# --------------------------------------------------------------------------

INES_MAGIC = b"NES\x1a"
INES_HEADER_SIZE = 16
INES_TRAINER_SIZE = 512
PRG_BANK_SIZE = 16384


def load_ines(path: Union[str, Path]) -> bytes:
    """Return the PRG-ROM bytes of an iNES (``.nes``) file.

    Only the header fields needed to locate PRG-ROM are interpreted (bank
    count, trainer flag). CHR-ROM and anything after it is ignored, which is
    fine here: this cartridge is mapper 0 with no CHR-ROM (the RP2350 is the
    CHR device -- see docs/pages/hardware.md).
    """
    path = Path(path)
    data = path.read_bytes()
    if len(data) < INES_HEADER_SIZE or data[:4] != INES_MAGIC:
        raise ValueError(f"{path}: not an iNES ROM (missing 'NES\\x1a' header)")
    header = data[:INES_HEADER_SIZE]
    prg_banks = header[4]
    flags6 = header[6]
    has_trainer = bool(flags6 & 0x04)
    offset = INES_HEADER_SIZE + (INES_TRAINER_SIZE if has_trainer else 0)
    prg_size = prg_banks * PRG_BANK_SIZE
    prg = data[offset : offset + prg_size]
    if len(prg) != prg_size:
        raise ValueError(
            f"{path}: truncated PRG-ROM (header promises {prg_size} bytes, "
            f"found {len(prg)})"
        )
    return prg


# --------------------------------------------------------------------------
# Mailbox construction (cartridge -> console direction; docs/pages/protocol.md)
# --------------------------------------------------------------------------

PF_MAGIC_CODE = 0xFC
MAILBOX_MAGIC_OFFSET = 1
MAILBOX_APU_OFFSET = 16  # PICO_BUF10 / PICO_SNDREG: zero page $30 in the tutorial
MAILBOX_APU_MAX_PAIRS = 24  # NMI's replay loop hard-caps at $30 = 48 bytes
APU_TERMINATOR = 0xFF


def build_mailbox(
    apu_pairs: Sequence[Tuple[int, int]] = (),
    *,
    size: int = 64,
    magic: int = PF_MAGIC_CODE,
) -> bytes:
    """Build one cartridge-to-console mailbox payload.

    Byte 0 is left unused (0, the vestigial PF_COM_SE slot), byte 1 carries
    the magic/validity sentinel, bytes 2..15 are the command list (left at
    PF_COM_NONE / 0, i.e. an empty command list), and bytes 16.. hold
    ``apu_pairs`` as (register, value) byte pairs terminated by 0xFF -- the
    v1, 64-byte layout from docs/pages/protocol.md. Pairs beyond what
    ``size`` can hold are silently dropped, matching
    ``rp_system::setPF_APU()``'s own overflow behaviour; with the default
    ``size=64`` that is ``MAILBOX_APU_MAX_PAIRS`` (24) pairs, the same limit
    the NMI handler's own replay loop enforces independently.
    """
    mailbox = bytearray(size)
    if size > MAILBOX_MAGIC_OFFSET:
        mailbox[MAILBOX_MAGIC_OFFSET] = magic & 0xFF

    offset = MAILBOX_APU_OFFSET
    for reg, value in apu_pairs:
        if offset + 1 >= size:
            break
        mailbox[offset] = reg & 0xFF
        mailbox[offset + 1] = value & 0xFF
        offset += 2
    if offset < size:
        mailbox[offset] = APU_TERMINATOR
    return bytes(mailbox)


# --------------------------------------------------------------------------
# Default RAM preset -- addresses from tutorial_project/BOOTROM/SysEqu.h
# --------------------------------------------------------------------------

NMI_FLG = 0xB3
PALFADE_VAL = 0x0308
PAL_CHG_FG = 0xB4
FLG_2000 = 0xB0
FLG_2001 = 0xB1
KEY_NEW = 0x85
PICO_COM = 0x60
NMI_CALL_ADR_HI = 0xBF  # NMI_CALL_ADR ($BE/$BF) high byte; 0 disables the user hook

#: Merged over caller-supplied overrides in `NmiHarness.run_nmi`; caller
#: values win. Chosen so the handler takes its "normal frame" path: not
#: re-entrant, no fade in progress (so sprite DMA runs), no pending palette
#: upload, default PPUCTRL/PPUMASK shadows, no queued command byte, no user
#: NMI hook installed.
DEFAULT_RAM_PRESET: Dict[int, int] = {
    NMI_FLG: 0x00,
    PALFADE_VAL: 0x00,
    PAL_CHG_FG: 0x00,
    FLG_2000: 0x88,  # FLG_PPU2000 (SysEqu.h): NMI on, BG $0000, sprites $1000, +1
    FLG_2001: 0x1E,  # FLG_PPU2001 (SysEqu.h): BG + sprites shown, incl. left 8px
    KEY_NEW: 0x00,
    PICO_COM: 0x00,
    NMI_CALL_ADR_HI: 0x00,
}

# --------------------------------------------------------------------------
# Cycle-counter attribute detection
# --------------------------------------------------------------------------

# py65 1.2.0's MPU.step()/nmi()/irq() accumulate into `processorCycles` (see
# py65/devices/mpu6502.py). Checked at runtime, rather than hard-coded,
# so a differently-named attribute in some other py65 version fails loudly
# instead of silently reporting 0 for every measurement.
_CYCLE_ATTR_CANDIDATES = ("processorCycles", "cycles", "total_cycles")


def cycle_attribute_name(mpu: MPU) -> str:
    """Return the MPU attribute name that holds the running cycle count."""
    for name in _CYCLE_ATTR_CANDIDATES:
        if hasattr(mpu, name):
            return name
    raise AttributeError(
        "py65's MPU exposes none of the expected cycle-counter attributes "
        f"({', '.join(_CYCLE_ATTR_CANDIDATES)}); inspect the installed py65 "
        "version (`python3 -c \"import py65,pathlib;print(pathlib.Path(py65.__file__).parent)\"`) "
        "and add its attribute name to _CYCLE_ATTR_CANDIDATES."
    )


# --------------------------------------------------------------------------
# Hardware constants
# --------------------------------------------------------------------------

PRG_BASE = 0x8000
PRG_SIZE = 0x8000
RTI_OPCODE = 0x40
NMI_ENTRY = 0xED00  # ROM_NMI_ENTRY (PG_main.asm): `jmp NMI`, fixed by the fix-bank contract
ENTRY_CYCLES = 7  # 6502 NMI recognition + vector fetch; step() never executes this
DMA_STALL_CYCLES = 513  # $4014 OAM DMA; py65 only counts the 4-cycle `sta` itself
NTSC_VBLANK_CYCLES = 2273  # 20 scanlines x 341 dots / 3 dots-per-cycle
MAX_STEPS = 200_000  # guards against a handler that never reaches RTI
SENTINEL_RETURN = 0xBEEF  # fake return PC pushed before entering the handler
STACK_TOP = 0xFD

WriteEvent = Tuple[int, int, int]  # (cycle, address, value)


@dataclasses.dataclass
class NmiResult:
    """Everything measured from one simulated NMI, $ED00 through RTI."""

    raw_cycles: int
    """``mpu.processorCycles`` from the first instruction at $ED00 (the
    `jmp NMI`) through RTI, inclusive -- i.e. exactly what py65 counted,
    with no correction. Does not include `entry_cycles`."""

    entry_cycles: int
    """Constant 7: the real 6502's NMI-recognition + vector-fetch overhead.
    py65's `step()` never executes this (there is no instruction for it), so
    it cannot appear in `raw_cycles`; it is reported separately instead."""

    extra_cycles: int
    """513 for every $4014 write observed (OAM DMA stall). py65's `step()`
    only ever counts the 4-cycle `sta $4014` itself; see fcpico/README.md."""

    corrected_cycles: int
    """`raw_cycles + extra_cycles`: the DMA-stall-corrected total."""

    total_cycles: int
    """`corrected_cycles + entry_cycles`: the full real-hardware-equivalent
    cost of the interrupt, from the NMI line's falling edge through RTI's
    last cycle. Compare *this* against a real vblank budget such as
    `NTSC_VBLANK_CYCLES`."""

    critical_section_cycles: Optional[int]
    """Cycle count (same 0-based-at-$ED00 basis as `writes`) at the second
    write to $2005, i.e. once both scroll-register writes have landed and
    nothing that follows can corrupt the next frame's rendering. `None` if
    the handler never writes $2005 twice."""

    writes: List[WriteEvent]
    """Every write to $2000-$2007 or $4000-$4017, in execution order, as
    (cycle, address, value). `cycle` is `mpu.processorCycles` measured at
    the *start* of the instruction performing the write (py65 only updates
    the counter after an instruction retires, so this is a slight
    approximation -- see fcpico/README.md), 0-based at the `jmp NMI` fetch; add
    `entry_cycles` for a number referenced to the real NMI line edge."""

    apu_writes: List[Tuple[int, int]]
    """(address, value) for just the $4000-$4013 writes, i.e. the decoded
    APU-register replay, in execution order."""

    reads_2007: int
    """Number of `lda $2007`-style reads observed, dummy read included."""

    mailbox: bytes
    """The mailbox bytes this run was fed."""

    final_pc: int
    """`mpu.pc` immediately after RTI executes. Equals `SENTINEL_RETURN`
    when the handler's stack usage was balanced (every push this run made
    has a matching pop) -- a basic sanity check that the simulated handler
    did not run off into the weeds."""

    memory: bytes
    """Final 64 KB CPU address-space snapshot.

    This makes protocol effects inspectable instead of limiting the harness
    to cycle and I/O traces. Callers can assert on zero-page mailbox copies,
    state flags, stack contents, or any other RAM location touched by the
    handler. ROM and memory-mapped I/O locations are included as observed by
    py65; only ordinary RAM locations should be treated as persistent state.
    """


class NmiHarness:
    """Runs one boot ROM's NMI handler under py65 and measures its cost.

    Builds a fresh 64 KB address space and MPU for every `run_nmi()` call,
    so consecutive calls never see each other's RAM state. The PRG image
    itself (32 KB, mapper 0, no CHR -- `$8000`-`$FFFF` direct-mapped, no
    bank switching) is decoded once, in `__init__`.
    """

    def __init__(self, prg: bytes):
        if len(prg) != PRG_SIZE:
            raise ValueError(
                f"expected a {PRG_SIZE}-byte (32 KB) mapper-0 PRG-ROM, got "
                f"{len(prg)} bytes -- load_ines() returns PRG only, and this "
                "harness has no bank switching for anything larger"
            )
        self._prg = bytes(prg)

    def _new_memory(self) -> ObservableMemory:
        subject = [0] * 0x10000
        subject[PRG_BASE : PRG_BASE + PRG_SIZE] = self._prg
        return ObservableMemory(subject=subject)

    def run_nmi(
        self,
        mailbox: Optional[bytes] = None,
        ram_preset: Optional[Dict[int, int]] = None,
    ) -> NmiResult:
        """Simulate one call to the NMI handler at $ED00, through RTI.

        ``mailbox`` defaults to ``build_mailbox()`` (magic byte set, empty
        command list, no APU pairs). ``ram_preset`` is merged over
        `DEFAULT_RAM_PRESET` (caller values win) and written before the
        handler starts.
        """
        if mailbox is None:
            mailbox = build_mailbox()

        mem = self._new_memory()
        mpu = MPU(memory=mem, pc=0x0000)
        cycle_attr = cycle_attribute_name(mpu)

        writes: List[WriteEvent] = []
        counters = {"reads_2007": 0, "extra_cycles": 0}

        def on_read_2002(_address: int) -> int:
            # Real hardware clears the vblank flag on read; every read this
            # harness ever makes during one NMI wants to see it set, so
            # "always $80" (the spec this harness was built to) already has
            # that effect without a stateful PPU model.
            return 0x80

        def on_read_2007(_address: int) -> int:
            n = counters["reads_2007"]
            counters["reads_2007"] = n + 1
            if n == 0:
                return 0x00  # stale PPU read-buffer byte; must be discarded
            index = n - 1
            return mailbox[index] if index < len(mailbox) else 0x00

        def log_write(address: int, value: int) -> None:
            writes.append((getattr(mpu, cycle_attr), address, value))
            if address == 0x4014:
                counters["extra_cycles"] += DMA_STALL_CYCLES
            return None  # do not override the value actually stored

        mem.subscribe_to_read([0x2002], on_read_2002)
        mem.subscribe_to_read([0x2007], on_read_2007)
        mem.subscribe_to_write(range(0x2000, 0x2008), log_write)
        mem.subscribe_to_write(range(0x4000, 0x4018), log_write)

        preset = dict(DEFAULT_RAM_PRESET)
        if ram_preset:
            preset.update(ram_preset)
        for address, value in preset.items():
            mem[address] = value

        # Reproduce the CPU's own interrupt-entry sequence by hand: push
        # PCH, PCL, then P (real hardware also sets the I flag here, which
        # nothing this handler does depends on), then vector to $ED00
        # directly rather than reading $FFFA/$FFFB -- for this ROM they are
        # the same address (see rom.NES's vectors), and going straight to
        # ROM_NMI_ENTRY is what the fix-bank contract actually guarantees.
        # `ENTRY_CYCLES` is what real hardware spends recognising the
        # interrupt and fetching that vector; `step()` never executes it
        # (there is no instruction for it), so it never lands in
        # `mpu.processorCycles` and is reported on `NmiResult` separately.
        mpu.sp = STACK_TOP
        status = (mpu.p & ~MPU.BREAK) | MPU.UNUSED
        mpu.stPushWord(SENTINEL_RETURN)
        mpu.stPush(status)
        mpu.pc = NMI_ENTRY
        setattr(mpu, cycle_attr, 0)

        steps = 0
        while True:
            opcode = mem[mpu.pc]
            if opcode == RTI_OPCODE:
                mpu.step()  # run RTI itself so its 6 cycles are counted
                break
            mpu.step()
            steps += 1
            if steps > MAX_STEPS:
                raise RuntimeError(
                    f"NMI handler did not reach RTI within {MAX_STEPS} "
                    f"steps (stuck at PC=${mpu.pc:04X}); {len(writes)} "
                    "writes were logged before giving up"
                )

        raw_cycles = getattr(mpu, cycle_attr)
        extra_cycles = counters["extra_cycles"]
        corrected_cycles = raw_cycles + extra_cycles
        total_cycles = corrected_cycles + ENTRY_CYCLES

        critical_section_cycles = None
        seen_2005 = 0
        for cycle, address, _value in writes:
            if address == 0x2005:
                seen_2005 += 1
                if seen_2005 == 2:
                    critical_section_cycles = cycle
                    break

        apu_writes = [
            (address, value)
            for _cycle, address, value in writes
            if 0x4000 <= address <= 0x4013
        ]

        return NmiResult(
            raw_cycles=raw_cycles,
            entry_cycles=ENTRY_CYCLES,
            extra_cycles=extra_cycles,
            corrected_cycles=corrected_cycles,
            total_cycles=total_cycles,
            critical_section_cycles=critical_section_cycles,
            writes=writes,
            apu_writes=apu_writes,
            reads_2007=counters["reads_2007"],
            mailbox=bytes(mailbox),
            final_pc=mpu.pc,
            memory=bytes(mem[address] for address in range(0x10000)),
        )


# --------------------------------------------------------------------------
# Reporting helper shared by the tutorial and (eventually) Doom test suites
# --------------------------------------------------------------------------


def cycle_table(
    harness: NmiHarness,
    apu_pair_counts: Sequence[int] = (0, 8, 16, 24),
    ram_preset: Optional[Dict[int, int]] = None,
    *,
    dma_off_value: int = 0x01,
) -> List[dict]:
    """Run ``harness`` over every (APU pair count) x (sprite DMA on/off)
    combination and return one row per combination, in the shape both the
    tutorial and (eventually) Doom JSON reports use.

    "sprite DMA on/off" is driven through `PALFADE_VAL`, which is what the
    *tutorial* NMI keys its `$4014` write on (see SysNMI.asm); a future
    Doom-specific caller that has no such switch can pass a `ram_preset`
    that pins whatever condition its own NMI uses instead, or ignore the
    "off" rows.
    """
    rows = []
    for pairs in apu_pair_counts:
        pair_values = [(i % 0x13, (i * 7 + 3) & 0xFF) for i in range(pairs)]
        mailbox = build_mailbox(pair_values)
        for dma_on in (True, False):
            preset = dict(ram_preset or {})
            preset[PALFADE_VAL] = 0x00 if dma_on else dma_off_value
            result = harness.run_nmi(mailbox, preset)
            rows.append(
                {
                    "apu_pairs": pairs,
                    "sprite_dma": "on" if dma_on else "off",
                    "raw_cycles": result.raw_cycles,
                    "corrected_cycles": result.corrected_cycles,
                    "total_cycles": result.total_cycles,
                    "critical_section_cycles": result.critical_section_cycles,
                    "entry_cycles": result.entry_cycles,
                    "extra_cycles": result.extra_cycles,
                    "reads_2007": result.reads_2007,
                    "num_writes": len(result.writes),
                }
            )
    return rows
