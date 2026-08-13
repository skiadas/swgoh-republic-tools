#!/usr/bin/env python3
"""FastAPI web layer for the SWGOH reviewer.

Serves each registered guild's generated pages (squad report, ROTE calculator)
and exposes token-gated admin endpoints to register guilds, refresh (fetch)
and regenerate. Viewing is open to anyone for guilds in the allowlist (the
DB); admin actions require SWGOH_ADMIN_TOKEN. Discord OAuth roles will layer
on top of the same guild registry later.
"""

import hmac
import html
import os
import re
import threading
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse

from swgoh_reviewer.comlink import DEFAULT_COMLINK
from swgoh_reviewer.config import data_root
from server.db import DB
from server.jobs import JobRunner, refresh_hour

GUILD_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def admin_token():
    return os.environ.get("SWGOH_ADMIN_TOKEN", "")


def admin_enabled():
    return bool(admin_token())


def check_admin(token: str = Query(default="")):
    if not admin_enabled():
        raise HTTPException(503, "SWGOH_ADMIN_TOKEN not configured")
    if not token or not hmac.compare_digest(token, admin_token()):
        raise HTTPException(401, "invalid admin token")
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

    def require_guild(guild_id: str):
        if not GUILD_ID_RE.match(guild_id):
            raise HTTPException(400, "bad guild id")
        g = db.get_guild(guild_id)
        if g is None:
            raise HTTPException(404, "guild not registered")
        return g

    @app.get("/", response_class=HTMLResponse)
    def index():
        rows = [g for g in db.list_guilds() if g["enabled"]]
        if not rows:
            body = '<div class="card">No guilds yet.</div>'
        else:
            body = "<table><tr><th>Guild</th><th>TB</th><th>Last refresh</th><th></th></tr>"
            for g in rows:
                body += (
                    f"<tr><td>{_esc(g['name'] or g['id'])}</td><td>{_esc(g['tb_id'])}</td>"
                    f"<td>{_esc((g['last_refresh'] or '—')[:19])}</td>"
                    f"<td><a href='/g/{g['id']}'>open</a></td></tr>"
                )
            body += "</table>"
        return _page("SWGOH reviewer", body)

    @app.get("/healthz")
    def healthz():
        return {"ok": True}

    @app.get("/g/{guild_id}", response_class=HTMLResponse)
    def guild_page(guild_id: str):
        g = require_guild(guild_id)
        links = ""
        report = outdir / "guilds" / f"{guild_id}.squads.html"
        calc_file = outdir / "guilds" / f"{guild_id}.calculator.html"
        links += f"<a href='/g/{guild_id}/report'>squad report</a>" if report.is_file() else "squad report (pending)"
        links += f"<a href='/g/{guild_id}/calc'>ROTE calculator</a>" if calc_file.is_file() else "ROTE calculator (pending)"
        body = f'<div class="card"><b>{_esc(g["name"] or g["id"])}</b> · TB {_esc(g["tb_id"])} · last refresh {_esc((g["last_refresh"] or "—")[:19])}</div>'
        body += f'<div class="card">{links}</div>'
        return _page(f"{g['name'] or guild_id} — SWGOH reviewer", body)

    @app.get("/g/{guild_id}/report")
    def guild_report(guild_id: str):
        require_guild(guild_id)
        return FileResponse(safe_guild_file(outdir, guild_id, "squads.html"))

    @app.get("/g/{guild_id}/calc")
    def guild_calc(guild_id: str):
        require_guild(guild_id)
        return FileResponse(safe_guild_file(outdir, guild_id, "calculator.html"))

    # ---------------- admin ----------------

    @app.get("/admin", response_class=HTMLResponse)
    def admin_page(_ok: bool = Depends(check_admin)):
        rows = "".join(
            f"<tr><td>{_esc(g['id'])}</td><td>{_esc(g['name'] or '—')}</td>"
            f"<td>{_esc(g['tb_id'])}</td><td>{'yes' if g['enabled'] else 'no'}</td>"
            f"<td>{_esc((g['last_refresh'] or '—')[:19])}</td>"
            f"<td><a href='/admin/g/{g['id']}'>manage</a></td></tr>"
            for g in db.list_guilds()
        )
        body = """
<h2>Register a guild</h2>
<form method="post" action="/admin/guilds">
  <input name="guild_id" placeholder="guild id (optional if ally code)">
  <input name="allycode" placeholder="member ally code">
  <input name="name" placeholder="display name (optional)">
  <select name="tb_id"><option value="t05D">t05D</option></select>
  <button>Register + refresh</button>
</form>
<h2>Guilds</h2>
<table><tr><th>id</th><th>name</th><th>tb</th><th>enabled</th><th>last refresh</th><th></th></tr>""" + rows + "</table>"
        return _page("Admin — SWGOH reviewer", body)

    @app.get("/admin/g/{guild_id}", response_class=HTMLResponse)
    def admin_guild(guild_id: str, _ok: bool = Depends(check_admin)):
        g = require_guild(guild_id)
        body = f"""
<div class="card"><b>{_esc(g['id'])}</b> — {_esc(g['name'] or '—')}<br>
  TB <b>{_esc(g['tb_id'])}</b> · enabled <b>{'yes' if g['enabled'] else 'no'}</b> · last refresh {_esc((g['last_refresh'] or '—')[:19])}
  <br><a href="/g/{guild_id}/report">report</a> <a href="/g/{guild_id}/calc">calc</a></div>
<form method="post" action="/admin/guilds/{guild_id}/refresh"><button>Refresh now (fetch from EA)</button></form>
<form method="post" action="/admin/guilds/{guild_id}/regen"><button>Regenerate pages (from cache)</button></form>
<form method="post" action="/admin/guilds/{guild_id}/settings">
  TB: <select name="tb_id"><option value="t05D">t05D</option></select>
  Enabled: <select name="enabled"><option value="1">yes</option><option value="0">no</option></select>
  <button>Update settings</button>
</form>
"""
        return _page(f"Admin {g['id']} — SWGOH reviewer", body)

    @app.post("/admin/guilds")
    def register_guild(
        guild_id: str = Query(default=""),
        allycode: str = Query(default=""),
        name: str = Query(default=""),
        tb_id: str = Query(default="t05D"),
        _ok: bool = Depends(check_admin),
    ):
        if not guild_id and not allycode:
            raise HTTPException(400, "need guild_id or allycode")
        if guild_id:
            if not GUILD_ID_RE.match(guild_id):
                raise HTTPException(400, "bad guild id")
        else:
            # resolve the guild from a member's ally code (needs comlink)
            from swgoh_comlink import SwgohComlink

            try:
                with SwgohComlink(url=comlink) as c:
                    guild_id = c.get_player(allycode=str(allycode)).get("guildId")
            except Exception as exc:  # noqa: BLE001
                raise HTTPException(502, f"could not resolve guild from ally code: {exc}") from exc
            if not guild_id:
                raise HTTPException(404, "ally code resolved to no guild")
        db.upsert_guild(guild_id, name=name or None, tb_id=tb_id or None)
        try:
            runner.refresh_guild(guild_id, comlink)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(502, str(exc)) from exc
        return {"guildId": guild_id, "status": "ok"}

    @app.post("/admin/guilds/{guild_id}/refresh")
    def refresh_guild(guild_id: str, _ok: bool = Depends(check_admin)):
        require_guild(guild_id)
        try:
            return runner.refresh_guild(guild_id, comlink)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(502, str(exc)) from exc

    @app.post("/admin/guilds/{guild_id}/regen")
    def regen_guild(guild_id: str, _ok: bool = Depends(check_admin)):
        g = require_guild(guild_id)
        try:
            return runner.regen(guild_id, tb_id=g["tb_id"] or "t05D", squads_json=g.get("squads_json"))
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(502, str(exc)) from exc

    @app.post("/admin/guilds/{guild_id}/settings")
    def update_settings(
        guild_id: str,
        tb_id: str = Query(default=""),
        enabled: str = Query(default=""),
        _ok: bool = Depends(check_admin),
    ):
        require_guild(guild_id)
        db.upsert_guild(guild_id, tb_id=tb_id or None, enabled=(1 if enabled == "1" else 0))
        return {"guildId": guild_id, "status": "ok"}

    @app.post("/admin/refresh")
    def refresh_all(_ok: bool = Depends(check_admin)):
        return {"results": runner.refresh_all(comlink)}

    return app


app = create_app()

if __name__ == "__main__":
    import uvicorn

    uvicorn.run("server.app:app", host="0.0.0.0", port=int(os.environ.get("SWGOH_PORT", "8000")), reload=False)
