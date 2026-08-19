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
    Mandalore is included (e.g. phase 3 with Zeffo = "3/3/Z3", phase 4 with
    Mandalore = "4/M4/4").
"""

from datetime import datetime, timezone

SLOTS_PER_PLATOON = 15


def _phase_planets(rote, phase):
    for ph in rote.get("phases", []):
        if ph.get("phase") == phase:
            return ph.get("planets") or []
    return []


def _chain_token(planet_id, name, phase, token):
    if planet_id.endswith("_bonus"):
        if name == "Zeffo":
            token["light"] = "Z" + token.get("light", str(phase))
        elif name == "Mandalore":
            token["neutral"] = "M" + token.get("neutral", str(phase))
        else:
            token.setdefault("dark", str(phase))
        return
    if "_conflict01" in planet_id:
        token["light"] = str(phase)
    elif "_conflict02" in planet_id:
        token["dark"] = str(phase)
    else:
        token["neutral"] = str(phase)


def phase_string(rote, phase):
    """The export's `phase` field for a phase's planets, e.g. "1/1/1"."""
    token = {}
    for planet in _phase_planets(rote, phase):
        _chain_token(planet.get("planetId") or "", planet.get("name") or "", phase, token)
    return "%s/%s/%s" % (token.get("dark", "1"), token.get("neutral", "1"), token.get("light", "1"))


def build_file(rote, fills, phase, timestamp=None):
    """The file payload: {phase, timestamp, platoonAssignments}.

    Emits one entry per filled slot across the phase's planets; slots whose
    fill references an out-of-range platoon/position are skipped.
    """
    if timestamp is None:
        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
    assigns = []
    for planet in _phase_planets(rote, phase):
        zone = planet["planetId"] + "_recon01"
        platoons = (planet.get("op") or {}).get("platoons") or []
        for slots in (fills or {}).get(planet.get("name"), {}).values():
            for k, ac in slots.items():
                s = int(k)
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
    return {"phase": phase_string(rote, phase), "timestamp": timestamp, "platoonAssignments": assigns}


def filename(phase, timestamp):
    """Download filename following the EchoBase convention (colons -> _)."""
    ts = timestamp.replace(":", "_")
    return "echobase-assignments-ROTE-P%s-%s.json" % (phase.replace("/", "_"), ts)
