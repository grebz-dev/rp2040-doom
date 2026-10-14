#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-3-Clause
"""respack.py -- pure-Python replacement for the Windows-only binlink.exe.

    respack.py <list.lst> <res_id.h> <out.bin> <id_base>

Reads a binlink-style list file (one input path per line, ``\\``-separated
paths resolved relative to the *list file's* own directory) and writes:

* ``<out.bin>``: an index table of little-endian ``int32`` (offset, size)
  pairs, one per file entry in list order, followed by the concatenated
  file contents.
* ``<res_id.h>``: one ``#define <EXT>_<BASENAME> <id_base + index>`` per
  file entry (uppercased; ``rom.NES`` -> ``NES_ROM``), wrapped in an
  include guard, matching ``tutorial_project/tuto1_hw/res/res_id.h``.

Archive padding
---------------
A first reading of ``tuto1_hw/res/res.bin`` suggests the files are concatenated with *no*
padding at all. That holds for ``tuto1_hw/res/res.bin`` only because its
four input sizes happen to sum to a multiple of 4 already. Checked against
the two other real archives in this tree
(``sample_game/res/res.bin`` and ``sample_game/res/res2.bin``, built by the
real ``binlink.exe`` from ``sample_game/res/binlink.lst`` and
``binlink2.lst``), each file's data is in fact zero-padded up to the next
multiple of 4 bytes -- including after the last file, which is why e.g.
``sample_game/res/res.bin`` is 90520 bytes even though its index table plus
its files' exact sizes sum to only 90518. This matches
``docs/pages/generated-resources.md``'s note that the archive is streamed
by 32-bit DMA, which requires 4-byte alignment throughout. The index
table's recorded ``size`` for each entry is still the exact, unpadded file
size (verified against both real archives); only the *placement* of the
next entry (and the end of the archive) rounds up. This tool reproduces
that padding, which is why it reproduces all three real archives
byte-for-byte, not just the one without padding.

The ``:LABEL`` form (``sample_game/res/binlink2.lst``)
--------------------------------------------------------
A list line starting with ``:`` does not add a file; it defines a constant
at the archive index the *next* file entry would get (id_base + however
many file entries precede it in the list), and does not itself advance
that index. This lets a label placed before the first of a run of entries
mark where the run starts, and a second label placed after the last of
them mark one past where it ends, exactly as
``sample_game/res/binlink2.lst``'s ``:MP3_RES_ID`` / ``:MP3_RES_ID_MAX``
pair does around its four MP3 entries. Reproduced here as: emitting a
``#define`` for the label at the current file-entry count (not
incrementing it), interleaved into the header in list order along with the
per-file defines. This reproduces ``sample_game/res/res_id2.h`` exactly
(verified byte-for-byte against the real file, using the real
``binlink2.lst`` and the real MP3 files that back it) -- no difference to
document there.
"""

from __future__ import annotations

import struct
import sys
from pathlib import Path

INDEX_ENTRY_STRUCT = struct.Struct("<ii")  # (offset, size), little-endian int32
ALIGN = 4


def parse_list_file(list_path: Path) -> list[tuple[str, str]]:
    """Parse a binlink-style list file into ``(kind, value)`` entries.

    ``kind`` is ``"file"`` (``value`` is the path as written, backslashes
    and all) or ``"label"`` (``value`` is the label name, a line beginning
    with ``:`` with the ``:`` stripped). Blank lines are skipped.
    """
    entries = []
    for raw_line in list_path.read_text().splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith(":"):
            entries.append(("label", line[1:].strip()))
        else:
            entries.append(("file", line))
    return entries


def resolve_list_path(list_dir: Path, raw: str) -> Path:
    """Resolve one list-file entry's path relative to the list's directory,
    converting ``\\`` (as used throughout the tutorial's .lst files) to the
    platform separator."""
    return (list_dir / raw.replace("\\", "/")).resolve()


def identifier_for(path: Path) -> str:
    """``<EXT>_<BASENAME>``, uppercased -- ``rom.NES`` -> ``NES_ROM``."""
    ext = path.suffix[1:].upper()
    base = path.stem.upper()
    return f"{ext}_{base}"


def _pad_len(n: int) -> int:
    return (-n) % ALIGN


def build_archive(list_path: Path, id_base: int):
    """Build the archive bytes and the ordered list of header defines for
    ``list_path``. Returns ``(archive_bytes, defines)`` where ``defines``
    is a list of ``(name, value)`` pairs in list order (both file entries
    and ``:LABEL`` entries)."""
    list_dir = list_path.parent
    entries = parse_list_file(list_path)

    defines: list[tuple[str, int]] = []
    file_paths: list[Path] = []
    index = 0
    for kind, value in entries:
        if kind == "label":
            defines.append((value, id_base + index))
        else:
            file_path = resolve_list_path(list_dir, value)
            defines.append((identifier_for(file_path), id_base + index))
            file_paths.append(file_path)
            index += 1

    datas = [p.read_bytes() for p in file_paths]
    index_table_size = len(datas) * INDEX_ENTRY_STRUCT.size

    offsets = []
    body = bytearray()
    cursor = index_table_size
    for data in datas:
        offsets.append(cursor)
        body.extend(data)
        pad = _pad_len(len(data))
        if pad:
            body.extend(b"\x00" * pad)
        cursor += len(data) + pad

    index_table = b"".join(
        INDEX_ENTRY_STRUCT.pack(offset, len(data))
        for offset, data in zip(offsets, datas)
    )
    return index_table + bytes(body), defines


def render_header(defines: list[tuple[str, int]], guard: str) -> str:
    """The ``res_id.h`` text: an include guard (named after ``guard``, the
    header's own filename with '.' replaced by '_', matching the real
    files) wrapping one ``#define`` per entry, CRLF line endings (matching
    every real ``res_id*.h`` in this tree)."""
    lines = [f"#ifndef {guard}", f"#define {guard}"]
    for name, value in defines:
        lines.append(f"#define {name} {value}")
    lines.append("#endif")
    return "\r\n".join(lines) + "\r\n"


def guard_for(header_path: Path) -> str:
    return header_path.name.replace(".", "_")


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 4:
        print(
            "usage: respack.py <list.lst> <res_id.h> <out.bin> <id_base>",
            file=sys.stderr,
        )
        return 1
    list_path, header_path, bin_path, id_base_arg = argv
    id_base = int(id_base_arg, 0)

    archive, defines = build_archive(Path(list_path), id_base)
    Path(bin_path).write_bytes(archive)
    Path(header_path).write_text(render_header(defines, guard_for(Path(header_path))))

    print(f"wrote {bin_path} ({len(archive)} bytes, {len(defines)} header define(s))")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
