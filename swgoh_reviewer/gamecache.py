#!/usr/bin/env python3
"""Build and cache compact game-data maps from a local swgoh-comlink service.

Caches live under <outdir>/game/:
    localization.json   key->value text map (category display names, TB text)
    units.json          baseId -> {combatType, categories, leader}
    categories.json     categoryId -> {descKey, visible}

(The old skills.json cache is gone: nothing reads ability/zeta/omicron data.)

Caches are rebuilt after a game update with --refresh-game on the scripts that
use them, or by calling ensure_caches(comlink, outdir, refresh=True).
"""

import json
from pathlib import Path

from swgoh_reviewer.comlink import retry
from swgoh_reviewer.io import atomic_write_text

GAME_CACHE_DIR = "game"
CACHE_NAMES = ("localization", "units", "categories")


def build_localization(comlink):
    loc = comlink.get_localization(locale="ENG_US", unzip=True)
    entries = {}
    for line in loc["Loc_ENG_US.txt"].splitlines():
        if "|" in line:
            key, _, value = line.partition("|")
            entries[key] = value
    return entries


def build_units(comlink):
    out = {}
    for unit in comlink.get_game_data(include_pve_units=False, items="units").get("units", []):
        base_id = unit.get("baseId")
        if not base_id:
            continue
        cats = unit.get("categoryId") or []
        out[base_id] = {
            "combatType": unit.get("combatType"),
            "categories": cats,
            "leader": bool(unit.get("leaderAbilityRef")) or ("role_leader" in cats),
        }
    return out


def build_categories(comlink):
    out = {}
    for cat in comlink.get_game_data(include_pve_units=False, items="category").get("category", []):
        out[cat.get("id")] = {"descKey": cat.get("descKey"), "visible": bool(cat.get("visible"))}
    return out


def ensure_caches(comlink, outdir, refresh=False):
    """Load the game-data caches, building any that are missing (or all, if
    refresh=True) using comlink. Pass comlink=None to only load from disk."""
    outdir = Path(outdir) / GAME_CACHE_DIR

    def get(name, builder):
        path = outdir / f"{name}.json"
        if not refresh and path.exists():
            return json.loads(path.read_text())
        if comlink is None:
            raise RuntimeError(f"cache {name}.json is missing and no comlink was provided")
        data = retry(builder)
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(path, json.dumps(data, separators=(",", ":"), ensure_ascii=False))
        return data

    return {
        "localization": get("localization", lambda: build_localization(comlink)),
        "units": get("units", lambda: build_units(comlink)),
        "categories": get("categories", lambda: build_categories(comlink)),
    }
