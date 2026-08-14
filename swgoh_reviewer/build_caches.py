#!/usr/bin/env python3
"""Build the game-data caches and unit-name map off-box.

The first guild refresh triggers comlink to build the game-data caches
(localization + units + categories), which spikes comlink's Node heap past the
small box's memory limit and OOMs it. Run this ONCE on a machine with plenty of
RAM against a local swgoh-comlink, then copy the produced files into the
server's data volume (see deploy/DEPLOY.md) so the box never performs the heavy
build. Re-run after a game update.

Usage:
    python build_caches.py [--comlink http://localhost:3200] [--outdir data]
"""

import argparse
import sys

from swgoh_comlink import SwgohComlink

from swgoh_reviewer import gamecache
from swgoh_reviewer.comlink import DEFAULT_COMLINK, build_name_map
from swgoh_reviewer.config import data_root


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--comlink", default=DEFAULT_COMLINK, help="swgoh-comlink base URL")
    parser.add_argument("--outdir", default=str(data_root()), help="data directory to write caches into")
    args = parser.parse_args(argv)

    with SwgohComlink(url=args.comlink) as comlink:
        caches = gamecache.ensure_caches(comlink, args.outdir, refresh=True)
        name_map = build_name_map(comlink, args.outdir, use_cache=False)
    print(f"wrote game caches: {', '.join(sorted(caches))}")
    print(f"wrote name map: {len(name_map)} units")
    return 0


if __name__ == "__main__":
    sys.exit(main())
