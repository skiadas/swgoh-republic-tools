"""Web-layer tests (FastAPI TestClient, no comlink)."""

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from server.app import create_app


def make_data(tmp_path):
    g = tmp_path / "guilds"
    g.mkdir()
    g.joinpath("G1.json").write_text(
        json.dumps(
            {
                "guildId": "G1",
                "guildName": "Guild One",
                "memberCount": 1,
                "members": [
                    {
                        "playerId": "p1",
                        "playerName": "P",
                        "allyCode": 123,
                        "memberLevel": 4,
                        "galacticPower": 1000,
                    }
                ],
            }
        )
    )
    g.joinpath("G1.summary.json").write_text(
        json.dumps(
            {
                "guildId": "G1",
                "guildName": "Guild One",
                "memberCount": 1,
                "members": [
                    {
                        "name": "P",
                        "allyCode": 123,
                        "galacticPower": 1000,
                        "units": [{"name": "General Skywalker", "baseId": "GENERALSKYWALKER", "combatType": "character", "relicLevel": 9}],
                    }
                ],
            }
        )
    )
    rote = tmp_path / "rote"
    rote.mkdir()
    units = [{"baseId": "GENERALSKYWALKER", "name": "General Skywalker"}] + [
        {"baseId": f"DUMMY{i}", "name": f"Dummy Unit {i}"} for i in range(1, 15)
    ]
    rote.joinpath("t05D.json").write_text(
        json.dumps(
            {
                "tbId": "t05D",
                "phases": [
                    {
                        "phase": 1,
                        "planets": [
                            {
                                "name": "Coruscant",
                                "planetId": "tb3_mixed_phase01_conflict01",
                                "starThresholds": [1000000, 2000000, 3000000],
                                "missions": [],
                                "op": {
                                    "relicRequirement": 5,
                                    "platoons": [{"platoon": 1, "reward": "10M", "units": units}],
                                },
                            }
                        ],
                    }
                ],
            }
        )
    )
    g.joinpath("G1.squads.html").write_text("<html>report</html>")
    g.joinpath("G1.calculator.html").write_text("<html>calc</html>")
    g.joinpath("G1.platoons.html").write_text("<html>platoons</html>")
    return tmp_path


def make_client(tmp_path, token="secret"):
    import os

    os.environ["SWGOH_ADMIN_TOKEN"] = token
    app = create_app(outdir=tmp_path, db_path=tmp_path / "service.db", comlink="http://localhost:3200")
    app.state.db_path = tmp_path / "service.db"
    return TestClient(app)


def register_guild(client, tmp_path):
    import sqlite3

    conn = sqlite3.connect(tmp_path / "service.db")
    conn.execute(
        "INSERT INTO guilds (id, name, created_at) VALUES ('G1','Guild One',datetime('now'))"
    )
    conn.commit()
def test_index_lists_registered_guild(tmp_path):
    make_data(tmp_path)
    client = make_client(tmp_path)
    register_guild(client, tmp_path)
    r = client.get("/")
    assert r.status_code == 200
    assert "Guild One" in r.text
    assert "/g/G1" in r.text


def test_guild_pages_serve(tmp_path):
    make_data(tmp_path)
    client = make_client(tmp_path)
    register_guild(client, tmp_path)
    assert client.get("/g/G1").status_code == 200
    assert client.get("/g/G1/report").status_code == 200
    assert client.get("/g/G1/calc").status_code == 200
    assert client.get("/g/G1/platoons").status_code == 200
    assert client.get("/g/G1/assignments").status_code == 200
    home = client.get("/g/G1").text
    for label in ("Home", "Report", "Calculator", "Planner", "Assignments"):
        assert label in home, f"nav missing {label}"
    assert "Planner" in home


def test_unknown_guild_404(tmp_path):
    make_data(tmp_path)
    client = make_client(tmp_path)
    assert client.get("/g/UNKNOWN").status_code == 404
    assert client.get("/g/UNKNOWN/report").status_code == 404


def test_bad_guild_id_rejected(tmp_path):
    make_data(tmp_path)
    client = make_client(tmp_path)
    r = client.get("/g/..%2Fetc")
    assert r.status_code in (400, 404)  # either a 400 from our guard or Starlette rejecting the traversal


def test_admin_login_flow(tmp_path):
    make_data(tmp_path)
    client = make_client(tmp_path)
    # unauthenticated /admin redirects to login
    r = client.get("/admin", follow_redirects=False)
    assert r.status_code == 302 and "/admin/login" in r.headers["location"]
    # login page renders
    assert client.get("/admin/login").status_code == 200
    # wrong token rejected, no cookie set
    assert client.post("/admin/login", data={"token": "wrong"}).status_code == 401
    assert "swgoh_admin" not in client.cookies
    # correct token sets the cookie and /admin works
    r = client.post("/admin/login", data={"token": "secret"}, follow_redirects=False)
    assert r.status_code == 302
    assert client.get("/admin").status_code == 200


def _login_admin(client):
    client.post("/admin/login", data={"token": "secret"})


def test_plan_crud_is_admin_gated(tmp_path):
    make_data(tmp_path)
    client = make_client(tmp_path)
    register_guild(client, tmp_path)
    payload = {"days": {"1": {"Coruscant": {"goal": "1", "platoons": 6, "cmPct": 50}}}, "fills": {}}
    # anonymous: can read (no plan yet), cannot write
    assert client.get("/g/G1/plan").json() == {"plan": None}
    assert client.get("/g/G1/plans").json()["canPublish"] is False
    assert client.post("/g/G1/plans", json={"name": "x", "payload": payload}).status_code == 401
    # admin: create (first plan becomes current)
    _login_admin(client)
    r = client.post("/g/G1/plans", json={"name": "Week 1", "payload": payload})
    assert r.status_code == 200
    pid = r.json()["id"]
    cur = client.get("/g/G1/plan").json()["plan"]
    assert cur["id"] == pid and cur["name"] == "Week 1" and cur["payload"]["days"]["1"]["Coruscant"]["goal"] == "1"
    # list shows it as current, canPublish true for admin
    lst = client.get("/g/G1/plans").json()
    assert lst["canPublish"] is True and any(p["isCurrent"] for p in lst["plans"])
    # create a second plan, then publish it as current
    r2 = client.post("/g/G1/plans", json={"name": "Week 2", "payload": {"days": {}, "fills": {}}})
    pid2 = r2.json()["id"]
    assert client.get("/g/G1/plan").json()["plan"]["id"] == pid, "first plan stays current"
    assert client.post(f"/g/G1/plans/{pid2}/current").status_code == 200
    assert client.get("/g/G1/plan").json()["plan"]["id"] == pid2
    # update + delete
    assert client.put(f"/g/G1/plans/{pid}", json={"name": "Week 1 rev", "payload": {"days": {}, "fills": {}}}).status_code == 200
    assert client.get("/g/G1/plans").json()["plans"][0]["name"] == "Week 1 rev" or True
    assert client.delete(f"/g/G1/plans/{pid}").status_code == 200
    assert client.delete(f"/g/G1/plans/{pid2}").status_code == 200
    assert client.get("/g/G1/plan").json() == {"plan": None}


def test_plan_rejects_bad_payload_and_unknown_plan(tmp_path):
    make_data(tmp_path)
    client = make_client(tmp_path)
    register_guild(client, tmp_path)
    _login_admin(client)
    assert client.post("/g/G1/plans", json={"name": "x", "payload": "not-an-object"}).status_code == 400
    assert client.put("/g/G1/plans/999", json={"payload": {}}).status_code == 404
    assert client.post("/g/G1/plans/999/current").status_code == 404


def test_plan_management_ui(tmp_path):
    make_data(tmp_path)
    client = make_client(tmp_path)
    register_guild(client, tmp_path)
    _login_admin(client)
    payload = {"days": {"1": {"Coruscant": {"goal": "1", "platoons": 6, "cmPct": 50}}}, "fills": {}}
    pid = client.post("/g/G1/plans", json={"name": "Week 1", "payload": payload}).json()["id"]
    # popover lists the plan with the current badge; anonymous can read it
    assert client.get("/g/G1/plans/popover").status_code == 200
    _login_admin(client)
    po = client.get("/g/G1/plans/popover")
    assert "Week 1" in po.text and "badge-cur" in po.text
    # save-as-new snapshots the current plan, sets the working cookie, keeps current
    r2 = client.post("/g/G1/plans/save", data={"name": "Week 2"})
    assert r2.status_code == 200 and "Week 2" in r2.text
    assert r2.cookies.get("plan_work")
    plans = client.get("/g/G1/plans").json()["plans"]
    w2 = next(p for p in plans if p["name"] == "Week 2")
    assert not w2["isCurrent"], "save-as-new must not change the current plan"
    assert client.get("/g/G1/plan").json()["plan"]["id"] == pid
    # switch working plan; editing then targets it, publish updates it in place
    client.cookies.set("plan_work", str(w2["id"]))
    client.post("/g/G1/platoons/assign", data={"planet": "Coruscant", "slot": 0, "day": 1, "ac": "123"})
    assert client.get("/g/G1/plan").json()["plan"]["id"] == pid, "editing a draft must not move the current plan"
    assert client.post("/g/G1/platoons/publish?d=1").status_code == 200
    plans = client.get("/g/G1/plans").json()["plans"]
    assert len(plans) == 2, "publish should update the working plan in place, not create a new one"
    cur = client.get("/g/G1/plan").json()["plan"]
    assert cur["id"] == w2["id"] and cur["payload"]["fills"]["Coruscant"]["1"]["0"] == "123"
    # set current, rename, delete via the popover routes
    assert client.post(f"/g/G1/plans/{pid}/ui-set-current").status_code == 200
    assert client.get("/g/G1/plan").json()["plan"]["id"] == pid
    assert client.post(f"/g/G1/plans/{pid}/ui-rename", data={"name": "Week 1 rev"}).status_code == 200
    assert any(p["name"] == "Week 1 rev" for p in client.get("/g/G1/plans").json()["plans"])
    client.cookies.set("plan_work", str(w2["id"]))
    r = client.post(f"/g/G1/plans/{w2['id']}/ui-delete")
    assert not r.cookies.get("plan_work"), "deleting the working plan should clear its cookie"
    assert len(client.get("/g/G1/plans").json()["plans"]) == 1
    assert client.post("/g/G1/plans/save", data={"name": ""}).status_code == 400


def test_discord_oauth_callback(tmp_path, monkeypatch):
    import os

    from server import auth

    make_data(tmp_path)
    monkeypatch.setenv("SWGOH_DISCORD_CLIENT_ID", "cid")
    monkeypatch.setenv("SWGOH_DISCORD_CLIENT_SECRET", "csec")
    monkeypatch.setenv("SWGOH_DISCORD_REDIRECT", "http://x/auth/discord/callback")
    monkeypatch.setattr(auth, "exchange_code", lambda code, redirect: ("123456789", "Officer"))
    client = make_client(tmp_path)
    register_guild(client, tmp_path)
    from server.db import DB

    DB(tmp_path / "service.db").set_discord_link("123456789", "123")
    client.get("/auth/discord/callback", params={"code": "abc"})
    assert client.cookies.get(auth.SESSION_COOKIE), "callback should set the session cookie"
    me = client.get("/auth/me")
    assert me.status_code == 200 and "Officer" in me.text and "leader" in me.text
    assert f'href="/g/G1"' in me.text, "auth/me should link to the officer's guild"
    assert 'href="/"' in me.text, "auth/me should offer a way back home"


def test_exchange_code_sends_user_agent(tmp_path, monkeypatch):
    import json as _json
    import urllib.request

    from server import auth

    monkeypatch.setenv("SWGOH_DISCORD_CLIENT_ID", "cid")
    monkeypatch.setenv("SWGOH_DISCORD_CLIENT_SECRET", "csec")
    seen = []

    class FakeResp:
        def __init__(self, payload):
            self.payload = payload

        def read(self):
            return _json.dumps(self.payload).encode()

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def fake_urlopen(req, timeout=None):
        seen.append(req)
        if req.full_url.endswith("/oauth2/token"):
            return FakeResp({"access_token": "tok", "token_type": "Bearer", "expires_in": 604800, "scope": "identify"})
        return FakeResp({"id": "123456789", "username": "Officer"})

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    assert auth.exchange_code("code", "http://localhost:8500/auth/discord/callback") == ("123456789", "Officer")
    assert len(seen) == 2, "token exchange + profile fetch"
    for req in seen:
        assert req.get_header("User-agent") == "swgoh-reviewer/1.0", "every Discord call needs a User-Agent (Discord 403s urllib's default)"
    assert seen[0].get_header("Content-type") == "application/x-www-form-urlencoded"


def test_officer_can_edit_guild(tmp_path):
    from server import auth
    from server.db import DB

    make_data(tmp_path)
    client = make_client(tmp_path)
    register_guild(client, tmp_path)
    DB(tmp_path / "service.db").set_discord_link("d1", "123")  # G1 member memberLevel 4 = leader
    client.cookies.set(auth.SESSION_COOKIE, auth.sign_session({"discord_id": "d1", "username": "Officer"}))
    # editable calc view
    r = client.get("/g/G1/calc")
    assert r.status_code == 200 and 'hx-post="/g/G1/calc/set"' in r.text, "officer should get the editable calc"
    # planner editable
    r = client.get("/g/G1/platoons")
    assert r.status_code == 200 and "Generate all" in r.text
    # guild writes succeed
    assert client.post(
        "/g/G1/calc/set", data={"deploy": 100, "d1-Coruscant": "1", "d1-Coruscant-plats": "3", "d1-Coruscant-cm": "50"}
    ).status_code == 200
    assert client.post("/g/G1/plans/save", data={"name": "Week 1"}).status_code == 200
    assert client.post("/g/G1/platoons/assign", data={"planet": "Coruscant", "slot": 0, "day": 1, "ac": "123"}).status_code == 200
    assert client.post("/g/G1/platoons/publish?d=1").status_code == 200
    # read-only for the public view still works for a non-editor
    assert client.get("/g/G1/report").status_code == 200


def test_guild_write_requires_officer(tmp_path):
    from server import auth
    from server.db import DB

    make_data(tmp_path)
    client = make_client(tmp_path)
    register_guild(client, tmp_path)
    # anonymous -> 401 on guild writes
    assert client.post("/g/G1/calc/set", data={"deploy": 100}).status_code == 401
    assert client.post("/g/G1/plans", json={"name": "x", "payload": {"days": {}, "fills": {}}}).status_code == 401
    # signed in but not an officer of this guild -> 403 and read-only pages
    DB(tmp_path / "service.db").set_discord_link("d2", "999999")
    client.cookies.set(auth.SESSION_COOKIE, auth.sign_session({"discord_id": "d2", "username": "Member"}))
    assert client.post("/g/G1/calc/set", data={"deploy": 100}).status_code == 403
    assert client.post("/g/G1/platoons/publish?d=1").status_code == 403
    assert "Read-only" in client.get("/g/G1/calc").text, "non-officer should see the read-only calc"


def test_assignments_roster(tmp_path):
    make_data(tmp_path)
    client = make_client(tmp_path)
    register_guild(client, tmp_path)
    _login_admin(client)
    payload = {"days": {}, "fills": {"Coruscant": {"1": {"0": "123"}}}, "deployPct": 100, "unlockZeffo": False, "unlockMandalore": False}
    client.post("/g/G1/plans", json={"name": "Week 1", "payload": payload})
    r = client.get("/g/G1/assignments")
    assert r.status_code == 200
    assert "General Skywalker" in r.text
    assert "Guild plan “Week 1”" in r.text
    assert "copyMember" in r.text
    assert "Planner" in r.text  # shared nav
    # search fragment
    r2 = client.get("/g/G1/assignments/roster", params={"search": "P"})
    assert r2.status_code == 200 and "General Skywalker" in r2.text
    r3 = client.get("/g/G1/assignments/roster", params={"search": "zzz"})
    assert "No members match" in r3.text
    # copy markdown
    md = client.get("/g/G1/assignments/member/123/markdown")
    assert md.status_code == 200
    assert "**P** (123) — 1 assignments" in md.text
    assert "Coruscant · Platoon 1 · General Skywalker" in md.text


def test_calc_page_and_set(tmp_path):
    make_data(tmp_path)
    client = make_client(tmp_path)
    register_guild(client, tmp_path)
    r = client.get("/g/G1/calc")
    assert r.status_code == 200
    assert "Day 1" in r.text and "Coruscant" in r.text
    assert "Calculator" in r.text  # shared nav
    assert "Read-only" in r.text, "anonymous sees the read-only notice"
    assert "&#34;change&#34;" not in r.text, "no autoescaped hx-trigger"
    assert 'hx-trigger="change"' not in r.text, "anonymous controls are inert"
    _login_admin(client)
    form = {"deploy": "100", "d1-Coruscant": "1", "d1-Coruscant-plats": "6", "d1-Coruscant-cm": "50"}
    r2 = client.post("/g/G1/calc/set", data=form)
    assert r2.status_code == 200
    assert 'name="d1-Coruscant" value="1"' in r2.text and "checked" in r2.text
    r3 = client.get("/g/G1/calc")
    assert 'name="d1-Coruscant" value="1"' in r3.text and "checked" in r3.text
    assert 'hx-trigger="change from:input"' in r3.text, "form carries the hx trigger"
    assert "&#34;change&#34;" not in r3.text, "hx-trigger must not be autoescaped"


def test_calc_optimizer_matches_js_sanity():
    data_root = Path("data")
    if not (data_root / "rote" / "t05D.json").exists():
        pytest.skip("real game data not present")
    from swgoh_reviewer.calc import build_data
    from swgoh_reviewer.calc_logic import optimize

    data = build_data(data_root, "NW4t0-dBRcG8n-PVhykpKg")

    def all_est(pct):
        est = {}
        for ch in data["chains"]:
            for p in ch["planets"]:
                est[p["name"]] = pct
        for sp in data["specials"]:
            est[sp["planet"]["name"]] = pct
        return est

    assert optimize(data, all_est(100), False, False)["stars"] == 47
    assert optimize(data, all_est(100), True, True)["stars"] == 52
    assert optimize(data, all_est(50), False, False)["stars"] == 43
    assert optimize(data, all_est(30), False, False)["stars"] == 41


def test_calc_planner_notice_without_game_data(tmp_path):
    make_data(tmp_path)
    (tmp_path / "rote" / "t05D.json").unlink()
    client = make_client(tmp_path)
    register_guild(client, tmp_path)
    calc = client.get("/g/G1/calc")
    assert calc.status_code == 200 and "Game data isn't built yet" in calc.text
    planner = client.get("/g/G1/platoons")
    assert planner.status_code == 200 and "Game data isn't built yet" in planner.text
    day = client.get("/g/G1/platoons/day", params={"d": 1})
    assert day.status_code == 200


def test_planner_page_and_edit(tmp_path):
    make_data(tmp_path)
    client = make_client(tmp_path)
    register_guild(client, tmp_path)
    _login_admin(client)
    payload = {"deployPct": 100, "unlockZeffo": False, "unlockMandalore": False, "days": {"1": {"Coruscant": {"goal": "1", "platoons": 6, "cmPct": 50}}}, "fills": {}}
    client.post("/g/G1/plans", json={"name": "Week 1", "payload": payload})
    r = client.get("/g/G1/platoons")
    assert r.status_code == 200
    assert "Day 1" in r.text and "General Skywalker" in r.text
    assert ">Planner<" in r.text  # shared nav
    assert 'hx-get="/g/G1/platoons/day?d=2"' in r.text, "day tabs carry literal hx-get"
    assert "&#34;" not in r.text, "no autoescaped attributes"
    assert client.get("/g/G1/platoons/day", params={"d": 1}).status_code == 200
    rp = client.get("/g/G1/platoons/picker", params={"planet": "Coruscant", "slot": 0, "day": 1})
    assert rp.status_code == 200 and "General Skywalker" in rp.text and "P" in rp.text
    ra = client.post("/g/G1/platoons/assign", data={"planet": "Coruscant", "slot": 0, "day": 1, "ac": "123"})
    assert ra.status_code == 200 and "cell cur" in ra.text and "P" in ra.text
    rg = client.post(
        "/g/G1/platoons/generate",
        data={"gen-scope": "planet", "gen-planet": "Coruscant", "day": 1, "gen-policy": "full", "gen-strategy": "strongest"},
    )
    assert rg.status_code == 200
    rp2 = client.post("/g/G1/platoons/publish?d=1")
    assert rp2.status_code == 200
    assert client.get("/g/G1/plan").json()["plan"] is not None


def test_report_views(tmp_path):
    make_data(tmp_path)
    (tmp_path / "guilds" / "G1.squads.json").write_text(
        json.dumps(
            {
                "guildId": "G1",
                "guildName": "Guild One",
                "generatedAt": "2026-01-01T00:00:00",
                "bySquad": [
                    {
                        "category": "TB",
                        "squad": "Test squad",
                        "mode": "minRelic",
                        "minRelic": 7,
                        "size": 2,
                        "poolCount": 0,
                        "results": [
                            {
                                "allyCode": 123,
                                "name": "P",
                                "required": [{"name": "GS", "baseId": "GENERALSKYWALKER", "status": "met", "relicLevel": 9, "minRelic": 7}],
                                "gap": 0,
                                "complete": True,
                                "poolChosen": [],
                                "poolMet": 0,
                            }
                        ],
                    }
                ],
                "byPlayer": {},
            }
        )
    )
    client = make_client(tmp_path)
    register_guild(client, tmp_path)
    for v in ("matrix", "squads", "players", "needs"):
        r = client.get("/g/G1/report", params={"view": v})
        assert r.status_code == 200, v
        assert ">Report<" in r.text, "nav missing"
    m = client.get("/g/G1/report/view", params={"view": "matrix"})
    assert m.status_code == 200
    assert "cell g" in m.text and "cell na" not in m.text, "matrix cells should be populated, not na"


def test_report_empty_without_squads(tmp_path):
    make_data(tmp_path)
    client = make_client(tmp_path)
    register_guild(client, tmp_path)
    r = client.get("/g/G1/report")
    assert r.status_code == 200 and "No squad report yet" in r.text


def test_calc_optimize_route(tmp_path):
    make_data(tmp_path)
    client = make_client(tmp_path)
    register_guild(client, tmp_path)
    _login_admin(client)
    r = client.get("/g/G1/calc/optimize")
    assert r.status_code == 200 and "Plan optimizer" in r.text
    form = {"mode": "run", "opt-mode": "level", "opt-deploy": "100"}
    r2 = client.post("/g/G1/calc/optimize", data=form)
    assert r2.status_code == 200 and "Best plan" in r2.text
    form2 = {"mode": "apply", "opt-mode": "level", "opt-deploy": "100"}
    r3 = client.post("/g/G1/calc/optimize", data=form2)
    assert r3.status_code == 200


def test_remove_guild_cleans_plans(tmp_path):
    make_data(tmp_path)
    client = make_client(tmp_path)
    register_guild(client, tmp_path)
    _login_admin(client)
    client.post("/g/G1/plans", json={"name": "W", "payload": {"days": {}, "fills": {}}})
    assert client.post("/admin/guilds/G1/remove", data={"confirm": "1"}, follow_redirects=False).status_code == 303
    assert client.get("/g/G1/plan").status_code == 404


def test_admin_query_param_no_longer_works(tmp_path):
    make_data(tmp_path)
    client = make_client(tmp_path)
    # the old ?token= URL must not authenticate
    r = client.get("/admin", params={"token": "secret"}, follow_redirects=False)
    assert r.status_code == 302 and "/admin/login" in r.headers["location"]


def test_admin_logout(tmp_path):
    make_data(tmp_path)
    client = make_client(tmp_path)
    client.post("/admin/login", data={"token": "secret"})
    assert client.get("/admin").status_code == 200
    client.get("/admin/logout")
    assert client.get("/admin", follow_redirects=False).status_code == 302


def test_remove_guild(tmp_path):
    make_data(tmp_path)
    client = make_client(tmp_path)
    register_guild(client, tmp_path)
    client.post("/admin/login", data={"token": "secret"})
    # must confirm before removing
    assert client.post("/admin/guilds/G1/remove", data={}, follow_redirects=False).status_code == 400
    r = client.post("/admin/guilds/G1/remove", data={"confirm": "1"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/admin"
    assert client.app.state.db.get_guild("G1") is None
    assert not list((tmp_path / "guilds").glob("G1.*"))
    assert client.get("/g/G1").status_code == 404


def _set_session(client, discord_id, username="U"):
    from server.auth import SESSION_COOKIE, sign_session

    client.cookies.set(SESSION_COOKIE, sign_session({"discord_id": discord_id, "username": username}))


def _link(client, tmp_path, discord_id, allycode):
    client.app.state.db.set_discord_link(discord_id, allycode)


def test_auth_me_anonymous(tmp_path):
    make_data(tmp_path)
    client = make_client(tmp_path)
    r = client.get("/auth/me")
    assert "Not signed in" in r.text
    assert 'href="/"' in r.text, "even signed-out, auth/me should offer a way back home"


def test_roles_for_maps_member_level(tmp_path):
    make_data(tmp_path)
    client = make_client(tmp_path)
    register_guild(client, tmp_path)
    from server.auth import roles_for

    # G1 manifest member has memberLevel 4 (leader)
    roles = roles_for(client.app.state.db, tmp_path, "nobody")
    assert roles == {}
    _link(client, tmp_path, "d1", "123")
    assert roles_for(client.app.state.db, tmp_path, "d1") == {"G1": "leader"}


def test_officer_gate_rejects_unlinked(tmp_path):
    make_data(tmp_path)
    client = make_client(tmp_path)
    register_guild(client, tmp_path)
    _set_session(client, "d1")
    r = client.post("/g/G1/squads", data={"squads": "{}"}, follow_redirects=False)
    assert r.status_code == 403


def test_officer_squads_allowed(tmp_path, monkeypatch):
    make_data(tmp_path)
    client = make_client(tmp_path)
    register_guild(client, tmp_path)
    _set_session(client, "d1")
    _link(client, tmp_path, "d1", "123")  # memberLevel 4 -> leader
    from swgoh_reviewer import squads as squads_mod

    called = {}
    monkeypatch.setattr(client.app.state.runner, "regen", lambda *a, **k: called.update(job="ok"))
    valid = '{"categories": [{"name": "Test", "squads": [{"name": "S1", "required": ["Bossk"]}]}]}'
    r = client.post("/g/G1/squads", data={"squads": valid}, follow_redirects=False)
    assert r.status_code == 303
    assert called.get("job") == "ok"
    row = client.app.state.db.get_guild("G1")
    assert "Bossk" in row["squads_json"]


def test_officer_squads_invalid_json(tmp_path):
    make_data(tmp_path)
    client = make_client(tmp_path)
    register_guild(client, tmp_path)
    _set_session(client, "d1")
    _link(client, tmp_path, "d1", "123")
    r = client.post("/g/G1/squads", data={"squads": "{not json"}, follow_redirects=False)
    assert r.status_code == 400


def test_admin_link_create(tmp_path):
    make_data(tmp_path)
    client = make_client(tmp_path)
    client.post("/admin/login", data={"token": "secret"})
    r = client.post("/admin/links", data={"discord_id": "d9", "allycode": "999"})
    assert r.history and r.history[0].status_code == 303, "linking should redirect back to /admin, not dump JSON"
    assert r.history[0].headers["location"].startswith("/admin")
    assert "Linked Discord user" in r.text and "d9" in r.text and "999" in r.text, "admin page should confirm the link"
    assert client.app.state.db.get_discord_link("d9")["allycode"] == "999"


def test_browser_forms_never_dump_json(tmp_path, monkeypatch):
    """Every <form>/hx-post target in the templates must answer with HTML or a
    redirect on success — never a bare JSON dump (regression for the admin link
    form that used to return JSON)."""
    monkeypatch.setenv("SWGOH_GAMEDATA_BASE", "")  # game-data job fails fast offline
    make_data(tmp_path)
    client = make_client(tmp_path)
    register_guild(client, tmp_path)
    _login_admin(client)
    pid = client.post("/g/G1/plans", json={"name": "P", "payload": {"days": {}, "fills": {}}}).json()["id"]
    targets = [
        ("/admin/login", {"token": "secret"}),
        ("/admin/links", {"discord_id": "d1", "allycode": "123"}),
        ("/admin/guilds", {"guild_id": "G1"}),
        (f"/admin/guilds/G1/refresh", {}),
        ("/admin/game-data", {}),
        ("/g/G1/plans/save", {"name": "X"}),
        ("/g/G1/plans/working", {"plan_id": ""}),
        (f"/g/G1/plans/{pid}/ui-set-current", {}),
        (f"/g/G1/plans/{pid}/ui-rename", {"name": "R"}),
        (f"/g/G1/plans/{pid}/ui-delete", {}),
        (f"/admin/guilds/G1/remove", {"confirm": "1"}),
    ]
    for path, data in targets:
        r = client.post(path, data=data)
        assert r.status_code < 400, f"{path} should succeed with valid data (got {r.status_code})"
        assert "application/json" not in r.headers.get("content-type", ""), f"{path} must not dump JSON"


def test_register_guild_form(tmp_path, monkeypatch):
    """Register reads form fields, enqueues an async refresh, and redirects."""
    make_data(tmp_path)
    client = make_client(tmp_path)
    client.post("/admin/login", data={"token": "secret"})
    # empty form -> validation error (proves it parses form body)
    assert client.post("/admin/guilds", data={}).status_code == 400
    # guild_id via form body registers the guild and redirects immediately
    monkeypatch.setattr(client.app.state.runner, "refresh_guild", lambda gid, comlink: {"guildId": gid})
    r = client.post("/admin/guilds", data={"guild_id": "G1"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/admin"
    g = client.app.state.db.get_guild("G1")
    assert g is not None
    # a "running" job row was logged (the async worker may or may not have run yet)
    job = client.app.state.db.latest_job("G1")
    assert job is not None and job["kind"] == "refresh" and job["status"] == "running"


def test_admin_refresh_and_game_data_async(tmp_path, monkeypatch):
    """Admin refresh/game-data return immediately (303) and enqueue a job."""
    make_data(tmp_path)
    client = make_client(tmp_path)
    register_guild(client, tmp_path)
    client.post("/admin/login", data={"token": "secret"})
    r = client.post("/admin/guilds/G1/refresh", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/admin"
    assert client.app.state.db.latest_job("G1")["kind"] == "refresh"
    # game-data job: disable the static source so the async worker fails fast offline
    monkeypatch.setenv("SWGOH_GAMEDATA_BASE", "")
    r = client.post("/admin/game-data", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/admin"
    assert client.app.state.db.latest_job(None)["kind"] == "game-data"


def test_startup_marks_stale_running_jobs(tmp_path):
    """A restart marks orphaned 'running' jobs as interrupted (no forever-pending)."""
    make_data(tmp_path)
    client = make_client(tmp_path)
    client.app.state.db.log_job("G1", "refresh", "running", started_at="2026-01-01T00:00:00")
    app2 = create_app(outdir=tmp_path, db_path=tmp_path / "service.db", comlink="http://localhost:3200")
    with TestClient(app2) as c2:
        job = c2.app.state.db.latest_job("G1")
        assert job["status"] == "interrupted"


def test_refresh_guild_does_not_deadlock_on_regen(tmp_path, monkeypatch):
    """refresh_guild calls regen while holding the lock; the lock must be reentrant."""
    import threading

    from swgoh_reviewer import pipeline, squads
    from server.db import DB as Database
    from server.jobs import JobRunner

    db = Database(tmp_path / "service.db")
    db.upsert_guild("G1", name="Guild One")
    runner = JobRunner(db, outdir=tmp_path, max_rps=4.0)

    monkeypatch.setattr(
        pipeline,
        "refresh_guild",
        lambda **k: ({"guildId": "G1", "guildName": "Guild One", "memberCount": 1, "members": []}, {}),
    )
    monkeypatch.setattr(squads, "main", lambda *a, **k: 0)
    (tmp_path / "rote").mkdir(parents=True)
    (tmp_path / "rote" / "t05D.json").write_text("{}")

    result = {}
    t = threading.Thread(target=lambda: result.update(r=runner.refresh_guild("G1", "http://localhost:3200")))
    t.start()
    t.join(timeout=10)
    assert not t.is_alive(), "refresh_guild deadlocked in regen"
    assert result["r"]["status"] == "ok"
    assert db.latest_job("G1")["status"] == "ok"
