"""Web-layer tests (FastAPI TestClient, no comlink)."""

import json

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
        json.dumps({"guildId": "G1", "guildName": "Guild One", "memberCount": 1, "members": []})
    )
    g.joinpath("G1.squads.html").write_text("<html>report</html>")
    g.joinpath("G1.calculator.html").write_text("<html>calc</html>")
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
    assert "Not signed in" in client.get("/auth/me").text


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
    assert r.status_code == 200
    assert client.app.state.db.get_discord_link("d9")["allycode"] == "999"


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
    assert r.status_code == 303 and r.headers["location"] == "/admin/g/G1"
    g = client.app.state.db.get_guild("G1")
    assert g is not None
    # a "running" job row was logged (the async worker may or may not have run yet)
    job = client.app.state.db.latest_job("G1")
    assert job is not None and job["kind"] == "refresh" and job["status"] == "running"


def test_admin_refresh_async(tmp_path):
    """Admin refresh/regen return immediately (303) and enqueue a job."""
    make_data(tmp_path)
    client = make_client(tmp_path)
    register_guild(client, tmp_path)
    client.post("/admin/login", data={"token": "secret"})
    r = client.post("/admin/guilds/G1/refresh", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/admin/g/G1"
    assert client.app.state.db.latest_job("G1")["kind"] == "refresh"
    r = client.post("/admin/guilds/G1/regen", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/admin/g/G1"
    assert client.app.state.db.latest_job("G1")["kind"] == "regen"


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

    from swgoh_reviewer import calc, dashboard, pipeline, squads
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
    monkeypatch.setattr(dashboard, "main", lambda *a, **k: 0)
    monkeypatch.setattr(calc, "main", lambda *a, **k: 0)
    (tmp_path / "rote").mkdir(parents=True)
    (tmp_path / "rote" / "t05D.json").write_text("{}")

    result = {}
    t = threading.Thread(target=lambda: result.update(r=runner.refresh_guild("G1", "http://localhost:3200")))
    t.start()
    t.join(timeout=10)
    assert not t.is_alive(), "refresh_guild deadlocked in regen"
    assert result["r"]["status"] == "ok"
    assert db.latest_job("G1")["status"] == "ok"
