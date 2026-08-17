"""Assignments-by-member logic: build the per-member roster from a plan's fills.

Server-side port of the old client-side roster builder. Inputs are the light
planet/roster projection (`platoons.build_data(light=True)`) plus a plan's
`fills` (`{planet: {day: {slot: allycode}}}`).
"""

SLOTS_PER_PLATOON = 15
MAX_UNITS = 10


def build_roster(planets, members, fills):
    """Per-member roster entries: total, per-day counts, grouped detail."""
    planet_map = {p["name"]: p for p in planets}
    planet_order = {p["name"]: p.get("order", 9) for p in planets}
    per = {}
    for pn, byday in (fills or {}).items():
        planet = planet_map.get(pn)
        if not planet:
            continue
        for d, slots in byday.items():
            day = int(d)
            for k, ac in (slots or {}).items():
                slot = int(k)
                platoon = slot // SLOTS_PER_PLATOON
                pos = slot % SLOTS_PER_PLATOON
                try:
                    unit = planet["platoons"][platoon]["slots"][pos]["n"]
                except (IndexError, KeyError):
                    continue
                r = per.setdefault(ac, {"ac": ac, "total": 0, "days": {}, "list": []})
                r["total"] += 1
                r["days"][day] = r["days"].get(day, 0) + 1
                r["list"].append({"day": day, "planet": pn, "platoon": platoon + 1, "unit": unit})
    for r in per.values():
        r["detail"] = _detail(r["list"], planet_order)
    out = []
    for m in members:
        ac = str(m["ac"])
        r = per.get(ac, {"ac": ac, "total": 0, "days": {}, "list": [], "detail": []})
        r["name"] = m["name"]
        out.append(r)
    out.sort(key=lambda r: (-r["total"], r["name"]))
    return out


def _detail(items, planet_order=None):
    by_day = {}
    for it in items:
        by_day.setdefault(it["day"], []).append(it)
    detail = []
    for d in sorted(by_day):
        by_planet = {}
        for it in by_day[d]:
            by_planet.setdefault(it["planet"], []).append(it)
        planets = sorted(by_planet, key=lambda pn: (planet_order.get(pn, 9), pn))
        detail.append(
            {
                "day": d,
                "count": len(by_day[d]),
                "planets": [
                    {"name": pn, "over": len(lst) > MAX_UNITS, "assigns": lst}
                    for pn in planets
                    for lst in [by_planet[pn]]
                ],
            }
        )
    return detail


def member_markdown(entry):
    md = f"**{entry['name']}** ({entry['ac']}) — {entry['total']} assignments"
    if not entry["list"]:
        return md + "\n\nNo assignments."
    for d in entry["detail"]:
        md += f"\n\n**Day {d['day']}** ({d['count']})"
        for p in d["planets"]:
            for it in p["assigns"]:
                md += f"\n- {it['planet']} · Platoon {it['platoon']} · {it['unit']}"
    return md
