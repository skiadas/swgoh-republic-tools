#!/usr/bin/env python3
"""Platoon planner data builder (the page is server-rendered via htmx now).

Builds the per-guild planet/roster projection the planner page renders: every
planet's platoons and slots (with combat type and Galactic-Legend tags), and
the member roster with unit maps for eligibility. The interactive model lives
in `planner.py`; `light=True` drops the member unit maps and slot tags for the
read-only pages.
"""

import json
import re
from pathlib import Path

from swgoh_reviewer.ops import load_combat_types

TB_ID = "t05D"

# Display order matching the calculator's dark/neutral/light/specials grouping.
PLANET_ORDER = {"dark": 0, "neutral": 1, "light": 2, "zeffo": 3, "mandalore": 4}


def _planet_order(planet_id):
    m = re.search(r"conflict(\d+)(_bonus)?", planet_id)
    if not m:
        return 9
    idx, bonus = int(m.group(1)), bool(m.group(2))
    if bonus:
        if idx == 1:
            return PLANET_ORDER["zeffo"]
        if idx == 3:
            return PLANET_ORDER["mandalore"]
        return 9
    if idx == 1:
        return PLANET_ORDER["light"]
    if idx == 2:
        return PLANET_ORDER["dark"]
    if idx == 3:
        return PLANET_ORDER["neutral"]
    return 9


def load_gl_units(outdir):
    """baseIds tagged 'galactic_legend' in the game unit catalog."""
    p = outdir / "game" / "units.json"
    if not p.exists():
        return set()
    out = set()
    for base, meta in json.loads(p.read_text()).items():
        if "galactic_legend" in (meta.get("categories") or []):
            out.add(base)
    return out


def build_data(outdir, guild_id, tb_id=TB_ID, light=False):
    """Planet/roster data for the planner pages.

    `light=True` drops what the read-only views don't need (per-member unit
    maps, slot combat type / GL tags), keeping the payload small.
    """
    rote = json.loads((outdir / "rote" / f"{tb_id}.json").read_text())
    summary = json.loads((outdir / "guilds" / f"{guild_id}.summary.json").read_text())
    unit_combat = load_combat_types(outdir) if not light else {}
    gl_units = load_gl_units(outdir) if not light else set()

    planets = []
    for ph in rote.get("phases", []):
        for p in ph.get("planets", []):
            op = p.get("op") or {}
            platoons = []
            for pl in op.get("platoons") or []:
                slots = []
                for u in pl.get("units") or []:
                    if light:
                        slots.append({"b": u["baseId"], "n": u["name"]})
                    else:
                        ct = unit_combat.get(u["baseId"])
                        slots.append({"b": u["baseId"], "n": u["name"], "c": 2 if ct == 2 else 1, "gl": 1 if u["baseId"] in gl_units else 0})
                platoons.append({"idx": pl.get("platoon", len(platoons) + 1), "slots": slots})
            planets.append(
                {
                    "name": p["name"],
                    "phase": ph.get("phase"),
                    "relicReq": op.get("relicRequirement") or 0,
                    "platoons": platoons,
                    "order": _planet_order(p.get("planetId") or ""),
                }
            )

    if not light:
        # Ship-ness of any slot not in the unit catalog falls back to the guild
        # roster (summary units carry combatType).
        ship_units = set()
        for m in summary.get("members", []):
            for u in m.get("units") or []:
                if u.get("combatType") == "ship":
                    ship_units.add(u["baseId"])
        for planet in planets:
            for pl in planet["platoons"]:
                for s in pl["slots"]:
                    if s["c"] == 1 and s["b"] in ship_units:
                        s["c"] = 2

    members = []
    for m in summary.get("members", []):
        if light:
            members.append({"ac": m.get("allyCode"), "name": m.get("name")})
            continue
        units = {}
        for u in m.get("units") or []:
            units[u["baseId"]] = [
                u.get("relicLevel") or 0,
                u.get("rarity") or 0,
                1 if u.get("combatType") == "ship" else 0,
            ]
        members.append({"ac": m.get("allyCode"), "name": m.get("name"), "u": units})

    return {
        "guildId": guild_id,
        "guildName": summary.get("guildName", guild_id),
        "planets": planets,
        "members": members,
    }
