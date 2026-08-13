#!/usr/bin/env python3
"""Plan op fills for a ROTE planet against the guild roster. Thin wrapper around
swgoh_reviewer.ops.

Usage:
    python rote_ops.py Malachor
    python rote_ops.py Malachor NW4t0-dBRcG8n-PVhykpKg
"""

import sys

from swgoh_reviewer.ops import main

if __name__ == "__main__":
    sys.exit(main())
