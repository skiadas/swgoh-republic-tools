#!/usr/bin/env python3
"""Discord interactions (slash-command bot) HTTP side.

The bot answers the app's own `POST /discord/interactions` route: every
request is verified with the application's public key (Ed25519), pings are
acknowledged, and slash commands are dispatched to
`swgoh_reviewer/discord_bot.handle_interaction`. Commands are registered
guild-scoped (instant) in every server the bot is in on startup, falling back
to global registration when the bot is in no servers yet.

Requires `SWGOH_DISCORD_BOT_TOKEN` and `SWGOH_DISCORD_PUBLIC_KEY`; without
them the route is disabled (404).
"""

import base64
import json
import logging
import os
import urllib.request

import nacl.signing

from swgoh_reviewer.discord_bot import handle_interaction

log = logging.getLogger("uvicorn.error")

API = "https://discord.com/api/v10"
_USER_AGENT = "swgoh-reviewer/1.0"

_COMMANDS = [
    {
        "name": "plan",
        "description": "Show the guild's TB plan",
        "options": [
            {"name": "day", "description": "Day 1-6 to show", "type": 4, "required": False},
            {"name": "allycode", "description": "Ally code whose guild plan to show", "type": 3, "required": False},
        ],
    },
    {
        "name": "ops",
        "description": "Show a player's platoon assignments",
        "options": [
            {"name": "allycode", "description": "Ally code (defaults to your linked player)", "type": 3, "required": False},
        ],
    },
]


def bot_token():
    return os.environ.get("SWGOH_DISCORD_BOT_TOKEN", "")


def public_key_hex():
    return os.environ.get("SWGOH_DISCORD_PUBLIC_KEY", "")


def enabled():
    return bool(bot_token() and public_key_hex())


def app_id():
    """Application id from the bot token.

    Newer tokens are `bot.<id>.<secret>`. Classic tokens prefix the numeric
    id as unpadded base64url (e.g. `MTIzNDU...`); Discord wants the numeric
    snowflake in API paths, so the prefix must be decoded.
    """
    parts = bot_token().split(".")
    if len(parts) >= 3 and parts[0] == "bot":
        return parts[1]
    token_id = parts[0] if parts else ""
    if token_id.isdigit():
        return token_id
    try:
        pad = "=" * (-len(token_id) % 4)
        decoded = base64.urlsafe_b64decode(token_id + pad).decode()
        return decoded if decoded.isdigit() else token_id
    except Exception:  # noqa: BLE001
        return token_id


def _request(method, path, token, body=None):
    req = urllib.request.Request(
        f"{API}{path}",
        data=json.dumps(body).encode() if body is not None else None,
        method=method,
        headers={
            "Authorization": f"Bot {token}",
            "Content-Type": "application/json" if body is not None else "",
            "User-Agent": _USER_AGENT,
        },
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read()) if resp.status != 204 else None


def bot_guilds(token):
    """Ids of the servers the bot is in ("" when none)."""
    try:
        out = _request("GET", "/users/@me/guilds", token)
    except Exception as exc:  # noqa: BLE001
        log.warning("discord bot guilds fetch failed: %s", exc)
        return []
    return [str(g.get("id")) for g in (out or []) if g.get("id")]


def register_commands():
    """Register the slash commands on startup (guild-scoped, then global)."""
    token = bot_token()
    servers = bot_guilds(token)
    if servers:
        for gid in servers:
            _request("PUT", f"/applications/{app_id()}/guilds/{gid}/commands", token, _COMMANDS)
        log.info("discord: registered %d commands in %d servers", len(_COMMANDS), len(servers))
    else:
        _request("PUT", f"/applications/{app_id()}/commands", token, _COMMANDS)
        log.info("discord: registered %d global commands (no servers yet)", len(_COMMANDS))


def send_followup(token, application_id, interaction_token, content):
    """Post a follow-up message to a deferred interaction (ephemeral)."""
    _request("POST", f"/webhooks/{application_id}/{interaction_token}", token, {"content": content, "flags": 64})


def execute_followup(token, application_id, interaction_token, render):
    """Render a deferred interaction's content and post it as a follow-up.

    Runs in a background task after the ACK; a render failure degrades to a
    generic error message rather than leaving the user staring at "thinking".
    """
    try:
        content = render()
    except Exception:  # noqa: BLE001
        log.exception("discord: follow-up render failed")
        content = "Something went wrong rendering that — try again in a moment."
    try:
        send_followup(token, application_id, interaction_token, content)
    except Exception as exc:  # noqa: BLE001
        log.warning("discord: follow-up send failed: %s", exc)


def verify(payload: bytes, signature: str, timestamp: str) -> bool:
    """True iff the Ed25519 signature over `timestamp + payload` is valid."""
    if not enabled() or not signature or not timestamp:
        return False
    try:
        key = nacl.signing.VerifyKey(bytes.fromhex(public_key_hex()))
        key.verify(timestamp.encode() + payload, bytes.fromhex(signature))
        return True
    except Exception:  # noqa: BLE001
        return False
