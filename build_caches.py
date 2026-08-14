#!/usr/bin/env python3
"""Build the game-data caches + unit-name map off-box (see swgoh_reviewer.build_caches).
Thin wrapper for local use.

Usage:
    python build_caches.py [--comlink http://localhost:3200] [--outdir data]
"""

import sys

from swgoh_reviewer.build_caches import main

if __name__ == "__main__":
    sys.exit(main())
