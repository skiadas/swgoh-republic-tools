#!/usr/bin/env python3
"""Plan op fills for a ROTE planet against the guild roster.

For a planet (e.g. Malachor) this shows which of the op's required unit fills
we can cover with the guild right now, what is missing, who is closest, and
who should fill what.

Model (per squads/ops doc + user rules):
  - An op has platoons; every listed unit is a required fill.
  - A member qualifies for a fill if they own the unit at relic >= the op's
    relic requirement (characters) or at 7 stars (ships).
  - A member fills each distinct unit at most once, and at most 10 units
    total on the planet.
  - Greedy: hardest fills first (fewest qualifying members); among qualifying
    members pick the lowest qualifying relic, then highest GP.
  - "Closest" = members who own the unit but below the requirement.

Outputs:
    data/rote/<planet>.ops.json   full assignment data
    data/rote/<planet>.ops.html   self-contained report
    plus a console summary

Usage:
    python rote_ops.py Malachor
    python rote_ops.py Malachor NW4t0-dBRcG8n-PVhykpKg
"""

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

from jinja2 import Environment

from swgoh_reviewer.config import data_root
from swgoh_reviewer.io import atomic_write_text

DEFAULT_GUILD = "NW4t0-dBRcG8n-PVhykpKg"
TB_ID = "t05D"
SHIP_STAR_REQ = 7
MAX_UNITS_PER_MEMBER = 10
CLOSEST_LIMIT = 5


def _norm(s):
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def find_planet(doc, name):
    for ph in doc["phases"]:
        for planet in ph["planets"]:
            if _norm(planet["name"]) == _norm(name):
                return ph, planet
    return None, None


def load_combat_types(outdir):
    p = outdir / "game" / "units.json"
    if not p.exists():
        return {}
    return {base: meta.get("combatType") for base, meta in json.loads(p.read_text()).items()}


def load_members(summary):
    members = []
    for m in summary["members"]:
        units = {}
        for u in m.get("units") or []:
            units[u["baseId"]] = u
        members.append(
            {
                "allyCode": m.get("allyCode"),
                "name": m.get("name"),
                "gp": m.get("galacticPower") or 0,
                "units": units,
            }
        )
    return members


def qualifies(unit, combat_type, relic_req):
    if combat_type == 2:  # ship
        return (unit.get("rarity") or 0) >= SHIP_STAR_REQ
    return (unit.get("relicLevel") or 0) >= relic_req


def unit_level(unit, combat_type):
    if combat_type == 2:
        return unit.get("rarity") or 0
    return unit.get("relicLevel") or 0


def eligible_for(slot, members, combat_types, relic_req):
    ct = combat_types.get(slot["unitBaseId"])
    out = []
    for m in members:
        u = m["units"].get(slot["unitBaseId"])
        if u is None:
            continue
        if ct is None:
            ct = u.get("combatType")
        if qualifies(u, ct, relic_req):
            out.append(
                {
                    "member": m,
                    "unit": u,
                    "level": unit_level(u, ct),
                    "combatType": ct,
                }
            )
    return out


def closest_for(slot, members, combat_types, relic_req):
    ct = combat_types.get(slot["unitBaseId"])
    out = []
    for m in members:
        u = m["units"].get(slot["unitBaseId"])
        if u is None:
            continue
        if ct is None:
            ct = u.get("combatType")
        if qualifies(u, ct, relic_req):
            continue
        out.append({"name": m["name"], "allyCode": m["allyCode"], "level": unit_level(u, ct), "gp": m["gp"]})
    out.sort(key=lambda c: (-c["level"], -(c["gp"] or 0)))
    return out[:CLOSEST_LIMIT]


def plan(planet, members, combat_types):
    op = planet["op"]
    relic_req = op["relicRequirement"]
    slots = [
        {"platoon": pl["platoon"], "unitBaseId": u["baseId"], "unitName": u["name"]}
        for pl in op["platoons"]
        for u in pl["units"]
    ]

    used = {m["allyCode"]: set() for m in members}
    counts = {m["allyCode"]: 0 for m in members}

    for slot in slots:
        slot["eligibleCount"] = len(eligible_for(slot, members, combat_types, relic_req))

    fills = []
    for slot in sorted(slots, key=lambda s: s["eligibleCount"]):
        elig = eligible_for(slot, members, combat_types, relic_req)
        # Lowest qualifying relic first; then fewest assignments so far (spread
        # the load); then highest GP.
        elig.sort(key=lambda e: (e["level"], counts[e["member"]["allyCode"]], -(e["member"]["gp"] or 0)))
        chosen = None
        for e in elig:
            ac = e["member"]["allyCode"]
            if counts[ac] >= MAX_UNITS_PER_MEMBER:
                continue
            if slot["unitBaseId"] in used[ac]:
                continue
            chosen = e
            break
        if chosen is not None:
            ac = chosen["member"]["allyCode"]
            counts[ac] += 1
            used[ac].add(slot["unitBaseId"])
            fills.append(
                {
                    "platoon": slot["platoon"],
                    "unitBaseId": slot["unitBaseId"],
                    "unitName": slot["unitName"],
                    "eligibleCount": slot["eligibleCount"],
                    "status": "filled",
                    "assigned": {
                        "name": chosen["member"]["name"],
                        "allyCode": ac,
                        "level": chosen["level"],
                        "gp": chosen["member"]["gp"],
                    },
                    "closest": [],
                }
            )
        else:
            fills.append(
                {
                    "platoon": slot["platoon"],
                    "unitBaseId": slot["unitBaseId"],
                    "unitName": slot["unitName"],
                    "eligibleCount": slot["eligibleCount"],
                    "status": "missing",
                    "assigned": None,
                    "closest": closest_for(slot, members, combat_types, relic_req),
                }
            )

    member_rows = []
    for m in members:
        mine = [f for f in fills if f["assigned"] and f["assigned"]["allyCode"] == m["allyCode"]]
        member_rows.append(
            {
                "name": m["name"],
                "allyCode": m["allyCode"],
                "gp": m["gp"],
                "count": len(mine),
                "units": [f["unitName"] for f in mine],
            }
        )
    member_rows.sort(key=lambda r: -r["count"])

    filled = sum(1 for f in fills if f["status"] == "filled")
    platoon_rows = []
    for pl in sorted({f["platoon"] for f in fills}):
        pf = [f for f in fills if f["platoon"] == pl]
        platoon_rows.append({"platoon": pl, "total": len(pf), "filled": sum(1 for f in pf if f["status"] == "filled")})

    hardest = sorted(fills, key=lambda f: f["eligibleCount"])[:10]
    missing = sorted((f for f in fills if f["status"] == "missing"), key=lambda f: f["eligibleCount"])

    return {
        "opName": op["name"],
        "relicRequirement": relic_req,
        "totalFills": len(fills),
        "filled": filled,
        "missing": len(fills) - filled,
        "fillable": filled == len(fills),
        "platoons": platoon_rows,
        "hardest": hardest,
        "fills": fills,
        "missingList": missing,
        "members": member_rows,
    }


def console_report(planet, report, phase_num):
    op = planet["op"]
    lines = []
    lines.append(f"{planet['name']} (Phase {phase_num}) — Operation '{report['opName']}' — relic R{report['relicRequirement']}")
    status = "FILLABLE" if report["fillable"] else f"NOT fillable ({report['filled']}/{report['totalFills']} filled, {report['missing']} missing)"
    lines.append(f"Fillable: {status}")
    lines.append("Per-platoon: " + ", ".join(f"P{p['platoon']} {p['filled']}/{p['total']}" for p in report["platoons"]))
    if report["hardest"]:
        lines.append("\nHardest fills (fewest qualifying members):")
        for f in report["hardest"][:8]:
            lines.append(f"  P{f['platoon']} {f['unitName']}: {f['eligibleCount']} eligible" +
                         (f" -> {f['assigned']['name']}" if f["status"] == "filled" else " (missing)"))
    if report["missingList"]:
        lines.append("\nMissing — closest owners:")
        for f in report["missingList"][:10]:
            closest = ", ".join(f"{c['name']} (R{c['level']})" for c in f["closest"][:3]) or "none below requirement"
            lines.append(f"  P{f['platoon']} {f['unitName']}: {closest}")
    lines.append("\nAssignments (top by fills):")
    for r in report["members"][:12]:
        if r["count"]:
            lines.append(f"  {r['name']} ({r['allyCode']}): {r['count']}/{MAX_UNITS_PER_MEMBER}")
    return "\n".join(lines)


HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{{ title }}</title>
<style>
  body { font-family: -apple-system, "Segoe UI", Roboto, sans-serif; margin: 0; background: #fafafa; color: #222; }
  header { background: #1c2541; color: #fff; padding: 14px 20px; }
  header h1 { margin: 0; font-size: 18px; }
  header .sub { font-size: 12px; opacity: .8; margin-top: 3px; }
  main { padding: 16px 20px; max-width: 1200px; margin: 0 auto; }
  .banner { padding: 10px 14px; border-radius: 6px; font-weight: 700; margin: 12px 0; }
  .banner.ok { background: #dcedc8; color: #2e7d32; }
  .banner.no { background: #ffcdd2; color: #b71c1c; }
  .stats { display: flex; gap: 16px; flex-wrap: wrap; margin-bottom: 14px; }
  .stat { background: #fff; border: 1px solid #ddd; border-radius: 6px; padding: 8px 14px; }
  .stat b { font-size: 18px; display: block; }
  .stat span { font-size: 11px; color: #666; text-transform: uppercase; }
  table { border-collapse: collapse; background: #fff; width: 100%; font-size: 13px; margin-bottom: 20px; border: 1px solid #ddd; }
  th, td { border: 1px solid #ddd; padding: 5px 8px; text-align: left; }
  th { background: #f0f2f5; }
  .badge { display: inline-block; padding: 1px 8px; border-radius: 10px; font-size: 11px; font-weight: 700; }
  .b-ok { background: #dcedc8; color: #2e7d32; }
  .b-miss { background: #ffcdd2; color: #b71c1c; }
  .muted { color: #888; }
  h2 { font-size: 16px; margin: 18px 0 6px; }
</style>
</head>
<body>
<header>
  <h1>{{ planet }} — op fills ({{ guild_name }})</h1>
  <div class="sub">Phase {{ phase }} · Operation '{{ report.opName }}' · relic R{{ report.relicRequirement }} · generated {{ generated }}</div>
</header>
<main>
  <div class="banner {{ 'ok' if report.fillable else 'no' }}">
    {{ 'Fillable now' if report.fillable else 'Not fillable now' }} — {{ report.filled }}/{{ report.totalFills }} fills covered
    {% if not report.fillable %} ({{ report.missing }} missing){% endif %}
  </div>
  <div class="stats">
    <div class="stat"><b>{{ report.totalFills }}</b><span>total fills</span></div>
    <div class="stat"><b>{{ report.filled }}</b><span>covered</span></div>
    <div class="stat"><b>{{ report.missing }}</b><span>missing</span></div>
    <div class="stat"><b>{{ report.members|selectattr('count')|list|length }}</b><span>members used</span></div>
  </div>

  <h2>Per-platoon coverage</h2>
  <table>
    <tr><th>Platoon</th><th>Filled</th><th>Total</th></tr>
    {% for p in report.platoons %}
    <tr><td>P{{ p.platoon }}</td><td>{{ p.filled }}</td><td>{{ p.total }}</td></tr>
    {% endfor %}
  </table>

  <h2>Hardest fills (fewest qualifying members)</h2>
  <table>
    <tr><th>Platoon</th><th>Unit</th><th>Eligible</th><th>Status</th><th>Assigned</th></tr>
    {% for f in report.hardest %}
    <tr>
      <td>P{{ f.platoon }}</td>
      <td>{{ f.unitName }}</td>
      <td>{{ f.eligibleCount }}</td>
      <td><span class="badge {{ 'b-ok' if f.status == 'filled' else 'b-miss' }}">{{ 'filled' if f.status == 'filled' else 'missing' }}</span></td>
      <td>{% if f.assigned %}{{ f.assigned.name }} <span class="muted">(R{{ f.assigned.level }})</span>{% else %}<span class="muted">—</span>{% endif %}</td>
    </tr>
    {% endfor %}
  </table>

  <h2>All fills</h2>
  <table>
    <tr><th>Platoon</th><th>Unit</th><th>Eligible</th><th>Status</th><th>Assigned</th><th>Closest (below req)</th></tr>
    {% for f in report.fills %}
    <tr>
      <td>P{{ f.platoon }}</td>
      <td>{{ f.unitBaseId }} <span class="muted">{{ f.unitName }}</span></td>
      <td>{{ f.eligibleCount }}</td>
      <td><span class="badge {{ 'b-ok' if f.status == 'filled' else 'b-miss' }}">{{ f.status }}</span></td>
      <td>{% if f.assigned %}{{ f.assigned.name }} <span class="muted">(R{{ f.assigned.level }})</span>{% else %}<span class="muted">—</span>{% endif %}</td>
      <td><span class="muted">{% for c in f.closest %}{{ c.name }} (R{{ c.level }}){% if not loop.last %}, {% endif %}{% endfor %}</span></td>
    </tr>
    {% endfor %}
  </table>

  <h2>Member assignments</h2>
  <table>
    <tr><th>Member</th><th>GP</th><th>Fills</th><th>Units</th></tr>
    {% for m in report.members %}
    {% if m.count %}
    <tr>
      <td>{{ m.name }} <span class="muted">({{ m.allyCode }})</span></td>
      <td>{{ m.gp }}</td>
      <td>{{ m.count }}/10</td>
      <td>{{ m.units|join(', ') }}</td>
    </tr>
    {% endif %}
    {% endfor %}
  </table>
</main>
</body>
</html>
"""


def render_html(planet, report, guild_name, phase_num):
    env = Environment(autoescape=True)
    return env.from_string(HTML_TEMPLATE).render(
        title=f"{planet['name']} op fills",
        planet=planet["name"],
        phase=phase_num,
        report=report,
        guild_name=guild_name,
        generated=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
    )


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("planet", help="planet name, e.g. Malachor")
    parser.add_argument("guild_id", nargs="?", default=DEFAULT_GUILD)
    parser.add_argument("--outdir", type=Path, default=data_root())
    args = parser.parse_args(argv)

    outdir = args.outdir
    rote_path = outdir / "rote" / f"{TB_ID}.json"
    summary_path = outdir / "guilds" / f"{args.guild_id}.summary.json"
    for p in (rote_path, summary_path):
        if not p.exists():
            print(f"missing {p}; run rote.py / guild_summary.py first", file=sys.stderr)
            return 2

    rote = json.loads(rote_path.read_text())
    phase, planet = find_planet(rote, args.planet)
    if planet is None:
        planets = [p["name"] for ph in rote["phases"] for p in ph["planets"]]
        print(f"planet '{args.planet}' not found; available: {', '.join(planets)}", file=sys.stderr)
        return 2
    phase_num = phase["phase"]

    summary = json.loads(summary_path.read_text())
    members = load_members(summary)
    combat_types = load_combat_types(outdir)
    report = plan(planet, members, combat_types)

    slug = re.sub(r"[^a-z0-9]+", "-", planet["name"].lower()).strip("-")
    outdir = outdir / "rote"
    outdir.mkdir(parents=True, exist_ok=True)
    json_path = outdir / f"{slug}.ops.json"
    atomic_write_text(json_path, json.dumps({"planet": planet["name"], "phase": phase_num, "report": report}, indent=2, ensure_ascii=False))
    html_path = outdir / f"{slug}.ops.html"
    atomic_write_text(html_path, render_html(planet, report, summary.get("guildName", args.guild_id), phase_num))

    print(console_report(planet, report, phase_num))
    print(f"\nwrote {json_path} and {html_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
