#!/usr/bin/env python3
"""Document a Territory Battle (default ROTE, t05D) locally. Thin wrapper around
swgoh_reviewer.tb. See that module's docstring for details.

Usage:
    python rote.py                 # t05D (Rise of the Empire)
    python rote.py --refresh
"""

import sys

from swgoh_reviewer.tb import main

if __name__ == "__main__":
    sys.exit(main())
