# SPDX-License-Identifier: BSD-3-Clause
"""fcbus/fcbus_protocol.h's own header comment requires v1 values to never
change: "v1 values ... MUST NEVER CHANGE (tests/protocol asserts equality
against those files)." This test is that tripwire.

It parses the historical v1 definitions straight out of the tutorial project
(never modifying them) and asserts our generated tools/fcpico/protocol.py
agrees with every one of them, following this name mapping:

    FC_COM_BUF_SIZE_V1   <-> FC_COM_BUF_SIZE          (rp_system.h)
    PPU_COUNT_VAL_V1     <-> PPU_COUNT_VAL   (= 15490, rp_system.h)
    VRAM_BUF_BYTES_V1/4  <-> VRAM_BUF_SIZE   (= 4336 words, rp_system.h)
    PF_MAGIC_NO          <-> PF_MAGIC_CODE   (SysPico.asm spelling)

Everything else shares its name across fcpico/fcbus/fcbus_protocol.h,
tutorial_project/tuto1_hw/sys/rp_system.h, tutorial_project/BOOTROM/SysPico.asm
and tutorial_project/BOOTROM/SysEqu.h.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from fcpico import protocol

DOOM_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = DOOM_ROOT.parent
TUTORIAL_ROOT = REPO_ROOT / "fcpico/external/fc-pico/tutorial_project"

RP_SYSTEM_H = TUTORIAL_ROOT / "tuto1_hw" / "sys" / "rp_system.h"
SYS_PICO_ASM = TUTORIAL_ROOT / "BOOTROM" / "SysPico.asm"
SYS_EQU_H = TUTORIAL_ROOT / "BOOTROM" / "SysEqu.h"

_MISSING = [p for p in (RP_SYSTEM_H, SYS_PICO_ASM, SYS_EQU_H) if not p.is_file()]
if _MISSING:
    pytest.skip(
        f"tutorial_project fixtures not found: {_MISSING}", allow_module_level=True
    )


# ---------------------------------------------------------------------------
# Minimal, independent parsers for the tutorial's own source dialects.
#
# These are deliberately separate from tools/gen_protocol.py's parser: this
# test exists to catch drift between two independently-read sources, not to
# exercise the generator (that is tests/protocol/test_generated_fresh.py's
# job). A little duplication here is the point.
# ---------------------------------------------------------------------------


def _c_define_values(text: str) -> dict[str, str]:
    """{NAME: raw value text} for every `#define NAME <value>` line.

    Tolerates the tutorial's variable inter-token whitespace and trailing
    `///<` / `//` comments (splitting on the first "//" strips both styles,
    since "///<" also starts with two slashes). Lines with no value at all,
    such as include guards, are omitted.
    """
    out: dict[str, str] = {}
    define_re = re.compile(r"^\s*#define\s+([A-Za-z_]\w*)\s*(.*)$")
    for line in text.splitlines():
        code, _, _comment = line.partition("//")
        m = define_re.match(code)
        if not m:
            continue
        value = m.group(2).strip()
        if value:
            out[m.group(1)] = value
    return out


def _eval_c_int(expr: str, env: dict[str, int]) -> int:
    """Evaluate a tiny, whitelisted subset of C integer arithmetic.

    Supports `+ - * /`, parentheses, decimal/hex literals, names present in
    `env`, and the literal token `sizeof(uint32_t)` (treated as 4, per the
    task spec for rp_system.h's VRAM_BUF_SIZE). `ast.parse` only builds a
    syntax tree -- it does not execute anything -- which is then walked by
    hand against the whitelist below; anything else raises.
    """
    text = expr.replace("sizeof(uint32_t)", "4")
    tree = ast.parse(text, mode="eval")

    def ev(node: ast.AST) -> int:
        if isinstance(node, ast.Expression):
            return ev(node.body)
        if isinstance(node, ast.Constant) and isinstance(node.value, int):
            return node.value
        if isinstance(node, ast.Name):
            return env[node.id]
        if isinstance(node, ast.BinOp):
            left, right = ev(node.left), ev(node.right)
            if isinstance(node.op, ast.Add):
                return left + right
            if isinstance(node.op, ast.Sub):
                return left - right
            if isinstance(node.op, ast.Mult):
                return left * right
            if isinstance(node.op, ast.Div):
                return left // right
        raise ValueError(f"unsupported C expression: {expr!r}")

    return ev(tree)


def _resolve_c_int(defines: dict[str, str], name: str, env: dict[str, int]) -> int:
    text = defines[name]
    try:
        return int(text, 0)
    except ValueError:
        return _eval_c_int(text, env)


def _enum_values(text: str) -> dict[str, int]:
    """{NAME: value} from `enum{ NAME = 0x2F, ... };`.

    Entries fully commented out with a leading `//` (FP_COM_ACK/NAK in
    rp_system.h) are skipped, matching what the tutorial's C++ actually
    compiles.
    """
    m = re.search(r"enum\s*\{(.*?)\}\s*;", text, re.DOTALL)
    assert m, "could not find the FP_COM_* enum block in rp_system.h"
    entry_re = re.compile(r"^([A-Za-z_]\w*)\s*=\s*([^,]+),")
    out: dict[str, int] = {}
    for raw_line in m.group(1).splitlines():
        line = raw_line.strip()
        if not line or line.startswith("//"):
            continue
        em = entry_re.match(line)
        if em:
            out[em.group(1)] = int(em.group(2).strip(), 0)
    return out


def _nesasm_assign_values(text: str) -> dict[str, int]:
    """{NAME: value} for SysPico.asm's `NAME = $XX` / `NAME = NN` lines."""
    out: dict[str, int] = {}
    eq_re = re.compile(r"^\s*([A-Za-z_]\w*)\s*=\s*(\S+)")
    for line in text.splitlines():
        code, _, _comment = line.partition(";")
        m = eq_re.match(code)
        if not m:
            continue
        name, value_text = m.group(1), m.group(2)
        if value_text.startswith("$"):
            out[name] = int(value_text[1:], 16)
        elif re.fullmatch(r"-?[0-9]+", value_text):
            out[name] = int(value_text)
    return out


def _nesasm_equ_values(text: str) -> dict[str, int]:
    """{NAME: value} for SysEqu.h's `NAME EQU $XX` / `NAME EQU OTHER_NAME` lines."""
    out: dict[str, int] = {}
    equ_re = re.compile(r"^\s*([A-Za-z_]\w*)\s+EQU\s+(\S+)")
    for line in text.splitlines():
        code, _, _comment = line.partition(";")
        m = equ_re.match(code)
        if not m:
            continue
        name, value_text = m.group(1), m.group(2)
        if value_text.startswith("$"):
            out[name] = int(value_text[1:], 16)
        elif value_text.startswith("%"):
            out[name] = int(value_text[1:].replace("_", ""), 2)
        elif re.fullmatch(r"-?[0-9]+", value_text):
            out[name] = int(value_text)
        elif value_text in out:
            out[name] = out[value_text]
    return out


# ---------------------------------------------------------------------------
# Parse the tutorial files once, at import time.
# ---------------------------------------------------------------------------

_RP_TEXT = RP_SYSTEM_H.read_text(encoding="utf-8")
_ASM_TEXT = SYS_PICO_ASM.read_text(encoding="utf-8")
_EQU_TEXT = SYS_EQU_H.read_text(encoding="utf-8")

_RP_DEFINES = _c_define_values(_RP_TEXT)
_FC_COM_BUF_SIZE = int(_RP_DEFINES["FC_COM_BUF_SIZE"], 0)

#: {NAME: int value}, rp_system.h's #defines (atomic ones) plus its enum,
#: plus the two expression-valued names resolved by hand.
RP_SYSTEM: dict[str, int] = {}
for _name, _text in _RP_DEFINES.items():
    try:
        RP_SYSTEM[_name] = int(_text, 0)
    except ValueError:
        pass  # expression-valued (PPU_COUNT_VAL, VRAM_BUF_SIZE); resolved below
RP_SYSTEM.update(_enum_values(_RP_TEXT))
RP_SYSTEM["PPU_COUNT_VAL"] = _resolve_c_int(
    _RP_DEFINES, "PPU_COUNT_VAL", {"FC_COM_BUF_SIZE": _FC_COM_BUF_SIZE}
)
RP_SYSTEM["VRAM_BUF_SIZE"] = _resolve_c_int(
    _RP_DEFINES, "VRAM_BUF_SIZE", {"FC_COM_BUF_SIZE": _FC_COM_BUF_SIZE}
)

#: {NAME: int value} from SysPico.asm's `=` assignments.
SYS_PICO: dict[str, int] = _nesasm_assign_values(_ASM_TEXT)

#: {NAME: int value} from SysEqu.h's `EQU` lines.
SYS_EQU: dict[str, int] = _nesasm_equ_values(_EQU_TEXT)


# ---------------------------------------------------------------------------
# Names shared verbatim between fcbus_protocol.h and rp_system.h.
# ---------------------------------------------------------------------------

RP_SYSTEM_SHARED_NAMES = [
    "PF_COM_NONE",
    "PF_COM_DMOD",
    "PF_COM_FDIN",
    "PF_COM_FDOT",
    "PF_COM_SE",
    "PF_COM_BGM",
    "PF_COM_VRAM",
    "PF_DAT_VRAM",
    "PF_DAT_RAM",
    "PF_DAT_STEP",
    "PF_MAGIC_NO",
    "FC_COM_BUF_SIZE16",
    "PICO_SNDREG",
    "PICO_APU_BUF_SIZE",
    "KEY_A",
    "KEY_B",
    "KEY_SEL",
    "KEY_RUN",
    "KEY_UP",
    "KEY_DOWN",
    "KEY_LEFT",
    "KEY_RIGHT",
    "FP_COM_VER",
    "FP_COM_ROM",
    "FP_COM_LOG",
    "FP_COM_DRQ",
    "FP_COM_DLD",
    "FP_COM_RST",
    "FP_COM_INI",
]


@pytest.mark.parametrize("name", RP_SYSTEM_SHARED_NAMES)
def test_matches_rp_system_h(name: str) -> None:
    assert name in RP_SYSTEM, f"{name} not found in rp_system.h"
    ours = getattr(protocol, name)
    theirs = RP_SYSTEM[name]
    assert ours == theirs, (
        f"{name}: fcbus_protocol.h says {ours:#x} but rp_system.h says {theirs:#x}"
    )


# ---------------------------------------------------------------------------
# Names shared verbatim between fcbus_protocol.h and SysPico.asm.
# ---------------------------------------------------------------------------

SYS_PICO_SHARED_NAMES = [
    "PF_COM_NONE",
    "PF_COM_DMOD",
    "PF_COM_FDIN",
    "PF_COM_FDOT",
    "PF_COM_SE",
    "PF_COM_BGM",
    "PF_COM_VRAM",
    "PF_DAT_VRAM",
    "PF_DAT_RAM",
    "PF_DAT_STEP",
    "FP_COM_ACK",
    "FP_COM_NAK",
    "FP_COM_VER",
    "FP_COM_ROM",
    "FP_COM_LOG",
    "FP_COM_DRQ",
    "FP_COM_DLD",
    "FP_COM_RST",
    "FP_COM_INI",
]


@pytest.mark.parametrize("name", SYS_PICO_SHARED_NAMES)
def test_matches_sys_pico_asm(name: str) -> None:
    assert name in SYS_PICO, f"{name} not found in SysPico.asm"
    ours = getattr(protocol, name)
    theirs = SYS_PICO[name]
    assert ours == theirs, (
        f"{name}: fcbus_protocol.h says {ours:#x} but SysPico.asm says {theirs:#x}"
    )


# ---------------------------------------------------------------------------
# Names shared verbatim between fcbus_protocol.h and SysEqu.h.
# ---------------------------------------------------------------------------

SYS_EQU_SHARED_NAMES = [
    "KEY_A",
    "KEY_B",
    "KEY_SEL",
    "KEY_RUN",
    "KEY_UP",
    "KEY_DOWN",
    "KEY_LEFT",
    "KEY_RIGHT",
]


@pytest.mark.parametrize("name", SYS_EQU_SHARED_NAMES)
def test_matches_sys_equ_h(name: str) -> None:
    assert name in SYS_EQU, f"{name} not found in SysEqu.h"
    ours = getattr(protocol, name)
    theirs = SYS_EQU[name]
    assert ours == theirs, (
        f"{name}: fcbus_protocol.h says {ours:#x} but SysEqu.h says {theirs:#x}"
    )


# ---------------------------------------------------------------------------
# Names that differ between fcbus_protocol.h and the tutorial.
# ---------------------------------------------------------------------------


def test_fc_com_buf_size_v1_matches_rp_system_h() -> None:
    assert protocol.FC_COM_BUF_SIZE_V1 == RP_SYSTEM["FC_COM_BUF_SIZE"] == 64


def test_ppu_count_val_v1_matches_rp_system_h() -> None:
    assert RP_SYSTEM["PPU_COUNT_VAL"] == 15490
    assert protocol.PPU_COUNT_VAL_V1 == RP_SYSTEM["PPU_COUNT_VAL"]


def test_vram_buf_size_matches_rp_system_h_in_words() -> None:
    assert RP_SYSTEM["VRAM_BUF_SIZE"] == 4336
    assert protocol.VRAM_BUF_BYTES_V1 % 4 == 0, "VRAM_BUF_BYTES_V1 must be a whole number of 32-bit words"
    assert protocol.VRAM_BUF_BYTES_V1 // 4 == RP_SYSTEM["VRAM_BUF_SIZE"]


def test_pf_magic_no_matches_sys_pico_asm_pf_magic_code() -> None:
    assert protocol.PF_MAGIC_NO == SYS_PICO["PF_MAGIC_CODE"] == 0xFC
    # The generator must also carry this as an nesasm alias so bootrom
    # sources spelled the old way still assemble.
    assert protocol.ASM_ALIASES["PF_MAGIC_CODE"] == "PF_MAGIC_NO"


# ---------------------------------------------------------------------------
# v2 additions and the shared mailbox tail position (03-protocol-v2.md).
# ---------------------------------------------------------------------------


def test_ppu_count_val_v2() -> None:
    assert protocol.PPU_COUNT_VAL_V2 == 15554


def test_mailbox_offset_is_the_same_tail_position_in_both_versions() -> None:
    assert protocol.VRAM_MAILBOX_OFF_V1 == 15426
    assert protocol.VRAM_MAILBOX_OFF_V2 == 15426
    assert protocol.VRAM_MAILBOX_OFF_V1 == protocol.VRAM_MAILBOX_OFF_V2


# ---------------------------------------------------------------------------
# Zero-page geometry cross-check (SysEqu.h): PICO_SNDREG is a zero-page
# alias for PICO_BUF0 + our PICO_SNDREG offset, and PICO_BUF0 is the base
# our v2 layout calls MBX_ZP_LO.
# ---------------------------------------------------------------------------


def test_pico_sndreg_zero_page_offset() -> None:
    assert SYS_EQU["PICO_BUF0"] == 0x20
    assert SYS_EQU["PICO_BUF10"] == 0x30
    assert SYS_EQU["PICO_SNDREG"] == SYS_EQU["PICO_BUF10"]
    assert SYS_EQU["PICO_BUF10"] == SYS_EQU["PICO_BUF0"] + protocol.PICO_SNDREG


def test_pico_buf0_matches_mbx_zp_lo() -> None:
    assert SYS_EQU["PICO_BUF0"] == protocol.MBX_ZP_LO == 0x20
