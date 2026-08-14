"""Unit tests for the TB doc helpers (no network)."""

from swgoh_reviewer import tb


def test_display_relic_converts_raw_to_game_level():
    # game data stores relic on the raw currentTier scale (+2 offset)
    assert tb._display_relic(7) == 5
    assert tb._display_relic(11) == 9
    assert tb._display_relic(12) == 10
    assert tb._display_relic(1) == 0
    assert tb._display_relic("11") == 9
    assert tb._display_relic(None) is None


def test_op_display_shortens_operation_and_mission():
    assert tb._op_display("Coruscant Operation") == "Coruscant Op"
    assert tb._op_display("Mandalore Mission") == "Mandalore Op"
    assert tb._op_display("Death Star Operation") == "Death Star Op"


def test_fmt_reward_compacts_points():
    assert tb._fmt_reward(10000000) == "10M"
    assert tb._fmt_reward(13200000) == "13.2M"
    assert tb._fmt_reward(18480000) == "18.5M"
    assert tb._fmt_reward(None) is None


class _Resolver:
    def unit_name(self, base_id):
        return base_id


def test_build_op_numbers_platoons_by_position():
    rop = {
        "nameKey": "Coruscant Operation",
        "squads": [
            {"id": "tb3-platoon-6", "points": 10000000, "units": [{"baseId": "A", "unitRelicTier": 7, "nameKey": "A Name"}]},
            {"id": "tb3-platoon-5", "points": 10000000, "units": [{"baseId": "B", "unitRelicTier": 7}]},
        ],
    }
    op = tb.build_op(rop, _Resolver())
    assert op["name"] == "Coruscant Op"
    assert op["relicRequirement"] == 5  # raw 7 -> R5
    assert [p["platoon"] for p in op["platoons"]] == [1, 2]
    assert op["platoons"][0]["units"][0] == {"baseId": "A", "name": "A Name"}
    assert op["platoons"][1]["units"][0] == {"baseId": "B", "name": "B"}
    assert op["platoons"][0]["reward"] == "10M"


def test_build_op_relic_requirement_uses_max_tier():
    rop = {
        "nameKey": "Scarif Operation",
        "squads": [
            {"id": "tb3-platoon-6", "points": 33300000, "units": [{"baseId": "A", "unitRelicTier": 11}]},
            {"id": "tb3-platoon-5", "points": 33300000, "units": [{"baseId": "B", "unitRelicTier": 10}]},
        ],
    }
    op = tb.build_op(rop, _Resolver())
    assert op["relicRequirement"] == 9  # max raw 11 -> R9
