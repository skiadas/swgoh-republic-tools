#!/usr/bin/env python3
"""ROTE platoon assignment planner — interactive self-contained HTML page.
Thin wrapper around swgoh_reviewer.platoons.

Usage:
    python rote_platoons.py
    python rote_platoons.py NW4t0-dBRcG8n-PVhykpKg
"""

import sys

from swgoh_reviewer.platoons import main

if __name__ == "__main__":
    sys.exit(main())
