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
    assert c.flights and not any(f.target_id for f in c.flights.values())   # no strikes yet
    assert {f.task for f in c.flights.values()} <= {Task.CAP, Task.QRA, Task.RECON}
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

# ----------------------------------------------------------------------------- Step 2: air ops
import logging
from pathlib import Path

import dcs

from cwdc.engine.crews import GROUNDED
from cwdc.missiongen import builder
from cwdc.model import MissionRecord, Side


def _day_crisis():
    c = _crisis(3)                     # turn 1 is dawn: a probe and a QRA are planned
    return c, next(f for f in c.flights.values() if f.task is Task.QRA)


def test_crisis_plans_probes_with_qra_against_them():
    c, q = _day_crisis()
    probe = c.flights[q.intercepts]
    assert probe.task is Task.RECON and probe.side is Side.RED
    assert q.player_eligible and q.area.dist(probe.area) < 1
    from cwdc import theater as T
    assert T.side_at_pos(c.front_line, probe.area) is Side.BLUE        # just over the border


def test_simulated_probes_move_tension():
    c, q = _day_crisis()
    t0 = c.tension
    lines = crisis.air_ops(c, [c.flights[q.intercepts]])               # no QRA, no CAP: unopposed
    assert c.tension == t0 + crisis.UNOPPOSED and "unopposed" in lines[0]


def _record(c, q):
    return MissionRecord(mission_id="m", turn=c.turn, player_flight_id=q.id, miz_path="x",
                         generated_at=0, spawned_flights=[q.id, q.intercepts],
                         manifest={f"{q.id}-1 {q.callsign}": {"pilot": q.pilot_ids[0]}})


def _ev(event, side, unit=None, **kw):
    return {"event": event, "initiator": {"coalition": side, "name": unit}, **kw}


def test_firing_first_is_a_breach_and_a_kill_brings_the_war_forward():
    c, q = _day_crisis()
    probe = c.flights[q.intercepts]
    unit = f"{q.id}-1 {q.callsign}"
    data = {"events": [_ev("S_EVENT_SHOT", 2, unit, roe_violation=True),
                       _ev("S_EVENT_KILL", 2, unit, target={"coalition": 1})]}
    t0 = c.tension
    lines = crisis.dcs_incidents(c, _record(c, q), data)
    assert c.tension >= t0 + crisis.INCIDENT_TENSION - crisis.INTERCEPTED
    assert c.pilots[q.pilot_ids[0]].fatigue > GROUNDED                  # grounded pending inquiry
    assert c.crisis_deadline_turn <= c.turn + crisis.KILL_DEADLINE_TURNS
    assert any("ROE BREACH" in l for l in lines) and c.counters["roe_breaches"] == 1
    assert any(probe.callsign in l and "without being intercepted" in l for l in lines)


def test_hostile_act_and_intercept_from_dcs():
    c, q = _day_crisis()
    probe = c.flights[q.intercepts]
    data = {"events": [_ev("S_EVENT_SHOT", 1, "MiG"),
                       {"event": "CWDC_INTERCEPT", "target": {"group": f"{probe.id} {probe.callsign}"}}]}
    t0 = c.tension
    lines = crisis.dcs_incidents(c, _record(c, q), data)
    assert c.tension == t0 + crisis.HOSTILE_ACT + crisis.INTERCEPTED
    assert any("HOSTILE ACT" in l for l in lines) and not c.counters.get("roe_breaches")


def test_qra_mission_is_weapons_tight_with_intercept_watch(tmp_path):
    c, q = _day_crisis()
    rec = builder.build(c, q.id, tmp_path, start="air", bot=True)
    assert q.intercepts in rec.spawned_flights
    logging.disable(logging.CRITICAL)
    m = dcs.Mission()
    m.load_file(rec.miz_path)
    probe = c.flights[q.intercepts]
    cfg = next(a for t in m.triggerrules.triggers for a in t.actions if "CWDC_CONFIG" in str(a.dict()))
    text = str(cfg.dict())
    assert "weapons_tight" in text and f"{probe.id} {probe.callsign}" in text and "intercept" in text
    assert any("WEAPONS TIGHT" in l for l in rec.briefing)


def test_war_opens_with_warsaw_pact_airfield_raids():
    c = _crisis(4)
    crisis.raise_tension(c, 100)
    turn.end_turn(c)
    assert c.at_war and c.war_turn == 1
    red_oca = [p for p in c.packages.values() if p.side is Side.RED and "OCA" in p.description]
    assert red_oca, [p.description for p in c.packages.values() if p.side is Side.RED]
    assert all(c.objectives[p.target_id].kind.value == "airbase" for p in red_oca)
    blue = next((f for f in c.flights.values() if f.player_eligible), None)
    if blue is not None:
        import tempfile
        rec = builder.build(c, blue.id, Path(tempfile.mkdtemp()), start="air")
        assert any(l.startswith("WAR:") for l in rec.briefing)
