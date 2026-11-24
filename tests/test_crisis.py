import json

from cwdc.engine import crisis, outcome, planner, turn
from cwdc.engine.factory import new_campaign
from cwdc.model import Campaign, Task


def _crisis(seed=3):
    return new_campaign("Crisis", seed=seed, crisis=True)


def test_campaign_opens_in_the_crisis():
    c = _crisis()
    assert c.phase == "crisis" and not c.at_war and c.war_turn == 0
    assert c.time.strftime("%d %b") == "11 Aug"
    assert 9 <= c.crisis_deadline_turn <= 17
    assert outcome.war_day(c) == 0
    assert c.flights and all(f.task is Task.CAP for f in c.flights.values())   # no strikes yet
    assert "Crisis" in c.news[-1].text


def test_war_always_starts_by_the_deadline_and_the_clock_starts_then():
    for seed in range(6):
        c = _crisis(seed)
        offsets = {s.id: s.offset_km for s in c.sectors.values()}
        arrivals = {f.id: f.arrival_turn for f in c.formations.values()}
        deadline = c.crisis_deadline_turn
        while not c.at_war:
            assert c.outcome is None
            assert {s.id: s.offset_km for s in c.sectors.values()} == offsets   # front frozen
            turn.end_turn(c)
        assert c.turn <= deadline and c.war_start_turn == c.turn
        assert c.war_turn == 1 and outcome.war_day(c) == 1
        shift = c.war_start_turn - 1
        assert all(f.arrival_turn == arrivals[f.id] + shift for f in c.formations.values())
        assert "WAR" in c.news[-1].text
        if planner.period(c) != "night":
            assert any(f.target_id for f in c.flights.values())       # strikes planned now


def test_nobody_is_lost_during_the_crisis():
    c = _crisis()
    lost = sum(1 for a in c.airframes.values() if a.status.value == "destroyed")
    for _ in range(4):
        turn.end_turn(c)
    assert sum(1 for a in c.airframes.values() if a.status.value == "destroyed") == lost


def test_tension_and_incidents_bring_the_war_forward():
    c = _crisis()
    crisis.raise_tension(c, 100, "Shots fired over the border.")
    turn.end_turn(c)
    assert c.at_war and c.war_start_turn == 2
    c = _crisis()
    crisis.bring_forward(c, 2)
    assert c.crisis_deadline_turn == 3


def test_old_saves_load_at_war():
    c = new_campaign("Old", seed=2)
    d = json.loads(c.model_dump_json())
    for k in ("phase", "tension", "war_start_turn", "crisis_deadline_turn"):
        d.pop(k)
    d["schema_version"] = 1
    old = Campaign.model_validate(d)
    assert old.at_war and old.war_turn == old.turn
