# SPDX-License-Identifier: BSD-3-Clause
"""Offline audio pipeline tools.

Modules:

* ``apus`` -- writer/reader for the ``.apus`` register-write stream format,
  plus the note-retrigger thinning rule.
* ``dpcm`` -- the 1-bit delta encoder/decoder for DMC sample data.
* ``sfx2dpcm`` -- CLI: Doom sound lump or WAV -> ``.dmc``.
* ``dpcm_pack`` -- lays out a set of ``.dmc`` files into the boot ROM's DPCM
  bank and emits the nesasm/C tables the sequencer uses to trigger them.
"""
