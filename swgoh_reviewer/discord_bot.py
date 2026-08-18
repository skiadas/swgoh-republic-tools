#!/usr/bin/env python3
"""Discord bot command logic: /plan and /ops.

Pure functions over the same data the web app uses (the current plan in the
DB, the calc payload, the light roster projection), formatted as Discord
messages. The HTTP/interactions side lives in `server/discord.py`.

Guild resolution is requester-driven: a Discord user known to the system via
their `discord_links` entry resolves to the guild containing their player;
otherwise the command must carry an ally code and the guild is resolved from
that player's roster membership.
"""

import json
from pathlib import Path

from swgoh_reviewer import assignments_logic, calc, calc_logic, platoons

DAYS = range(1, 7)


def int_or(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return value


def _load_manifest(outdir, guild_id):
    p = Path(outdir) / "guilds" / f"{guild_id}.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text())
    except (OSError, ValueError):
        return None


def resolve_guild(outdir, db, discord_id=None, allycode=None):
    """The registered guild a player belongs to, or None if unknown.

    `discord_id` resolves the caller's linked ally code first; an explicit
    `allycode` wins. The guild is found by scanning each guild's roster
    manifest, matching the pattern in `server/auth.py:roles_for`.
    """
    if allycode is None and discord_id is not None:
        link = db.get_discord_link(str(discord_id))
        if link:
            allycode = link.get("allycode")
    if allycode is None:
        return None
    ac = int_or(allycode)
    for g in db.list_guilds():
        manifest = _load_manifest(outdir, g["id"])
        if not manifest:
            continue
        for member in manifest.get("members", []):
            if int_or(member.get("allyCode")) == ac:
                return g["id"]
    return None


def _goal_label(row):
    if row["goal"] == "0" or int(row["goal"]) == 0:
        return "preload"
    stars = int(row["goal"])
    if row["special"] and stars < 3:
        return "🎁"
    return "★" * stars


def format_plan(outdir, db, guild_id, day=None):
    """The guild's current plan: total stars + one line per day (or a day)."""
    if day is not None and day not in DAYS:
        return f"Day must be 1–{DAYS[-1]}."
    row = db.get_current_plan(guild_id)
    if row is None:
        return "No plan has been published for this guild yet."
    payload = json.loads(row["payload"] or "{}")
    data = calc.build_data(outdir, guild_id)
    days = payload.get("days") or {}
    computed = calc_logic.compute(
        data,
        days,
        payload.get("deployPct") or 100,
        bool(payload.get("unlockZeffo")),
        bool(payload.get("unlockMandalore")),
    )
    cs = computed[-1]["chainStars"]
    unlocks = ", ".join(
        n for n, on in (("Zeffo", payload.get("unlockZeffo")), ("Mandalore", payload.get("unlockMandalore"))) if on
    )
    lines = [
        f"**{data['guildName']} — {computed[-1]['totalStars']}★** "
        f"(deploy {payload.get('deployPct') or 100}%{', unlocks: ' + unlocks if unlocks else ''})",
        f"Dark {cs['dark']} · Neutral {cs['neutral']} · Light {cs['light']} · Zeffo {cs['zeffo']} · Mandalore {cs['mandalore']}",
    ]
    for r in computed:
        if day is not None and r["day"] != day:
            continue
        bits = []
        for row in r["rows"]:
            bits.append(f"{row['a']['planet']['name']} {_goal_label(row)}")
        flag = ""
        if not r["feasible"]:
            flag = " ⚠ goals unreachable"
        elif r["shortEst"]:
            flag = " ⚠ CM short"
        lines.append(f"Day {r['day']}: {' · '.join(bits)} — minCM {r['minPct']:.0f}%{flag}")
    return "\n".join(lines)


def format_ops(outdir, db, guild_id, allycode):
    """A player's platoon assignments from the current plan (their 'ops')."""
    row = db.get_current_plan(guild_id)
    if row is None:
        return "No plan has been published for this guild yet."
    payload = json.loads(row["payload"] or "{}")
    data = platoons.build_data(outdir, guild_id, light=True)
    entries = assignments_logic.build_roster(data["planets"], data["members"], payload.get("fills") or {})
    entry = next((e for e in entries if str(e["ac"]) == str(allycode)), None)
    if entry is None:
        return f"No player with ally code {allycode} in this guild."
    return f"**{data['guildName']}**\n" + assignments_logic.member_markdown(entry)


def handle_interaction(payload, outdir, db):
    """Dispatch a verified interaction payload to a Discord response dict.

    `payload` is the JSON body Discord posted; returns the JSON-serializable
    response for `type 4` channel messages (or `type 1` for pings).
    """
    if payload.get("type") == 1:
        return {"type": 1}
    if payload.get("type") != 2:
        return {"type": 4, "data": {"content": "Unsupported interaction."}}
    data = payload.get("data") or {}
    user = (payload.get("member") or {}).get("user") or payload.get("user") or {}
    discord_id = str(user.get("id")) if user.get("id") else None
    opts = {o.get("name"): o.get("value") for o in (data.get("options") or []) if isinstance(o, dict)}
    name = data.get("name")
    if name == "plan":
        return _cmd_plan(outdir, db, discord_id, opts)
    if name == "ops":
        return _cmd_ops(outdir, db, discord_id, opts)
    return {"type": 4, "data": {"content": f"Unknown command: {name}."}}


def _message(text):
    return {"type": 4, "data": {"content": text}}


def _cmd_plan(outdir, db, discord_id, opts):
    allycode = opts.get("allycode")
    guild_id = resolve_guild(outdir, db, discord_id, allycode)
    if guild_id is None:
        hint = "Register your ally code with an admin, or pass one: `/plan allycode:123456789`."
        if allycode:
            return _message(f"Ally code {allycode} isn't in any registered guild. " + hint)
        return _message("You're not linked to a player yet. " + hint)
    day = opts.get("day")
    return _message(format_plan(outdir, db, guild_id, day=int(day) if day is not None else None))


def _cmd_ops(outdir, db, discord_id, opts):
    allycode = opts.get("allycode")
    if allycode is None and discord_id is not None:
        link = db.get_discord_link(discord_id)
        if link:
            allycode = link.get("allycode")
    if allycode is None:
        return _message("You're not linked to a player yet. Register your ally code with an admin, or pass one: `/ops allycode:123456789`.")
    guild_id = resolve_guild(outdir, db, None, allycode)
    if guild_id is None:
        return _message(f"Ally code {allycode} isn't in any registered guild.")
    return _message(format_ops(outdir, db, guild_id, allycode))
