#!/usr/bin/env python3
"""Evaluate squad requirements (squads.json) against downloaded guild data.
Thin wrapper around swgoh_reviewer.squads.

Usage:
    python squad_report.py <guild_id>
"""

import sys

from swgoh_reviewer.squads import main

if __name__ == "__main__":
    sys.exit(main())
