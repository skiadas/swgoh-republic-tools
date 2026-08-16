#!/usr/bin/env python3
"""ROTE calculator data builder (the page is server-rendered via htmx now).

Builds the per-guild payload the calculator page renders: the dark/neutral/
light chains plus the special planets (Zeffo, Mandalore), each with star
thresholds, CM max and platoon rewards, from the cached TB doc and roster GP.
The interactive model lives in `calc_logic.py`.
"""

import json
import re
from pathlib import Path

TB_ID = "t05D"
CM_MULTIPLIER = 50


def parse_num(s):
    m = re.match(r"([\d.]+)\s*([MK]?)", (s or "").strip())
    if not m:
        return 0
    v = float(m.group(1))
    if m.group(2) == "M":
        v *= 1e6
    if m.group(2) == "K":
        v *= 1e3
    return int(v)


def build_data(outdir, guild_id, tb_id=TB_ID):
    rote = json.loads((outdir / "rote" / f"{tb_id}.json").read_text())
    summary = json.loads((outdir / "guilds" / f"{guild_id}.summary.json").read_text())
    guild_gp = sum(int(m.get("galacticPower") or 0) for m in summary.get("members", []))

    chains = {"light": [], "dark": [], "neutral": []}
    specials = {"zeffo": None, "mandalore": None}
    for ph in rote.get("phases", []):
        for p in ph.get("planets", []):
            if not p.get("op"):
                continue
            m = re.search(r"conflict(\d+)(_bonus)?", p.get("planetId") or "")
            if not m:
                continue
            idx, bonus = int(m.group(1)), bool(m.group(2))
            reward = parse_num((p["op"]["platoons"][0] or {}).get("reward")) if p["op"].get("platoons") else 0
            cm = sum(sum(m2.get("pointsPerWave") or []) for m2 in p.get("missions", [])) * CM_MULTIPLIER
            rec = {
                "name": p["name"],
                "phase": ph.get("phase"),
                "relicReq": (p.get("op") or {}).get("relicRequirement"),
                "thresholds": [int(x) for x in p.get("starThresholds") or [0, 0, 0]],
                "cmMax": cm,
                "platoonReward": reward,
                "platoonsTotal": len(p["op"].get("platoons") or []),
            }
            if bonus:
                key = "zeffo" if idx == 1 else ("mandalore" if idx == 3 else None)
                if key and specials[key] is None:
                    specials[key] = rec
            elif idx == 1:
                chains["light"].append(rec)
            elif idx == 2:
                chains["dark"].append(rec)
            elif idx == 3:
                chains["neutral"].append(rec)

    return {
        "guildName": summary.get("guildName", guild_id),
        "guildGp": guild_gp,
        "chains": [
            {"id": "light", "name": "Light Side", "planets": chains["light"]},
            {"id": "dark", "name": "Dark Side", "planets": chains["dark"]},
            {"id": "neutral", "name": "Neutral", "planets": chains["neutral"]},
        ],
        "specials": [
            {"id": "zeffo", "name": "Zeffo", "chain": "light", "triggerIndex": 2, "triggerName": "Bracca", "planet": specials["zeffo"]},
            {"id": "mandalore", "name": "Mandalore", "chain": "neutral", "triggerIndex": 3, "triggerName": "Tatooine", "planet": specials["mandalore"]},
        ],
    }
