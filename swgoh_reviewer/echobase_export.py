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

The export is scoped to a single day: only slots filled on the selected day
are emitted (a slot's fill is the player assigned to it on that day; fills
from earlier days are ignored — a slot can be re-assigned on a later day as
a backup). When `active` (the day's active planets, as the planner tab shows
them) is given, only those planets are included; otherwise every planet with
at least one slot filled on the day is included. The phase string's per-chain
token is the highest level among the covered planets of that chain
(Zeffo/Mandalore add the Z/M prefix). The filename carries both the phase
string and the day (e.g. `...-P5_M4_4-D6-<timestamp>.json`).
"""

from datetime import datetime, timezone

from swgoh_reviewer.planner import SLOTS_PER_PLATOON


def _all_planets(rote, active=None):
    for ph in rote.get("phases", []):
        for planet in ph.get("planets") or []:
            if active is not None and (planet.get("name") or "") not in active:
                continue
            yield planet, ph.get("phase", 1)


def _day_fills(fills, name, day):
    by_day = (fills or {}).get(name) or {}
    return by_day.get(day) or by_day.get(str(day)) or {}


def _covered_planets(rote, fills, day, active=None):
    """(planet, phase) pairs with at least one slot filled on `day`."""
    for planet, phase in _all_planets(rote, active):
        if _day_fills(fills, planet.get("name") or "", day):
            yield planet, phase


def _chain_levels(rote, fills, day, active=None):
    """Per-chain highest covered level + Zeffo/Mandalore presence.

    Returns (dark, neutral, light, zeffo, mandalore) where each level is 0
    when the chain has no covered planet.
    """
    dark = neutral = light = 0
    zeffo = mandalore = False
    for planet, phase in _covered_planets(rote, fills, day, active):
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


def phase_string(rote, fills, day, active=None):
    """The export's `phase` field for the planets covered on `day`.

    e.g. "1/1/1", "3/3/Z3", "4/M4/4", or "2/M4/Z4" for a day covering
    Geonosis + Zeffo + Mandalore + level-4 planets. A chain with no covered
    planet reads "1".
    """
    dark, neutral, light, zeffo, mandalore = _chain_levels(rote, fills, day, active)

    def tok(level, pref):
        return pref + str(level) if level else "1"

    return "%s/%s/%s" % (
        tok(dark, ""),
        tok(neutral, "M" if mandalore else ""),
        tok(light, "Z" if zeffo else ""),
    )


def build_file(rote, fills, day, active=None, timestamp=None):
    """The file payload: {phase, timestamp, platoonAssignments}.

    Emits one entry per slot filled on `day` for the planets in `active`
    (all planets in the TB doc when `active` is None); slots whose fill
    references an out-of-range platoon/position are skipped.
    """
    if timestamp is None:
        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
    assigns = []
    for planet, _phase in _all_planets(rote, active):
        zone = planet["planetId"] + "_recon01"
        platoons = (planet.get("op") or {}).get("platoons") or []
        name = planet.get("name") or ""
        for k, ac in _day_fills(fills, name, day).items():
            s = int(k)
            if not ac:
                continue
            plat, pos = divmod(s, SLOTS_PER_PLATOON)
            if plat >= len(platoons) or pos >= len(platoons[plat].get("units") or []):
                continue
            unit = platoons[plat]["units"][pos]["baseId"]
            assigns.append(
                {
                    "allyCode": str(ac),
                    "unitBaseId": unit,
                    "zoneId": zone,
                    "platoonDefinitionId": "tb3-platoon-%d" % (plat + 1),
                }
            )
    return {"phase": phase_string(rote, fills, day, active), "timestamp": timestamp, "platoonAssignments": assigns}


def filename(phase, timestamp, day=None):
    """Download filename following the EchoBase convention (colons -> _).

    The selected day is appended after the phase token, e.g.
    `echobase-assignments-ROTE-P5_M4_4-D6-<timestamp>.json`.
    """
    ts = timestamp.replace(":", "_")
    suffix = "-D%d" % day if day is not None else ""
    return "echobase-assignments-ROTE-P%s%s-%s.json" % (phase.replace("/", "_"), suffix, ts)