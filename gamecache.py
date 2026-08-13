#!/usr/bin/env python3
"""Build and cache compact game-data maps from a local swgoh-comlink service.

Caches live under <outdir>/game/:
    localization.json   full key->value text map (ability/category display names)
    units.json          baseId -> {combatType, categories, leader}
    skills.json         skillId -> {nameKey, zetaIndex, omicronIndex}
    categories.json     categoryId -> {descKey, visible}

Caches are rebuilt after a game update with --refresh-game on the scripts that
use them, or by calling ensure_caches(comlink, outdir, refresh=True).
"""

import json
from pathlib import Path

from swgohdata import retry

GAME_CACHE_DIR = "game"
CACHE_NAMES = ("localization", "units", "skills", "categories")


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


def build_skills(comlink):
    out = {}
    for skill in comlink.get_game_data(include_pve_units=False, items="skill").get("skill", []):
        tiers = skill.get("tier") or []
        out[skill.get("id")] = {
            "nameKey": skill.get("nameKey"),
            "zetaIndex": next((i for i, t in enumerate(tiers) if t.get("isZetaTier")), None),
            "omicronIndex": next((i for i, t in enumerate(tiers) if t.get("isOmicronTier")), None),
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
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False))
        return data

    return {
        "localization": get("localization", lambda: build_localization(comlink)),
        "units": get("units", lambda: build_units(comlink)),
        "skills": get("skills", lambda: build_skills(comlink)),
        "categories": get("categories", lambda: build_categories(comlink)),
    }
