#!/usr/bin/env python3
"""ROTE day-by-day star calculator — interactive self-contained HTML page.
Thin wrapper around swgoh_reviewer.calc.

Usage:
    python rote_calc.py
    python rote_calc.py NW4t0-dBRcG8n-PVhykpKg
"""

import sys

from swgoh_reviewer.calc import main

if __name__ == "__main__":
    sys.exit(main())
