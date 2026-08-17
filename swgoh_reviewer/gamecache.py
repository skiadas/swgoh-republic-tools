#!/usr/bin/env python3
"""Build and cache compact game-data maps.

Caches live under <outdir>/game/:
    localization.json   key->value text map (category display names, TB text)
    units.json          baseId -> {combatType, categories, leader, name, factions}
    categories.json     categoryId -> {descKey, visible}
    factions.json       sorted list of visible, localized category names

(The old skills.json cache is gone: nothing reads ability/zeta/omicron data.)

The per-unit `name` and `factions` (visible localized category names) are
projected into `units.json` at build time, so summary building only ever loads
that small file — the large localization.json is touched only while building
the projection, not while reading it.

Caches are rebuilt after a game update with --refresh-game on the scripts that
use them, or by calling ensure_caches(source, outdir, refresh=True). The
`source` is either a comlink-python client or a swgoh_reviewer.static_gamedata
.StaticGameData (duck-typed drop-in), and None means load from disk only.
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


def build_units(source, localization, categories):
    """Project each unit to {combatType, categories, leader, name, factions,
    thumb}.

    `factions` is the sorted, deduped set of localized names of the unit's
    visible categories; `name` is the localized display name; `thumb` is the
    game-defined portrait bundle name (thumbnailName, minus the leading
    "tex."), which is authoritative for ae2 — the naive charui_<baseId> rule
    misses units like ACKBAR whose bundle is charui_ackbaradmiral.
    """
    out = {}
    for unit in source.get_game_data(include_pve_units=False, items="units").get("units", []):
        base_id = unit.get("baseId")
        if not base_id:
            continue
        cats = unit.get("categoryId") or []
        factions = []
        for cid in cats:
            cdef = categories.get(cid)
            if not cdef or not cdef.get("visible") or not cdef.get("descKey"):
                continue
            name = localization.get(cdef["descKey"])
            if name:
                factions.append(name)
        name_key = unit.get("nameKey")
        thumb = unit.get("thumbnailName") or ""
        out[base_id] = {
            "combatType": unit.get("combatType"),
            "categories": cats,
            "leader": bool(unit.get("leaderAbilityRef")) or ("role_leader" in cats),
            "name": localization.get(name_key, base_id) if name_key else base_id,
            "factions": sorted(set(factions)),
            "thumb": thumb[4:] if thumb.startswith("tex.") else thumb,
        }
    return out


def build_categories(comlink):
    out = {}
    for cat in comlink.get_game_data(include_pve_units=False, items="category").get("category", []):
        out[cat.get("id")] = {"descKey": cat.get("descKey"), "visible": bool(cat.get("visible"))}
    return out


def build_factions(localization, categories):
    """Sorted, deduped localized names of every visible category."""
    names = set()
    for cdef in categories.values():
        if cdef.get("visible") and cdef.get("descKey"):
            name = localization.get(cdef["descKey"])
            if name:
                names.add(name)
    return sorted(names)


def _units_projection_ok(units):
    """Whether a units.json dict carries the current name/factions projection.

    An empty dict or a pre-projection layout (entries without `factions` or
    without the `thumb` portrait bundle name) is treated as stale so it gets
    rebuilt rather than silently yielding empty factions / naive asset names.
    """
    if not isinstance(units, dict):
        return False
    for entry in units.values():
        if not isinstance(entry, dict):
            return False
        if "factions" not in entry or "thumb" not in entry:
            return False
    return True


def ensure_caches(source, outdir, refresh=False, names=None):
    """Load the game-data caches, building any that are missing (or all, if
    refresh=True) using `source` (comlink or StaticGameData). Pass None to only
    load from disk. `names` selects which caches to return (default: all).

    Because units.json carries the name/faction projection, loading just
    `units` (the summary path) never reads the large localization file. A
    pre-projection units cache (e.g. from before this change) is detected and
    rebuilt automatically.
    """
    names = names or CACHE_NAMES
    outdir = Path(outdir) / GAME_CACHE_DIR

    def get(name, builder, valid=None):
        path = outdir / f"{name}.json"
        if not refresh and path.exists():
            data = json.loads(path.read_text())
            if valid is None or valid(data):
                return data
        if source is None:
            raise RuntimeError(f"cache {name}.json is missing or stale and no game-data source was provided")
        data = retry(builder)
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(path, json.dumps(data, separators=(",", ":"), ensure_ascii=False))
        return data

    result = {}
    want_units = "units" in names
    if want_units:
        # Projection intermediates (localization + categories) are only needed
        # when the units cache itself is missing or stale.
        units_path = outdir / "units.json"
        stale = (
            not refresh
            and units_path.exists()
            and not _units_projection_ok(json.loads(units_path.read_text()))
        )
        if refresh or not units_path.exists() or stale:
            result["localization"] = get("localization", lambda: build_localization(source))
            result["categories"] = get("categories", lambda: build_categories(source))
        result["units"] = get(
            "units",
            lambda: build_units(source, result["localization"], result["categories"]),
            valid=_units_projection_ok,
        )
    if "localization" in names and "localization" not in result:
        result["localization"] = get("localization", lambda: build_localization(source))
    if "categories" in names and "categories" not in result:
        result["categories"] = get("categories", lambda: build_categories(source))

    # Refresh the small derived faction-name list whenever both are in hand.
    if "localization" in result and "categories" in result:
        factions = build_factions(result["localization"], result["categories"])
        atomic_write_text(outdir / "factions.json", json.dumps(factions, separators=(",", ":"), ensure_ascii=False))

    return {name: result[name] for name in names}
