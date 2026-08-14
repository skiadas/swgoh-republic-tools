#!/usr/bin/env python3
"""Rebuild a guild summary from already-downloaded raw rosters (offline).

Reads data/<allyCode>.json rosters + game-data caches and regenerates
data/guilds/<guildId>.summary.json without touching comlink for player data.
The hosted service uses the streaming path (fetch_guild.py) instead; this
script remains a local/dev rebuild tool.

Usage:
    python guild_summary.py NW4t0-dBRcG8n-PVhykpKg
    python guild_summary.py NW4t0-dBRcG8n-PVhykpKg --refresh-game
"""

import argparse
import sys

from swgoh_reviewer import gamecache, pipeline
from swgoh_reviewer.config import data_root, gamedata_base_url
from swgoh_reviewer.static_gamedata import StaticGameData


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("guild_id")
    parser.add_argument("--outdir", default=str(data_root()), help="output directory")
    parser.add_argument("--refresh-game", action="store_true", help="rebuild game-data caches from the static gamedata repo")
    parser.add_argument("--pretty", action="store_true", help="write indented JSON (debug)")
    args = parser.parse_args(argv)

    if args.refresh_game:
        if not gamedata_base_url():
            parser.error("static game data is disabled (SWGOH_GAMEDATA_BASE unset/empty)")
        static = StaticGameData(outdir=args.outdir)
        static.ensure_raw(refresh=True)
        gamecache.ensure_caches(static, args.outdir, refresh=True)

    summary = pipeline.summarize_from_files(args.outdir, args.guild_id, pretty=args.pretty)
    return 0 if summary else 2


if __name__ == "__main__":
    sys.exit(main())
