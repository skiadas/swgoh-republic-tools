#!/usr/bin/env python3
"""Evaluate squad requirements (squads.json) against downloaded guild data.

Reads only local files - no comlink, no player re-fetching:
    data/guilds/<guildId>.summary.json   collected guild rosters
    squads.json + squads.schema.json     requirements (see squads.md)
    data/names.json (+ data/game/*)      optional name/tag lookups for warnings

Writes data/guilds/<guildId>.squads.json with a `bySquad` (full detail) and a
`byPlayer` (compact) view, and prints a console summary.

Usage:
    python squad_report.py NW4t0-dBRcG8n-PVhykpKg
    python squad_report.py NW4t0-dBRcG8n-PVhykpKg --player 679577173
"""

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import jsonschema
from jsonschema import validate

from swgoh_reviewer.config import PROJECT, data_root
from swgoh_reviewer.io import atomic_write_text

DEFAULT_SQUADS = PROJECT / "squads.json"
DEFAULT_SCHEMA = PROJECT / "squads.schema.json"

MISSING_WEIGHT = 2
UPGRADE_WEIGHT = 1
DEFAULT_THRESHOLDS = [0, 1, 3, 5, 7, 8, 9]


def load_squads(squads_path, schema_path):
    squads = json.loads(squads_path.read_text())
    schema = json.loads(schema_path.read_text())
    validate(instance=squads, schema=schema)
    for category in squads["categories"]:
        for squad in category["squads"]:
            _check_squad_structure(squad, category["name"])
    return squads


def _check_squad_structure(squad, category_name):
    squad["mode"] = squad.get("mode", "minRelic")
    squad["minRelic"] = squad.get("minRelic", 0)
    if squad["mode"] == "commonRelic":
        squad["thresholds"] = sorted(set(squad.get("thresholds", DEFAULT_THRESHOLDS)))
    required = len(_normalize_units(squad["required"], squad["minRelic"]))
    pool = squad.get("pool")
    pool_count = squad.get("poolCount", squad.get("size", 5) - required)
    if required + pool_count != squad.get("size", 5):
        where = f'{category_name} / {squad["name"]}'
        raise ValueError(
            f"inconsistent squad {where!r}: {required} required + {pool_count} pool "
            f"!= size {squad.get('size', 5)}"
        )
    squad["poolCount"] = pool_count
    squad["size"] = squad.get("size", 5)


def _normalize_units(entries, default_min_relic):
    out = []
    for entry in entries:
        if isinstance(entry, str):
            out.append({"name": entry, "minRelic": default_min_relic})
        else:
            out.append({"name": entry["name"], "minRelic": entry.get("minRelic", default_min_relic)})
    return out


def load_known(data_dir):
    """Return (name->baseId, set of known faction tags) for warnings/baseIds."""
    name_map = {}
    names_path = data_dir / "names.json"
    if names_path.exists():
        for base_id, name in json.loads(names_path.read_text()).items():
            name_map[name.strip().lower()] = base_id

    tags = set()
    factions_path = data_dir / "game" / "factions.json"
    if factions_path.exists():
        for name in json.loads(factions_path.read_text()):
            tags.add(name.strip().lower())
    else:
        # fall back to the older cache layout (categories + full localization)
        cat_path = data_dir / "game" / "categories.json"
        loc_path = data_dir / "game" / "localization.json"
        if cat_path.exists() and loc_path.exists():
            cats = json.loads(cat_path.read_text())
            loc = json.loads(loc_path.read_text())
            for cdef in cats.values():
                if cdef.get("visible") and cdef.get("descKey") and cdef["descKey"] in loc:
                    tags.add(loc[cdef["descKey"]].strip().lower())
    return name_map, tags


def load_summary(path):
    return json.loads(path.read_text())


def roster_index(units):
    return {u["name"].strip().lower(): u for u in units}


def unit_status(unit, min_relic):
    if unit is None:
        return "missing"
    relic = unit.get("relicLevel") or 0
    if relic >= min_relic:
        return "met"
    return "upgrade"


def unit_detail(unit, status, name=None, base_id=None, min_relic=None):
    return {
        "name": name or (unit.get("name") if unit else None),
        "baseId": base_id or (unit.get("baseId") if unit else None),
        "status": status,
        "relicLevel": (unit.get("relicLevel") or 0) if unit else None,
        "gearLevel": unit.get("gearLevel") if unit else None,
        "minRelic": min_relic,
    }


def pool_candidates(squad, units, idx, known_names, min_relic):
    req_names = {r["name"].strip().lower() for r in _normalize_units(squad["required"], min_relic)}
    pool = squad.get("pool")
    candidates = []
    if isinstance(pool, list):
        for entry in pool:
            r = _normalize_units([entry], min_relic)[0]
            key = r["name"].strip().lower()
            if key in req_names:
                continue
            unit = idx.get(key)
            candidates.append(
                unit_detail(unit, unit_status(unit, r["minRelic"]), name=r["name"],
                            base_id=known_names.get(key) if not unit else None,
                            min_relic=r["minRelic"])
            )
    elif isinstance(pool, dict):
        tag = pool["tag"].strip().lower()
        for unit in units:
            if unit.get("combatType") != "character":
                continue
            if unit["name"].strip().lower() in req_names:
                continue
            if any(f.strip().lower() == tag for f in unit.get("factions") or []):
                candidates.append(unit_detail(unit, unit_status(unit, min_relic), min_relic=min_relic))
    return candidates


def evaluate_squad(squad, member_units, known_names):
    idx = roster_index(member_units)
    if squad.get("mode") == "commonRelic":
        return _evaluate_common(squad, member_units, idx, known_names)
    return _evaluate_min(squad, member_units, idx, known_names)


def _evaluate_min(squad, member_units, idx, known_names):
    required = _normalize_units(squad["required"], squad["minRelic"])

    required_detail = []
    req_gap = 0
    for r in required:
        unit = idx.get(r["name"].strip().lower())
        status = unit_status(unit, r["minRelic"])
        detail = unit_detail(unit, status, name=r["name"],
                             base_id=known_names.get(r["name"].strip().lower()) if not unit else None,
                             min_relic=r["minRelic"])
        required_detail.append(detail)
        req_gap += MISSING_WEIGHT if status == "missing" else (UPGRADE_WEIGHT if status == "upgrade" else 0)

    pool_count = squad["poolCount"]
    candidates = pool_candidates(squad, member_units, idx, known_names, squad["minRelic"])
    rank = {"met": 0, "upgrade": 1, "missing": 2}
    candidates.sort(key=lambda c: (rank[c["status"]], -(c["relicLevel"] or 0)))
    chosen = candidates[:pool_count]

    pool_met = sum(1 for c in chosen if c["status"] == "met")
    pool_upgrade = sum(1 for c in chosen if c["status"] == "upgrade")
    pool_missing = sum(1 for c in chosen if c["status"] == "missing")
    pool_gap = pool_upgrade * UPGRADE_WEIGHT + pool_missing * MISSING_WEIGHT
    owned = sum(1 for c in candidates if c["status"] != "missing")

    gap = req_gap + pool_gap
    return {
        "complete": gap == 0,
        "gap": gap,
        "required": required_detail,
        "poolCount": pool_count,
        "poolTotal": owned,
        "poolMet": pool_met,
        "poolUpgrade": pool_upgrade,
        "poolMissing": pool_missing,
        "poolChosen": chosen,
    }


def _evaluate_common(squad, member_units, idx, known_names):
    thresholds = squad["thresholds"]
    pool_count = squad["poolCount"]
    required = _normalize_units(squad["required"], 0)

    best = None
    req_detail = []
    chosen = []
    pool_total = 0
    for t in reversed(thresholds):
        req_detail = []
        ok = True
        for r in required:
            unit = idx.get(r["name"].strip().lower())
            status = unit_status(unit, t)
            req_detail.append(
                unit_detail(unit, status, name=r["name"],
                            base_id=known_names.get(r["name"].strip().lower()) if not unit else None)
            )
            if status != "met":
                ok = False
        if not ok:
            continue
        candidates = pool_candidates(squad, member_units, idx, known_names, t)
        met = [c for c in candidates if c["status"] == "met"]
        if len(met) >= pool_count:
            best = t
            chosen = met[:pool_count]
            pool_total = sum(1 for c in candidates if c["status"] != "missing")
            break

    if best is None:
        req_detail = []
        for r in required:
            unit = idx.get(r["name"].strip().lower())
            status = unit_status(unit, 0)
            req_detail.append(
                unit_detail(unit, status, name=r["name"],
                            base_id=known_names.get(r["name"].strip().lower()) if not unit else None)
            )
        candidates = pool_candidates(squad, member_units, idx, known_names, 0)
        candidates.sort(key=lambda c: (0 if c["status"] == "met" else 1, -(c["relicLevel"] or 0)))
        chosen = candidates[:pool_count]
        pool_total = sum(1 for c in candidates if c["status"] != "missing")

    next_t = next((t for t in thresholds if best is not None and t > best), None)
    bottlenecks = []
    if next_t is not None:
        for u in req_detail:
            if (u.get("relicLevel") or 0) < next_t:
                bottlenecks.append(u["name"])
        for c in chosen:
            if (c.get("relicLevel") or 0) < next_t:
                bottlenecks.append(c["name"])

    return {
        "commonRelic": best,
        "nextThreshold": next_t,
        "required": req_detail,
        "poolCount": pool_count,
        "poolTotal": pool_total,
        "poolMet": sum(1 for c in chosen if c["status"] == "met"),
        "poolUpgrade": sum(1 for c in chosen if c["status"] == "upgrade"),
        "poolMissing": sum(1 for c in chosen if c["status"] == "missing"),
        "poolChosen": chosen,
        "bottlenecks": bottlenecks,
    }


def warn_unknown(squads, name_map, known_tags):
    for category in squads["categories"]:
        for squad in category["squads"]:
            where = f'{category["name"]} / {squad["name"]}'
            for entry in _normalize_units(squad["required"], squad["minRelic"]):
                if entry["name"].strip().lower() not in name_map:
                    print(f"warning: unknown unit {entry['name']!r} in {where!r}", file=sys.stderr)
            pool = squad.get("pool")
            if isinstance(pool, list):
                for entry in _normalize_units(pool, squad["minRelic"]):
                    if entry["name"].strip().lower() not in name_map:
                        print(f"warning: unknown unit {entry['name']!r} in {where!r}", file=sys.stderr)
            elif isinstance(pool, dict):
                if pool["tag"].strip().lower() not in known_tags:
                    print(f"warning: unknown tag {pool['tag']!r} in {where!r}", file=sys.stderr)


def build_report(summary, squads, name_map):
    by_squad = []
    by_player = {m["allyCode"]: {c["name"]: {} for c in squads["categories"]} for m in summary["members"]}
    members_by_code = {m["allyCode"]: m for m in summary["members"]}

    for category in squads["categories"]:
        for squad in category["squads"]:
            results = []
            for member in summary["members"]:
                units = member.get("units") or []
                res = evaluate_squad(squad, units, name_map)
                entry = {
                    "playerId": member["playerId"],
                    "allyCode": member["allyCode"],
                    "name": member["name"],
                    "required": res["required"],
                    "poolCount": res["poolCount"],
                    "poolTotal": res["poolTotal"],
                    "poolMet": res["poolMet"],
                    "poolUpgrade": res["poolUpgrade"],
                    "poolMissing": res["poolMissing"],
                    "poolChosen": res["poolChosen"],
                }
                compact = {
                    "requiredMet": sum(1 for r in res["required"] if r["status"] == "met"),
                    "poolMet": res["poolMet"],
                }
                if squad["mode"] == "commonRelic":
                    entry.update(
                        {
                            "commonRelic": res["commonRelic"],
                            "nextThreshold": res["nextThreshold"],
                            "bottlenecks": res["bottlenecks"],
                        }
                    )
                    compact.update(
                        {"commonRelic": res["commonRelic"], "nextThreshold": res["nextThreshold"]}
                    )
                else:
                    entry.update({"complete": res["complete"], "gap": res["gap"]})
                    compact.update({"complete": res["complete"], "gap": res["gap"]})
                results.append(entry)
                by_player[member["allyCode"]][category["name"]][squad["name"]] = compact
            squad_meta = {
                "category": category["name"],
                "squad": squad["name"],
                "mode": squad["mode"],
                "size": squad["size"],
                "poolCount": squad["poolCount"],
                "results": results,
            }
            if squad["mode"] == "commonRelic":
                squad_meta["thresholds"] = squad["thresholds"]
            else:
                squad_meta["minRelic"] = squad["minRelic"]
            by_squad.append(squad_meta)

    return {
        "guildId": summary["guildId"],
        "guildName": summary["guildName"],
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "bySquad": by_squad,
        "byPlayer": [
            {"allyCode": code, "name": members_by_code[code]["name"], "categories": cats}
            for code, cats in by_player.items()
        ],
    }


def print_console_summary(report, player_code=None):
    for squad_group in report["bySquad"]:
        results = squad_group["results"]
        header = f"[{squad_group['category']}] {squad_group['squad']} (size {squad_group['size']}"
        if squad_group["mode"] == "commonRelic":
            header += ", common relic " + "/".join(f"R{t}" for t in squad_group["thresholds"]) + ")"
        else:
            header += f", minRelic {squad_group['minRelic']})"

        if squad_group["mode"] == "commonRelic":
            counts = {}
            for r in results:
                key = "none" if r["commonRelic"] is None else f"R{r['commonRelic']}"
                counts[key] = counts.get(key, 0) + 1
            dist = ", ".join(
                f"{k}:{v}"
                for k, v in sorted(
                    counts.items(),
                    key=lambda kv: (-1,) if kv[0] == "none" else (int(kv[0][1:]),),
                    reverse=True,
                )
            )
            print(header + ": " + dist)
        else:
            complete = sum(1 for r in results if r["complete"])
            gaps = [r["gap"] for r in results]
            avg = sum(gaps) / len(gaps) if gaps else 0
            print(
                header + f": {complete}/{len(results)} complete, avg gap {avg:.1f}, "
                f"min gap {min(gaps) if gaps else 0}"
            )

        if player_code is not None:
            row = next((r for r in results if str(r["allyCode"]) == str(player_code)), None)
            if row is not None:
                print(f"    {row['name']} ({row['allyCode']})")
                if squad_group["mode"] == "commonRelic":
                    cr = "-" if row["commonRelic"] is None else f"R{row['commonRelic']}"
                    nt = "-" if row["nextThreshold"] is None else f"R{row['nextThreshold']}"
                    req = ", ".join(
                        f"{r['name']}: R{r['relicLevel']}" if r["relicLevel"] is not None
                        else f"{r['name']}: missing"
                        for r in row["required"]
                    )
                    print(f"      common relic: {cr} (next {nt})")
                    print(f"      required: {req}")
                    if row.get("bottlenecks"):
                        print(f"      blocks next: {', '.join(row['bottlenecks'])}")
                else:
                    status = "complete" if row["complete"] else f"gap {row['gap']}"
                    req = ", ".join(
                        f"{r['name']}: {r['status']}" + (f"({r['relicLevel']})" if r["status"] != "missing" else "")
                        for r in row["required"]
                    )
                    pool = (
                        f"{row['poolMet']}/{row['poolCount']} pool met "
                        f"(+{row['poolUpgrade']} upgrade, +{row['poolMissing']} missing)"
                    )
                    print(f"      status: {status}")
                    print(f"      required: {req}")
                    print(f"      pool: {pool}")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("guild_id")
    parser.add_argument("--squads", type=Path, default=DEFAULT_SQUADS, help="path to squads.json")
    parser.add_argument("--schema", type=Path, default=DEFAULT_SCHEMA, help="path to squads.schema.json")
    parser.add_argument("--outdir", type=Path, default=data_root(), help="data directory")
    parser.add_argument("--player", type=str, default=None, help="drill into one player's ally code")
    args = parser.parse_args(argv)

    outdir = args.outdir
    summary_path = outdir / "guilds" / f"{args.guild_id}.summary.json"
    if not summary_path.exists():
        print(f"no summary at {summary_path}; run fetch_guild.py + guild_summary.py first", file=sys.stderr)
        return 2

    squads = load_squads(args.squads, args.schema)
    summary = load_summary(summary_path)
    name_map, known_tags = load_known(outdir)
    if not name_map:
        print("note: data/names.json not found; skipping unit-name validation and baseId resolution", file=sys.stderr)
    warn_unknown(squads, name_map, known_tags)

    report = build_report(summary, squads, name_map)
    outpath = outdir / "guilds" / f"{args.guild_id}.squads.json"
    atomic_write_text(outpath, json.dumps(report, indent=2, ensure_ascii=False))

    print(f"wrote {outpath} ({outpath.stat().st_size / 1e6:.2f} MB)")
    print()
    print_console_summary(report, player_code=args.player)
    return 0


if __name__ == "__main__":
    sys.exit(main())
