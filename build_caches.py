#!/usr/bin/env python3
"""Build the game-data caches + unit-name map (see swgoh_reviewer.build_caches).
Thin wrapper for local use.
"""

import sys

from swgoh_reviewer.build_caches import main

if __name__ == "__main__":
    sys.exit(main())
