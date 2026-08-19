"""EchoBase assignments-file export for the ROTE planner.

Serializes a plan's fills into the JSON contract EchoBase's TB Platoon
Assigner saves out (`echobase-assignments-ROTE-P*.json`), which HotUtils'
"import EchoBase file" consumes. Verified against real EchoBase exports:

  - one entry per filled slot: {allyCode, unitBaseId, zoneId,
    platoonDefinitionId}, ally codes as strings, no names.
  - zoneId = the TB doc's planetId + "_recon01", including the
    Zeffo/Mandalore bonus zones (`..._conflict01_bonus_recon01`,
    `..._conflict03_bonus_recon01`).
  - platoonDefinitionId = "tb3-platoon-{n}", n = 1-based platoon index,
    slot ordering matching the doc's platoon unit lists.
  - `phase` is "{dark}/{neutral}/{light}" (conflict01 = light, 02 = dark,
    03 = neutral), with a Z/M letter prefix on a chain token when Zeffo or
    Mandalore is included (e.g. day 3 with Zeffo = "3/3/Z3", day 4 with
    Mandalore = "4/M4/4").

The export is scoped to a day, not a TB phase: every planet with at least
one slot covered on that day (a slot's latest assignment with day <= the
selected day, per planner.latest_assignee) is included, across all phases.
The phase string's per-chain token is the highest level among the covered
planets of that chain (Zeffo/Mandalore add the Z/M prefix).
"""

from datetime import datetime, timezone

from swgoh_reviewer.planner import SLOTS_PER_PLATOON, latest_assignee


def _all_planets(rote):
    for ph in rote.get("phases", []):
        for planet in ph.get("planets") or []:
            yield planet, ph.get("phase", 1)


def _covered_planets(rote, fills, day):
    """(planet, phase) pairs with at least one slot covered on `day`."""
    fills = fills or {}
    for planet, phase in _all_planets(rote):
        name = planet.get("name") or ""
        for slots in (fills.get(name) or {}).values():
            if any(latest_assignee(fills, name, s, day) for s in slots):
                yield planet, phase
                break


def _chain_levels(rote, fills, day):
    """Per-chain highest covered level + Zeffo/Mandalore presence.

    Returns (dark, neutral, light, zeffo, mandalore) where each level is 0
    when the chain has no covered planet.
    """
    dark = neutral = light = 0
    zeffo = mandalore = False
    for planet, phase in _covered_planets(rote, fills, day):
        pid = planet.get("planetId") or ""
        lvl = int(phase)
        if pid.endswith("_bonus"):
            if (planet.get("name") or "") == "Zeffo":
                light = max(light, lvl)
                zeffo = True
            elif (planet.get("name") or "") == "Mandalore":
                neutral = max(neutral, lvl)
                mandalore = True
            else:
                dark = max(dark, lvl)
        elif "_conflict01" in pid:
            light = max(light, lvl)
        elif "_conflict02" in pid:
            dark = max(dark, lvl)
        else:
            neutral = max(neutral, lvl)
    return dark, neutral, light, zeffo, mandalore


def phase_string(rote, fills, day):
    """The export's `phase` field for the planets covered on `day`.

    e.g. "1/1/1", "3/3/Z3", "4/M4/4", or "2/M4/Z4" for a day covering
    Geonosis + Zeffo + Mandalore + level-4 planets. A chain with no covered
    planet reads "1".
    """
    dark, neutral, light, zeffo, mandalore = _chain_levels(rote, fills, day)

    def tok(level, pref):
        return pref + str(level) if level else "1"

    return "%s/%s/%s" % (
        tok(dark, ""),
        tok(neutral, "M" if mandalore else ""),
        tok(light, "Z" if zeffo else ""),
    )


def build_file(rote, fills, day, timestamp=None):
    """The file payload: {phase, timestamp, platoonAssignments}.

    Emits one entry per slot covered on `day`, across every planet in the
    TB doc; slots whose fill references an out-of-range platoon/position
    are skipped. A slot assigned more than once (on different days) is
    emitted once, with the latest assignment at or before `day`.
    """
    if timestamp is None:
        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
    assigns = []
    fills = fills or {}
    for planet, _phase in _all_planets(rote):
        zone = planet["planetId"] + "_recon01"
        platoons = (planet.get("op") or {}).get("platoons") or []
        name = planet.get("name") or ""
        emitted = set()
        for dd, slots in (fills.get(name) or {}).items():
            if int(dd) > day:
                continue
            for k in slots:
                s = int(k)
                if s in emitted:
                    continue
                ac = latest_assignee(fills, name, s, day)
                if ac is None:
                    continue
                plat, pos = divmod(s, SLOTS_PER_PLATOON)
                if plat >= len(platoons) or pos >= len(platoons[plat].get("units") or []):
                    continue
                emitted.add(s)
                unit = platoons[plat]["units"][pos]["baseId"]
                assigns.append(
                    {
                        "allyCode": str(ac[0]),
                        "unitBaseId": unit,
                        "zoneId": zone,
                        "platoonDefinitionId": "tb3-platoon-%d" % (plat + 1),
                    }
                )
    return {"phase": phase_string(rote, fills, day), "timestamp": timestamp, "platoonAssignments": assigns}


def filename(phase, timestamp):
    """Download filename following the EchoBase convention (colons -> _)."""
    ts = timestamp.replace(":", "_")
    return "echobase-assignments-ROTE-P%s-%s.json" % (phase.replace("/", "_"), ts)
