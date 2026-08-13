#!/usr/bin/env python3
"""Document a Territory Battle (default ROTE, t05D) locally.

Two data sources are merged:
  1. swgoh.gg "Territory Battle Platoons" pages saved by the user as
     data/swgoh-gg-ops*.html (one per phase). Each file lists the ops
     (one per planet), their relic requirement, and platoons 1-6 with the
     exact units (baseId + display name) and rewards.
  2. swgoh-comlink game data: the TB structure (phases, planets/conflicts,
     combat missions with their deploy requirements from entryCategoryAllowed,
     enemies, rewards) plus recon/ops zone metadata.

Raw comlink collections are cached under data/rote/ so re-runs are offline
(--refresh re-fetches). Outputs:
    data/rote/<tbId>.json   structured doc
    data/rote/<tbId>.md     human-readable dump

Usage:
    python rote.py                 # t05D (Rise of the Empire)
    python rote.py --refresh
"""

import argparse
import html as _html
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

from swgoh_comlink import SwgohComlink

from swgoh_reviewer.comlink import DEFAULT_COMLINK
from swgoh_reviewer.config import data_root

TB_ID = "t05D"
TB_SLICED = {"campaign", "territoryBattleDefinition"}

def _norm(s):
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


# Canonical per-phase planet/op names (from swgoh.gg). Used to identify which
# phase each saved file contains and to merge ops into the comlink planets.
PHASE_PLANETS = {
    1: ["Coruscant", "Mustafar", "Corellia"],
    2: ["Bracca", "Geonosis", "Felucia"],
    3: ["Kashyyyk", "Zeffo", "Dathomir", "Tatooine"],
    4: ["Lothal", "Haven-class Medical Station", "Kessel", "Mandalore"],
    5: ["Ring of Kafrene", "Malachor", "Vandor"],
    6: ["Scarif", "Death Star", "Hoth"],
}
PHASE_NORM = {phase: {_norm(p) for p in planets} for phase, planets in PHASE_PLANETS.items()}

NAV_HEADINGS = {
    "units", "gac", "tierlists", "reports", "database", "campaigns", "gopremium",
    "grandarena", "toolsstats", "gamedata", "community", "ournetwork",
}


def parse_ops_file(path):
    """Parse one saved swgoh.gg platoons page into a list of op dicts."""
    doc = _html.unescape(path.read_text())
    ops = []
    headers = list(re.finditer(r'<div class="panel__header">', doc))
    for i, m in enumerate(headers):
        end = headers[i + 1].start() if i + 1 < len(headers) else len(doc)
        section = doc[m.start():end]
        h3 = re.search(r"<h3[^>]*>([^<]+)</h3>", section)
        if not h3:
            continue
        name = _html.unescape(h3.group(1)).strip()
        if _norm(name) in NAV_HEADINGS:
            continue
        badge = re.search(r"relic-badge.*?<text[^>]*>(\d+)</text>", section, re.S)
        platoons = []
        inners = list(re.finditer(r'<div class="panel panel--inner">', section))
        for j, pm in enumerate(inners):
            pend = inners[j + 1].start() if j + 1 < len(inners) else len(section)
            psec = section[pm.start():pend]
            pnum = re.search(r"<span>\s*Platoon (\d)\s*</span>", psec)
            reward = re.search(r"text-gg-gray-300\">([^<]+)</span>", psec)
            units = []
            for um in re.finditer(r'<a[^>]*?title="([^"]*)"[^>]*>(.*?)</a>', psec, re.S):
                bid = re.search(r'data-unit-def-tooltip-app="([^"]+)"', um.group(2))
                if not bid:
                    continue
                units.append({"baseId": bid.group(1), "name": _html.unescape(um.group(1)).strip()})
            platoons.append(
                {
                    "platoon": int(pnum.group(1)) if pnum else None,
                    "reward": reward.group(1) if reward else None,
                    "units": units,
                }
            )
        ops.append(
            {
                "name": name,
                "relicRequirement": int(badge.group(1)) if badge else None,
                "platoons": platoons,
            }
        )
    return ops


def detect_phase_and_filter(ops):
    """Return (phase, ops-filtered-to-that-phase) or (None, [])."""
    names = {_norm(o["name"]) for o in ops}
    best, best_count = None, -1
    for phase, canon in PHASE_NORM.items():
        overlap = len(names & canon)
        if overlap > best_count:
            best, best_count = phase, overlap
    if best is None or best_count == 0:
        return None, []
    return best, [o for o in ops if _norm(o["name"]) in PHASE_NORM[best]]


def load_swgoh_ops(outdir):
    ops_by_phase = {}
    for path in sorted(outdir.glob("swgoh-gg-ops*.html")):
        ops = parse_ops_file(path)
        phase, filtered = detect_phase_and_filter(ops)
        if phase is None or not filtered:
            print(f"warning: could not identify phase in {path.name}", file=sys.stderr)
            continue
        if phase in ops_by_phase:
            print(f"warning: duplicate phase {phase} from {path.name}", file=sys.stderr)
        ops_by_phase[phase] = filtered
    return ops_by_phase


class Resolver:
    """Resolve localization keys, categories, unit/enemy ids to display names."""

    def __init__(self, outdir):
        self.names = {}
        p = outdir / "names.json"
        if p.exists():
            self.names = json.loads(p.read_text())
        self.loc = {}
        p = outdir / "game" / "localization.json"
        if p.exists():
            self.loc = json.loads(p.read_text())
        self.cats = {}
        p = outdir / "game" / "categories.json"
        if p.exists():
            self.cats = json.loads(p.read_text())
        self.de = {}

    def set_displayable(self, de_list):
        self.de = {e.get("id"): e.get("nameKey") for e in (de_list or [])}

    def text(self, key):
        return self.loc.get(key, key) if key else None

    def unit_name(self, base_id):
        return self.names.get(base_id, base_id)

    def category_name(self, cid):
        desc = self.cats.get(cid, {}).get("descKey")
        return self.text(desc) if desc else cid

    def enemy_name(self, eid):
        if not eid:
            return eid
        nk = self.de.get(eid)
        if nk:
            return self.text(nk)
        base = eid.split(":")[0]
        for prefix in ("PVE_TB_", "PVE_", "TB_"):
            if base.startswith(prefix):
                cand = base[len(prefix):]
                if cand in self.names:
                    return self.names[cand]
        if base.endswith("_PVE"):
            cand = base[:-4]
            if cand in self.names:
                return self.names[cand]
        return self.names.get(base, eid)


def fetch_raw(outdir, tb_id, refresh):
    raw_dir = outdir / "rote"
    raw_dir.mkdir(parents=True, exist_ok=True)
    cache = {}
    for key in ("territoryBattleDefinition", "campaign", "displayableEnemy", "table"):
        path = raw_dir / f"{key}.json"
        cache[key] = json.loads(path.read_text()) if not refresh and path.exists() else None
    if any(v is None for v in cache.values()):
        with SwgohComlink(url=DEFAULT_COMLINK) as comlink:
            for key, val in cache.items():
                if val is None:
                    data = comlink.get_game_data(include_pve_units=False, items=key)
                    rows = data.get(key, [])
                    # Only the target TB is needed; the full campaign collection
                    # (all game modes) is ~91MB vs the ~683KB t05D slice.
                    if key in TB_SLICED:
                        rows = [x for x in rows if x.get("id") == tb_id]
                    cache[key] = rows
                    (raw_dir / f"{key}.json").write_text(json.dumps(cache[key], separators=(",", ":"), ensure_ascii=False))
    return (
        cache["territoryBattleDefinition"],
        cache["campaign"],
        cache["displayableEnemy"],
        cache["table"],
    )


def phase_of_zone(zone_id):
    m = re.search(r"phase(\d+)", zone_id or "")
    return int(m.group(1)) if m else None


def find_mission(campaign, mission_id):
    for tb in campaign:
        if tb.get("id") != TB_ID:
            continue
        maps = tb.get("campaignMap") or []
        if not maps:
            continue
        for grp in maps[0].get("campaignNodeDifficultyGroup", []):
            for node in grp.get("campaignNode", []):
                for mission in node.get("campaignNodeMission", []):
                    if mission.get("id") == mission_id:
                        return mission
    return None


def extract_deploy(ec):
    if not ec:
        return None
    return {
        "allowedCategories": [c for c in ec.get("categoryId") or []],
        "mandatoryUnits": [u.get("id") for u in ec.get("mandatoryRosterUnit") or []],
        "excludedCategories": [c for c in ec.get("excludeCategoryId") or []],
        "deployCount": ec.get("minimumRequiredUnitQuantity"),
        "maxDeploy": ec.get("maximumAllowedUnitQuantity"),
        "minRelic": ec.get("minimumRelicTier"),
        "minRarity": ec.get("minimumUnitRarity"),
        "minModRarity": ec.get("minimumModRarity"),
        "minUnitTier": ec.get("minimumUnitTier"),
        "minUnitLevel": ec.get("minimumUnitLevel"),
        "legendLimit": ec.get("legendLimit"),
        "matchType": ec.get("matchType"),
    }


def extract_enemies(mission):
    out = []
    for e in mission.get("enemyUnitPreview") or []:
        item = e.get("baseEnemyItem") or {}
        if not item.get("id"):
            continue
        out.append(
            {
                "id": item.get("id"),
                "level": e.get("enemyLevel"),
                "tier": e.get("enemyTier"),
                "threatLevel": e.get("threatLevel"),
            }
        )
    return out


def extract_rewards(mission):
    out = []
    seen = set()
    for field in (
        "rewardPreview",
        "firstCompleteRewardPreview",
        "instanceFirstCompleteRewardPreview",
        "immediateRegularRankRewardPreview",
        "rankRewardPreview",
    ):
        rl = mission.get(field) or []
        if not isinstance(rl, list):
            continue
        for item in rl:
            if not isinstance(item, dict) or not item.get("id"):
                continue
            key = (field, item["id"])
            if key in seen:
                continue
            seen.add(key)
            out.append(
                {
                    "source": field,
                    "id": item["id"],
                    "type": item.get("type"),
                    "qty": item.get("maxQuantity") or item.get("minQuantity"),
                }
            )
    return out


def extract_mission(mission):
    return {
        "nameKey": mission.get("nameKey") or mission.get("shortNameKey"),
        "deploy": extract_deploy(mission.get("entryCategoryAllowed")),
        "enemies": extract_enemies(mission),
        "rewards": extract_rewards(mission),
    }


def points_from_table(table_id, tables):
    """Per-wave galactic-score deltas from an encounter reward table.

    Table rows are cumulative GALACTIC_SCORE per wave completed (key 0, 1, 2...);
    the per-wave points are the deltas between consecutive rows.
    """
    if not table_id:
        return None
    for t in tables:
        if t.get("id") != table_id:
            continue
        rows = sorted(t.get("row") or [], key=lambda r: int(r.get("key", 0) or 0))
        scores = []
        for r in rows:
            val = (r.get("value") or "").split(":", 1)
            if len(val) == 2:
                try:
                    scores.append(int(val[1]))
                except ValueError:
                    pass
        if len(scores) >= 2:
            return [scores[i] - scores[i - 1] for i in range(1, len(scores))]
    return None


def build_doc(tb, campaign, resolver, swgoh_ops, tables):
    conflicts = {}
    for cz in tb.get("conflictZoneDefinition") or []:
        zd = cz.get("zoneDefinition") or {}
        zid = zd.get("zoneId")
        if not zid:
            continue
        conflicts[zid] = {
            "planetId": zid,
            "phase": phase_of_zone(zid),
            "nameKey": zd.get("nameKey"),
            "descriptionKey": zd.get("descriptionKey"),
            "starThresholds": [
                int(v["galacticScoreRequirement"])
                for v in cz.get("victoryPointRewards") or []
                if v.get("galacticScoreRequirement")
            ],
            "missions": [],
            "recon": None,
            "op": None,
        }

    def attach_zone(zone, kind):
        zd = zone.get("zoneDefinition") or {}
        conflict = conflicts.get(zd.get("linkedConflictId"))
        if conflict is None:
            return
        mission = find_mission(campaign, (zone.get("campaignElementIdentifier") or {}).get("campaignMissionId"))
        entry = {
            "kind": kind,
            "missionId": (zone.get("campaignElementIdentifier") or {}).get("campaignMissionId"),
            "zoneId": zd.get("zoneId"),
            "nameKey": zd.get("nameKey"),
            "combatType": zone.get("combatType"),
            "waves": None,
            "pointsPerWave": None,
            "deploy": None,
            "enemies": [],
            "rewards": [],
        }
        if mission is not None:
            entry.update(extract_mission(mission))
            waves = len(mission.get("campaignNodeEncounter") or []) or None
            entry["waves"] = waves
            deltas = points_from_table(zone.get("encounterRewardTableId"), tables)
            entry["pointsPerWave"] = deltas[:waves] if deltas and waves else deltas
        conflict["missions"].append(entry)

    for sz in tb.get("strikeZoneDefinition") or []:
        attach_zone(sz, "combat")
    for cz in tb.get("covertZoneDefinition") or []:
        attach_zone(cz, "covert")
    for bz in tb.get("bonusZoneDefinition") or []:
        zd = bz.get("zoneDefinition") or {}
        conflict = conflicts.get(bz.get("linkedBonusConflictId"))
        if conflict is not None:
            conflict["missions"].append(
                {"kind": "bonus", "missionId": None, "zoneId": zd.get("zoneId"), "nameKey": zd.get("nameKey")}
            )

    for rz in tb.get("reconZoneDefinition") or []:
        zd = rz.get("zoneDefinition") or {}
        conflict = conflicts.get(zd.get("linkedConflictId"))
        if conflict is not None:
            conflict["recon"] = {
                "zoneId": zd.get("zoneId"),
                "nameKey": zd.get("nameKey"),
                "rarity": rz.get("unitRarity"),
                "relicTier": rz.get("unitRelicTier"),
            }

    # merge swgoh.gg ops into the matching planet (by phase + normalized name)
    for phase, ops in swgoh_ops.items():
        for op in ops:
            target = next(
                (c for c in conflicts.values() if c["phase"] == phase and _norm(resolver.text(c["nameKey"])) == _norm(op["name"])),
                None,
            )
            if target is None:
                placeholder = {
                    "planetId": None,
                    "phase": phase,
                    "nameKey": None,
                    "descriptionKey": None,
                    "starThresholds": [],
                    "missions": [],
                    "recon": None,
                    "op": None,
                }
                conflicts[f"_orphan_{phase}_{_norm(op['name'])}"] = placeholder
                target = placeholder
            target["op"] = op

    # finalize: order phases/planets, resolve names
    doc = {
        "tbId": tb.get("id"),
        "name": resolver.text(tb.get("nameKey")),
        "rounds": tb.get("roundCount"),
        "phases": [],
    }
    by_phase = {}
    for conflict in conflicts.values():
        by_phase.setdefault(conflict["phase"], []).append(conflict)
    for phase in sorted(by_phase):
        planets = []
        for c in by_phase[phase]:
            op = None
            if c["op"]:
                op = {
                    "name": c["op"]["name"],
                    "relicRequirement": c["op"]["relicRequirement"],
                    "platoons": [
                        {
                            "platoon": p["platoon"],
                            "reward": p["reward"],
                            "units": [
                                {"baseId": u["baseId"], "name": resolver.unit_name(u["baseId"])}
                                for u in p["units"]
                            ],
                        }
                        for p in c["op"]["platoons"]
                    ],
                }
            planets.append(
                {
                    "planetId": c["planetId"],
                    "name": resolver.text(c["nameKey"]),
                    "description": resolver.text(c["descriptionKey"]),
                    "starThresholds": c["starThresholds"],
                    "op": op,
                    "recon": (
                        {
                            "name": resolver.text(c["recon"]["nameKey"]),
                            "rarity": c["recon"]["rarity"],
                            "relicTier": c["recon"]["relicTier"],
                        }
                        if c["recon"]
                        else None
                    ),
                    "missions": [
                        {
                            "kind": m.get("kind", "combat"),
                            "missionId": m.get("missionId"),
                            "name": resolver.text(m.get("nameKey")),
                            "combatType": m.get("combatType"),
                            "waves": m.get("waves"),
                            "pointsPerWave": m.get("pointsPerWave"),
                            "deploy": resolve_deploy(m.get("deploy"), resolver),
                            "enemies": [
                                {
                                    "name": resolver.enemy_name(e["id"]),
                                    "level": e["level"],
                                    "tier": e["tier"],
                                    "threatLevel": e["threatLevel"],
                                }
                                for e in m.get("enemies") or []
                            ],
                            "rewards": m.get("rewards") or [],
                        }
                        for m in c["missions"]
                    ],
                }
            )
        doc["phases"].append({"phase": phase, "planets": planets})
    return doc


def resolve_deploy(deploy, resolver):
    if not deploy:
        return None
    return {
        "allowedFactions": [
            {"id": cid, "name": resolver.category_name(cid)} for cid in deploy["allowedCategories"]
        ],
        "mandatoryUnits": [
            {"baseId": uid, "name": resolver.unit_name(uid)} for uid in deploy["mandatoryUnits"]
        ],
        "excludedFactions": [
            {"id": cid, "name": resolver.category_name(cid)} for cid in deploy["excludedCategories"]
        ],
        "deployCount": deploy["deployCount"],
        "maxDeploy": deploy["maxDeploy"],
        "minRelic": deploy["minRelic"],
        "minRarity": deploy["minRarity"],
        "minModRarity": deploy["minModRarity"],
        "minUnitTier": deploy["minUnitTier"],
        "minUnitLevel": deploy["minUnitLevel"],
        "legendLimit": deploy["legendLimit"],
        "matchType": deploy["matchType"],
    }


def fmt_deploy(d):
    if not d:
        return "—"
    parts = [f"{d['deployCount'] or d['maxDeploy'] or '?'} units"]
    if d["allowedFactions"]:
        names = ", ".join(f["name"] for f in d["allowedFactions"])
        parts.append(f"[{names}]")
    if d["mandatoryUnits"]:
        parts.append("mandatory: " + ", ".join(u["name"] for u in d["mandatoryUnits"]))
    reqs = []
    if d["minRelic"]:
        reqs.append(f"R{d['minRelic']}")
    if d["minRarity"]:
        reqs.append(f"{d['minRarity']}*")
    if d["minModRarity"]:
        reqs.append(f"mods {d['minModRarity']}")
    if reqs:
        parts.append("min " + ", ".join(reqs))
    if d["legendLimit"]:
        parts.append(f"max {d['legendLimit']} GL")
    return "; ".join(parts)


def write_markdown(doc):
    lines = [f"# {doc['name']} ({doc['tbId']}) — {doc['rounds']} rounds", ""]
    for ph in doc["phases"]:
        lines.append(f"## Phase {ph['phase']}")
        for planet in ph["planets"]:
            lines.append(f"\n### {planet['name']}")
            if planet.get("description"):
                lines.append(f"*{planet['description']}*")
            if planet.get("starThresholds"):
                lines.append("Star thresholds: " + " · ".join(f"{n}★ {s:,}" for n, s in zip((1, 2, 3), planet["starThresholds"])))
            op = planet.get("op")
            if op:
                lines.append(f"\n**Operation** — relic R{op['relicRequirement']}")
                lines.append("| Platoon | Reward | Units |")
                lines.append("|---|---|---|")
                for p in op["platoons"]:
                    units = ", ".join(u["name"] for u in p["units"])
                    lines.append(f"| {p['platoon']} | {p['reward'] or ''} | {units} |")
            else:
                lines.append("\n*No op data.*")
            recon = planet.get("recon")
            if recon:
                lines.append(f"\nRecon zone: {recon['name']} (rarity {recon['rarity']}, relic R{recon['relicTier']})")
            if planet["missions"]:
                lines.append("\n**Missions:**")
                for m in planet["missions"]:
                    extra = f" ({m['kind']})" if m.get("kind") and m["kind"] != "combat" else ""
                    label = m.get("name") or m.get("missionId")
                    if m.get("name") and m["name"] == m.get("missionId"):
                        label = m["name"]
                    elif m.get("name") and re.fullmatch(r"[IVX]+", m["name"] or ""):
                        label = m["missionId"]
                    waves = f"{m['waves']} wave" + ("s" if m.get("waves") != 1 else "") if m.get("waves") else ""
                    pts = f" · pts/wave: {', '.join(f'{p:,}' for p in m['pointsPerWave'])}" if m.get("pointsPerWave") else ""
                    lines.append(f"- {label}{extra}: {fmt_deploy(m.get('deploy'))}" + (f" — {waves}{pts}" if waves or pts else ""))
                    if m.get("enemies"):
                        names = ", ".join(f"{e['name']}" for e in m["enemies"][:6])
                        more = " …" if len(m["enemies"]) > 6 else ""
                        lines.append(f"  - enemies: {names}{more}")
            else:
                lines.append("\n*No missions.*")
    return "\n".join(lines) + "\n"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("tb_id", nargs="?", default=TB_ID)
    parser.add_argument("--outdir", type=Path, default=data_root())
    parser.add_argument("--refresh", action="store_true", help="re-fetch raw comlink collections")
    args = parser.parse_args(argv)

    outdir = args.outdir
    resolver = Resolver(outdir)
    swgoh_ops = load_swgoh_ops(outdir)
    if len(swgoh_ops) != 6:
        print(f"warning: expected 6 phases of ops, found {len(swgoh_ops)}: {sorted(swgoh_ops)}", file=sys.stderr)

    tbd, camp, de, tables = fetch_raw(outdir, args.tb_id, args.refresh)
    resolver.set_displayable(de)
    tb = next((x for x in tbd if x.get("id") == args.tb_id), None)
    if tb is None:
        print(f"TB '{args.tb_id}' not found in game data", file=sys.stderr)
        return 2

    doc = build_doc(tb, camp, resolver, swgoh_ops, tables)
    doc["generatedAt"] = datetime.now(timezone.utc).isoformat()

    outdir = outdir / "rote"
    outdir.mkdir(parents=True, exist_ok=True)
    json_path = outdir / f"{args.tb_id}.json"
    md_path = outdir / f"{args.tb_id}.md"
    json_path.write_text(json.dumps(doc, indent=2, ensure_ascii=False))
    md_path.write_text(write_markdown(doc))
    print(f"wrote {json_path} ({json_path.stat().st_size / 1e6:.2f} MB)")
    print(f"wrote {md_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
