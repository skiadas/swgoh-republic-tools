#!/usr/bin/env python3
"""Fetch full rosters for every member of a guild via a local swgoh-comlink service.

Resolves a guild from a player's ally code (or --guild-id), then downloads each
member's roster with a client-side rate limit so we stay well under EA's caps
(~20 req/s total, /player up to ~100 req/s; default is a conservative 4 req/s).

Data layout:
    data/guilds/<guildId>.json          guild manifest (members + statuses)
    data/guilds/<guildId>.guild.json    raw guild response
    data/<allyCode>.json                each member's full roster (same format
                                        as fetch_roster.py output)

Already-downloaded members are skipped on re-runs unless --refresh is given.

Usage:
    python fetch_guild.py 679577173
    python fetch_guild.py --guild-id NW4t0-dBRcG8n-PVhykpKg
"""

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from swgoh_comlink import SwgohComlink

from swgohdata import (
    DEFAULT_COMLINK,
    RateLimiter,
    build_name_map,
    enrich_player,
    retry,
    summarize,
)


def resolve_guild(comlink, allycode):
    player = comlink.get_player(allycode=str(allycode))
    return player.get("guildId"), player.get("guildName"), player


def load_manifest(path):
    if path.exists():
        try:
            return json.loads(path.read_text())
        except json.JSONDecodeError:
            return None
    return None


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("allycode", nargs="?", type=str, help="a player's ally code to resolve their guild")
    parser.add_argument("--guild-id", type=str, help="guild id (skips ally-code resolution)")
    parser.add_argument("--comlink", default=DEFAULT_COMLINK, help="swgoh-comlink base URL")
    parser.add_argument("--outdir", default=str(Path(__file__).resolve().parent / "data"), help="output directory")
    parser.add_argument("--max-rps", type=float, default=4.0, help="max requests per second (default 4)")
    parser.add_argument("--refresh", action="store_true", help="re-fetch members even if already downloaded")
    parser.add_argument("--limit", type=int, default=0, help="only fetch the first N members (0 = all)")
    parser.add_argument("--refresh-names", action="store_true", help="rebuild the defId->name cache")
    args = parser.parse_args(argv)

    if not args.guild_id and not args.allycode:
        parser.error("provide an ally code or --guild-id")
    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    limiter = RateLimiter(args.max_rps)

    with SwgohComlink(url=args.comlink) as comlink:
        if args.guild_id:
            guild_id, guild_name = args.guild_id, None
        else:
            limiter.wait()
            guild_id, guild_name, _ = retry(lambda: resolve_guild(comlink, args.allycode))
            print(f"resolved guild: {guild_name} ({guild_id})")

        name_map = retry(lambda: build_name_map(comlink, outdir, use_cache=not args.refresh_names))
        print(f"name map: {len(name_map)} units")

        guild_dir = outdir / "guilds"
        guild_dir.mkdir(parents=True, exist_ok=True)
        manifest_path = guild_dir / f"{guild_id}.json"
        manifest = load_manifest(manifest_path) or {"guildId": guild_id, "members": []}
        by_player_id = {m["playerId"]: m for m in manifest["members"]}

        limiter.wait()
        guild, members = retry(
            lambda: (lambda g: (g, g.get("member", [])))(
                comlink.get_guild(guild_id=guild_id, include_recent_guild_activity_info=True)
            )
        )
        guild_name = guild_name or guild.get("profile", {}).get("name")
        print(f"guild: {guild_name} | members: {len(members)}")

        (guild_dir / f"{guild_id}.guild.json").write_text(json.dumps(guild, indent=2, ensure_ascii=False))

        members = members[: args.limit] if args.limit > 0 else members
        ok = skipped = failed = 0
        for i, member in enumerate(members, 1):
            pid = member["playerId"]
            pname = member.get("playerName", "?")
            record = by_player_id.get(pid) or {"playerId": pid, "playerName": pname}
            record.update(
                {
                    "galacticPower": member.get("galacticPower"),
                    "characterGalacticPower": member.get("characterGalacticPower"),
                    "shipGalacticPower": member.get("shipGalacticPower"),
                }
            )

            file_key = record.get("allyCode")
            already_have = file_key and (outdir / f"{file_key}.json").exists()
            if already_have and not args.refresh:
                record.setdefault("status", "skipped")
                skipped += 1
                print(f"[{i}/{len(members)}] skip {pname} ({file_key})")
                continue

            try:
                limiter.wait()
                player = retry(lambda p=pid: comlink.get_player(player_id=p))
                enrich_player(player, name_map)
                allycode = str(player.get("allyCode") or pid)
                outpath = outdir / f"{allycode}.json"
                outpath.write_text(json.dumps(player, indent=2, ensure_ascii=False))
                record.update(
                    {
                        "playerId": pid,
                        "playerName": player.get("name") or pname,
                        "allyCode": allycode,
                        "file": str(outpath),
                        "status": "ok",
                    }
                )
                ok += 1
                print(f"[{i}/{len(members)}] ok   {player.get('name')} ({allycode}) -> {outpath.name}")
            except Exception as exc:  # noqa: BLE001 - record and keep going
                record.update({"playerId": pid, "playerName": pname, "status": "error", "error": str(exc)})
                failed += 1
                print(f"[{i}/{len(members)}] FAIL {pname}: {exc}")

            by_player_id[pid] = record

        manifest.update(
            {
                "guildId": guild_id,
                "guildName": guild_name,
                "memberCount": len(members),
                "fetchedAt": datetime.now(timezone.utc).isoformat(),
                "members": list(by_player_id.values()),
            }
        )
        manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False))

        print(f"\ndone: {ok} fetched, {skipped} skipped, {failed} failed")
        print(f"manifest: {manifest_path}")
        return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
