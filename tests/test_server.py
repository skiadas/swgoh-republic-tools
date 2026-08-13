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
        "INSERT INTO guilds (id, name, tb_id, enabled, created_at) VALUES ('G1','Guild One','t05D',1,datetime('now'))"
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


def test_admin_requires_token(tmp_path):
    make_data(tmp_path)
    client = make_client(tmp_path)
    assert client.get("/admin").status_code == 401
    assert client.get("/admin", params={"token": "wrong"}).status_code == 401
    assert client.get("/admin", params={"token": "secret"}).status_code == 200


def test_settings_update(tmp_path):
    make_data(tmp_path)
    client = make_client(tmp_path)
    register_guild(client, tmp_path)
    r = client.post("/admin/guilds/G1/settings", params={"token": "secret", "tb_id": "t06D", "enabled": "0"})
    assert r.status_code == 200
    g = client.app.state.db if hasattr(client.app.state, "db") else None
    # re-read via a fresh client connection
    import sqlite3

    conn = sqlite3.connect(tmp_path / "service.db")
    row = conn.execute("SELECT tb_id, enabled FROM guilds WHERE id='G1'").fetchone()
    assert row[0] == "t06D"
    assert row[1] == 0
