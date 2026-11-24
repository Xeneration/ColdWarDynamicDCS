"""Mission variety (2026-11-24): scramble intercepts, sweeps, RESCAP, armed recon, FAC-marked
CAS, Iron Hand SEAD, runway attack and laser-guided strikes."""
import logging
import tempfile
from collections import Counter
from pathlib import Path

import dcs
import pytest
from dcs import task as dtask

from cwdc.engine import resolver, turn
from cwdc.engine.factory import new_campaign
from cwdc.missiongen import builder
from cwdc.model import Side, Task


def _find(task: Task, seeds=range(6), turns=8):
    """First player-eligible flight of `task` in a few campaigns (campaign, flight)."""
    for seed in seeds:
        c = new_campaign("Var", seed=seed)
        for _ in range(turns):
            f = next((x for x in c.flights.values() if x.player_eligible and x.task is task), None)
            if f is not None:
                return c, f
            turn.end_turn(c)
    pytest.skip(f"no {task.value} flight planned in the sample")


def _groups(rec):
    logging.disable(logging.CRITICAL)
    m = dcs.Mission()
    m.load_file(rec.miz_path)
    return m, {g.name: g for co in m.coalition.values() for cc in co.countries.values()
               for g in cc.plane_group + cc.vehicle_group + cc.helicopter_group}


def test_scrambles_and_sweeps_are_planned_for_the_player():
    seen = Counter()
    for seed in range(4):
        c = new_campaign("Var", seed=seed)
        for _ in range(8):
            seen.update(f.task for f in c.flights.values() if f.player_eligible)
            turn.end_turn(c)
    assert seen[Task.INTERCEPT] and seen[Task.SWEEP]
    assert seen[Task.CAS] + seen[Task.BAI] > 0                  # the battlefield is still offered


def test_scramble_mission_brings_the_raid_and_meets_it():
    c, f = _find(Task.INTERCEPT)
    raid = c.flights[f.intercepts]
    rec = builder.build(c, f.id, Path(tempfile.mkdtemp()), start="runway")
    assert raid.id in rec.spawned_flights and f.loadout == "QRA"
    assert any(l.startswith("SCRAMBLE:") and raid.callsign in l for l in rec.briefing)
    bot = builder.build(c, f.id, Path(tempfile.mkdtemp()), start="air", bot=True)
    _, groups = _groups(bot)
    wps = groups[f"{f.id} {f.callsign}"].points
    assert any(isinstance(t, dtask.EngageTargetsInZone) for p in wps for t in p.tasks)
    assert c.flights[f.intercepts].side is Side.RED


def test_sweep_mission_hunts_the_patrol_and_counts_as_a_fighter_in_the_sim():
    c, f = _find(Task.SWEEP)
    rec = builder.build(c, f.id, Path(tempfile.mkdtemp()), start="air")
    reds = [c.flights[x] for x in rec.spawned_flights if c.flights[x].side is Side.RED]
    assert any(x.task is Task.CAP and x.area.dist(f.area) <= builder.SWEEP_TARGET_RADIUS for x in reds)
    assert any(l.startswith("SWEEP:") for l in rec.briefing)
    assert Task.SWEEP in resolver.FIGHTER_TASKS


def _evader(seed=3):
    from cwdc import theater as T
    from cwdc.model import PilotStatus, Pos
    c = new_campaign("Var", seed=seed)
    p = next(x for x in c.pilots.values() if x.side is Side.BLUE and not x.is_player
             and x.status is PilotStatus.ACTIVE and x.id not in {i for f in c.flights.values() for i in f.pilot_ids})
    p.status, p.down_pos, p.down_turn = PilotStatus.MIA, T.latlon_to_pos(50.75, 10.25), c.turn   # in the GDR
    from cwdc.engine import planner
    planner.plan_turn(c)
    return c, p


def test_downed_crew_gets_a_rescap_with_helicopter_and_search_party():
    from cwdc.engine import planner
    c, p = _evader()
    if planner.period(c) == "night":
        pytest.skip("night turn")
    f = next(x for x in c.flights.values() if x.task is Task.RESCAP)
    assert f.rescue_of == p.id and f.player_eligible and f.loadout == "CAS_ROCKEYE"
    rec = builder.build(c, f.id, Path(tempfile.mkdtemp()), start="air")
    m, groups = _groups(rec)
    assert "Dustoff 1" in groups and "Search party" in groups
    assert len(groups["Search party"].units) == 3
    assert any(l.startswith("RESCAP:") and p.name in l for l in rec.briefing)
    cfg = str(next(a.dict() for t in m.triggerrules.triggers for a in t.actions if "CWDC_CONFIG" in str(a.dict())))
    assert 'helo = "Dustoff 1"' in cfg


def test_rescue_outcomes_pickup_capture_and_deadline():
    from cwdc.engine import rescue
    from cwdc.model import MissionRecord, PilotStatus
    c, p = _evader()
    f = next((x for x in c.flights.values() if x.task is Task.RESCAP), None)
    if f is None:
        pytest.skip("night turn")
    mission = MissionRecord(mission_id="m", turn=c.turn, player_flight_id=f.id, miz_path="x", generated_at=0)
    assert rescue.dcs_pickups(c, mission, {"events": []}) and p.status is PilotStatus.MIA
    lines = rescue.dcs_pickups(c, mission, {"events": [{"event": "CWDC_PICKUP"}]})
    assert "RESCUED" in lines[0] and p.status in (PilotStatus.ACTIVE, PilotStatus.WIA) and p.down_pos is None
    c, p = _evader(4)
    c.turn += rescue.EVADE_TURNS
    rescue.rescue_turn(c, [])
    assert p.status is PilotStatus.POW                       # evaded too long: caught
