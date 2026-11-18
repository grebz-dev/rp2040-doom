#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-3-Clause
"""Derive the native world sprite scope from the engine tables and a WAD.

The reviewed `assets/world-sprite-scope.json` lists sprite families by hand
for the shareware IWAD. For other WADs this module builds the same kind of
scope automatically:

* Thing types and their sprites come from the engine's own `src/doom/info.c`
  (`mobjinfo` and `states`): every sprite reachable from a thing's spawn, see,
  pain, attack, death and raise states belongs to it.
* Roles follow the thing flags the runtime already ranks by: `MF_COUNTKILL`
  monsters, and shootable things with attack states (the lost soul), are
  enemies; `MF_SPECIAL` things are pickups; other map-placed things that are
  not missiles are scenery (only with `include_scenery`), using their spawn
  sprites only so that, for example, barrel explosions stay streamed.
  The player sprite and things never placed on maps (missiles, puffs, fog)
  stay streamed.
* Priority, used to trim the atlas to its flash region, is how often the
  WAD's maps place the family's things (THINGS lumps), enemies before pickups
  before scenery.

Families absent from the WAD are left for build_world_atlas to report as
missing, exactly as with the reviewed scope.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import re
import struct

ROLE_ORDER = {"enemy": 0, "pickup": 1, "scenery": 2}
STATE_FIELDS = ("spawnstate", "seestate", "painstate", "meleestate", "missilestate",
                "deathstate", "xdeathstate", "raisestate")


@dataclass
class Thing:
    name: str
    doomednum: int
    flags: set[str]
    states: dict[str, str]
    sprites: set[str] = field(default_factory=set)


def parse_states(info_c: str) -> dict[str, tuple[str, str]]:
    """S_NAME -> (sprite, next state) from the one-line STATE(...) rows of info.c."""
    row = re.compile(r"^\s*STATE\(SPR_([A-Z0-9]+),[^,]*,[^,]*,[^,]*,\s*(S_[A-Z0-9_]+)\s*,"
                     r"[^,]*,[^)]*\),?\s*//\s*(S_[A-Z0-9_]+)\s*$")
    states = {}
    for line in info_c.splitlines():
        match = row.match(line)
        if match:
            sprite, nextstate, name = match.groups()
            states[name] = (sprite, nextstate)
    if states.get("S_TROO_STND", ("", ""))[0] != "TROO":
        raise ValueError("unexpected states table in info.c")
    return states


def parse_things(info_c: str) -> list[Thing]:
    """mobjinfo entries: name, doomednum, flags and state fields."""
    body = info_c.split("mobjinfo[NUMMOBJTYPES]", 1)[1]
    things = []
    for name, block in re.findall(r"\{\s*//\s*(MT_[A-Z0-9_]+)(.*?)\n\s*\}", body, re.S):
        values = dict((key, value.strip()) for value, key in
                      re.findall(r"^\s*(.*?),?\s*//\s*([a-z]+)\s*$", block, re.M))
        try:
            doomednum = int(values["doomednum"])
        except (KeyError, ValueError) as exc:
            raise ValueError(f"cannot read doomednum of {name}") from exc
        flags = {flag.strip() for flag in values.get("flags", "0").split("|")} - {"0"}
        states = {key: values[key] for key in STATE_FIELDS if values.get(key, "0") not in ("0", "S_NULL")}
        things.append(Thing(name, doomednum, flags, states))
    if not any(thing.name == "MT_TROOP" for thing in things):
        raise ValueError("unexpected mobjinfo table in info.c")
    return things


def thing_sprites(thing: Thing, states: dict[str, tuple[str, str]],
                  fields: tuple[str, ...] = STATE_FIELDS) -> set[str]:
    sprites, seen = set(), set()
    for start in (thing.states[f] for f in fields if f in thing.states):
        state = start
        # S_NULL ends a chain; its sprite field is merely enum value 0 (TROO).
        while state in states and state not in seen and state != "S_NULL":
            seen.add(state)
            sprite, state = states[state]
            sprites.add(sprite)
    return sprites


def role_of(thing: Thing, include_scenery: bool) -> str | None:
    if thing.name == "MT_PLAYER" or thing.doomednum < 0:
        return None
    attacks = "meleestate" in thing.states or "missilestate" in thing.states
    if "MF_COUNTKILL" in thing.flags or ("MF_SHOOTABLE" in thing.flags and attacks):
        return "enemy"
    if "MF_SPECIAL" in thing.flags:
        return "pickup"
    if include_scenery and "MF_MISSILE" not in thing.flags:
        return "scenery"
    return None


def map_thing_counts(wad: bytes) -> dict[int, int]:
    """How often each thing type is placed across every map's THINGS lump."""
    _, count, directory = struct.unpack_from("<4sii", wad)
    counts: dict[int, int] = {}
    for index in range(count):
        offset, size, raw = struct.unpack_from("<ii8s", wad, directory + 16 * index)
        if raw.split(b"\0")[0] != b"THINGS":
            continue
        for record in range(size // 10):
            kind = struct.unpack_from("<h", wad, offset + record * 10 + 6)[0]
            counts[kind] = counts.get(kind, 0) + 1
    return counts


def auto_scope(wad: bytes, info_c: Path, include_scenery: bool = False,
               reviewed: dict | None = None) -> dict:
    """A scope dict for build_world_atlas.build, plus `priority` (families,
    most important first) and `placed` (map placements per family)."""
    text = info_c.read_text()
    states = parse_states(text)
    counts = map_thing_counts(wad)
    roles: dict[str, str] = {}
    placed: dict[str, int] = {}
    for thing in parse_things(text):
        role = role_of(thing, include_scenery)
        if role is None:
            continue
        fields = ("spawnstate",) if role == "scenery" else STATE_FIELDS
        for sprite in thing_sprites(thing, states, fields):
            if sprite == "PLAY":
                continue
            if sprite not in roles or ROLE_ORDER[role] < ROLE_ORDER[roles[sprite]]:
                roles[sprite] = role
            placed[sprite] = placed.get(sprite, 0) + counts.get(thing.doomednum, 0)
    priority = sorted(roles, key=lambda f: (ROLE_ORDER[roles[f]], -placed[f], f))
    scope = {"version": 2, "families": {f: roles[f] for f in priority},
             "priority": priority, "placed": {f: placed[f] for f in priority}}
    if reviewed:
        # Reviewed palettes still apply to families both scopes contain.
        scope["palette_overrides"] = {f: v for f, v in reviewed.get("palette_overrides", {}).items()
                                      if f in roles}
        scope["palette_exclusive"] = [f for f in reviewed.get("palette_exclusive", []) if f in roles]
    return scope
