"""Aggregate guild roster stats for the guild home page.

Computes compact totals from the existing `summary.json` (per-member GP fields
plus the reduced `units` list, which already carries normalized in-game
`relicLevel` 0-10). Cached per file identity so repeated home renders don't
re-parse the summary.
"""

import functools
import json

GL_FACTION = "Galactic Legend"


def _ident(outdir, guild_id):
    path = outdir / "guilds" / f"{guild_id}.summary.json"
    return path, (path.stat().st_mtime_ns, path.stat().st_size)


@functools.lru_cache(maxsize=8)
def _compute(identity):
    path, _ = identity
    summary = json.loads(path.read_text())
    members = summary.get("members", [])

    total_gp = char_gp = ship_gp = 0
    relics = {"total": 0, "r5plus": 0, "r9plus": 0}
    gl_counts = {}
    gl_names = {}

    for m in members:
        total_gp += m.get("galacticPower") or 0
        char_gp += m.get("characterGalacticPower") or 0
        ship_gp += m.get("shipGalacticPower") or 0
        owned = set()
        for u in m.get("units", []):
            rl = u.get("relicLevel") or 0
            relics["total"] += 1 if rl else 0
            if rl >= 5:
                relics["r5plus"] += 1
            if rl >= 9:
                relics["r9plus"] += 1
            if u.get("combatType") != "ship" and GL_FACTION in (u.get("factions") or []):
                owned.add(u.get("baseId"))
        for base_id in owned:
            if base_id not in gl_names:
                unit = next((x for x in m.get("units", []) if x.get("baseId") == base_id), {})
                gl_names[base_id] = unit.get("name") or base_id
            gl_counts[base_id] = gl_counts.get(base_id, 0) + 1

    count = len(members) or 1
    gls = {
        gl_names[base_id]: gl_counts[base_id]
        for base_id in sorted(gl_counts, key=lambda b: (gl_names[b].lower(), b))
    }
    return {
        "memberCount": len(members),
        "totalGP": total_gp,
        "averageGP": round(total_gp / count),
        "charGP": char_gp,
        "shipGP": ship_gp,
        "relics": relics,
        "gls": gls,
    }


def guild_stats(outdir, guild_id):
    try:
        return _compute(_ident(outdir, guild_id))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return None