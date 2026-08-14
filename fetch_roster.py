#!/usr/bin/env python3
"""Fetch full SWGOH player rosters from a local swgoh-comlink service.

Requires swgoh-comlink running locally (see start_comlink.sh). One request per
ally code returns the entire roster, so no swgoh.gg scraping is involved.

Usage:
    python fetch_roster.py 679577173 [more_allycodes ...]
"""

import argparse
import sys
from pathlib import Path

from swgoh_comlink import SwgohComlink

from swgoh_reviewer.comlink import (
    DEFAULT_COMLINK,
    build_name_map,
    fetch_player,
    retry,
    summarize,
)
from swgoh_reviewer.config import data_root, gamedata_base_url
from swgoh_reviewer.static_gamedata import StaticGameData


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("allycodes", nargs="+", type=str, help="player ally codes")
    parser.add_argument("--comlink", default=DEFAULT_COMLINK, help="swgoh-comlink base URL")
    parser.add_argument("--outdir", default=str(data_root()), help="output directory")
    parser.add_argument("--refresh-names", action="store_true", help="rebuild the defId->name cache")
    args = parser.parse_args(argv)

    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    with SwgohComlink(url=args.comlink) as comlink:
        game_source = StaticGameData(outdir=outdir) if gamedata_base_url() else comlink
        name_map = retry(lambda: build_name_map(game_source, outdir, use_cache=not args.refresh_names))
        print(f"name map: {len(name_map)} units")
        for allycode in args.allycodes:
            player, outpath = retry(
                lambda a=allycode: fetch_player(comlink, allycode=a, name_map=name_map, outdir=outdir)
            )
            print(f"saved: {outpath}")
            print(summarize(player))
            print()


if __name__ == "__main__":
    sys.exit(main())
