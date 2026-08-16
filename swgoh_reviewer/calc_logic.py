"""ROTE star-calculator model: per-day aggregate compute + plan optimizer.

Faithful Python port of the calculator's client-side model (calc.py JS
`compute()` and `optimizePlan()`). Pure functions over the `build_data`
payload (`swgoh_reviewer.calc.build_data`), a plan's `days`, deploy % and
special unlocks. Verified against the JS sanity numbers: 100% CM -> 47 stars
(no unlocks) / 52 (both specials); 50% -> 43; 30% -> 41.
"""

CHAIN_IDS = ["light", "dark", "neutral"]
SP_CHAIN_IDX = {"zeffo": 0, "mandalore": 2}
PLANET_ORDER = {"dark": 0, "neutral": 1, "light": 2, "zeffo": 3, "mandalore": 4}
LEVEL_EST_DEFAULT = {1: 30, 2: 20, 3: 10, 4: 5, 5: 0, 6: 0}


def phase_groups(data):
    """Planets grouped by phase (chains + specials), for the optimizer UI."""
    by = {}
    for ch in data["chains"]:
        for p in ch["planets"]:
            by.setdefault(p["phase"], []).append(p)
    for sp in data["specials"]:
        if sp.get("planet"):
            by.setdefault(sp["planet"]["phase"], []).append(sp["planet"])
    return [
        {"phase": ph, "relic": sorted(by[ph], key=lambda p: p["name"])[0].get("relicReq"),
         "planets": sorted(by[ph], key=lambda p: p["name"])}
        for ph in sorted(by)
    ]


def clamp(v, lo, hi):
    return max(lo, min(hi, v))


def chain_ids():
    return CHAIN_IDS


def trigger_planet_name(data, sp):
    ch = next((c for c in data["chains"] if c["id"] == sp["chain"]), None)
    if not ch or sp["triggerIndex"] - 1 >= len(ch["planets"]):
        return None
    return ch["planets"][sp["triggerIndex"] - 1]["name"]


def compute(data, days, deploy_pct=100, unlock_zeffo=False, unlock_mandalore=False):
    chain_idx = {"light": 0, "dark": 0, "neutral": 0}
    filled_platoon = {}
    done = {}
    unlocked = {"zeffo": False, "mandalore": False}
    chain_stars = {"light": 0, "dark": 0, "neutral": 0, "zeffo": 0, "mandalore": 0}
    unlock_shown = set()
    bank_in = 0
    out = []

    for d in range(1, 7):
        unlock_toggles = []
        for sp in data["specials"]:
            want = unlock_zeffo if sp["id"] == "zeffo" else unlock_mandalore
            if not done.get(sp["id"]) and want and chain_idx[sp["chain"]] >= sp["triggerIndex"]:
                unlocked[sp["id"]] = True
        accessible = []
        for ch in data["chains"]:
            p = ch["planets"][chain_idx[ch["id"]]] if chain_idx[ch["id"]] < len(ch["planets"]) else None
            if p:
                accessible.append({"planet": p, "chain": ch["id"], "special": None})
        for sp in data["specials"]:
            if unlocked.get(sp["id"]) and not done.get(sp["id"]) and sp.get("planet"):
                accessible.append({"planet": sp["planet"], "chain": sp["chain"], "special": sp["id"]})

        inputs = days.get(d) or days.get(str(d)) or {}
        min_tp = max_tp = platoon = est_cm = total_cm = 0
        rows = []
        for a in accessible:
            p = a["planet"]
            inp = inputs.get(p["name"]) or {}
            goal = "0" if inp.get("goal") is None else str(inp["goal"])
            action = "preload" if goal == "0" else "finish"
            stars = 1 if goal == "0" else clamp(int(goal) or 1, 1, 3)
            remaining = max(0, (p.get("platoonsTotal") or 6) - filled_platoon.get(p["name"], 0))
            plats = clamp(int(inp.get("platoons") or 0), 0, remaining)
            cp = int(inp.get("cmPct") or 0)
            total_cm += p.get("cmMax") or 0
            if action == "finish":
                th = p["thresholds"][stars - 1] if stars - 1 < len(p["thresholds"]) else 0
                min_tp += th
                max_tp += th
            else:
                max_tp += (p["thresholds"][0] - 1)
            platoon += plats * (p.get("platoonReward") or 0)
            est_cm += cp / 100 * (p.get("cmMax") or 0)
            rows.append(
                {
                    "a": a,
                    "action": action,
                    "stars": stars,
                    "goal": goal,
                    "plats": plats,
                    "cp": cp,
                    "remaining": remaining,
                    "order": PLANET_ORDER.get(a["special"] or a["chain"], 9),
                    "special": bool(a["special"]),
                }
            )

        gp = deploy_pct / 100 * data.get("guildGp", 0)
        cm_min_pts = min_tp - bank_in - platoon - gp
        cm_max_pts = max_tp - bank_in - platoon - gp
        min_pct = clamp(cm_min_pts / total_cm * 100, 0, 100) if total_cm else 0
        max_pct = clamp(cm_max_pts / total_cm * 100, 0, 100) if total_cm else 0
        capacity = max(0, max_tp - min_tp)
        carry = clamp(bank_in + gp + platoon + est_cm - min_tp, 0, capacity)
        wasted_gp = clamp(bank_in + gp + platoon + est_cm - max_tp, 0, gp)
        feasible = cm_min_pts <= total_cm
        est_pct = est_cm / total_cm * 100 if total_cm else 0
        short_est = False if not feasible else cm_min_pts > est_cm + 0.5
        short_pts = cm_min_pts - est_cm

        for r in rows:
            if r["action"] == "finish":
                credit = (1 if r["stars"] == 3 else 0) if r["a"]["special"] else r["stars"]
                if r["a"]["special"]:
                    done[r["a"]["special"]] = True
                    chain_stars[r["a"]["special"]] += credit
                else:
                    chain_idx[r["a"]["chain"]] += 1
                    chain_stars[r["a"]["chain"]] += credit
                    for sp in data["specials"]:
                        if sp["id"] not in unlock_shown and not done.get(sp["id"]) and r["a"]["planet"]["name"] == trigger_planet_name(data, sp):
                            unlock_shown.add(sp["id"])
                            unlock_toggles.append({"id": sp["id"], "name": sp["name"], "trigger": sp["triggerName"]})
        for r in rows:
            filled_platoon[r["a"]["planet"]["name"]] = filled_platoon.get(r["a"]["planet"]["name"], 0) + r["plats"]

        total_stars = sum(chain_stars.values())
        out.append(
            {
                "day": d,
                "rows": rows,
                "minTP": min_tp,
                "maxTP": max_tp,
                "platoon": platoon,
                "estCM": est_cm,
                "totalCM": total_cm,
                "gp": gp,
                "bankIn": bank_in,
                "cmMinPts": cm_min_pts,
                "cmMaxPts": cm_max_pts,
                "minPct": min_pct,
                "maxPct": max_pct,
                "estPct": est_pct,
                "carry": carry,
                "wastedGp": wasted_gp,
                "feasible": feasible,
                "shortEst": short_est,
                "shortPts": short_pts,
                "totalStars": total_stars,
                "chainStars": dict(chain_stars),
                "unlockToggles": unlock_toggles,
                "unlocked": dict(unlocked),
            }
        )
        bank_in = carry
    return out


# ---- optimizer (beam search) ----

def _stars_of(s):
    return s["cs"]["light"] + s["cs"]["dark"] + s["cs"]["neutral"] + s["cs"]["zeffo"] + s["cs"]["mandalore"]


def _accessible_planets(data, st, unlock_z, unlock_m):
    acc = []
    for i, cid in enumerate(CHAIN_IDS):
        ch = next(c for c in data["chains"] if c["id"] == cid)
        p = ch["planets"][st["idx"][i]] if st["idx"][i] < len(ch["planets"]) else None
        if p:
            acc.append({"p": p, "chain": i, "special": None})
    for sp in data["specials"]:
        if not sp.get("planet"):
            continue
        unlocked = unlock_z if sp["id"] == "zeffo" else unlock_m
        done = st["z"] if sp["id"] == "zeffo" else st["m"]
        if unlocked and not done and st["idx"][SP_CHAIN_IDX[sp["id"]]] >= sp["triggerIndex"]:
            acc.append({"p": sp["planet"], "chain": SP_CHAIN_IDX[sp["id"]], "special": sp["id"]})
    return acc


def _est_of(est, key):
    v = est.get(key)
    return 100 if v is None else v


def _plat_of(cap, phase):
    v = cap.get(phase)
    return 6 if v is None else v


def _optimizer_step(data, st, day, acc, gmap, est, plat_cap, deploy_pct):
    min_tp = max_tp = platoon = est_cm = total_cm = 0
    for a in acc:
        p = a["p"]
        g = gmap[p["name"]]
        total_cm += p.get("cmMax") or 0
        if g == 0:
            max_tp += (p["thresholds"][0] - 1)
        else:
            th = p["thresholds"][g - 1] if g - 1 < len(p["thresholds"]) else 0
            min_tp += th
            max_tp += th
            plats = max(0, min(p.get("platoonsTotal") or 6, _plat_of(plat_cap, p["phase"])) - st["plats"].get(p["name"], 0))
            platoon += plats * (p.get("platoonReward") or 0)
        est_cm += _est_of(est, p["name"]) / 100 * (p.get("cmMax") or 0)
    income = st["bank"] + deploy_pct / 100 * data.get("guildGp", 0) + platoon + est_cm
    if income < min_tp:
        return None
    capacity = max(0, max_tp - min_tp)
    carry = clamp(income - min_tp, 0, capacity)
    ns = {
        "idx": list(st["idx"]),
        "z": st["z"],
        "m": st["m"],
        "bank": carry,
        "cs": dict(st["cs"]),
        "plats": dict(st["plats"]),
        "days": list(st["days"]),
    }
    acts = {}
    for a in acc:
        g = gmap[a["p"]["name"]]
        plats = max(0, min(a["p"].get("platoonsTotal") or 6, _plat_of(plat_cap, a["p"]["phase"])) - st["plats"].get(a["p"]["name"], 0)) if g >= 1 else 0
        acts[a["p"]["name"]] = {"goal": g, "plats": plats, "cmPct": _est_of(est, a["p"]["name"]), "special": bool(a["special"])}
        if g >= 1:
            if a["special"]:
                if a["special"] == "zeffo":
                    ns["z"] = True
                else:
                    ns["m"] = True
                if g == 3:
                    ns["cs"][a["special"]] += 1
                ns["plats"][a["p"]["name"]] = st["plats"].get(a["p"]["name"], 0) + plats
            else:
                ns["idx"][a["chain"]] += 1
                ns["cs"][CHAIN_IDS[a["chain"]]] += g
                ns["plats"][a["p"]["name"]] = st["plats"].get(a["p"]["name"], 0) + plats
    ns["days"].append({"day": day, "acts": acts})
    return ns


def _optimizer_prio(s, unlock_z, unlock_m):
    v = _stars_of(s)
    if unlock_z and s["z"]:
        v += 1000
    if unlock_m and s["m"]:
        v += 1000
    if unlock_z and s["idx"][0] >= 2:
        v += 2
    if unlock_m and s["idx"][2] >= 3:
        v += 2
    return [v, s["bank"]]


def optimize(data, est, unlock_zeffo=False, unlock_mandalore=False, deploy_pct=100, plat_cap=None):
    plat_cap = plat_cap or {}
    max_beam = 90
    start = {"idx": [0, 0, 0], "z": False, "m": False, "bank": 0, "cs": {"light": 0, "dark": 0, "neutral": 0, "zeffo": 0, "mandalore": 0}, "plats": {}, "days": []}
    beam = [start]
    for day in range(1, 7):
        cand = []
        for st in beam:
            acc = _accessible_planets(data, st, unlock_zeffo, unlock_mandalore)
            if not acc:
                cand.append(st)
                continue
            goal_opts = [[0, 3] if a["special"] else [0, 1, 2, 3] for a in acc]
            total = 1
            for o in goal_opts:
                total *= len(o)
            for t in range(total):
                gmap = {}
                rem = t
                for i, a in enumerate(acc):
                    gmap[a["p"]["name"]] = goal_opts[i][rem % len(goal_opts[i])]
                    rem //= len(goal_opts[i])
                ns = _optimizer_step(data, st, day, acc, gmap, est, plat_cap, deploy_pct)
                if ns:
                    cand.append(ns)
        by = {}
        for s in cand:
            key = f"{s['idx'][0]},{s['idx'][1]},{s['idx'][2]},{1 if s['z'] else 0},{1 if s['m'] else 0}"
            e = by.get(key)
            if e is None or _stars_of(s) > _stars_of(e) or (_stars_of(s) == _stars_of(e) and s["bank"] > e["bank"]):
                by[key] = s
        vals = list(by.values())
        vals.sort(key=lambda s: _optimizer_prio(s, unlock_zeffo, unlock_mandalore), reverse=True)
        beam = vals[:max_beam]

    req = []
    if unlock_zeffo:
        req.append("zeffo")
    if unlock_mandalore:
        req.append("mandalore")
    valid = [s for s in beam if all((s["z"] if r == "zeffo" else s["m"]) for r in req)]
    if valid:
        best = valid[0]
        unmet = []
    else:
        best = beam[0]
        unmet = [r for r in req if not (best["z"] if r == "zeffo" else best["m"])]
    return {"stars": _stars_of(best), "cs": dict(best["cs"]), "days": best["days"], "unmet": unmet}
