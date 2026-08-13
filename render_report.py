#!/usr/bin/env python3
"""Render a self-contained HTML dashboard of a squad report. Thin wrapper around
swgoh_reviewer.dashboard.

Usage:
    python render_report.py <guild_id>
"""

import sys

from swgoh_reviewer.dashboard import main

if __name__ == "__main__":
    sys.exit(main())
