"""Canonical ROTE planet display order: Dark, Neutral, Light, then specials.

Every page that lists planets renders them in the same sequence so the set of
planets never appears reordered between views. The three chains are keyed by
id ("light"/"dark"/"neutral", the middle one displaying as "Neutral"); the
special planets are "zeffo" and "mandalore".
"""

import re

# Canonical display/order key for a chain id or special planet id.
PLANET_ORDER = {"dark": 0, "neutral": 1, "light": 2, "zeffo": 3, "mandalore": 4}

# planetId conflicts are numbered conflict<idx>(_bonus)?; idx 1 = light,
# 2 = dark, 3 = neutral, and the bonus conflicts are Zeffo (1) and
# Mandalore (3).
_CONFLICT = re.compile(r"conflict(\d+)(_bonus)?")
_CHAIN_BY_IDX = {1: "light", 2: "dark", 3: "neutral"}
_SPECIAL_BY_IDX = {1: "zeffo", 3: "mandalore"}


def chain_of(planet_id):
    """Map a planetId to its chain/special id, or None if unrecognized."""
    m = _CONFLICT.search(planet_id or "")
    if not m:
        return None
    idx = int(m.group(1))
    bonus = bool(m.group(2))
    if bonus:
        return _SPECIAL_BY_IDX.get(idx)
    return _CHAIN_BY_IDX.get(idx)


def planet_key(planet_id):
    """Canonical display key for a planetId; unrecognized planets sort last."""
    return PLANET_ORDER.get(chain_of(planet_id), 9)
