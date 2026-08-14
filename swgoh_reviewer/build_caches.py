#!/usr/bin/env python3
"""Build the game-data caches and unit-name map from the static gamedata repo.

Sourced from the swgoh-utils/gamedata repo instead of swgoh-comlink, so no
comlink (and its baked-in heap cap / OOM risk) is involved. Run after a game
update to refresh data/game/ and names.json, then copy the files into the
server's data volume if needed (see deploy/DEPLOY.md).

Usage:
    python build_caches.py [--base-url <gamedata repo>] [--outdir data]
"""

import argparse
import sys

from swgoh_reviewer import gamecache
from swgoh_reviewer.comlink import build_name_map
from swgoh_reviewer.config import data_root, gamedata_base_url
from swgoh_reviewer.static_gamedata import StaticGameData


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default=gamedata_base_url(), help="swgoh-utils/gamedata base URL")
    parser.add_argument("--outdir", default=str(data_root()), help="data directory to write caches into")
    args = parser.parse_args(argv)

    if not args.base_url:
        parser.error("static game data is disabled (SWGOH_GAMEDATA_BASE unset/empty)")

    static = StaticGameData(base_url=args.base_url, outdir=args.outdir)
    versions = static.ensure_raw(refresh=True)
    caches = gamecache.ensure_caches(static, args.outdir, refresh=True)
    name_map = build_name_map(static, args.outdir, use_cache=False)
    print(f"game version: {versions.get('gameVersion')}")
    print(f"wrote game caches: {', '.join(sorted(caches))}")
    print(f"wrote name map: {len(name_map)} units")
    return 0


if __name__ == "__main__":
    sys.exit(main())
