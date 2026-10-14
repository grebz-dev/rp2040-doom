# SPDX-License-Identifier: BSD-3-Clause
"""
Shared helpers for the L4 PIO program tests.

Every test in this directory exercises the *real* `fcpico/fcbus/fcppu.pio` file -- never a copy --
by assembling it with Adafruit's `adafruit_pioasm` (1.3.8) and running the result through
`rp2040-pio-emulator` (`pioemu`, 0.88.0). `load_program()` below is the single place that reads
the file and knows how to work around the two libraries' quirks; see fcpico/README.md
for the full narrative and the emulator-API facts this was built from.

Quick index of what was verified empirically (see fcpico/README.md for the evidence and the exact
probe transcripts) and is relied on throughout this test suite:

  * `adafruit_pioasm.Program(text)` assembles exactly one `.program` block per call ("Multiple
    programs not supported") -- fcppu.pio's four programs must be extracted and assembled one
    at a time. `load_program()` does this by slicing the file on `.program` boundaries.
  * `.define` / `.define public` directives are not implemented by adafruit_pioasm at all (a
    line starting with `.define` falls through to "Unknown instruction: .define"). The symbolic
    pin names are therefore expanded by plain text substitution before assembling.
  * adafruit_pioasm is case-sensitive and only spells its keywords in lower case. fcppu.pio,
    written for the official (case-insensitive) Raspberry Pi `pioasm`, spells the `jmp` "PIN"
    condition and one `IRQ clear` mnemonic in upper case; both are lower-cased here before
    assembling ("jmp PIN 0" -> `ValueError: Invalid jmp condition 'PIN'`; "IRQ clear 7" ->
    `RuntimeError: Unknown instruction: IRQ`).
  * adafruit_pioasm has no "set" modifier for `irq` (only bare/`wait`/`clear`); `irq set N`
    raises `ValueError: invalid literal for int() with base 0: 'set'`. Dropping the "set" token
    (`irq N`) assembles to the same opcode bits as the default/"set" encoding (mode bits left at
    0), so it is semantically the plain `irq set N` even though adafruit_pioasm cannot parse
    that spelling.
  * `.wrap_target` / `.wrap` *are* implemented by adafruit_pioasm and surface as
    `Program.pio_kwargs["wrap_target"]` / `Program.pio_kwargs["wrap"]`; these map directly onto
    pioemu's `emulate(..., wrap_target=, wrap_top=)` keyword arguments (pioemu's `wrap_top` is
    adafruit's `wrap`).
  * pioemu's `emulate()` has no `in_base`/`in_count` parameter at all: `in pins, N` always reads
    starting at GPIO0 (see `pioemu/instruction_decoder.py`'s `in_sources[0] = read_from_pins`,
    which returns the raw `state.pin_values` word, and `_decode_in`, which never applies an
    offset). `out pins, N` and `out pindirs, N`, by contrast, *do* honour a configurable
    `out_base`/`out_count` (default 0/32) via `emulate()`'s keyword arguments, wired to
    `write_to_pins(out_base, out_count, ...)`.
  * `irq` (set/wait/clear) opcodes are not decoded by either of pioemu's instruction decoders
    (`decoding/instruction_decoder.py` has `# TODO: Add support for MOV, IRQ and SET
    instructions` for the top-level dispatch, and the legacy decoder's IRQ slot is
    `lambda _: None`). Hitting one makes `emulate()`'s generator end immediately with **no
    exception and no further yields** (`emulate()`: `if emulation is None: return`) -- verified
    empirically (see fcpico/README.md) that a 2-instruction program stopped after an `irq` opcode
    yields zero tuples even when `stop_when` asks for more cycles.
  * `wait <pol> irq N` assembles fine but is emulated as plain `wait <pol> gpio N` -- pioemu's
    `WaitInstruction.source` field is decoded but never consulted by
    `InstructionDecoder._decode_wait()`, which always calls `gpio_high`/`gpio_low` on `index`
    regardless of whether the source was `gpio`, `pin` or `irq`. Verified empirically: driving
    GPIO7 high unstalls a `wait 1 irq 7`.
  * `jmp pin, target` branches (`program_counter := target`) exactly when the configured
    `jmp_pin` GPIO reads high, and falls through otherwise -- verified empirically with a
    3-instruction program where the branch and fall-through targets differ.
  * FIFOs are modelled as plain 4-deep `collections.deque`s (`receive_fifo`/`transmit_fifo` on
    `State`); there is no equivalent of `PIO_FIFO_JOIN_RX`/`TX` (no such keyword exists on
    `emulate()`), so autopush/autopull stall at 4 outstanding words even for a program
    configured to join its FIFOs to 8 on real hardware. Tests in this directory keep the number
    of undrained words comfortably below 4.
"""
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List

from pioemu import State

# ---------------------------------------------------------------------------
# Locating and slicing the real fcppu.pio
# ---------------------------------------------------------------------------

FCPPU_PIO = Path(__file__).resolve().parents[2] / "fcbus" / "fcppu.pio"

_DEFINE_RE = re.compile(r"^\s*\.define(?:\s+public)?\s+(\w+)\s+(\S+)\s*$")
_PROGRAM_RE = re.compile(r"^\s*\.program\s+(\w+)\s*$")


def _strip_comment(line: str) -> str:
    """adafruit_pioasm treats ';' as a line comment marker (`line.split(';')[0]`); this also
    quietly disposes of fcppu.pio's Doxygen-style ';///' comment lines, so no special-casing of
    those is needed anywhere else in this module."""
    return line.split(";", 1)[0]


def _read_fcppu_pio_text() -> str:
    return FCPPU_PIO.read_text()


def _extract_defines(full_text: str) -> Dict[str, str]:
    """Collect every `.define [public] NAME VALUE` in the file (fcppu.pio keeps them all in the
    preamble before the first `.program`, but this scans the whole file so it keeps working if
    that ever changes) and return {NAME: decimal-string-value}."""
    defines: Dict[str, str] = {}
    for raw_line in full_text.splitlines():
        match = _DEFINE_RE.match(_strip_comment(raw_line))
        if match:
            name, value = match.group(1), match.group(2)
            defines[name] = str(int(value, 0))
    return defines


def _split_programs(full_text: str) -> Dict[str, str]:
    """Return {program_name: raw block text} for every `.program` block in the file. A block
    runs from its `.program NAME` line up to (but not including) the next `.program` line, or
    end of file for the last one -- truncated early if a `% c-sdk { ... %}` block (pico-sdk only,
    meaningless to adafruit_pioasm) is encountered, so it is stripped.
    Today that block trails the last `.wrap` of the last program, outside every block already,
    but the truncation is kept as a guard against the file growing another program after it."""
    lines = full_text.splitlines()
    boundaries = [
        (i, m.group(1))
        for i, line in enumerate(lines)
        if (m := _PROGRAM_RE.match(_strip_comment(line)))
    ]
    if not boundaries:
        raise ValueError(f"No .program blocks found in {FCPPU_PIO}")

    blocks: Dict[str, str] = {}
    for idx, (start, name) in enumerate(boundaries):
        end = boundaries[idx + 1][0] if idx + 1 < len(boundaries) else len(lines)
        block_lines: List[str] = []
        for candidate in lines[start:end]:
            if candidate.strip().startswith("%"):
                break  # start of a `% c-sdk { ... %}` block: not PIO assembly, discard the rest
            block_lines.append(candidate)
        if name in blocks:
            raise ValueError(f"Duplicate .program {name!r} in {FCPPU_PIO}")
        blocks[name] = "\n".join(block_lines)
    return blocks


def _apply_defines(text: str, defines: Dict[str, str]) -> str:
    for name, value in defines.items():
        text = re.sub(rf"\b{re.escape(name)}\b", value, text)
    return text


def _fix_pioasm_case_quirks(text: str) -> str:
    """Work around adafruit_pioasm 1.3.8's case-sensitive, lower-case-only keyword matching --
    see the module docstring and fcpico/README.md for the empirical evidence behind each substitution."""
    text = re.sub(r"\bPIN\b", "pin", text)
    text = re.sub(r"\bIRQ\b", "irq", text)
    text = re.sub(r"\birq\s+set\b", "irq", text, flags=re.IGNORECASE)
    return text


def _preprocess(name: str, blocks: Dict[str, str], defines: Dict[str, str]) -> str:
    if name not in blocks:
        raise KeyError(f"No .program {name!r} in {FCPPU_PIO} (found: {sorted(blocks)})")
    text = _apply_defines(blocks[name], defines)
    text = _fix_pioasm_case_quirks(text)
    return text


@dataclass(frozen=True)
class AsmProgram:
    """An assembled `.program` block plus the metadata `pioemu.emulate()` needs alongside the
    raw opcodes."""

    name: str
    opcodes: List[int]
    wrap_target: int
    wrap_top: int
    public_labels: Dict[str, int] = field(default_factory=dict)
    preprocessed_source: str = ""  # what was actually handed to adafruit_pioasm, for debugging


def load_program(name: str) -> AsmProgram:
    """Assemble one `.program` block (`fcppu_w`, `fcppu_r`, `fcppu_dir` or `fcppu_rna`) from the
    real `fcpico/fcbus/fcppu.pio`, working around the adafruit_pioasm quirks documented above.
    Always re-reads and re-slices the real file -- there is no copy of the program text anywhere
    in this test suite."""
    import adafruit_pioasm  # local import: keep a missing dependency from breaking collection

    full_text = _read_fcppu_pio_text()
    defines = _extract_defines(full_text)
    blocks = _split_programs(full_text)
    text = _preprocess(name, blocks, defines)

    program = adafruit_pioasm.Program(text)
    opcodes = list(program.assembled)

    return AsmProgram(
        name=name,
        opcodes=opcodes,
        wrap_target=program.pio_kwargs.get("wrap_target", 0),
        wrap_top=program.pio_kwargs.get("wrap", len(opcodes) - 1),
        public_labels=dict(program.public_labels),
        preprocessed_source=text,
    )


def pin_defines() -> Dict[str, int]:
    """The `.define`/`.define public` pin assignments from the real fcppu.pio, as ints. Tests
    use this instead of hard-coding GPIO numbers so they track the real file if it ever changes."""
    return {name: int(value) for name, value in _extract_defines(_read_fcppu_pio_text()).items()}


# ---------------------------------------------------------------------------
# NOP substitute for the unsupported `irq` instruction (test_fcppu_r.py)
# ---------------------------------------------------------------------------

# `mov y, y` (opcode 0xA042, confirmed by assembling ".program t\nmov y, y\n"). Confirmed inert
# under pioemu 0.88 by direct emulation (see fcpico/README.md): with X/Y/pins/pindirs preloaded to
# non-zero/non-default values, running this opcode for several cycles changes only the program
# counter and clock -- X, Y, pin_values, pin_directions, ISR, OSR and both FIFOs are untouched.
NOP_OPCODE = 0xA042


def patch_opcode(opcodes: List[int], index: int, replacement: int) -> List[int]:
    """Return a copy of `opcodes` with the instruction at `index` replaced. Used to neutralise
    fcppu_r's `irq set IRQ_DIR`, which pioemu cannot execute at all (see module docstring)."""
    patched = list(opcodes)
    patched[index] = replacement
    return patched


# ---------------------------------------------------------------------------
# Driving GPIO pins over time
# ---------------------------------------------------------------------------
#
# pioemu's *only* mechanism for changing input pins over time is the `input_source` argument to
# `emulate()`: a callable invoked once per emulated clock cycle, before that cycle's instruction
# is decoded, with either the current `State` or the current `state.clock` (chosen by inspecting
# the callable's type hints -- an unannotated parameter is treated as `int` clock with a logged
# warning). Its return value is a 32-bit word; per `emulate()`'s source, only the bits at GPIO
# positions where `pin_directions` is 0 (i.e. configured as inputs) actually take effect --
# direction-1 (output) bits keep whatever the state machine last wrote. Every program in this
# suite runs with `pin_directions == 0` throughout (none of them touch `pindirs`), so the whole
# word returned here lands in `pin_values` verbatim each cycle.
#
# `timeline_source()` turns a plain per-cycle list of 32-bit words into such a callable: cycle
# `state.clock` reads `timeline[state.clock]`, and the timeline's last entry holds for any cycle
# beyond its length (so callers only need to spell out the interesting prefix).


def timeline_source(timeline: List[int]):
    """Build a pioemu `input_source` from a list of per-cycle 32-bit GPIO words (index == clock
    cycle). Held at its last entry for any cycle past the end of the list."""
    if not timeline:
        raise ValueError("timeline must contain at least one entry")

    def _source(state: State) -> int:
        index = state.clock if state.clock < len(timeline) else len(timeline) - 1
        return timeline[index]

    return _source
