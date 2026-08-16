#!/usr/bin/env python3
"""Assignments by member — read-only roster page for a guild's platoon plan.
Thin wrapper around swgoh_reviewer.assignments.

Usage:
    python rote_assignments.py
    python rote_assignments.py NW4t0-dBRcG8n-PVhykpKg
"""

import sys

from swgoh_reviewer.assignments import main

if __name__ == "__main__":
    sys.exit(main())
