#!/usr/bin/env python3
"""FastAPI web layer for the SWGOH reviewer.

Serves each registered guild's generated pages (squad report, ROTE calculator)
and exposes token-gated admin endpoints to register guilds, refresh (fetch)
and regenerate. Viewing is open to anyone for guilds in the allowlist (the
DB); admin actions require SWGOH_ADMIN_TOKEN. Discord OAuth roles will layer
on top of the same guild registry later.
"""

import os
import sys
from pathlib import Path

# Make the repo root importable when run as `python server/app.py` (sys.path[0]
# would otherwise be `server/`); the gitignored local `.env` is loaded in
# `__main__` so `SWGOH_PORT` etc. apply for local dev.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import hmac
import html
import json
import re
import threading
import time
from contextlib import asynccontextmanager

import jsonschema
from fastapi import Depends, FastAPI, Form, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse

from swgoh_reviewer.comlink import DEFAULT_COMLINK
from swgoh_reviewer.config import data_root
from server import auth
from server.db import DB
from server.jobs import JobRunner, refresh_hour

GUILD_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def admin_token():
    return os.environ.get("SWGOH_ADMIN_TOKEN", "")


def admin_enabled():
    return bool(admin_token())


def is_admin(request):
    """Admin via the signed admin cookie, or the configured Discord admin user."""
    if not admin_enabled():
        return False
    if auth.read_admin(request.cookies.get(auth.ADMIN_COOKIE)):
        return True
    session = auth.read_session(request.cookies.get(auth.SESSION_COOKIE))
    return bool(session) and auth.is_admin_user(session)


def require_admin(request: Request):
    """Dependency for admin API routes: raise 401 unless an admin session exists."""
    if not admin_enabled():
        raise HTTPException(503, "SWGOH_ADMIN_TOKEN not configured")
    if not is_admin(request):
        raise HTTPException(401, "admin session required")
    return True


def safe_guild_file(outdir: Path, guild_id: str, suffix: str):
    if not GUILD_ID_RE.match(guild_id):
        raise HTTPException(400, "bad guild id")
    path = (outdir / "guilds" / f"{guild_id}.{suffix}").resolve()
    root = outdir.resolve()
    if not str(path).startswith(str(root)) or not path.is_file():
        raise HTTPException(404, "not found")
    return path


def _esc(s):
    return html.escape(str(s or ""))


def _page(title, body):
    return f"""<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{_esc(title)}</title>
<style>
 body {{ font-family: -apple-system,"Segoe UI",Roboto,sans-serif; margin:0; background:#fafafa; color:#222; }}
 header {{ background:#1c2541; color:#fff; padding:12px 20px; }}
 header h1 {{ margin:0; font-size:18px; }}
 main {{ padding:16px 20px; max-width:900px; margin:0 auto; }}
 .card {{ background:#fff; border:1px solid #ddd; border-radius:8px; padding:10px 14px; margin:10px 0; }}
 .card a {{ margin-right:12px; }}
 table {{ border-collapse:collapse; width:100%; font-size:13px; }}
 th,td {{ border:1px solid #ddd; padding:5px 8px; text-align:left; }}
 th {{ background:#f0f2f5; }}
 input,select {{ padding:3px 6px; }}
 .muted {{ color:#888; }}
</style></head><body><header><h1>{_esc(title)}</h1></header><main>{body}</main></body></html>"""


def create_app(outdir=None, db_path=None, comlink=None):
    outdir = Path(outdir or data_root())
    if db_path is None:
        db_path = outdir / "service.db"
    comlink = comlink or os.environ.get("SWGOH_COMLINK", DEFAULT_COMLINK)

    db = DB(db_path)
    runner = JobRunner(db, outdir=outdir)
    stop = threading.Event()
    thread = None

    @asynccontextmanager
    async def lifespan(app):
        nonlocal thread
        db.mark_running_interrupted()
        if os.environ.get("SWGOH_NIGHTLY", "0") == "1":
            thread = threading.Thread(
                target=runner.nightly_loop,
                args=(stop, comlink, refresh_hour()),
                daemon=True,
            )
            thread.start()
        yield
        stop.set()
        if thread is not None:
            thread.join(timeout=5)

    app = FastAPI(title="SWGOH reviewer", lifespan=lifespan)
    app.state.db = db
    app.state.outdir = outdir
    app.state.runner = runner
    app.state.comlink = comlink

    def guild_display(g):
        """Display name: DB name, else the manifest's guildName, else the id."""
        if g.get("name"):
            return g["name"]
        p = outdir / "guilds" / f"{g['id']}.json"
        if p.exists():
            try:
                name = json.loads(p.read_text()).get("guildName")
                if name:
                    return name
            except (OSError, ValueError):
                pass
        return g["id"]

    def require_guild(guild_id: str):
        if not GUILD_ID_RE.match(guild_id):
            raise HTTPException(400, "bad guild id")
        g = db.get_guild(guild_id)
        if g is None:
            raise HTTPException(404, "guild not registered")
        return g

    def session_user(request):
        return auth.read_session(request.cookies.get(auth.SESSION_COOKIE))

    def user_roles(request):
        session = session_user(request)
        if not session:
            return {}
        return auth.roles_for(db, outdir, session.get("discord_id"))

    def require_guild_role(guild_id: str, request: Request):
        """Allow if an admin session exists, or the viewer is an officer/leader
        of the guild per the roster."""
        if is_admin(request):
            return
        session = session_user(request)
        roles = auth.roles_for(db, outdir, session.get("discord_id")) if session else {}
        if not auth.is_officer(roles, guild_id):
            raise HTTPException(403, "officer role required for this guild")
        return

    # ---------------- discord auth ----------------

    @app.get("/auth/login")
    def auth_login():
        if not auth.discord_enabled():
            raise HTTPException(503, "Discord login not configured")
        return RedirectResponse(auth.discord_authorize_url())

    @app.get("/auth/discord/callback")
    def auth_callback(code: str = Query(default="")):
        if not auth.discord_enabled() or not code:
            raise HTTPException(400, "missing code")
        redirect = os.environ.get("SWGOH_DISCORD_REDIRECT", "")
        try:
            discord_id, username = auth.exchange_code(code, redirect)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(502, f"Discord exchange failed: {exc}") from exc
        resp = RedirectResponse("/auth/me", status_code=302)
        resp.set_cookie(
            auth.SESSION_COOKIE,
            auth.sign_session({"discord_id": discord_id, "username": username}),
            max_age=60 * 60 * 24 * 30,
            httponly=True,
            samesite="lax",
            secure=os.environ.get("SWGOH_COOKIE_SECURE", "0") == "1",
        )
        return resp

    @app.get("/auth/logout")
    def auth_logout():
        resp = RedirectResponse("/", status_code=302)
        resp.delete_cookie(auth.SESSION_COOKIE)
        return resp

    @app.get("/auth/me", response_class=HTMLResponse)
    def auth_me(request: Request):
        session = session_user(request)
        if not session:
            return _page("Not signed in", '<p>Not signed in.</p><p><a href="/auth/login">Sign in with Discord</a></p>')
        roles = auth.roles_for(db, outdir, session["discord_id"])
        body = f"<p>Signed in as <b>{_esc(session.get('username'))}</b> (discord id <code>{_esc(session['discord_id'])}</code>).</p>"
        if roles:
            body += "<p>Roles: " + ", ".join(f"{_esc(g)}: {r}" for g, r in roles.items()) + "</p>"
        else:
            body += '<p>No linked player. Share your discord id above with an admin to link an ally code.</p>'
        body += '<p><a href="/auth/logout">Sign out</a></p>'
        return _page("Signed in", body)

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request):
        admin = ""
        if is_admin(request):
            admin = '<div class="card"><a href="/admin">Admin</a></div>'
        rows = db.list_guilds()
        if not rows:
            body = admin + '<div class="card">No guilds yet.</div>'
        else:
            body = admin + "<table><tr><th>Guild</th><th>Last refresh</th><th></th></tr>"
            for g in rows:
                body += (
                    f"<tr><td>{_esc(guild_display(g))}</td>"
                    f"<td>{_esc((g['last_refresh'] or '—')[:19])}</td>"
                    f"<td><a href='/g/{g['id']}'>open</a></td></tr>"
                )
            body += "</table>"
        return _page("SWGOH reviewer", body)

    @app.get("/healthz")
    def healthz():
        from swgoh_reviewer import __version__

        return {"ok": True, "version": __version__}

    @app.get("/g/{guild_id}", response_class=HTMLResponse)
    def guild_page(guild_id: str, request: Request):
        g = require_guild(guild_id)
        links = ""
        report = outdir / "guilds" / f"{guild_id}.squads.html"
        calc_file = outdir / "guilds" / f"{guild_id}.calculator.html"
        links += f"<a href='/g/{guild_id}/report'>squad report</a>" if report.is_file() else "squad report (pending)"
        links += f"<a href='/g/{guild_id}/calc'>ROTE calculator</a>" if calc_file.is_file() else "ROTE calculator (pending)"
        body = f'<div class="card"><b>{_esc(guild_display(g))}</b> · last refresh {_esc((g["last_refresh"] or "—")[:19])}</div>'
        job = db.latest_job(guild_id)
        if job:
            body += f'<div class="card">Job: <b>{_esc(job["kind"])}</b> · {_esc(job["status"])} · started {_esc((job["started_at"] or "")[:19])}'
            if job.get("message"):
                body += f' · <span class="muted">{_esc(job["message"][:120])}</span>'
            body += "</div>"
        body += f'<div class="card">{links}</div>'
        if is_admin(request):
            body += f"""
<div class="card"><a href="/admin/g/{guild_id}">Manage</a> · <a href="/admin">Admin</a>
<form method="post" action="/admin/guilds/{guild_id}/refresh" style="display:inline"><button>Refresh now</button></form></div>"""
        # officer controls
        session = session_user(request)
        roles = auth.roles_for(db, outdir, session["discord_id"]) if session else {}
        officer = auth.is_admin_user(session) or auth.is_officer(roles, guild_id)
        if officer:
            current_squads = g.get("squads_json") or ""
            body += f"""
<div class="card"><h3>Officer settings</h3>
<form method="post" action="/g/{guild_id}/squads">
  <p>Squad definitions (JSON, validated against squads.schema.json):</p>
  <textarea name="squads" rows="12" cols="80">{_esc(current_squads)}</textarea><br>
  <button>Save squad definitions</button>
</form>
</div>"""
        return _page(f"{guild_display(g)} — SWGOH reviewer", body)

    @app.post("/g/{guild_id}/squads")
    async def set_guild_squads(guild_id: str, request: Request):
        require_guild(guild_id)
        require_guild_role(guild_id, request)
        form = await request.form()
        text = (form.get("squads") or "").strip()
        if not text:
            raise HTTPException(400, "empty squads")
        try:
            data = json.loads(text)
        except ValueError as exc:
            raise HTTPException(400, f"invalid JSON: {exc}") from exc
        schema = json.loads((Path(__file__).resolve().parent.parent / "squads.schema.json").read_text())
        try:
            jsonschema.validate(data, schema)
        except jsonschema.ValidationError as exc:
            raise HTTPException(400, f"schema: {exc.message}") from exc
        db.upsert_guild(guild_id, squads_json=json.dumps(data, indent=2))
        runner.regen(guild_id, squads_json=data)
        return RedirectResponse(f"/g/{guild_id}", status_code=303)

    @app.get("/g/{guild_id}/report")
    def guild_report(guild_id: str):
        require_guild(guild_id)
        return FileResponse(safe_guild_file(outdir, guild_id, "squads.html"))

    @app.get("/g/{guild_id}/calc")
    def guild_calc(guild_id: str):
        require_guild(guild_id)
        return FileResponse(safe_guild_file(outdir, guild_id, "calculator.html"))

    # ---------------- admin ----------------

    @app.get("/admin/login", response_class=HTMLResponse)
    def admin_login_page(request: Request):
        if is_admin(request):
            return RedirectResponse("/admin", status_code=302)
        body = (
            "<p>Enter the admin token (set via SWGOH_ADMIN_TOKEN). Your session lasts 24h.</p>"
            '<form method="post" action="/admin/login">'
            '<label>Token: <input type="password" name="token" autofocus autocomplete="current-password"></label> '
            "<button>Sign in</button></form>"
        )
        return _page("Admin login — SWGOH reviewer", body)

    @app.post("/admin/login")
    async def admin_login(request: Request):
        if not admin_enabled():
            raise HTTPException(503, "SWGOH_ADMIN_TOKEN not configured")
        form = await request.form()
        token = str(form.get("token") or "")
        if not token or not hmac.compare_digest(token, admin_token()):
            time.sleep(0.5)
            raise HTTPException(401, "invalid admin token")
        resp = RedirectResponse("/admin", status_code=302)
        resp.set_cookie(
            auth.ADMIN_COOKIE,
            auth.sign_admin(),
            max_age=auth.ADMIN_TTL,
            httponly=True,
            samesite="lax",
            secure=os.environ.get("SWGOH_COOKIE_SECURE", "0") == "1",
        )
        return resp

    @app.get("/admin/logout")
    def admin_logout():
        resp = RedirectResponse("/", status_code=302)
        resp.delete_cookie(auth.ADMIN_COOKIE)
        return resp

    @app.get("/admin", response_class=HTMLResponse)
    def admin_page(request: Request):
        if not is_admin(request):
            return RedirectResponse("/admin/login", status_code=302)
        rows = "".join(
            f"<tr><td>{_esc(guild_display(g))} <span class='muted'>({_esc(g['id'])})</span></td>"
            f"<td>{_esc((g['last_refresh'] or '—')[:19])}</td>"
            f"<td><a href='/admin/g/{g['id']}'>manage</a></td></tr>"
            for g in db.list_guilds()
        )
        links = "".join(
            f"<tr><td>{_esc(l['discord_id'])}</td><td>{_esc(l['allycode'] or '')}</td><td>{_esc(l['linked_by'] or '')}</td></tr>"
            for l in db.list_discord_links()
        )
        body = """
<h2>Register a guild</h2>
<form method="post" action="/admin/guilds">
  <input name="guild_id" placeholder="guild id (optional if ally code)">
  <input name="allycode" placeholder="member ally code">
  <button>Register + refresh</button>
</form>
<h2>Link a Discord user to a player</h2>
<form method="post" action="/admin/links">
  <input name="discord_id" placeholder="discord user id" required>
  <input name="allycode" placeholder="ally code" required>
  <button>Create link</button>
</form>
<h2>Guilds</h2>
<table><tr><th>id</th><th>name</th><th>last refresh</th><th></th></tr>""" + rows + "</table>"
        if links:
            body += "<h2>Discord links</h2><table><tr><th>discord id</th><th>allycode</th><th>linked by</th></tr>" + links + "</table>"
        body += '<p><a href="/admin/logout">Sign out</a></p>'
        return _page("Admin — SWGOH reviewer", body)

    @app.post("/admin/links")
    def create_link(discord_id: str = Form(default=""), allycode: str = Form(default=""), _ok: bool = Depends(require_admin)):
        if not discord_id or not allycode:
            raise HTTPException(400, "need discord_id and allycode")
        db.set_discord_link(discord_id.strip(), allycode.strip())
        return {"discord_id": discord_id, "allycode": allycode, "status": "ok"}

    @app.get("/admin/g/{guild_id}", response_class=HTMLResponse)
    def admin_guild(guild_id: str, request: Request):
        if not is_admin(request):
            return RedirectResponse("/admin/login", status_code=302)
        g = require_guild(guild_id)
        report = outdir / "guilds" / f"{guild_id}.squads.html"
        calc_file = outdir / "guilds" / f"{guild_id}.calculator.html"
        links = f"<a href='/g/{guild_id}/report'>report</a>" if report.is_file() else "report (pending)"
        links += f" <a href='/g/{guild_id}/calc'>calc</a>" if calc_file.is_file() else " calc (pending)"
        job = db.latest_job(guild_id)
        jobline = ""
        if job:
            jobline = f'<div class="card">Job: <b>{_esc(job["kind"])}</b> · {_esc(job["status"])} · started {_esc((job["started_at"] or "")[:19])}'
            if job.get("message"):
                jobline += f' · <span class="muted">{_esc(job["message"][:120])}</span>'
            jobline += "</div>"
        body = f"""
<div class="card"><a href="/admin">&larr; Admin</a> · <a href="/g/{guild_id}">View public page</a></div>
<div class="card"><b>{_esc(guild_display(g))}</b> <span class="muted">({_esc(g['id'])})</span><br>
  last refresh {_esc((g['last_refresh'] or '—')[:19])}
  <br>{links}</div>
{jobline}
<form method="post" action="/admin/guilds/{guild_id}/refresh"><button>Refresh now (fetch from EA)</button></form>
<form method="post" action="/admin/guilds/{guild_id}/regen"><button>Regenerate pages (from cache)</button></form>
<div class="card">
  <form method="post" action="/admin/guilds/{guild_id}/remove">
    <label><input type="checkbox" name="confirm" value="1" required> Remove this guild and its data</label>
    <button>Remove guild</button>
  </form>
</div>
"""
        return _page(f"{guild_display(g)} — SWGOH reviewer", body)

    @app.post("/admin/guilds")
    def register_guild(
        guild_id: str = Form(default=""),
        allycode: str = Form(default=""),
        _ok: bool = Depends(require_admin),
    ):
        if not guild_id and not allycode:
            raise HTTPException(400, "need guild_id or allycode")
        name = None
        if guild_id:
            if not GUILD_ID_RE.match(guild_id):
                raise HTTPException(400, "bad guild id")
        else:
            # resolve the guild from a member's ally code (needs comlink)
            from swgoh_comlink import SwgohComlink

            try:
                with SwgohComlink(url=comlink) as c:
                    player = c.get_player(allycode=str(allycode))
                    guild_id = player.get("guildId")
                    name = player.get("guildName") or None
            except Exception as exc:  # noqa: BLE001
                raise HTTPException(502, f"could not resolve guild from ally code: {exc}") from exc
            if not guild_id:
                raise HTTPException(404, "ally code resolved to no guild")
        db.upsert_guild(guild_id, name=name)
        runner.enqueue("refresh", guild_id, lambda: runner.refresh_guild(guild_id, comlink))
        return RedirectResponse(f"/admin/g/{guild_id}", status_code=303)

    @app.post("/admin/guilds/{guild_id}/refresh")
    def refresh_guild(guild_id: str, _ok: bool = Depends(require_admin)):
        require_guild(guild_id)
        runner.enqueue("refresh", guild_id, lambda: runner.refresh_guild(guild_id, comlink))
        return RedirectResponse(f"/admin/g/{guild_id}", status_code=303)

    @app.post("/admin/guilds/{guild_id}/regen")
    def regen_guild(guild_id: str, _ok: bool = Depends(require_admin)):
        g = require_guild(guild_id)
        runner.enqueue("regen", guild_id, lambda: runner.regen(guild_id, squads_json=g.get("squads_json")))
        return RedirectResponse(f"/admin/g/{guild_id}", status_code=303)

    @app.post("/admin/guilds/{guild_id}/remove")
    def remove_guild(guild_id: str, confirm: str = Form(default=""), _ok: bool = Depends(require_admin)):
        require_guild(guild_id)
        if confirm != "1":
            raise HTTPException(400, "check the confirm box to remove the guild")
        db.delete_guild(guild_id)
        for path in (outdir / "guilds").glob(f"{guild_id}.*"):
            path.unlink()
        return RedirectResponse("/admin", status_code=303)

    @app.post("/admin/refresh")
    def refresh_all(_ok: bool = Depends(require_admin)):
        runner.enqueue("refresh-all", None, lambda: runner.refresh_all(comlink))
        return RedirectResponse("/admin", status_code=303)

    return app


app = create_app()

if __name__ == "__main__":
    import uvicorn

    env_path = Path(__file__).resolve().parent.parent / ".env"
    if env_path.exists():
        for raw in env_path.read_text().splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            if key and key not in os.environ:
                os.environ[key] = value.strip()

    uvicorn.run("server.app:app", host="0.0.0.0", port=int(os.environ.get("SWGOH_PORT", "8000")), reload=False)
