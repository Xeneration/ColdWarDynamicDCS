import pytest

from cwdc import cli_campaign
from cwdc.engine import ground, outcome, turn
from cwdc.engine.factory import new_campaign
from cwdc.model import AirframeStatus, Side


@pytest.fixture
def camp():
    return new_campaign("Outcome", seed=9)


def _front(c, **offsets):
    for s in c.sectors.values():
        s.offset_km = offsets.get(s.id, 0.0)
    ground.update_front(c)


def _day(c, day, end_of_day=True):
    tpd = outcome.turns_per_day(c)
    c.turn = day * tpd if end_of_day else (day - 1) * tpd + 1


def test_war_goes_on_at_the_start(camp):
    assert outcome.check(camp) is None and camp.outcome is None
    lines = outcome.situation(camp)
    assert any("Nearest Warsaw Pact objective" in l for l in lines)


def test_reaching_the_main_is_a_warsaw_pact_victory(camp):
    _front(camp, rhoen=110)                       # through Wuerzburg and Aschaffenburg
    o = outcome.check(camp)
    assert o.winner is Side.RED and o.decisive and "Main" in o.reason


def test_breakthrough_is_a_warsaw_pact_victory(camp):
    _front(camp, fulda=outcome.rules(camp)["breakthrough_km"])
    o = outcome.check(camp)
    assert o.winner is Side.RED and "breakthrough" in o.reason


def test_nato_air_exhaustion_loses_the_war(camp):
    blue = [a for a in camp.airframes.values() if camp.squadrons[a.squadron_id].side is Side.BLUE]
    for a in blue[: int(len(blue) * 0.8)]:
        a.status = AirframeStatus.DESTROYED
    o = outcome.check(camp)
    assert o.winner is Side.RED and "air power" in o.reason


def test_nato_wins_by_holding_the_border_past_the_hold_period(camp):
    hold = outcome.rules(camp)["nato_hold_days"]
    _front(camp, goettingen=-5)
    _day(camp, hold)
    assert outcome.check(camp) is None            # still inside the hold period
    _day(camp, hold + 1, end_of_day=False)
    o = outcome.check(camp)
    assert o.winner is Side.BLUE and o.decisive


@pytest.mark.parametrize("offsets,winner,title", [
    ({"fulda": 60, "rhoen": 30}, Side.RED, "Warsaw Pact gains"),
    ({"fulda": 40}, None, "stalemate"),
    ({"fulda": 12}, Side.BLUE, "NATO holds"),
])
def test_ceasefire_is_judged_by_territory(camp, offsets, winner, title):
    _front(camp, **offsets)
    _day(camp, outcome.rules(camp)["max_days"])
    o = outcome.check(camp)
    assert o.winner is winner and not o.decisive and title in o.title


def test_end_turn_closes_the_campaign(camp, capsys):
    _front(camp, rhoen=110)
    lines = turn.end_turn(camp)
    assert camp.outcome and any("WARSAW PACT VICTORY" in l for l in lines)
    assert not camp.flights and not camp.packages
    assert any(l.strip().startswith("You:") for l in lines)
    with pytest.raises(ValueError):
        turn.end_turn(camp)
    assert cli_campaign._war_over(camp)
    assert "The war is over" in capsys.readouterr().out
