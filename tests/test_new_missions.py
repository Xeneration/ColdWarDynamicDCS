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


def test_armed_recon_hunts_a_moving_supply_column():
    c, f = _find(Task.ARMED_RECON)
    tgt = c.objectives[f.target_id]
    assert tgt.kind.value == "convoy" and tgt.side is Side.RED and f.loadout == "CAS_ROCKEYE"
    rec = builder.build(c, f.id, Path(tempfile.mkdtemp()), start="air")
    _, groups = _groups(rec)
    column = groups[f"{tgt.id} {tgt.name}"]
    assert len(column.points) >= 2                               # on the move toward the front
    assert any(l.startswith("ARMED RECON:") for l in rec.briefing)


def test_cas_missions_get_a_fac():
    c, f = _find(Task.CAS)
    rec = builder.build(c, f.id, Path(tempfile.mkdtemp()), start="air")
    m, _ = _groups(rec)
    cfg = str(next(a.dict() for t in m.triggerrules.triggers for a in t.actions if "CWDC_CONFIG" in str(a.dict())))
    assert f'callsign = "{builder.FAC_CALLSIGN}"' in cfg and "menu = true" in cfg
    assert any(l.startswith("FAC:") for l in rec.briefing)


def _special_day(name, seed=2):
    """A war campaign on a daytime turn of the day whose player special is `name`."""
    from cwdc.engine import planner
    c = new_campaign("Var", seed=seed)
    for _ in range(4 * 6):
        if planner.player_special(c) == name and planner.period(c) in ("dawn", "day"):
            from cwdc.model import WeatherState
            c.weather = WeatherState(kind="clear", cloud_base_m=0)     # no weather scrubs
            c.counters = {k: v for k, v in c.counters.items()
                          if not k.startswith(("special:", "oca:", "recon:"))}
            planner.plan_turn(c)
            return c
        turn.end_turn(c)
    pytest.skip(f"no {name} day reached")


def test_player_special_rotates_daily():
    from cwdc.engine import planner
    c = new_campaign("Var", seed=1)
    seen = []
    for _ in range(16):
        seen.append(planner.player_special(c))
        turn.end_turn(c)
    assert seen[0] == "oca" and set(seen) == set(planner.SPECIALS)


@pytest.mark.parametrize("special,label,loadout", [("iron_hand", "Iron Hand", "SEAD_SHRIKE"),
                                                   ("precision", "Precision strike", "PRECISION_GBU8")])
def test_special_of_the_day_goes_to_the_player(special, label, loadout):
    for seed in range(2, 8):
        c = _special_day(special, seed)
        pkg = next((p for p in c.packages.values() if p.description.startswith(label)), None)
        if pkg is None:
            continue
        main = c.flights[pkg.flight_ids[0]]
        assert main.player_eligible and main.loadout == loadout
        rec = builder.build(c, main.id, Path(tempfile.mkdtemp()), start="air")
        key = "IRON HAND:" if special == "iron_hand" else "PRECISION STRIKE:"
        assert any(l.startswith(key) for l in rec.briefing)
        return
    pytest.skip(f"no {label} package in the sample")


def test_runway_attack_craters_the_field():
    from cwdc.engine import airbases
    from cwdc.model import MissionRecord
    for seed in range(2, 8):
        c = _special_day("runway", seed)
        f = next((x for x in c.flights.values() if x.loadout == airbases.RUNWAY_LOADOUT), None)
        if f is None:
            continue
        assert f.player_eligible
        rec = builder.build(c, f.id, Path(tempfile.mkdtemp()), start="air")
        assert any(l.startswith("RUNWAY ATTACK:") for l in rec.briefing)
        m, _ = _groups(rec)
        cfg = str(next(a.dict() for t in m.triggerrules.triggers for a in t.actions if "CWDC_CONFIG" in str(a.dict())))
        assert "impacts" in cfg
        ab = c.airbases[c.objectives[f.target_id].airbase_id]
        before = ab.damage
        near = {"event": "CWDC_IMPACT", "pos": {"x": ab.pos.x + 300, "z": ab.pos.z}}
        far = {"event": "CWDC_IMPACT", "pos": {"x": ab.pos.x + 5000, "z": ab.pos.z}}
        mission = MissionRecord(mission_id="m", turn=c.turn, player_flight_id=f.id, miz_path="x", generated_at=0)
        lines = airbases.dcs_craters(c, mission, {"events": [near, near, far]})
        assert abs(ab.damage - before - 2 * airbases.CRATER_DAMAGE) < 1e-9 and "2 bomb hit" in lines[0]
        return
    pytest.skip("no runway attack in the sample")
