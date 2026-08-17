#!/usr/bin/env python3
"""Environment-driven configuration for the SWGOH reviewer.

All paths and endpoints are overridable via env vars so the same code runs
locally, in tests, and in the hosted service:
    SWGOH_DATA_ROOT       data directory (default: <project>/data)
    SWGOH_COMLINK         swgoh-comlink base URL (default: http://localhost:3200)
    SWGOH_GAMEDATA_BASE   swgoh-utils/gamedata base URL; set to "" to disable the
                          static game-data source and fall back to comlink
                          (default: https://raw.githubusercontent.com/swgoh-utils/gamedata/main)
    SWGOH_ASSET_BASE      swgoh-ae2 base URL for the 2D texture extractor
                          (default: http://ae2:8080 — the compose service)
"""

import os
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent

GAMEDATA_BASE = "https://raw.githubusercontent.com/swgoh-utils/gamedata/main"


def data_root() -> Path:
    return Path(os.environ.get("SWGOH_DATA_ROOT", PROJECT / "data"))


def comlink_url() -> str:
    return os.environ.get("SWGOH_COMLINK", "http://localhost:3200")


def gamedata_base_url() -> str:
    """Base URL of the static swgoh-utils/gamedata repo ("" disables it)."""
    return os.environ.get("SWGOH_GAMEDATA_BASE", GAMEDATA_BASE)


def asset_base_url() -> str:
    """Base URL of the swgoh-ae2 texture-extractor service ("" disables it)."""
    return os.environ.get("SWGOH_ASSET_BASE", "http://ae2:8080")
