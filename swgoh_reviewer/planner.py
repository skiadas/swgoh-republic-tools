"""Platoon planner logic: slot coverage, conflicts, and auto-generation.

Python port of the planner's client-side model (swgoh_reviewer/platoons.py JS
`generateAssignments`, `cellWarnings`, coverage helpers). Pure functions over
the full planet/roster projection (`platoons.build_data`), the star plan
(`days`), and the assignment plan (`fills`).

Model:
  - A slot is `platoon*15 + position`; it is *covered* by its latest
    assignment on or before the viewed day.
  - A member qualifies for a slot at relic >= op relic (characters) or
    rarity >= 7 stars (ships).
  - Conflicts (warnings): >10 fills on a planet-day, a unit placed twice by a
    member in a day (across all planets), completing a platoon before the
    planet's planned star day.
  - Generation fills uncovered slots hardest-first under the chosen policy
    (full / per-plan) and member strategy (strongest / weakest / minimize).
"""

SLOTS_PER_PLATOON = 15
MAX_UNITS = 10
SHIP_STAR_REQ = 7


def slot_platoon(slot):
    return slot // SLOTS_PER_PLATOON


def slot_pos(slot):
    return slot % SLOTS_PER_PLATOON


def unit_at(planet, slot):
    return planet["platoons"][slot_platoon(slot)]["slots"][slot_pos(slot)]


def member_map(members):
    return {str(m["ac"]): m for m in members}


def planets_map(planets):
    return {p["name"]: p for p in planets}


# ---- coverage / counts ----

def latest_assignee(fills, pn, slot, d):
    """Latest (ac, day) assignment for a slot with day <= d, or None."""
    by_day = (fills or {}).get(pn, {})
    last = None
    last_day = -1
    for dd, slots in by_day.items():
        n = int(dd)
        if n > d:
            continue
        ac = slots.get(slot) if slot in slots else slots.get(str(slot))
        if ac is not None and n > last_day:
            last = ac
            last_day = n
    return (last, last_day) if last is not None else None


def platoon_fill_count(fills, pn, platoon_idx, d):
    by_day = (fills or {}).get(pn, {})
    filled = 0
    for s in range(platoon_idx * SLOTS_PER_PLATOON, (platoon_idx + 1) * SLOTS_PER_PLATOON):
        last_day = -1
        for dd, slots in by_day.items():
            n = int(dd)
            if n <= d and (slots.get(s) if s in slots else slots.get(str(s))) is not None:
                last_day = max(last_day, n)
        if last_day >= 0:
            filled += 1
    return filled


def would_complete_platoon(fills, pn, slot, d):
    return platoon_fill_count(fills, pn, slot_platoon(slot), d) >= SLOTS_PER_PLATOON


def count_on_planet_day(fills, pn, d, ac):
    by_day = (fills or {}).get(pn, {})
    slots = by_day.get(d) or by_day.get(str(d)) or {}
    return sum(1 for v in slots.values() if v == ac)


def unit_assigned_on_day(fills, p_map, ac, unit_base_id, d, skip_p=None, skip_slot=None):
    for pn, by_day in (fills or {}).items():
        slots = by_day.get(d) or by_day.get(str(d)) or {}
        for k, v in slots.items():
            s = int(k)
            if v == ac and not (pn == skip_p and s == skip_slot):
                if pn in p_map and unit_at(p_map[pn], s)["b"] == unit_base_id:
                    return True
    return False


# ---- star plan ----

def plan_entry(days, pn, d):
    day = (days or {}).get(d) or (days or {}).get(str(d)) or {}
    return day.get(pn)


def star_day(days, pn):
    for d in range(1, 7):
        pe = plan_entry(days, pn, d)
        if pe and int(pe.get("goal") or 0) > 0:
            return d
    return None


def active_planets(days, fills, planets, d):
    names = set((days or {}).get(d, {}).keys()) | set((days or {}).get(str(d), {}).keys())
    for pn, by_day in (fills or {}).items():
        if by_day.get(d) or by_day.get(str(d)):
            names.add(pn)
    out = [p for p in planets if p["name"] in names]
    out.sort(key=lambda p: (p.get("order", 9), p.get("phase", 99), p["name"]))
    return out


# ---- eligibility / conflicts ----

def eligible_for_slot(planet, members, slot):
    sl = unit_at(planet, slot)
    ship = sl.get("c") == 2
    req = planet.get("relicReq") or 0
    out = []
    for m in members:
        u = m.get("u", {}).get(sl["b"])
        if not u:
            continue
        if ship:
            if u[1] < SHIP_STAR_REQ:
                continue
            level = u[1]
        else:
            if u[0] < req:
                continue
            level = u[0]
        out.append({"ac": str(m["ac"]), "name": m["name"], "level": level, "ship": ship})
    out.sort(key=lambda e: (-e["level"], e["name"]))
    return out


def eligible_count(planet, members, slot):
    return len(eligible_for_slot(planet, members, slot))


def cell_conflicts(planets, members, fills, days, pn, slot, d, ac):
    p_map = planets_map(planets)
    planet = p_map.get(pn)
    if planet is None:
        return []
    sl = unit_at(planet, slot)
    who = next((m["name"] for m in members if str(m["ac"]) == str(ac)), str(ac))
    out = []
    cnt = count_on_planet_day(fills, pn, d, ac)
    if cnt > MAX_UNITS:
        out.append({"t": "cap", "msg": f"{who} has {cnt} fills on {pn} day {d} (max {MAX_UNITS})"})
    if unit_assigned_on_day(fills, p_map, ac, sl["b"], d, pn, slot):
        out.append({"t": "dup", "msg": f"{who} already places {sl['n']} elsewhere on day {d}"})
    if would_complete_platoon(fills, pn, slot, d):
        sd = star_day(days, pn)
        if sd is not None and sd > d:
            out.append({"t": "early", "msg": f"completes platoon P{slot_platoon(slot) + 1} on day {d} but {pn} is planned to star day {sd}"})
    return out


# ---- generation ----

def _gen_pick(cands, strategy):
    if not cands:
        return None
    if strategy == "weakest":
        cands.sort(key=lambda c: (c["level"], c["day_total"], c["name"]))
    elif strategy == "minimize":
        cands.sort(key=lambda c: (c["day_total"], -c["level"], c["name"]))
    else:
        cands.sort(key=lambda c: (-c["level"], c["day_total"], c["name"]))
    return cands[0]


def _open_score(planet, members, slot):
    sl = unit_at(planet, slot)
    return sl.get("gl", 0) * 1000 + eligible_count(planet, members, slot)


def _platoon_target(days, planet, pn, d):
    pe = plan_entry(days, pn, d)
    if pe is None:
        return len(planet["platoons"])
    return max(0, min(int(pe.get("platoons") or 0), len(planet["platoons"])))


def _slots_to_fill(planet, pn, d, policy, days, fills, members):
    n = len(planet["platoons"]) if policy == "full" else _platoon_target(days, planet, pn, d)
    fill = []
    for pidx in range(len(planet["platoons"])):
        base = pidx * SLOTS_PER_PLATOON
        uncovered = [base + s for s in range(SLOTS_PER_PLATOON) if latest_assignee(fills, pn, base + s, d) is None]
        if pidx < n:
            fill.extend(uncovered)
        else:
            uncovered.sort(key=lambda s: -_open_score(planet, members, s))
            fill.extend(uncovered[1:])
    return fill


class _DayCtx:
    def __init__(self, fills, p_map, d):
        self.used_units = {}
        self.planet_counts = {}
        self.day_total = {}
        for pn, by_day in (fills or {}).items():
            slots = by_day.get(d) or by_day.get(str(d)) or {}
            for k, ac in slots.items():
                b = unit_at(p_map[pn], int(k))["b"]
                self.used_units.setdefault(ac, set()).add(b)
                self.planet_counts[(pn, ac)] = self.planet_counts.get((pn, ac), 0) + 1
                self.day_total[ac] = self.day_total.get(ac, 0) + 1


def _eligible_candidates(planet, members, pn, slot, ctx):
    sl = unit_at(planet, slot)
    ship = sl.get("c") == 2
    req = planet.get("relicReq") or 0
    out = []
    for m in members:
        u = m.get("u", {}).get(sl["b"])
        if not u:
            continue
        if ship:
            if u[1] < SHIP_STAR_REQ:
                continue
            level = u[1]
        else:
            if u[0] < req:
                continue
            level = u[0]
        ac = str(m["ac"])
        if ac in ctx.used_units and sl["b"] in ctx.used_units[ac]:
            continue
        if ctx.planet_counts.get((pn, ac), 0) >= MAX_UNITS:
            continue
        out.append({"ac": ac, "name": m["name"], "level": level, "day_total": ctx.day_total.get(ac, 0)})
    return out


def _scope_days(scope, days, fills, planets):
    if scope and scope.get("mode") == "planet":
        return [(scope["day"], [scope["planet"]])]
    if scope and scope.get("mode") == "day":
        return [(scope["day"], [p["name"] for p in active_planets(days, fills, planets, scope["day"])])]
    out = []
    for d in range(1, 7):
        out.append((d, [p["name"] for p in active_planets(days, fills, planets, d)]))
    return out


def generate(planets, members, fills, days, scope=None, strategy="strongest", policy="plan"):
    """Fill uncovered slots for the scope; returns (new_fills, added)."""
    p_map = planets_map(planets)
    new_fills = {pn: {dd: dict(slots) for dd, slots in by_day.items()} for pn, by_day in (fills or {}).items()}
    added = 0
    for d, pns in _scope_days(scope, days, new_fills, planets):
        ctx = _DayCtx(new_fills, p_map, d)
        for pn in pns:
            planet = p_map.get(pn)
            if planet is None:
                continue
            fill = sorted(
                _slots_to_fill(planet, pn, d, policy, days, new_fills, members),
                key=lambda s: eligible_count(planet, members, s),
            )
            for slot in fill:
                sl = unit_at(planet, slot)
                cands = _eligible_candidates(planet, members, pn, slot, ctx)
                pick = _gen_pick(cands, strategy)
                if pick is None:
                    continue
                new_fills.setdefault(pn, {}).setdefault(str(d), {})[str(slot)] = pick["ac"]
                ctx.used_units.setdefault(pick["ac"], set()).add(sl["b"])
                ctx.planet_counts[(pn, pick["ac"])] = ctx.planet_counts.get((pn, pick["ac"]), 0) + 1
                ctx.day_total[pick["ac"]] = ctx.day_total.get(pick["ac"], 0) + 1
                added += 1
    return new_fills, added


def planet_render_model(planet, members, fills, days, d):
    """Per-planet data the planner page needs to render a day's grid."""
    pn = planet["name"]
    platoons = []
    for pi in range(len(planet["platoons"])):
        cells = []
        for s in range(pi * SLOTS_PER_PLATOON, pi * SLOTS_PER_PLATOON + SLOTS_PER_PLATOON):
            sl = unit_at(planet, s)
            cov = latest_assignee(fills, pn, s, d)
            by_day = (fills or {}).get(pn, {})
            slots = by_day.get(d) or by_day.get(str(d)) or {}
            cur = slots.get(s) if s in slots else slots.get(str(s))
            elig = eligible_for_slot(planet, members, s)
            warns = cell_conflicts([planet], members, fills, days, pn, s, d, cur) if cur else []
            cells.append(
                {
                    "slot": s,
                    "unit": sl["n"],
                    "baseId": sl["b"],
                    "gl": sl.get("gl", 0),
                    "c": sl.get("c", 1),
                    "eligible": len(elig),
                    "covered_ac": cov[0] if cov else None,
                    "covered_day": cov[1] if cov else None,
                    "cur": cur,
                    "warnings": [w["msg"] for w in warns],
                }
            )
        platoons.append({"idx": pi + 1, "filled": platoon_fill_count(fills, pn, pi, d), "cells": cells})
    return {
        "name": pn,
        "relicReq": planet.get("relicReq", 0),
        "starDay": star_day(days, pn),
        "platoons": platoons,
    }
