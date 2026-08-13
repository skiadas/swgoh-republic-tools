#!/usr/bin/env python3
"""Generate a single JSON summary of a guild from downloaded player data.

Reads only the already-downloaded files in <outdir> (manifest + per-player
rosters) plus the game-data caches; builds the caches from comlink on first
run. Does not re-fetch any player data.

Usage:
    python guild_summary.py NW4t0-dBRcG8n-PVhykpKg
    python guild_summary.py NW4t0-dBRcG8n-PVhykpKg --refresh-game
"""

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import gamecache
from swgoh_comlink import SwgohComlink
from swgohdata import DEFAULT_COMLINK, relic_level

ABILITY_TYPES = {
    "basicskill": "basic",
    "specialskill": "special",
    "leaderskill": "leader",
    "uniqueskill": "unique",
    "contractskill": "contract",
    "hardwareskill": "hardware",
}


def ability_type(skill_id):
    prefix = skill_id.split("_", 1)[0] if "_" in skill_id else ""
    return ABILITY_TYPES.get(prefix, "other")


def int_or(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return value


def build_unit(unit, caches):
    base_id = unit.get("baseId") or ""
    udef = caches["units"].get(base_id, {})
    cat_defs = caches["categories"]
    loc = caches["localization"]

    factions = []
    for cid in udef.get("categories") or []:
        cdef = cat_defs.get(cid)
        if not cdef or not cdef.get("visible"):
            continue
        name = loc.get(cdef.get("descKey")) if cdef.get("descKey") else None
        if name:
            factions.append(name)

    skills = caches["skills"]
    abilities = []
    for skl in unit.get("skill") or []:
        sid = skl.get("id", "")
        sdef = skills.get(sid, {})
        tier = skl.get("tier")
        zeta = sdef.get("zetaIndex") is not None and tier >= sdef["zetaIndex"]
        omicron = sdef.get("omicronIndex") is not None and tier >= sdef["omicronIndex"]
        abilities.append(
            {
                "id": sid,
                "type": ability_type(sid),
                "tier": tier,
                "zeta": bool(zeta),
                "omicron": bool(omicron),
            }
        )

    return {
        "name": unit.get("name") or base_id,
        "baseId": base_id,
        "combatType": "ship" if udef.get("combatType") == 2 else "character",
        "gearLevel": unit.get("currentTier"),
        "relicLevel": relic_level(unit),
        "rarity": unit.get("currentRarity"),
        "leader": bool(udef.get("leader")),
        "factions": sorted(set(factions)),
        "abilities": abilities,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("guild_id")
    parser.add_argument("--comlink", default=DEFAULT_COMLINK, help="swgoh-comlink base URL")
    parser.add_argument("--outdir", default=str(Path(__file__).resolve().parent / "data"), help="output directory")
    parser.add_argument("--refresh-game", action="store_true", help="rebuild game-data caches")
    args = parser.parse_args(argv)

    outdir = Path(args.outdir)
    manifest_path = outdir / "guilds" / f"{args.guild_id}.json"
    if not manifest_path.exists():
        print(f"no manifest at {manifest_path}; run fetch_guild.py first", file=sys.stderr)
        return 2
    manifest = json.loads(manifest_path.read_text())

    missing = [n for n in gamecache.CACHE_NAMES if not (outdir / "game" / f"{n}.json").exists()]
    if args.refresh_game or missing:
        with SwgohComlink(url=args.comlink) as comlink:
            caches = gamecache.ensure_caches(comlink, outdir, refresh=args.refresh_game)
    else:
        caches = gamecache.ensure_caches(None, outdir, refresh=False)

    members_out = []
    for member in manifest["members"]:
        allycode = member.get("allyCode")
        player = {}
        if allycode:
            pfile = outdir / f"{allycode}.json"
            if pfile.exists():
                player = json.loads(pfile.read_text())
            else:
                print(f"warning: no player file for {member.get('playerName')} ({allycode})", file=sys.stderr)
        units = [build_unit(u, caches) for u in player.get("rosterUnit") or []]
        units.sort(
            key=lambda u: (
                u["combatType"],
                -(u["relicLevel"] or 0),
                -(u["gearLevel"] or 0),
                u["name"].lower(),
            )
        )
        members_out.append(
            {
                "name": member.get("playerName"),
                "playerId": member.get("playerId"),
                "allyCode": int_or(allycode),
                "galacticPower": int_or(member.get("galacticPower")),
                "characterGalacticPower": int_or(member.get("characterGalacticPower")),
                "shipGalacticPower": int_or(member.get("shipGalacticPower")),
                "units": units,
            }
        )

    summary = {
        "guildId": manifest["guildId"],
        "guildName": manifest.get("guildName"),
        "memberCount": len(members_out),
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "members": members_out,
    }

    outpath = outdir / "guilds" / f"{args.guild_id}.summary.json"
    outpath.write_text(json.dumps(summary, indent=2, ensure_ascii=False))
    size_mb = outpath.stat().st_size / 1e6
    print(f"wrote {outpath} ({size_mb:.1f} MB, {len(members_out)} members)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
