#!/usr/bin/env python3
"""Streaming fetch-and-summarize pipeline for a guild.

Replaces the old two-step flow (fetch_guild writes raw rosters, then
guild_summary re-reads them). Each member's roster is fetched, reduced to its
summary entry, and the raw payload is discarded — nothing is persisted except
the compact manifest and summary. Roles (memberLevel) from the guild response
are carried into both so the web layer can authorize without the raw guild doc.
"""

import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

from swgoh_comlink import SwgohComlink

from swgoh_reviewer.comlink import (
    DEFAULT_COMLINK,
    RateLimiter,
    build_name_map,
    enrich_player,
    relic_level,
    retry,
)
from swgoh_reviewer import gamecache


def int_or(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return value


def build_unit(unit, caches):
    """Reduce one raw roster unit to the compact summary record.

    Note: ability/zeta/omicron data is deliberately not stored — no view
    consumes it (was ~30% of the summary's size). leader/gearLevel are kept.
    """
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

    return {
        "name": unit.get("name") or base_id,
        "baseId": base_id,
        "combatType": "ship" if udef.get("combatType") == 2 else "character",
        "gearLevel": unit.get("currentTier"),
        "relicLevel": relic_level(unit),
        "rarity": unit.get("currentRarity"),
        "leader": bool(udef.get("leader")),
        "factions": sorted(set(factions)),
    }


def build_summary_member(member, player, caches):
    units = [build_unit(u, caches) for u in player.get("rosterUnit") or []]
    units.sort(
        key=lambda u: (
            u["combatType"],
            -(u["relicLevel"] or 0),
            -(u["gearLevel"] or 0),
            u["name"].lower(),
        )
    )
    return {
        "name": member.get("playerName") or player.get("name"),
        "playerId": member.get("playerId") or player.get("playerId"),
        "allyCode": int_or(player.get("allyCode") or member.get("allyCode")),
        "memberLevel": member.get("memberLevel"),
        "galacticPower": int_or(member.get("galacticPower") or player.get("galacticPower")),
        "characterGalacticPower": int_or(member.get("characterGalacticPower")),
        "shipGalacticPower": int_or(member.get("shipGalacticPower")),
        "units": units,
    }


def resolve_guild(comlink, allycode):
    player = comlink.get_player(allycode=str(allycode))
    return player.get("guildId"), player.get("guildName"), player


def get_guild_members(comlink, guild_id):
    guild = retry(lambda: comlink.get_guild(guild_id=guild_id, include_recent_guild_activity_info=True))
    name = (guild.get("profile") or {}).get("name")
    members = guild.get("member", []) or []
    return guild, name, members


def write_json(path, obj, pretty=False):
    text = json.dumps(obj, indent=2 if pretty else None, separators=None if pretty else (",", ":"), ensure_ascii=False)
    path.write_text(text)


def refresh_guild(
    outdir,
    guild_id=None,
    allycode=None,
    comlink_url=DEFAULT_COMLINK,
    max_rps=4.0,
    refresh_names=False,
    refresh_game=False,
    limit=0,
    pretty=False,
    progress=print,
):
    """Fetch a guild's members and write its manifest + summary, streaming.

    Nothing is kept per-player beyond the summary entry; the raw guild
    response is reduced to member roles and discarded.
    """
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    limiter = RateLimiter(max_rps)

    with SwgohComlink(url=comlink_url) as comlink:
        if guild_id is None:
            limiter.wait()
            guild_id, guild_name, _ = retry(lambda: resolve_guild(comlink, allycode))
            progress(f"resolved guild: {guild_name} ({guild_id})")

        name_map = retry(lambda: build_name_map(comlink, outdir, use_cache=not refresh_names))
        caches = gamecache.ensure_caches(comlink, outdir, refresh=refresh_game)

        limiter.wait()
        guild, guild_name, members = get_guild_members(comlink, guild_id)
        progress(f"guild: {guild_name} | members: {len(members)}")
        role_by_player = {m.get("playerId"): m.get("memberLevel") for m in members}

        members = members[:limit] if limit > 0 else members
        members_out = []
        ok = failed = 0
        for i, member in enumerate(members, 1):
            pid = member.get("playerId")
            pname = member.get("playerName", "?")
            try:
                limiter.wait()
                player = retry(lambda p=pid: comlink.get_player(player_id=p))
                enrich_player(player, name_map)
                member = dict(member)
                member["allyCode"] = player.get("allyCode") or member.get("allyCode")
                members_out.append(build_summary_member(member, player, caches))
                ok += 1
                progress(f"[{i}/{len(members)}] ok   {player.get('name')} ({member.get('allyCode')})")
            except Exception as exc:  # noqa: BLE001 - record and keep going
                failed += 1
                progress(f"[{i}/{len(members)}] FAIL {pname}: {exc}", file=sys.stderr)
                members_out.append(
                    {
                        "name": pname,
                        "playerId": pid,
                        "allyCode": int_or(member.get("allyCode")),
                        "memberLevel": member.get("memberLevel"),
                        "galacticPower": int_or(member.get("galacticPower")),
                        "characterGalacticPower": int_or(member.get("characterGalacticPower")),
                        "shipGalacticPower": int_or(member.get("shipGalacticPower")),
                        "units": [],
                    }
                )

        guild_dir = outdir / "guilds"
        guild_dir.mkdir(parents=True, exist_ok=True)
        manifest = {
            "guildId": guild_id,
            "guildName": guild_name,
            "memberCount": len(members),
            "fetchedAt": datetime.now(timezone.utc).isoformat(),
            "members": [
                {
                    "playerId": m.get("playerId"),
                    "playerName": m.get("name"),
                    "allyCode": m.get("allyCode"),
                    "memberLevel": m.get("memberLevel"),
                    "galacticPower": m.get("galacticPower"),
                    "characterGalacticPower": m.get("characterGalacticPower"),
                    "shipGalacticPower": m.get("shipGalacticPower"),
                    "status": "ok" if m.get("units") else "error",
                }
                for m in members_out
            ],
        }
        write_json(guild_dir / f"{guild_id}.json", manifest, pretty=pretty)

        summary = {
            "guildId": guild_id,
            "guildName": guild_name,
            "memberCount": len(members_out),
            "generatedAt": datetime.now(timezone.utc).isoformat(),
            "members": members_out,
        }
        summary_path = guild_dir / f"{guild_id}.summary.json"
        write_json(summary_path, summary, pretty=pretty)
        size_kb = summary_path.stat().st_size / 1e3
        progress(f"wrote {summary_path} ({size_kb:.0f} KB, {len(members_out)} members)")
        progress(f"done: {ok} ok, {failed} failed")

    return manifest, summary


def summarize_from_files(outdir, guild_id, pretty=False, progress=print):
    """Rebuild a summary from already-downloaded raw rosters (dev tool).

    Requires data/<allyCode>.json files plus the game caches; no comlink for
    player data. Used by guild_summary.py to regenerate offline.
    """
    outdir = Path(outdir)
    guild_dir = outdir / "guilds"
    manifest_path = guild_dir / f"{guild_id}.json"
    if not manifest_path.exists():
        progress(f"no manifest at {manifest_path}", file=sys.stderr)
        return None
    manifest = json.loads(manifest_path.read_text())
    caches = gamecache.ensure_caches(None, outdir, refresh=False)

    members_out = []
    for member in manifest.get("members", []):
        allycode = member.get("allyCode")
        player = {}
        if allycode:
            pfile = outdir / f"{allycode}.json"
            if pfile.exists():
                player = json.loads(pfile.read_text())
        members_out.append(build_summary_member(member, player, caches))

    summary = {
        "guildId": manifest.get("guildId", guild_id),
        "guildName": manifest.get("guildName"),
        "memberCount": len(members_out),
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "members": members_out,
    }
    summary_path = guild_dir / f"{guild_id}.summary.json"
    write_json(summary_path, summary, pretty=pretty)
    progress(f"wrote {summary_path} ({summary_path.stat().st_size / 1e3:.0f} KB, {len(members_out)} members)")
    return summary
