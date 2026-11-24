import tempfile
from pathlib import Path

from cwdc.engine import planner, turn
from cwdc.engine.factory import new_campaign
from cwdc.missiongen import builder
from cwdc.model import Side, WeatherState


def _blue_oca(c):
    return [p for p in c.packages.values() if p.side is Side.BLUE and "OCA" in p.description]


def _day_war(seed):
    c = new_campaign("OCA", seed=seed)
    c.weather = WeatherState(kind="clear", cloud_base_m=0)
    while planner.period(c) == "night":
        turn.end_turn(c)
    c.weather = WeatherState(kind="clear", cloud_base_m=0)
    c.counters = {k: v for k, v in c.counters.items() if not k.startswith("oca:")}
    planner.plan_turn(c)
    return c


def test_nato_flies_a_counter_air_package_a_day_in_the_opening_days():
    hits = 0
    for seed in range(6):
        c = _day_war(seed)
        oca = _blue_oca(c)
        if oca:
            hits += 1
            main = c.flights[oca[0].flight_ids[0]]
            assert c.objectives[main.target_id].kind.value == "airbase"
            # the player's squadron flies it on its OCA / runway days, the 23rd TFS otherwise
            assert main.player_eligible == (planner.player_special(c) in ("oca", "runway"))
            if main.player_eligible:
                rec = builder.build(c, main.id, Path(tempfile.mkdtemp()), start="air")
                assert any("airfield attack (OCA)" in l for l in rec.briefing)
            planner.plan_turn(c)                                 # same day: no second one
            assert not _blue_oca(c) or c.counters[f"oca:blue:{c.time:%Y%m%d}"] >= 1
    assert hits >= 4


def test_oca_justification_after_the_opening_days():
    from cwdc.engine import outcome
    c = _day_war(2)
    c.turn = c.war_start_turn + planner.OCA_OPENING_TURNS + 4     # past the opening days
    for ab in c.airbases.values():
        ab.hit_turn = -1
    slipping = outcome.air_strength(c, Side.BLUE) < outcome.air_strength(c, Side.RED)
    assert planner.oca_justified(c, Side.BLUE) is slipping
    next(ab for ab in c.airbases.values() if ab.side is Side.BLUE).hit_turn = c.turn - 1
    assert planner.oca_justified(c, Side.BLUE)                    # a raid on one of ours
