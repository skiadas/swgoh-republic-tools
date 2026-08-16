"""Squad-report view data: matrix, squad tables, player detail, needs.

Port of the dashboard's client-side computations (swgoh_reviewer/dashboard.py
JS) so the report page renders server-side. Inputs: `squads.json` (from
`squad_report.py`) plus the guild summary for GP info.
"""

import json

CR_COLORS = ["cr0", "cr1", "cr2", "cr3", "cr4", "cr5", "cr6", "cr7", "cr8"]
GAP_CLASS = ["g0", "g1", "g2", "g3"]
STATUS_DOT = {"met": "\u2713", "upgrade": "\u25b2", "missing": "\u2717"}


def load_report(outdir, guild_id):
    path = outdir / "guilds" / f"{guild_id}.squads.json"
    if not path.exists():
        return {"guildId": guild_id, "guildName": guild_id, "bySquad": [], "byPlayer": {}}, []
    report = json.loads(path.read_text())
    members = []
    summary_path = outdir / "guilds" / f"{guild_id}.summary.json"
    if summary_path.exists():
        summary = json.loads(summary_path.read_text())
        for m in summary.get("members", []):
            members.append(
                {
                    "allyCode": m.get("allyCode"),
                    "name": m.get("name"),
                    "galacticPower": m.get("galacticPower"),
                    "characterGalacticPower": m.get("characterGalacticPower"),
                    "shipGalacticPower": m.get("shipGalacticPower"),
                }
            )
    return report, members


def prepare(report, members):
    """Attach resByAlly to each squad; return (players, categories)."""
    gp_by = {str(m["allyCode"]): m for m in members}
    for s in report.get("bySquad", []):
        s["resByAlly"] = {str(r["allyCode"]): r for r in s.get("results", [])}
    players = []
    seen = set()
    for s in report.get("bySquad", []):
        for r in s.get("results", []):
            code = str(r["allyCode"])
            if code in seen:
                continue
            seen.add(code)
            player = {"allyCode": code, "name": r["name"]}
            info = gp_by.get(code, {})
            for k, v in info.items():
                if k != "allyCode":
                    player[k] = v
            players.append(player)
    players.sort(key=lambda p: p["name"])
    categories = []
    for s in report.get("bySquad", []):
        c = next((x for x in categories if x["name"] == s["category"]), None)
        if c is None:
            c = {"name": s["category"], "squads": []}
            categories.append(c)
        c["squads"].append(s)
    return players, categories


def squad_title(s):
    if s.get("mode") == "commonRelic":
        return "common relic " + "/".join(f"R{t}" for t in s.get("thresholds", [])) + f", size {s.get('size')}"
    return f"minRelic {s.get('minRelic')}, size {s.get('size')}"


def cr_class(v, s):
    if v is None:
        return "cr-none"
    try:
        i = s.get("thresholds", []).index(v)
    except ValueError:
        return "cr6"
    return CR_COLORS[i] if i < len(CR_COLORS) else "cr6"


def matrix_cell(r, s):
    if not r:
        return {"cls": "na", "text": "-"}
    if s.get("mode") == "commonRelic":
        v = r.get("commonRelic")
        return {"cls": cr_class(v, s), "text": "-" if v is None else f"R{v}"}
    if r.get("complete"):
        return {"cls": "g0", "text": "\u2713"}
    up = sum(1 for u in r.get("required", []) if u.get("status") == "upgrade") + sum(
        1 for u in r.get("poolChosen", []) if u.get("status") == "upgrade"
    )
    miss = sum(1 for u in r.get("required", []) if u.get("status") == "missing") + sum(
        1 for u in r.get("poolChosen", []) if u.get("status") == "missing"
    )
    sub = f" \u25b2{up}\u2717{miss}" if (up or miss) else ""
    gap = r.get("gap", 0)
    return {"cls": GAP_CLASS[gap] if gap < len(GAP_CLASS) else "g3", "text": str(gap) + sub}


def squad_rows(s):
    rows = list(s.get("results", []))
    if s.get("mode") == "commonRelic":
        rows.sort(key=lambda r: -(r.get("commonRelic") if r.get("commonRelic") is not None else -1))
    else:
        rows.sort(key=lambda r: (r.get("complete") is False, r.get("gap", 0)))
    return rows


def unit_status_class(u, s, next_threshold):
    missing = u.get("relicLevel") is None
    if missing:
        return "missing"
    if s.get("mode") == "commonRelic" and next_threshold is not None and u.get("relicLevel") < next_threshold:
        return "upgrade"
    if s.get("mode") != "commonRelic" and u.get("status") == "upgrade":
        return "upgrade"
    return "met"


def needs_rows(s):
    rows = []
    if s.get("mode") == "commonRelic":
        missing = [r for r in s.get("results", []) if r.get("commonRelic") is None]
        if missing:
            rows.append(
                {
                    "label": "Missing a required unit",
                    "players": [
                        {
                            "name": r["name"],
                            "code": r["allyCode"],
                            "title": "missing: " + ", ".join(u["name"] for u in r.get("required", []) if u.get("relicLevel") is None),
                        }
                        for r in missing
                    ],
                }
            )
        for i, t in enumerate(s.get("thresholds", [])):
            if i == 0:
                continue
            near = [r for r in s.get("results", []) if r.get("nextThreshold") == t]
            if not near:
                continue
            rows.append(
                {
                    "label": f"Need R{t} (next step)",
                    "players": [
                        {
                            "name": r["name"],
                            "code": r["allyCode"],
                            "title": "at R" + (str(r.get("commonRelic")) if r.get("commonRelic") is not None else "0")
                            + ((" \u00b7 blocked by " + ", ".join(r.get("bottlenecks", []))) if r.get("bottlenecks") else ""),
                        }
                        for r in near
                    ],
                }
            )
        return rows
    req_names = s.get("results", [{}])[0].get("required", []) if s.get("results") else []
    for i, uname in enumerate([u.get("name") for u in req_names]):
        missing = [r for r in s.get("results", []) if r.get("required", [])[i].get("status") == "missing"] if len(s.get("results", [])[0].get("required", [])) > i else []
        below = [r for r in s.get("results", []) if r.get("required", [])[i].get("status") == "upgrade"] if len(s.get("results", [])[0].get("required", [])) > i else []
        if missing:
            rows.append({"label": "Missing: " + uname, "players": [{"name": r["name"], "code": r["allyCode"], "title": "not owned"} for r in missing]})
        if below:
            rows.append(
                {
                    "label": f"Below relic {s.get('minRelic')}: {uname}",
                    "players": [
                        {
                            "name": r["name"],
                            "code": r["allyCode"],
                            "title": "R" + str(r.get("required", [])[i].get("relicLevel")) + " \u2192 needs R" + str(r.get("required", [])[i].get("minRelic") or s.get("minRelic")),
                        }
                        for r in below
                    ],
                }
            )
    short = [r for r in s.get("results", []) if r.get("poolMet", 0) < r.get("poolCount", 0)]
    if short:
        rows.append(
            {
                "label": f"Pool short (need {s.get('poolCount')})",
                "players": [
                    {
                        "name": r["name"],
                        "code": r["allyCode"],
                        "title": f"met {r.get('poolMet')}, upgrade {r.get('poolUpgrade')}, missing {r.get('poolMissing')}",
                    }
                    for r in short
                ],
            }
        )
    return rows


def needs_count(s):
    if s.get("mode") == "commonRelic":
        return sum(1 for r in s.get("results", []) if r.get("commonRelic") is None or r.get("nextThreshold") is not None)
    return sum(1 for r in s.get("results", []) if not r.get("complete"))


def squad_status(s, r):
    if s.get("mode") == "commonRelic":
        if r.get("commonRelic") is None:
            return {"cls": "b-missing", "text": "none"}
        label = f"R{r['commonRelic']}"
        if r.get("nextThreshold") is None:
            return {"cls": "b-complete", "text": label + " max"}
        return {"cls": "b-gap", "text": f"{label} \u2192 R{r['nextThreshold']}"}
    if r.get("complete"):
        return {"cls": "b-complete", "text": "ready"}
    return {"cls": "b-gap", "text": f"gap {r.get('gap')}"}


def unit_chip(u, s, next_th):
    st = unit_status_class(u, s, next_th)
    sym = STATUS_DOT[st]
    relic = "missing" if u.get("relicLevel") is None else f"R{u['relicLevel']}"
    return {"status": st, "sym": sym, "name": u["name"], "relic": relic}


def squad_row_class(s, r):
    if s.get("mode") == "commonRelic":
        return "row-none" if r.get("commonRelic") is None else ("row-ready" if r.get("nextThreshold") is None else "row-progress")
    return "row-ready" if r.get("complete") else "row-progress"


def total_gap(squads, code):
    t = 0
    for s in squads:
        r = s.get("resByAlly", {}).get(code)
        if r and s.get("mode") != "commonRelic":
            t += r.get("gap", 0)
    return t


def is_complete(squads, code):
    for s in squads:
        r = s.get("resByAlly", {}).get(code)
        if not r:
            return False
        if s.get("mode") == "commonRelic":
            if r.get("commonRelic") is None or r.get("nextThreshold") is not None:
                return False
        elif not r.get("complete"):
            return False
    return True


def filter_players(players, squads, search="", hide=False, sort="name"):
    q = (search or "").strip().lower()
    out = [p for p in players if not q or q in (p.get("name") or "").lower()]
    if hide:
        out = [p for p in out if not is_complete(squads, p["allyCode"])]
    if sort == "gp":
        out.sort(key=lambda p: -(p.get("galacticPower") or 0))
    elif sort == "gap":
        out.sort(key=lambda p: -total_gap(squads, p["allyCode"]))
    else:
        out.sort(key=lambda p: p["name"])
    return out
