"""Aggregate guild roster stats for the guild home page.

Computes compact totals from the existing `summary.json` (per-member GP fields
plus the reduced `units` list, which already carries normalized in-game
`relicLevel` 0-10). Cached per file identity so repeated home renders don't
re-parse the summary.
"""

import functools
import json

# Curated list of Galactic Legend baseIds (verified against game data). Must
# be updated as new GLs release (same caveat as the calculator optimizer's
# fixed expectations). DARTHJARJAR (an April-Fool's unit) is deliberately
# excluded.
GL_BASE_IDS = {
    "Rey": "GLREY",
    "Kylo Ren (Unmasked)": "KYLORENUNMASKED",
    "Supreme Leader Kylo Ren": "SUPREMELEADERKYLOREN",
    "Sith Eternal Emperor": "SITHPALPATINE",
    "Jedi Master Luke": "GRANDMASTERLUKE",
    "Lord Vader": "LORDVADER",
    "Jedi Master Kenobi": "JEDIMASTERKENOBI",
    "Leia Organa": "GLLEIA",
    "Ahsoka Tano": "GLAHSOKATANO",
    "Bo-Katan (Mand'alor)": "MANDALORBOKATAN",
    "Pirate King Hondo": "GLHONDO",
}


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
    gl_counts = {base_id: 0 for base_id in GL_BASE_IDS.values()}

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
            if u.get("combatType") != "ship":
                owned.add(u.get("baseId"))
        for base_id in owned & gl_counts.keys():
            gl_counts[base_id] += 1

    count = len(members) or 1
    gls = {
        name: gl_counts[base_id]
        for name, base_id in sorted(GL_BASE_IDS.items(), key=lambda kv: -gl_counts[kv[1]])
        if gl_counts[base_id]
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