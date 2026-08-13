"""Unit tests for the streaming pipeline (no comlink, no live data)."""

import json

from swgoh_reviewer import pipeline


def make_caches():
    return {
        "localization": {"CAT_X": "Sith"},
        "units": {"UNIT_X": {"combatType": 1, "categories": ["cat1"], "leader": True}},
        "categories": {"cat1": {"descKey": "CAT_X", "visible": True}},
    }


def make_unit():
    return {
        "baseId": "UNIT_X",
        "name": "Unit X",
        "currentTier": 13,
        "relic": {"currentTier": 5},
        "currentRarity": 7,
        "skill": [{"id": "basicskill_UNIT_X", "tier": 8}],
    }


def test_build_unit_drops_abilities():
    u = pipeline.build_unit(make_unit(), make_caches())
    assert "abilities" not in u
    assert "skill" not in u


def test_build_unit_keeps_identity_fields():
    u = pipeline.build_unit(make_unit(), make_caches())
    assert u["name"] == "Unit X"
    assert u["baseId"] == "UNIT_X"
    assert u["combatType"] == "character"
    assert u["gearLevel"] == 13
    assert u["relicLevel"] == 3  # relic.currentTier 5 -> R3
    assert u["rarity"] == 7
    assert u["leader"] is True
    assert u["factions"] == ["Sith"]


def test_build_unit_ship_factionless():
    caches = {
        "localization": {},
        "units": {"SHIP": {"combatType": 2, "categories": [], "leader": False}},
        "categories": {},
    }
    u = pipeline.build_unit({"baseId": "SHIP", "name": "Ship", "currentRarity": 7}, caches)
    assert u["combatType"] == "ship"
    assert u["factions"] == []


def test_build_summary_member_carries_roles_and_sorts():
    member = {
        "playerId": "p1",
        "playerName": "P",
        "allyCode": 123,
        "memberLevel": 3,
        "galacticPower": 1000,
        "characterGalacticPower": 800,
        "shipGalacticPower": 200,
    }
    player = {
        "name": "P",
        "playerId": "p1",
        "allyCode": 123,
        "rosterUnit": [
            {"baseId": "LOW", "name": "Low", "currentTier": 8, "currentRarity": 6},
            make_unit(),
        ],
    }
    m = pipeline.build_summary_member(member, player, make_caches())
    assert m["memberLevel"] == 3
    assert m["galacticPower"] == 1000
    assert m["allyCode"] == 123
    # sorted by relic desc
    assert m["units"][0]["relicLevel"] >= m["units"][1]["relicLevel"]


def test_write_json_compact_vs_pretty(tmp_path):
    obj = {"a": [1, 2], "b": {"c": "x"}}
    compact = tmp_path / "c.json"
    pipeline.write_json(compact, obj)
    assert compact.read_text() == '{"a":[1,2],"b":{"c":"x"}}'
    pretty = tmp_path / "p.json"
    pipeline.write_json(pretty, obj, pretty=True)
    assert "\n  " in pretty.read_text()


def test_summarize_from_files(tmp_path):
    gid = "G1"
    guild_dir = tmp_path / "guilds"
    guild_dir.mkdir()
    (tmp_path / "game").mkdir()
    for name in ("localization", "units", "categories"):
        (tmp_path / "game" / f"{name}.json").write_text("{}")
    guild_dir.joinpath(f"{gid}.json").write_text(
        json.dumps(
            {
                "guildId": gid,
                "guildName": "G",
                "members": [
                    {
                        "playerId": "p1",
                        "playerName": "P",
                        "allyCode": 123,
                        "memberLevel": 2,
                        "galacticPower": 1000,
                    }
                ],
            }
        )
    )
    tmp_path.joinpath("123.json").write_text(
        json.dumps({"name": "P", "allyCode": 123, "rosterUnit": [{"baseId": "A", "name": "A", "currentTier": 13}]})
    )
    summary = pipeline.summarize_from_files(tmp_path, gid, progress=lambda *a, **k: None)
    assert summary["memberCount"] == 1
    assert summary["members"][0]["units"][0]["baseId"] == "A"
    assert summary["members"][0]["memberLevel"] == 2
    # manifest memberLevel not present should not crash
    guild_dir.joinpath(f"{gid}.json").write_text(
        json.dumps({"guildId": gid, "guildName": "G", "members": [{"playerId": "p1", "playerName": "P", "allyCode": 123}]})
    )
    summary2 = pipeline.summarize_from_files(tmp_path, gid, progress=lambda *a, **k: None)
    assert summary2["members"][0]["memberLevel"] is None
