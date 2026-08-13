#!/usr/bin/env python3
"""Shared helpers for fetching SWGOH data through a local swgoh-comlink service."""

import json
import time

from swgoh_comlink import SwgohComlink

from swgoh_reviewer.config import comlink_url

DEFAULT_COMLINK = comlink_url()
NAME_CACHE = "names.json"

STAT_NAMES = {
    "Health": "health",
    "Strength": "strength",
    "Agility": "agility",
    "Tactics": "tactics",
    "Speed": "speed",
    "Physical Damage": "physical_damage",
    "Special Damage": "special_damage",
    "Armor": "armor",
    "Resistance": "resistance",
    "Physical Critical Chance": "physical_critical_chance",
    "Special Critical Chance": "special_critical_chance",
    "Critical Damage": "critical_damage",
    "Potency": "potency",
    "Tenacity": "tenacity",
}


class RateLimiter:
    """Throttle calls to at most `rps` per second. Pass rps=None to disable."""

    def __init__(self, rps=None):
        self.interval = 1.0 / rps if rps and rps > 0 else 0.0
        self._next = 0.0

    def wait(self):
        if self.interval <= 0:
            return
        now = time.monotonic()
        if now < self._next:
            time.sleep(self._next - now)
        self._next = time.monotonic() + self.interval


def retry(fn, retries=4, base_delay=1.0):
    last = None
    for attempt in range(retries):
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001 - retry any transient service error
            last = exc
            time.sleep(base_delay * (2**attempt))
    raise last


def get_units(comlink):
    gamedata = comlink.get_game_data(include_pve_units=False, items="units")
    return gamedata.get("units", [])


def get_localization_text(comlink):
    loc = comlink.get_localization(locale="ENG_US", unzip=True)
    return loc["Loc_ENG_US.txt"]


def build_name_map(comlink, outdir, use_cache=True):
    outdir = outdir if hasattr(outdir, "read_text") else __import__("pathlib").Path(outdir)
    cache_path = outdir / NAME_CACHE
    if use_cache and cache_path.exists():
        return json.loads(cache_path.read_text())

    units = get_units(comlink)
    entries = {}
    for line in get_localization_text(comlink).splitlines():
        if "|" in line:
            key, _, value = line.partition("|")
            entries[key] = value

    name_map = {}
    for unit in units:
        base_id = unit.get("baseId")
        if not base_id:
            continue
        name_key = unit.get("nameKey")
        name_map[base_id] = entries.get(name_key, base_id) if name_key else base_id

    outdir.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(name_map, indent=2, ensure_ascii=False))
    return name_map


def enrich_unit(unit, name_map):
    definition_id = unit.get("definitionId", "")
    base_id = definition_id.split(":", 1)[0] if definition_id else ""
    unit["baseId"] = base_id
    unit["name"] = name_map.get(base_id, base_id)

    stats = {}
    for stat in unit.get("unitStat") or []:
        stat_id = str(stat.get("statId", ""))
        name = stat_id.split("_", 1)[-1] if "_" in stat_id else stat_id
        stats[STAT_NAMES.get(name, name)] = stat.get("unitStatValue")
    if stats:
        unit["computedStats"] = stats

    return unit


def enrich_player(player, name_map):
    for unit in player.get("rosterUnit") or []:
        enrich_unit(unit, name_map)
    return player


def fetch_player(comlink, allycode=None, player_id=None, name_map=None, outdir=None):
    kwargs = {}
    if allycode is not None:
        kwargs["allycode"] = str(allycode)
    if player_id is not None:
        kwargs["player_id"] = player_id
    data = comlink.get_player(**kwargs)
    if name_map:
        enrich_player(data, name_map)
    if outdir is not None:
        outdir = __import__("pathlib").Path(outdir)
        outdir.mkdir(parents=True, exist_ok=True)
        key = data.get("allyCode") or player_id
        outpath = outdir / f"{key}.json"
        outpath.write_text(json.dumps(data, indent=2, ensure_ascii=False))
        return data, outpath
    return data


def player_gp(player):
    for stat in player.get("profileStat") or []:
        if stat.get("nameKey") == "STAT_GALACTIC_POWER_ACQUIRED_NAME":
            try:
                return int(stat.get("value"))
            except (TypeError, ValueError):
                return None
    return None


def relic_level(unit):
    """Actual relic level (0 = no relic) from the raw relic.currentTier.

    The game's internal relic tier is offset by +2: no relic / pre-G13 is
    currentTier 1-2, R1 is 3, ..., R10 is 12.
    """
    tier = (unit.get("relic") or {}).get("currentTier", 0) or 0
    return max(0, tier - 2)


def summarize(player):
    roster = player.get("rosterUnit") or []

    def rank(u):
        return (relic_level(u), u.get("currentTier", 0), u.get("currentRarity", 0), u.get("currentLevel", 0))

    top = sorted(roster, key=rank, reverse=True)[:5]
    gp = player_gp(player)
    lines = [
        f"  name:      {player.get('name')} (allyCode {player.get('allyCode')})",
        f"  guild:     {player.get('guildName')}",
        f"  level:     {player.get('level')}",
        f"  GP:        {gp:,}" if gp else f"  GP:        n/a",
        f"  units:     {len(roster)}",
        f"  7-star:    {sum(1 for u in roster if u.get('currentRarity', 0) >= 7)}",
        f"  gear 13+:  {sum(1 for u in roster if u.get('currentTier', 0) >= 13)}",
        f"  relics:    {sum(1 for u in roster if relic_level(u) >= 1)}",
        "  top units: " + ", ".join(
            f"{u.get('name')} ({u.get('currentRarity')}* L{u.get('currentLevel')} G{u.get('currentTier')}"
            f"{' R' + str(relic_level(u)) if relic_level(u) >= 1 else ''})"
            for u in top
        ),
    ]
    return "\n".join(lines)
