"""Unit tests for the static gamedata source (no network)."""

import json

import brotli
import pytest

from swgoh_reviewer import gamecache, pipeline
from swgoh_reviewer.static_gamedata import StaticGameData


def make_files(version="1.0.0:AAAA", units_data=None, locale_entries=None):
    units_data = units_data if units_data is not None else [
        {
            "baseId": "UNIT_X",
            "combatType": 1,
            "categoryId": ["cat1", "alignment_light"],
            "nameKey": "UNIT_X_NAME",
            "leaderAbilityRef": {"abilityId": "lead_x"},
        },
        {"baseId": "SHIP_X", "combatType": 2, "categoryId": []},
    ]
    locale_entries = locale_entries if locale_entries is not None else {"CAT1_DESC": "Sith", "UNIT_X_NAME": "Unit X"}
    return {
        "allVersions.json": json.dumps(
            {"gameVersion": version, "localeVersion": "LOC_A", "assetVersion": 100}
        ).encode(),
        "units.json.br": brotli.compress(
            json.dumps({"version": version, "data": units_data}).encode()
        ),
        "category.json": json.dumps(
            {
                "version": version,
                "data": [
                    {"id": "cat1", "descKey": "CAT1_DESC", "visible": True},
                    {"id": "hidden_cat", "descKey": "H_DESC", "visible": False},
                ],
            }
        ).encode(),
        "Loc_ENG_US.txt.json.br": brotli.compress(
            json.dumps({"version": "LOC_A", "data": locale_entries}).encode()
        ),
    }


def make_static(tmp_path, files, monkeypatch):
    static = StaticGameData(base_url="https://example.invalid", outdir=tmp_path)
    monkeypatch.setattr(StaticGameData, "_fetch", lambda self, fn: files[fn])
    return static


def test_first_run_downloads_and_serves_collections(tmp_path, monkeypatch):
    files = make_files()
    static = make_static(tmp_path, files, monkeypatch)

    versions = static.ensure_raw(refresh=True)
    assert versions["gameVersion"] == "1.0.0:AAAA"

    units = static.get_game_data(include_pve_units=False, items="units")
    assert [u["baseId"] for u in units["units"]] == ["UNIT_X", "SHIP_X"]

    cats = static.get_game_data(items="category")
    assert cats["category"][0]["id"] == "cat1"
    assert cats["category"][1]["visible"] is False

    loc = static.get_localization(locale="ENG_US", unzip=True)
    assert loc["Loc_ENG_US.txt"] == "CAT1_DESC|Sith\nUNIT_X_NAME|Unit X"

    # raw files are cached on disk (the .br ones stay compressed)
    raw_dir = tmp_path / "game" / "static"
    assert (raw_dir / "units.json.br").exists()
    assert (raw_dir / "category.json").exists()
    assert (raw_dir / "Loc_ENG_US.txt.json.br").exists()
    assert (raw_dir / "all-versions.json").exists()


def test_offline_reuse_after_first_fetch(tmp_path, monkeypatch):
    files = make_files()
    static = make_static(tmp_path, files, monkeypatch)
    static.ensure_raw(refresh=True)

    # no network afterwards: _fetch raises, but refresh=False must still work
    monkeypatch.setattr(StaticGameData, "_fetch", lambda self, fn: (_ for _ in ()).throw(OSError("offline")))
    static2 = StaticGameData(base_url="https://example.invalid", outdir=tmp_path)
    versions = static2.ensure_raw(refresh=False)
    assert versions["gameVersion"] == "1.0.0:AAAA"
    assert [u["baseId"] for u in static2.get_game_data(items="units")["units"]] == ["UNIT_X", "SHIP_X"]


def test_version_change_redownloads_on_refresh(tmp_path, monkeypatch):
    files = make_files(version="1.0.0:AAAA")
    static = make_static(tmp_path, files, monkeypatch)
    static.ensure_raw(refresh=True)

    # game update: version + unit data change together
    new_units = [{"baseId": "NEW_UNIT", "combatType": 1, "categoryId": ["cat1"], "leaderAbilityRef": None}]
    files.update(make_files(version="1.0.0:BBBB", units_data=new_units))
    static.ensure_raw(refresh=True)

    units = static.get_game_data(items="units")["units"]
    assert [u["baseId"] for u in units] == ["NEW_UNIT"]

    # unchanged version -> no re-download (served from cache)
    files["allVersions.json"] = json.dumps(
        {"gameVersion": "1.0.0:BBBB", "localeVersion": "LOC_A", "assetVersion": 100}
    ).encode()
    static2 = StaticGameData(base_url="https://example.invalid", outdir=tmp_path)
    static2.ensure_raw(refresh=True)
    assert [u["baseId"] for u in static2.get_game_data(items="units")["units"]] == ["NEW_UNIT"]


def test_unknown_items_return_empty(tmp_path, monkeypatch):
    files = make_files()
    static = make_static(tmp_path, files, monkeypatch)
    assert static.get_game_data(items="campaign") == {}


def test_ensure_caches_builds_standard_cache_files(tmp_path, monkeypatch):
    files = make_files()
    static = make_static(tmp_path, files, monkeypatch)

    caches = gamecache.ensure_caches(static, tmp_path, refresh=True)
    assert set(caches) == {"localization", "units", "categories"}
    assert caches["localization"]["CAT1_DESC"] == "Sith"
    assert caches["units"]["UNIT_X"]["categories"] == ["cat1", "alignment_light"]
    assert caches["units"]["UNIT_X"]["combatType"] == 1
    assert caches["units"]["UNIT_X"]["leader"] is True
    assert caches["units"]["UNIT_X"]["name"] == "Unit X"
    assert caches["units"]["UNIT_X"]["factions"] == ["Sith"]
    assert caches["units"]["SHIP_X"]["factions"] == []
    assert caches["categories"]["cat1"] == {"descKey": "CAT1_DESC", "visible": True}

    # small derived faction-name list for squads.py
    factions = json.loads((tmp_path / "game" / "factions.json").read_text())
    assert factions == ["Sith"]

    # the summary builder needs only the units projection
    u = pipeline.build_unit(
        {"baseId": "UNIT_X", "currentTier": 13, "currentRarity": 7}, {"units": caches["units"]}
    )
    assert u["name"] == "Unit X"
    assert u["combatType"] == "character"
    assert u["factions"] == ["Sith"]


def test_ensure_caches_units_only_fast_path(tmp_path, monkeypatch):
    files = make_files()
    static = make_static(tmp_path, files, monkeypatch)
    gamecache.ensure_caches(static, tmp_path, refresh=True)

    # offline, requesting only the units projection must not need the source
    caches = gamecache.ensure_caches(None, tmp_path, refresh=False, names=("units",))
    assert set(caches) == {"units"}
    assert caches["units"]["UNIT_X"]["name"] == "Unit X"
    assert caches["units"]["UNIT_X"]["factions"] == ["Sith"]


def test_stale_units_cache_is_rebuilt(tmp_path, monkeypatch):
    files = make_files()
    static = make_static(tmp_path, files, monkeypatch)
    gamecache.ensure_caches(static, tmp_path, refresh=True)

    # simulate a pre-projection units cache (the box's old layout after an app update)
    old = json.loads((tmp_path / "game" / "units.json").read_text())
    old = {k: {kk: vv for kk, vv in v.items() if kk not in ("name", "factions")} for k, v in old.items()}
    (tmp_path / "game" / "units.json").write_text(json.dumps(old))

    # offline: refuse to silently serve stale (factionless) data
    with pytest.raises(RuntimeError):
        gamecache.ensure_caches(None, tmp_path, refresh=False, names=("units",))

    # with a source, the stale cache is rebuilt in place
    caches = gamecache.ensure_caches(static, tmp_path, refresh=False, names=("units",))
    assert caches["units"]["UNIT_X"]["name"] == "Unit X"
    assert caches["units"]["UNIT_X"]["factions"] == ["Sith"]
    repaired = json.loads((tmp_path / "game" / "units.json").read_text())
    assert "factions" in repaired["UNIT_X"]


def test_load_known_uses_factions_json(tmp_path):
    from swgoh_reviewer import squads

    (tmp_path / "game").mkdir()
    (tmp_path / "names.json").write_text(json.dumps({"DARTHVADER": "Darth Vader"}))
    (tmp_path / "game" / "factions.json").write_text(json.dumps(["Sith", "Empire"]))
    name_map, tags = squads.load_known(tmp_path)
    assert name_map == {"darth vader": "DARTHVADER"}
    assert tags == {"sith", "empire"}


def test_load_known_falls_back_without_factions_json(tmp_path):
    from swgoh_reviewer import squads

    (tmp_path / "game").mkdir()
    (tmp_path / "game" / "categories.json").write_text(json.dumps({"c1": {"descKey": "CAT_X", "visible": True}}))
    (tmp_path / "game" / "localization.json").write_text(json.dumps({"CAT_X": "Sith"}))
    _, tags = squads.load_known(tmp_path)
    assert tags == {"sith"}


def test_ensure_caches_loads_from_disk_offline(tmp_path, monkeypatch):
    files = make_files()
    static = make_static(tmp_path, files, monkeypatch)
    gamecache.ensure_caches(static, tmp_path, refresh=True)

    monkeypatch.setattr(StaticGameData, "_fetch", lambda self, fn: (_ for _ in ()).throw(OSError("offline")))
    caches = gamecache.ensure_caches(None, tmp_path, refresh=False)
    assert caches["units"]["UNIT_X"]["combatType"] == 1
