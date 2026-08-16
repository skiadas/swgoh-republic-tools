#!/usr/bin/env python3
"""Discord OAuth login + roles derived from the guild roster.

Users sign in with Discord (OAuth2 `identify` scope — no bot, no passwords).
Their stable Discord id is stored in a signed cookie; roles are computed on
the fly from the guild manifest's `memberLevel` for the linked allycode, so a
nightly roster refresh is reflected immediately without re-login.

SWGOH has no public way to prove a player owns an allycode, so links are
created by an admin (discord user id + allycode) or via self-service with
admin confirmation later.
"""

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

from itsdangerous import BadSignature, SignatureExpired, TimestampSigner

from swgoh_reviewer.config import data_root

SESSION_COOKIE = "swgoh_session"
SESSION_SALT = "swgoh-session-v1"
ADMIN_COOKIE = "swgoh_admin"
ADMIN_SALT = "swgoh-admin-v1"
ADMIN_TTL = 60 * 60 * 24  # 24h admin session

# SWGOH memberLevel -> role. 1 = unknown/other, 2 = member, 3 = officer,
# 4 = leader (leaders unique per guild).
ROLE_MAP = {2: "member", 3: "officer", 4: "leader"}


def app_secret():
    return os.environ.get("SWGOH_APP_SECRET", "")


def signer():
    secret = app_secret()
    if not secret:
        # stable per-process random secret: cookies reset on restart (dev)
        secret = os.environ.setdefault("_SWGOH_DEV_SECRET", os.urandom(32).hex())
    return TimestampSigner(secret, salt=SESSION_SALT)


def sign_session(data):
    return signer().sign(json.dumps(data)).decode()


def _admin_signer():
    secret = app_secret()
    if not secret:
        secret = os.environ.setdefault("_SWGOH_DEV_SECRET", os.urandom(32).hex())
    return TimestampSigner(secret, salt=ADMIN_SALT)


def sign_admin():
    """Sign a fresh admin-session cookie value (no secrets inside, just a claim)."""
    return _admin_signer().sign(json.dumps({"admin": True})).decode()


def read_admin(cookie):
    """True iff the cookie is a valid, unexpired admin session."""
    if not cookie:
        return False
    try:
        _admin_signer().unsign(cookie, max_age=ADMIN_TTL)
        return True
    except (BadSignature, SignatureExpired, ValueError):
        return False


def read_session(cookie):
    if not cookie:
        return None
    try:
        payload = signer().unsign(cookie, max_age=60 * 60 * 24 * 30)  # 30 days
        return json.loads(payload)
    except (BadSignature, SignatureExpired, ValueError):
        return None


def discord_enabled():
    return bool(os.environ.get("SWGOH_DISCORD_CLIENT_ID") and os.environ.get("SWGOH_DISCORD_CLIENT_SECRET"))


def discord_authorize_url():
    params = {
        "client_id": os.environ["SWGOH_DISCORD_CLIENT_ID"],
        "redirect_uri": os.environ.get("SWGOH_DISCORD_REDIRECT", ""),
        "response_type": "code",
        "scope": "identify",
    }
    return "https://discord.com/oauth2/authorize?" + urllib.parse.urlencode(params)


def _post_form(url, fields):
    data = urllib.parse.urlencode(fields).encode()
    req = urllib.request.Request(
        url,
        data=data,
        method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded", "User-Agent": "swgoh-reviewer/1.0"},
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        body = exc.read().decode(errors="replace")[:300]
        raise RuntimeError(f"Discord API {exc.code}: {body or exc.reason}") from exc


def exchange_code(code, redirect_uri):
    """Exchange the OAuth code for a (discord_id, username)."""
    token = _post_form(
        "https://discord.com/api/v10/oauth2/token",
        {
            "client_id": os.environ["SWGOH_DISCORD_CLIENT_ID"],
            "client_secret": os.environ["SWGOH_DISCORD_CLIENT_SECRET"],
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
        },
    )
    access = token["access_token"]
    req = urllib.request.Request(
        "https://discord.com/api/v10/users/@me",
        headers={"Authorization": f"Bearer {access}"},
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        me = json.loads(resp.read())
    return str(me.get("id")), me.get("username") or str(me.get("id"))


def int_or(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return value


def roles_for(db, outdir, discord_id):
    """Map {guild_id: role} for a discord user from their linked player's roster row."""
    link = db.get_discord_link(discord_id)
    if not link or not link.get("allycode"):
        return {}
    ally = int_or(link["allycode"])
    roles = {}
    for g in db.list_guilds():
        p = (outdir / "guilds" / f"{g['id']}.json")
        if not p.exists():
            continue
        try:
            manifest = json.loads(p.read_text())
        except (OSError, ValueError):
            continue
        for member in manifest.get("members", []):
            if int_or(member.get("allyCode")) == ally:
                role = ROLE_MAP.get(member.get("memberLevel"))
                if role:
                    roles[g["id"]] = role
                break
    return roles


def is_officer(roles, guild_id):
    return roles.get(guild_id) in ("officer", "leader")


def admin_discord_id():
    return os.environ.get("SWGOH_ADMIN_DISCORD_ID", "")


def is_admin_user(session):
    return bool(session and admin_discord_id() and str(session.get("discord_id")) == admin_discord_id())


if __name__ == "__main__":  # pragma: no cover
    # quick self-check of the signer round-trip
    s = sign_session({"discord_id": "1", "username": "x"})
    print("roundtrip:", read_session(s))
    print("tampered:", read_session(s[:-2] + "xx"))
