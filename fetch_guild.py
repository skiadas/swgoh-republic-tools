#!/usr/bin/env python3
"""Fetch a guild and write its manifest + compact roster summary, streaming.

Resolves a guild from a player's ally code (or --guild-id), then downloads
each member's roster with a client-side rate limit (default 4 req/s) and
reduces it to a summary entry on the fly. Raw rosters are NOT persisted —
only data/guilds/<guildId>.json (manifest, incl. memberLevel roles) and
data/guilds/<guildId>.summary.json are written.

Usage:
    python fetch_guild.py 679577173
    python fetch_guild.py --guild-id NW4t0-dBRcG8n-PVhykpKg
"""

import argparse
import sys

from swgoh_reviewer import pipeline
from swgoh_reviewer.config import data_root


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("allycode", nargs="?", type=str, help="a player's ally code to resolve their guild")
    parser.add_argument("--guild-id", type=str, help="guild id (skips ally-code resolution)")
    parser.add_argument("--comlink", default=None, help="swgoh-comlink base URL")
    parser.add_argument("--outdir", default=str(data_root()), help="output directory")
    parser.add_argument("--max-rps", type=float, default=4.0, help="max requests per second (default 4)")
    parser.add_argument("--refresh", action="store_true", help="accepted for back-compat; fetches are always fresh")
    parser.add_argument("--limit", type=int, default=0, help="only fetch the first N members (0 = all)")
    parser.add_argument("--refresh-names", action="store_true", help="rebuild the defId->name cache")
    parser.add_argument("--refresh-game", action="store_true", help="rebuild game-data caches")
    parser.add_argument("--pretty", action="store_true", help="write indented JSON (debug)")
    args = parser.parse_args(argv)

    if not args.guild_id and not args.allycode:
        parser.error("provide an ally code or --guild-id")

    manifest, summary = pipeline.refresh_guild(
        outdir=args.outdir,
        guild_id=args.guild_id,
        allycode=args.allycode,
        comlink_url=args.comlink,
        max_rps=args.max_rps,
        refresh_names=args.refresh_names,
        refresh_game=args.refresh_game,
        limit=args.limit,
        pretty=args.pretty,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
