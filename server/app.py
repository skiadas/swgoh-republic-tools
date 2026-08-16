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
import json
import logging
import re
import threading
import time
from contextlib import asynccontextmanager
from urllib.parse import quote

import jsonschema
from fastapi import Depends, FastAPI, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from swgoh_reviewer import assignments_logic, calc, calc_logic, planner, platoons, report_logic
from swgoh_reviewer.comlink import DEFAULT_COMLINK
from swgoh_reviewer.config import data_root
from server import auth
from server.db import DB
from server.jobs import JobRunner, refresh_hour
from server.nav import guild_nav

GUILD_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
PLAN_COOKIE = "plan_work"

SERVER_DIR = Path(__file__).resolve().parent
REPO_ROOT = SERVER_DIR.parent
TEMPLATES_DIR = REPO_ROOT / "templates"
STATIC_DIR = SERVER_DIR / "static"


def _fmt(n, compact=True):
    v = float(n or 0)
    if not compact:
        return f"{int(v):,}"
    a = abs(v)
    for mul, suf in ((1e12, "T"), (1e9, "B"), (1e6, "M"), (1e3, "K")):
        if a >= mul:
            x = v / mul
            return (f"{x:.1f}".rstrip("0").rstrip(".") if x != int(x) else str(int(x))) + suf
    return str(int(v))


def _pfmt(n):
    return f"{int(n or 0):,}"


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
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
    templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
    templates.env.filters["fmt"] = _fmt
    templates.env.filters["pfmt"] = _pfmt

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
        of the guild per the roster. Anonymous -> 401, non-officer -> 403."""
        if is_admin(request):
            return
        session = session_user(request)
        if not session:
            raise HTTPException(401, "sign in required")
        roles = auth.roles_for(db, outdir, session.get("discord_id"))
        if not auth.is_officer(roles, guild_id):
            raise HTTPException(403, "officer role required for this guild")
        return

    def can_edit(guild_id, request):
        """View/edit rights on a guild's calculator/planner/plans: an admin
        session, or a Discord-signed-in officer/leader of that guild."""
        if is_admin(request):
            return True
        session = session_user(request)
        if not session:
            return False
        roles = auth.roles_for(db, outdir, session.get("discord_id"))
        return auth.is_officer(roles, guild_id)

    def auth_state(request):
        """For the shared header: is Discord login enabled, and who is signed in."""
        return {"enabled": auth.discord_enabled(), "user": session_user(request)}

    templates.env.globals["auth_state"] = auth_state

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
            logging.getLogger("uvicorn.error").error("Discord exchange failed: %s", exc)
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
        roles = auth.roles_for(db, outdir, session["discord_id"]) if session else {}
        role_links = []
        for gid, role in roles.items():
            g = db.get_guild(gid)
            role_links.append({"guild_id": gid, "name": guild_display(g) if g else gid, "role": role})
        return templates.TemplateResponse(
            request,
            "auth_me.html",
            {"session": session, "roles": roles, "role_links": role_links, "discord_on": auth.discord_enabled()},
        )

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request):
        return templates.TemplateResponse(
            request,
            "index.html",
            {"guilds": db.list_guilds(), "admin": is_admin(request)},
        )

    @app.get("/healthz")
    def healthz():
        from swgoh_reviewer import __version__

        return {"ok": True, "version": __version__}

    @app.get("/g/{guild_id}", response_class=HTMLResponse)
    def guild_page(guild_id: str, request: Request):
        g = require_guild(guild_id)
        job = db.latest_job(guild_id)
        session = session_user(request)
        roles = auth.roles_for(db, outdir, session["discord_id"]) if session else {}
        officer = auth.is_admin_user(session) or auth.is_officer(roles, guild_id)
        return templates.TemplateResponse(
            request,
            "guild.html",
            {
                "guild_id": guild_id,
                "guild_name": guild_display(g),
                "last_refresh": (g["last_refresh"] or "")[:19],
                "job": job,
                "admin": is_admin(request),
                "officer": officer,
                "current_squads": g.get("squads_json") or "",
                "nav": guild_nav("Home", guild_id),
            },
        )

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

    # ---- squad report (htmx views) ----
    def report_view_ctx(guild_id, search="", squad=0, player="", sort="name", hide=False, view=""):
        report, members = report_logic.load_report(outdir, guild_id)
        players, categories = report_logic.prepare(report, members)
        all_squads = report.get("bySquad", [])
        if view == "players" and not player and players:
            player = str(players[0]["allyCode"])
        return {
            "guild_id": guild_id,
            "report": report,
            "players": players,
            "categories": categories,
            "all_squads": all_squads,
            "R": report_logic,
            "search": search,
            "sort": sort,
            "hide": hide,
            "squad": max(0, min(squad, len(all_squads) - 1)),
            "player": player,
        }

    @app.get("/g/{guild_id}/report", response_class=HTMLResponse)
    def guild_report(guild_id: str, request: Request, view: str = "matrix"):
        require_guild(guild_id)
        view = view if view in ("matrix", "squads", "players", "needs") else "matrix"
        ctx = report_view_ctx(guild_id, view=view)
        ctx["guild_name"] = ctx["report"].get("guildName", guild_id)
        ctx["nav"] = guild_nav("Report", guild_id)
        ctx["view"] = view
        return templates.TemplateResponse(request, "report.html", ctx)

    @app.get("/g/{guild_id}/report/view", response_class=HTMLResponse)
    def report_view(
        guild_id: str,
        request: Request,
        view: str = "matrix",
        squad: int = 0,
        player: str = "",
        search: str = "",
        sort: str = "name",
        hide: bool = False,
    ):
        require_guild(guild_id)
        view = view if view in ("matrix", "squads", "players", "needs") else "matrix"
        ctx = report_view_ctx(guild_id, search=search, squad=squad, player=player, sort=sort, hide=hide, view=view)
        if view == "matrix":
            ctx["players"] = report_logic.filter_players(ctx["players"], ctx["all_squads"], search, hide, sort)
        return templates.TemplateResponse(request, f"_report_{view}.html", ctx)

    # ---- ROTE star calculator ----
    def working_base(guild_id, request):
        """The plan a page shows/edits: the in-progress draft, else the admin's
        selected working plan (per-session cookie), else the current plan."""
        draft = db.get_draft(guild_id)
        if draft:
            return draft
        wid = request.cookies.get(PLAN_COOKIE) if request is not None else None
        if wid:
            p = db.get_plan(wid, guild_id)
            if p:
                return p
        return db.get_current_plan(guild_id)

    def calc_view(guild_id, request):
        data = calc.build_data(outdir, guild_id)
        working = working_base(guild_id, request)
        payload = json.loads(working["payload"]) if working else {}
        days_state = payload.get("days") or {}
        deploy = int(payload.get("deployPct") or 100)
        unlock_zeffo = bool(payload.get("unlockZeffo"))
        unlock_mandalore = bool(payload.get("unlockMandalore"))
        days = calc_logic.compute(data, days_state, deploy, unlock_zeffo, unlock_mandalore)
        return data, days, days_state, deploy, unlock_zeffo, unlock_mandalore

    def calc_body_context(guild_id, data, days, deploy, unlock_zeffo, unlock_mandalore, can_edit=False, compact=True):
        last = days[-1] if days else None
        return {
            "guild_id": guild_id,
            "can_edit": can_edit,
            "days": days,
            "deploy": deploy,
            "unlock_zeffo": unlock_zeffo,
            "unlock_mandalore": unlock_mandalore,
            "compact": compact,
            "guild_gp": data.get("guildGp", 0),
            "total_stars": last["totalStars"] if last else 0,
            "cs": last["chainStars"] if last else {},
        }

    def game_data_missing():
        """True when the ROTE doc the calculator/planner read per request
        isn't on disk yet (a missing units cache is tolerated)."""
        return not (outdir / "rote" / "t05D.json").exists()

    @app.get("/g/{guild_id}/calc", response_class=HTMLResponse)
    def guild_calc(guild_id: str, request: Request):
        require_guild(guild_id)
        if game_data_missing():
            return templates.TemplateResponse(
                request,
                "calc.html",
                {"guild_id": guild_id, "guild_name": guild_display(db.get_guild(guild_id)),
                 "nav": guild_nav("Calculator", guild_id), "game_data_missing": True},
            )
        data, days, _state, deploy, uz, um = calc_view(guild_id, request)
        ctx = calc_body_context(guild_id, data, days, deploy, uz, um, can_edit=can_edit(guild_id, request))
        ctx["guild_name"] = data["guildName"]
        ctx["nav"] = guild_nav("Calculator", guild_id)
        return templates.TemplateResponse(request, "calc.html", ctx)

    @app.post("/g/{guild_id}/calc/set", response_class=HTMLResponse)
    async def calc_set(guild_id: str, request: Request, _ok: bool = Depends(require_guild_role)):
        require_guild(guild_id)
        form = await request.form()
        days = {}
        for key in form.keys():
            if not key.startswith("d"):
                continue
            base, field = key, "goal"
            if base.endswith("-plats"):
                base, field = base[:-6], "platoons"
            elif base.endswith("-cm"):
                base, field = base[:-3], "cmPct"
            m = re.match(r"^d(\d)-(.+)$", base)
            if not m:
                continue
            day, planet = int(m.group(1)), m.group(2)
            days.setdefault(day, {}).setdefault(planet, {})
            v = form.get(key)
            days[day][planet][field] = str(v) if field == "goal" else int(v or 0)
        deploy = int(form.get("deploy") or 100)
        unlock_zeffo = "unlock-zeffo" in form
        unlock_mandalore = "unlock-mandalore" in form
        compact = "compact" in form
        data = calc.build_data(outdir, guild_id)
        working = working_base(guild_id, request)
        old = json.loads(working["payload"]) if working else {}
        payload = {
            "deployPct": deploy,
            "unlockZeffo": unlock_zeffo,
            "unlockMandalore": unlock_mandalore,
            "days": days,
            "fills": old.get("fills") or {},
        }
        db.set_draft(guild_id, (working or {}).get("name") or "Draft", json.dumps(payload))
        computed = calc_logic.compute(data, days, deploy, unlock_zeffo, unlock_mandalore)
        ctx = calc_body_context(guild_id, data, computed, deploy, unlock_zeffo, unlock_mandalore, can_edit=True, compact=compact)
        return templates.TemplateResponse(request, "_calc_body.html", ctx)

    @app.get("/g/{guild_id}/calc/optimize", response_class=HTMLResponse)
    def calc_optimize_form(guild_id: str, request: Request):
        require_guild(guild_id)
        data, _days, _state, deploy, unlock_zeffo, unlock_mandalore = calc_view(guild_id, request)
        return templates.TemplateResponse(
            request,
            "_calc_optimize.html",
            {
                "guild_id": guild_id,
                "groups": calc_logic.phase_groups(data),
                "defaults": calc_logic.LEVEL_EST_DEFAULT,
                "deploy": deploy,
                "unlock_zeffo": unlock_zeffo,
                "unlock_mandalore": unlock_mandalore,
            },
        )

    @app.post("/g/{guild_id}/calc/optimize", response_class=HTMLResponse)
    async def calc_optimize(guild_id: str, request: Request, _ok: bool = Depends(require_guild_role)):
        require_guild(guild_id)
        form = await request.form()
        mode = str(form.get("mode") or "run")
        opt_mode = str(form.get("opt-mode") or "level")
        deploy = int(form.get("opt-deploy") or 100)
        unlock_zeffo = "opt-unlock-zeffo" in form
        unlock_mandalore = "opt-unlock-mandalore" in form
        data = calc.build_data(outdir, guild_id)
        est = {}
        plat_cap = {}
        for g in calc_logic.phase_groups(data):
            ph = g["phase"]
            plat_cap[ph] = int(form.get(f"plats-{ph}") or 6)
            for p in g["planets"]:
                if opt_mode == "planet":
                    est[p["name"]] = int(form.get(f"est-planet-{p['name']}") or 100)
                else:
                    est[p["name"]] = int(form.get(f"est-level-{ph}") or 100)
        res = calc_logic.optimize(data, est, unlock_zeffo, unlock_mandalore, deploy, plat_cap)
        if mode == "run":
            return templates.TemplateResponse(request, "_calc_optimize_result.html", {"guild_id": guild_id, "res": res})
        days = {}
        for day_rec in res["days"]:
            days[day_rec["day"]] = {
                nm: {"goal": str(a["goal"]), "platoons": a["plats"], "cmPct": a["cmPct"]}
                for nm, a in day_rec["acts"].items()
            }
        working = working_base(guild_id, request)
        old = json.loads(working["payload"]) if working else {}
        payload = {"deployPct": deploy, "unlockZeffo": unlock_zeffo, "unlockMandalore": unlock_mandalore, "days": days, "fills": old.get("fills") or {}}
        db.set_draft(guild_id, (working or {}).get("name") or "Draft", json.dumps(payload))
        computed = calc_logic.compute(data, days, deploy, unlock_zeffo, unlock_mandalore)
        ctx = calc_body_context(guild_id, data, computed, deploy, unlock_zeffo, unlock_mandalore, can_edit=True)
        return templates.TemplateResponse(request, "_calc_body.html", ctx)

    # ---- ROTE platoon planner (htmx) ----
    _planner_cache = {}

    def planner_data(guild_id):
        stamp = tuple(
            (str(p), p.stat().st_mtime_ns) if p.exists() else ("", 0)
            for p in (outdir / "rote" / "t05D.json", outdir / "guilds" / f"{guild_id}.summary.json", outdir / "game" / "units.json")
        )
        hit = _planner_cache.get(guild_id)
        if hit and hit[0] == stamp:
            return hit[1]
        data = platoons.build_data(outdir, guild_id)
        _planner_cache[guild_id] = (stamp, data)
        return data

    def planner_view(guild_id, request):
        data = planner_data(guild_id)
        draft = db.get_draft(guild_id)
        working = working_base(guild_id, request)
        payload = json.loads(working["payload"]) if working else {}
        return data, payload.get("days") or {}, payload.get("fills") or {}, (working or {}).get("name"), draft is not None

    def planner_day_ctx(guild_id, days_state, fills, d, can_edit):
        data = planner_data(guild_id)
        active = planner.active_planets(days_state, fills, data["planets"], d)
        models = [planner.planet_render_model(p, data["members"], fills, days_state, d) for p in active]
        names = {str(m["ac"]): m["name"] for m in data["members"]}
        return {
            "guild_id": guild_id,
            "day": d,
            "planets": models,
            "member_names": names,
            "can_edit": can_edit,
        }

    def save_draft(guild_id, days_state, fills, request):
        working = working_base(guild_id, request)
        old = json.loads(working["payload"]) if working else {}
        payload = {
            "deployPct": old.get("deployPct", 100),
            "unlockZeffo": old.get("unlockZeffo", False),
            "unlockMandalore": old.get("unlockMandalore", False),
            "days": days_state,
            "fills": fills,
        }
        db.set_draft(guild_id, (working or {}).get("name") or "Draft", json.dumps(payload))

    @app.get("/g/{guild_id}/platoons", response_class=HTMLResponse)
    def guild_platoons(guild_id: str, request: Request, day: int = 1):
        require_guild(guild_id)
        if game_data_missing():
            return templates.TemplateResponse(
                request,
                "planner.html",
                {"guild_id": guild_id, "guild_name": guild_display(db.get_guild(guild_id)),
                 "nav": guild_nav("Planner", guild_id), "game_data_missing": True, "day": 1},
            )
        data, days_state, fills, plan_name, is_draft = planner_view(guild_id, request)
        d = max(1, min(6, day))
        ctx = planner_day_ctx(guild_id, days_state, fills, d, can_edit(guild_id, request))
        ctx["guild_name"] = data["guildName"]
        ctx["nav"] = guild_nav("Planner", guild_id)
        ctx["plan_name"] = plan_name
        ctx["is_draft"] = is_draft
        return templates.TemplateResponse(request, "planner.html", ctx)

    @app.get("/g/{guild_id}/platoons/day", response_class=HTMLResponse)
    def platoons_day(guild_id: str, request: Request, d: int = 1):
        require_guild(guild_id)
        if game_data_missing():
            return HTMLResponse('<div class="notice">Game data isn\'t built yet — an admin should rebuild it.</div>')
        data, days_state, fills, _n, _d = planner_view(guild_id, request)
        ctx = planner_day_ctx(guild_id, days_state, fills, max(1, min(6, d)), can_edit(guild_id, request))
        return templates.TemplateResponse(request, "_platoons_day.html", ctx)

    @app.get("/g/{guild_id}/platoons/picker", response_class=HTMLResponse)
    def platoons_picker(guild_id: str, request: Request, planet: str = "", slot: int = 0, day: int = 1):
        require_guild(guild_id)
        data, days_state, fills, _n, _d = planner_view(guild_id, request)
        p = next((x for x in data["planets"] if x["name"] == planet), None)
        if p is None:
            raise HTTPException(404, "no such planet")
        slot = max(0, min(89, slot))
        elig = planner.eligible_for_slot(p, data["members"], slot)
        by_day = (fills.get(planet, {}).get(day) or fills.get(planet, {}).get(str(day)) or {})
        cur = by_day.get(str(slot))
        sl = planner.unit_at(p, slot)
        for m in elig:
            dims = []
            if planner.unit_assigned_on_day(fills, planner.planets_map(data["planets"]), m["ac"], sl["b"], day, planet, slot):
                dims.append("already places this unit elsewhere today")
            cnt = planner.count_on_planet_day(fills, planet, day, m["ac"]) + (0 if cur == m["ac"] else 1)
            if cnt > planner.MAX_UNITS:
                dims.append(f"already has {cnt - 1} fills on {planet} today (max {planner.MAX_UNITS})")
            m["dim"] = bool(dims)
            m["dim_title"] = "; ".join(dims)
        return templates.TemplateResponse(
            request,
            "_picker.html",
            {"guild_id": guild_id, "planet": planet, "slot": slot, "day": day, "unit": sl["n"], "members": elig, "cur": cur},
        )

    @app.get("/g/{guild_id}/platoons/gen", response_class=HTMLResponse)
    def platoons_gen(guild_id: str, request: Request, d: int = 1, scope: str = "all", planet: str = ""):
        require_guild(guild_id)
        data, days_state, fills, _n, _d = planner_view(guild_id, request)
        day = max(1, min(6, d))
        if scope not in ("all", "day", "planet"):
            scope = "all"
        if scope == "planet":
            active = {p["name"] for p in planner.active_planets(days_state, fills, data["planets"], day)}
            if planet not in active:
                scope = "day"
        else:
            planet = ""
        return templates.TemplateResponse(
            request,
            "_gen.html",
            {"guild_id": guild_id, "day": day, "scope": scope, "planet": planet},
        )

    @app.post("/g/{guild_id}/platoons/assign", response_class=HTMLResponse)
    async def platoons_assign(guild_id: str, request: Request, _ok: bool = Depends(require_guild_role)):
        require_guild(guild_id)
        form = await request.form()
        planet = str(form.get("planet") or "")
        slot = int(form.get("slot") or 0)
        day = int(form.get("day") or 1)
        ac = str(form.get("ac") or "")
        data, days_state, fills, _n, _d = planner_view(guild_id, request)
        new_fills = {p: {dd: dict(s) for dd, s in by.items()} for p, by in fills.items()}
        by_day = new_fills.setdefault(planet, {})
        slots = by_day.setdefault(str(day), {})
        if not ac:
            slots.pop(str(slot), None)
            if not slots:
                by_day.pop(str(day), None)
        else:
            slots[str(slot)] = ac
        save_draft(guild_id, days_state, new_fills, request)
        ctx = planner_day_ctx(guild_id, days_state, new_fills, day, True)
        return templates.TemplateResponse(request, "_platoons_day.html", ctx)

    @app.post("/g/{guild_id}/platoons/generate", response_class=HTMLResponse)
    async def platoons_generate(guild_id: str, request: Request, _ok: bool = Depends(require_guild_role)):
        require_guild(guild_id)
        form = await request.form()
        scope_mode = str(form.get("gen-scope") or "all")
        planet = str(form.get("gen-planet") or "")
        strategy = str(form.get("gen-strategy") or "strongest")
        policy = str(form.get("gen-policy") or "plan")
        day = max(1, min(6, int(form.get("day") or 1)))
        data, days_state, fills, _n, _d = planner_view(guild_id, request)
        if scope_mode == "planet" and planet:
            scope = {"mode": "planet", "day": day, "planet": planet}
        elif scope_mode == "day":
            scope = {"mode": "day", "day": day}
        else:
            scope = None
        new_fills, _added = planner.generate(data["planets"], data["members"], fills, days_state, scope, strategy, policy)
        save_draft(guild_id, days_state, new_fills, request)
        ctx = planner_day_ctx(guild_id, days_state, new_fills, day, True)
        return templates.TemplateResponse(request, "_platoons_day.html", ctx)

    @app.post("/g/{guild_id}/platoons/clear", response_class=HTMLResponse)
    def platoons_clear(guild_id: str, request: Request, d: int = 1, _ok: bool = Depends(require_guild_role)):
        require_guild(guild_id)
        data, days_state, _fills, _n, _d = planner_view(guild_id, request)
        save_draft(guild_id, days_state, {}, request)
        ctx = planner_day_ctx(guild_id, days_state, {}, max(1, min(6, d)), True)
        return templates.TemplateResponse(request, "_platoons_day.html", ctx)

    @app.post("/g/{guild_id}/platoons/publish", response_class=HTMLResponse)
    def platoons_publish(guild_id: str, request: Request, d: int = 1, _ok: bool = Depends(require_guild_role)):
        require_guild(guild_id)
        draft = db.get_draft(guild_id)
        if draft is None:
            raise HTTPException(400, "no draft to publish")
        wid = request.cookies.get(PLAN_COOKIE)
        target = db.get_plan(wid, guild_id) if wid else None
        if target is None:
            target = db.get_current_plan(guild_id)
        if target is not None:
            db.update_plan(target["id"], guild_id, payload=draft["payload"])
            db.set_current_plan(guild_id, target["id"])
        else:
            plan_id = db.create_plan(guild_id, draft["name"] or "Guild plan", draft["payload"], owner_name="admin")
            db.set_current_plan(guild_id, plan_id)
        db.clear_draft(guild_id)
        data, days_state, fills, _n, _d = planner_view(guild_id, request)
        ctx = planner_day_ctx(guild_id, days_state, fills, max(1, min(6, d)), True)
        resp = templates.TemplateResponse(request, "_platoons_day.html", ctx)
        resp.headers["HX-Refresh"] = "true"
        return resp

    # ---- assignments by member ----
    def assignments_view(guild_id):
        data = platoons.build_data(outdir, guild_id, light=True)
        row = db.get_current_plan(guild_id)
        payload = json.loads(row["payload"]) if row else {}
        entries = assignments_logic.build_roster(data["planets"], data["members"], payload.get("fills") or {})
        assigned = sum(1 for r in entries if r["total"])
        total = sum(r["total"] for r in entries)
        summary_line = f"{assigned} of {len(entries)} members assigned · {total} fills"
        plan_line = ""
        if row:
            at = (row["updated_at"] or "")[:16].replace("T", " ")
            plan_line = f"Guild plan “{row['name']}” · updated by {row['owner_name'] or 'admin'}" + (f" · {at}" if at else "")
        return data, entries, summary_line, plan_line, row is None

    @app.get("/g/{guild_id}/assignments", response_class=HTMLResponse)
    def guild_assignments(guild_id: str, request: Request):
        require_guild(guild_id)
        data, entries, summary_line, plan_line, no_plan = assignments_view(guild_id)
        return templates.TemplateResponse(
            request,
            "assignments.html",
            {
                "guild_id": guild_id,
                "guild_name": data["guildName"],
                "nav": guild_nav("Assignments", guild_id),
                "entries": entries,
                "no_plan": no_plan,
                "summary_line": summary_line,
                "plan_line": plan_line,
            },
        )

    @app.get("/g/{guild_id}/assignments/roster", response_class=HTMLResponse)
    def assignments_roster(guild_id: str, request: Request, search: str = Query(default="")):
        require_guild(guild_id)
        data, entries, _s, _p, no_plan = assignments_view(guild_id)
        q = (search or "").strip().lower()
        if q:
            entries = [r for r in entries if q in r["name"].lower() or q in str(r["ac"])]
        return templates.TemplateResponse(
            request,
            "_assignments_roster.html",
            {"guild_id": guild_id, "entries": entries, "no_plan": no_plan},
        )

    @app.get("/g/{guild_id}/assignments/member/{allycode}/markdown")
    def assignments_markdown(guild_id: str, allycode: int):
        require_guild(guild_id)
        data, entries, _s, _p, _n = assignments_view(guild_id)
        entry = next((r for r in entries if str(r["ac"]) == str(allycode)), None)
        if entry is None:
            raise HTTPException(404, "no such member")
        return Response(content=assignments_logic.member_markdown(entry), media_type="text/plain; charset=utf-8")

    # ---- guild plans (server-side shared plans) ----

    def _plan_json(row):
        return {
            "id": row["id"],
            "name": row["name"],
            "payload": json.loads(row["payload"]),
            "ownerName": row["owner_name"],
            "isCurrent": bool(row["is_current"]),
            "updatedAt": row["updated_at"],
        }

    @app.get("/g/{guild_id}/plan")
    def guild_plan(guild_id: str):
        require_guild(guild_id)
        row = db.get_current_plan(guild_id)
        return {"plan": _plan_json(row) if row else None}

    @app.get("/g/{guild_id}/plans")
    def guild_plans_list(guild_id: str, request: Request):
        require_guild(guild_id)
        plans = [_plan_json(r) for r in db.list_plans(guild_id)]
        return {"plans": plans, "canPublish": can_edit(guild_id, request)}

    @app.post("/g/{guild_id}/plans")
    async def create_plan(guild_id: str, request: Request, _ok: bool = Depends(require_guild_role)):
        require_guild(guild_id)
        data = await request.json()
        payload = data.get("payload")
        if not isinstance(payload, dict):
            raise HTTPException(400, "payload must be a JSON object")
        name = str(data.get("name") or "Plan")[:80]
        plan_id = db.create_plan(guild_id, name, json.dumps(payload))
        return {"id": plan_id}

    @app.put("/g/{guild_id}/plans/{plan_id}")
    async def update_plan(guild_id: str, plan_id: int, request: Request, _ok: bool = Depends(require_guild_role)):
        require_guild(guild_id)
        if db.get_plan(plan_id, guild_id) is None:
            raise HTTPException(404, "no such plan")
        data = await request.json()
        payload = data.get("payload")
        if payload is not None and not isinstance(payload, dict):
            raise HTTPException(400, "payload must be a JSON object")
        db.update_plan(
            plan_id,
            guild_id,
            name=str(data["name"])[:80] if "name" in data and data["name"] is not None else None,
            payload=json.dumps(payload) if payload is not None else None,
        )
        return {"ok": True}

    @app.post("/g/{guild_id}/plans/{plan_id}/current")
    def set_current_plan(guild_id: str, plan_id: int, _ok: bool = Depends(require_guild_role)):
        require_guild(guild_id)
        if db.get_plan(plan_id, guild_id) is None:
            raise HTTPException(404, "no such plan")
        db.set_current_plan(guild_id, plan_id)
        return {"ok": True}

    @app.delete("/g/{guild_id}/plans/{plan_id}")
    def delete_plan(guild_id: str, plan_id: int, _ok: bool = Depends(require_guild_role)):
        require_guild(guild_id)
        db.delete_plan(plan_id, guild_id)
        return {"ok": True}

    # ---- plan management (htmx popover for admins) ----
    def plans_popover_ctx(guild_id, request, base=None, next=""):
        if base is None:
            base = working_base(guild_id, request)
        draft_exists = db.get_draft(guild_id) is not None
        plans = []
        for p in db.list_plans(guild_id):
            plans.append({
                "id": p["id"],
                "name": p["name"],
                "is_current": bool(p["is_current"]),
                "is_editing": base is not None and not draft_exists and base.get("id") == p["id"],
            })
        return {
            "guild_id": guild_id,
            "plans": plans,
            "working_name": (base or {}).get("name"),
            "is_draft": draft_exists,
            "next": next,
        }

    def _plan_cookie(resp, plan_id):
        resp.set_cookie(
            PLAN_COOKIE,
            str(plan_id),
            max_age=60 * 60 * 24 * 365,
            httponly=True,
            samesite="lax",
            secure=os.environ.get("SWGOH_COOKIE_SECURE", "0") == "1",
        )
        return resp

    @app.get("/g/{guild_id}/plans/popover", response_class=HTMLResponse)
    def plans_popover(guild_id: str, request: Request, next: str = Query(default="")):
        require_guild(guild_id)
        return templates.TemplateResponse(request, "_plans.html", plans_popover_ctx(guild_id, request, next=next))

    @app.post("/g/{guild_id}/plans/working", response_class=HTMLResponse)
    def plans_working(guild_id: str, request: Request, plan_id: str = Form(default=""), next: str = Form(default=""), _ok: bool = Depends(require_guild_role)):
        require_guild(guild_id)
        if plan_id and db.get_plan(plan_id, guild_id) is None:
            raise HTTPException(404, "no such plan")
        db.clear_draft(guild_id)
        resp = templates.TemplateResponse(request, "_plans.html", plans_popover_ctx(guild_id, request, next=next))
        if plan_id:
            resp = _plan_cookie(resp, plan_id)
        else:
            resp.delete_cookie(PLAN_COOKIE)
        resp.headers["HX-Redirect"] = next or f"/g/{guild_id}/platoons"
        return resp

    @app.post("/g/{guild_id}/plans/save", response_class=HTMLResponse)
    def plans_save(guild_id: str, request: Request, name: str = Form(default=""), next: str = Form(default=""), _ok: bool = Depends(require_guild_role)):
        require_guild(guild_id)
        name = name.strip()[:80]
        if not name:
            raise HTTPException(400, "plan name required")
        base = working_base(guild_id, request)
        payload = json.loads(base["payload"]) if base else {}
        plan_id = db.create_plan(guild_id, name, json.dumps(payload))
        db.clear_draft(guild_id)
        resp = templates.TemplateResponse(request, "_plans.html", plans_popover_ctx(guild_id, request, base={"id": plan_id, "name": name}, next=next))
        return _plan_cookie(resp, plan_id)

    @app.post("/g/{guild_id}/plans/{plan_id}/ui-set-current", response_class=HTMLResponse)
    def plans_set_current_ui(guild_id: str, plan_id: int, request: Request, next: str = Form(default=""), _ok: bool = Depends(require_guild_role)):
        require_guild(guild_id)
        if db.get_plan(plan_id, guild_id) is None:
            raise HTTPException(404, "no such plan")
        db.set_current_plan(guild_id, plan_id)
        return templates.TemplateResponse(request, "_plans.html", plans_popover_ctx(guild_id, request, next=next))

    @app.post("/g/{guild_id}/plans/{plan_id}/ui-rename", response_class=HTMLResponse)
    def plans_rename_ui(guild_id: str, plan_id: int, request: Request, name: str = Form(default=""), next: str = Form(default=""), _ok: bool = Depends(require_guild_role)):
        require_guild(guild_id)
        name = name.strip()[:80]
        if not name:
            raise HTTPException(400, "plan name required")
        if db.get_plan(plan_id, guild_id) is None:
            raise HTTPException(404, "no such plan")
        db.update_plan(plan_id, guild_id, name=name)
        return templates.TemplateResponse(request, "_plans.html", plans_popover_ctx(guild_id, request, next=next))

    @app.post("/g/{guild_id}/plans/{plan_id}/ui-delete", response_class=HTMLResponse)
    def plans_delete_ui(guild_id: str, plan_id: int, request: Request, next: str = Form(default=""), _ok: bool = Depends(require_guild_role)):
        require_guild(guild_id)
        db.delete_plan(plan_id, guild_id)
        resp = templates.TemplateResponse(request, "_plans.html", plans_popover_ctx(guild_id, request, next=next))
        if request.cookies.get(PLAN_COOKIE) == str(plan_id):
            resp.delete_cookie(PLAN_COOKIE)
        return resp

    # ---------------- admin ----------------

    @app.get("/admin/login", response_class=HTMLResponse)
    def admin_login_page(request: Request):
        if is_admin(request):
            return RedirectResponse("/admin", status_code=302)
        return templates.TemplateResponse(request, "admin_login.html", {})

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
    def admin_page(request: Request, linked: str = Query(default=""), linked_ally: str = Query(default="")):
        if not is_admin(request):
            return RedirectResponse("/admin/login", status_code=302)
        guilds = []
        for g in db.list_guilds():
            job = db.latest_job(g["id"])
            guilds.append({**g, "last_job_status": job["status"] if job else None})
        return templates.TemplateResponse(
            request,
            "admin.html",
            {
                "guilds": guilds,
                "links": db.list_discord_links(),
                "recent_jobs": db.recent_jobs(12),
                "linked": linked,
                "linked_ally": linked_ally,
            },
        )

    @app.post("/admin/links")
    def create_link(discord_id: str = Form(default=""), allycode: str = Form(default=""), _ok: bool = Depends(require_admin)):
        if not discord_id or not allycode:
            raise HTTPException(400, "need discord_id and allycode")
        db.set_discord_link(discord_id.strip(), allycode.strip())
        return RedirectResponse(f"/admin?linked={quote(discord_id.strip())}&linked_ally={quote(allycode.strip())}", status_code=303)

    @app.post("/admin/game-data")
    def rebuild_game_data(_ok: bool = Depends(require_admin)):
        runner.enqueue("game-data", None, runner.rebuild_game_data)
        return RedirectResponse("/admin", status_code=303)

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
        return RedirectResponse("/admin", status_code=303)

    @app.post("/admin/guilds/{guild_id}/refresh")
    def refresh_guild(guild_id: str, _ok: bool = Depends(require_admin)):
        require_guild(guild_id)
        runner.enqueue("refresh", guild_id, lambda: runner.refresh_guild(guild_id, comlink))
        return RedirectResponse("/admin", status_code=303)

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
