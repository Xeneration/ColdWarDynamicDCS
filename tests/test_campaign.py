import json
import time

import dcs
import pytest

from cwdc import store
from cwdc import theater as T
from cwdc.engine import planner, resolver, turn
from cwdc.engine.factory import new_campaign
from cwdc.ingest.flown import results_from_dcs
from cwdc.missiongen import builder
from cwdc.model import AirframeStatus, FlightStatus, PilotStatus, Side, Task


@pytest.fixture
def camp():
    return new_campaign("Test Campaign", seed=7, player_name="Capt Test")


def _flight(c, task):
    return next(f for f in c.flights.values() if f.player_eligible and f.task is task)


def test_new_campaign(camp):
    c = camp
    assert c.player_pilot().name == "Capt Test"
    assert {f.task for f in c.flights.values() if f.player_eligible} >= {Task.CAP, Task.STRIKE}
    assert any(f.side is Side.RED for f in c.flights.values())
    # all airbases and objectives sit on the right side of the front
    for o in c.objectives.values():
        assert T.side_at_pos(c.front_line, o.pos) is o.side, o.id
    for ab in c.airbases.values():
        assert T.side_at_pos(c.front_line, ab.pos) is ab.side, ab.id


def test_resolver_is_deterministic_and_consistent(camp):
    a = resolver.resolve(camp, list(camp.flights.values()))
    b = resolver.resolve(new_campaign("Test Campaign", seed=7), list(camp.flights.values()))
    assert [r.model_dump() for r in a] == [r.model_dump() for r in b]
    for _ in range(6):     # several turns: every air kill must match a loss on the other side
        res = resolver.resolve(camp, list(camp.flights.values()))
        lost = {aid for r in res for aid in r.airframes_lost}
        for r in res:
            for k in r.kills:
                if k.air:
                    assert k.target in lost
        turn.end_turn(camp)


@pytest.mark.parametrize("task", [Task.CAP, Task.STRIKE])
def test_build_mission(camp, task, tmp_path):
    f = _flight(camp, task)
    planner.assign_player(camp, f.id)
    rec = builder.build(camp, f.id, tmp_path)
    m = dcs.Mission()
    m.load_file(rec.miz_path)
    player = m.find_group(f"{f.id} {f.callsign}")
    assert player.units[0].skill == dcs.unit.Skill.Player
    assert player.units[0].type == "F-4E-45MC"
    names = {u.name for coal in m.coalition.values() for co in coal.countries.values()
             for g in co.plane_group + co.vehicle_group for u in g.units}
    assert set(rec.manifest) <= names
    for coal in m.coalition.values():
        for co in coal.countries.values():
            for g in co.plane_group:
                assert g.units[0].pylons, f"{g.name} has no weapons"
                for p in g.points:
                    if p.name in ("IP", "TGT"):
                        assert p.alt_type == "RADIO", f"{g.name} {p.name} would fly into the hills"
    assert rec.spawned_flights[0] == f.id and len(rec.spawned_flights) > 1
    if task is Task.STRIKE:
        assert any(e.get("objective") == f.target_id for e in rec.manifest.values())


def _unit(name, typ, coal=2, x=0.0, z=0.0, player=None, cat=1):
    return {"name": name, "type": typ, "coalition": coal, "category": cat, "player": player,
            "pos": {"x": x, "z": z}}


def _crafted_result(c, rec):
    """A strike where the player kills 2 guns, the wingman is shot down over the GDR."""
    f = c.flights[rec.player_flight_id]
    lead, wing = f"{f.id}-1 {f.callsign}", f"{f.id}-2 {f.callsign}"
    tgt_units = [n for n, e in rec.manifest.items() if e.get("objective") == f.target_id]
    red_pos = c.objectives[f.target_id].pos
    mig = next(n for n, e in rec.manifest.items()
               if e["kind"] == "airframe" and c.flights[e["flight"]].side is Side.RED)
    ev = [
        {"t": 100, "event": "S_EVENT_TAKEOFF", "initiator": _unit(lead, "F-4E-45MC", player="P")},
        {"t": 900, "event": "S_EVENT_KILL", "initiator": _unit(lead, "F-4E-45MC", player="P"),
         "target": _unit(tgt_units[0], "SAU Gvozdika", 1, cat=1), "weapon": "M117"},
        {"t": 901, "event": "S_EVENT_DEAD", "initiator": _unit(tgt_units[1], "SAU Gvozdika", 1)},
        {"t": 950, "event": "S_EVENT_HIT", "initiator": _unit(mig, "MiG-21Bis", 1),
         "target": _unit(wing, "F-4E-45MC")},
        {"t": 951, "event": "S_EVENT_EJECTION", "initiator": _unit(wing, "F-4E-45MC", x=red_pos.x, z=red_pos.z)},
        {"t": 952, "event": "S_EVENT_UNIT_LOST", "initiator": _unit(wing, "F-4E-45MC")},
        {"t": 1000, "event": "S_EVENT_KILL", "initiator": _unit(lead, "F-4E-45MC", player="P"),
         "target": _unit(mig, "MiG-21Bis", 1), "weapon": "AIM-9J"},
        {"t": 1080, "event": "S_EVENT_LANDING_AFTER_EJECTION",
         "initiator": _unit("pilot_parachute", "", x=red_pos.x + 500, z=red_pos.z), "place": wing},
        {"t": 2000, "event": "S_EVENT_LAND", "initiator": _unit(lead, "F-4E-45MC", player="P")},
    ]
    groups = [{"name": "x", "exists": True, "units": [
        {"name": n, "life": 10, "life0": 10} for n, e in rec.manifest.items()
        if e["kind"] == "objective" and n not in tgt_units[:2]]}]
    return {"source": "hook", "final": True, "ended": True, "run_started": time.time(),
            "mission_id": rec.mission_id, "start": {}, "snapshot": {"groups": groups},
            "events": ev}


def test_ingest_flown_strike_then_end_turn(camp, tmp_path):
    c = camp
    f = _flight(c, Task.STRIKE)
    planner.assign_player(c, f.id)
    rec = builder.build(c, f.id, tmp_path)
    c.pending_mission = rec
    data = _crafted_result(c, rec)
    results, destroyed = results_from_dcs(c, rec, data)
    mine = next(r for r in results if r.flight_id == f.id)
    assert mine.airframes_lost == [f.airframe_ids[1]]
    assert mine.pilot_fates[0].status is PilotStatus.POW
    assert [k.air for k in mine.kills] == [False, True]
    assert len(destroyed) == 2 and all(d.startswith(f.target_id) for d in destroyed)

    wingman = f.pilot_ids[1]
    player = c.player_pilot()
    lines = turn.end_turn(c, results, destroyed)
    assert c.turn == 2 and c.pending_mission is None
    assert c.pilots[wingman].status is PilotStatus.POW
    assert c.airframes[f.airframe_ids[1]].status is AirframeStatus.DESTROYED
    assert player.sorties == 1 and player.air_kills == 1 and player.ground_kills == 1
    assert sum(not u.alive for u in c.objectives[f.target_id].units) >= 2
    assert any("(flown in DCS)" in line for line in lines)
    assert c.flights and all(fl.status is FlightStatus.PLANNED for fl in c.flights.values())


def test_skip_returns_displaced_pilot(camp):
    c = camp
    f = _flight(c, Task.CAP)
    original = list(f.pilot_ids)
    planner.assign_player(c, f.id)
    assert f.pilot_ids[0] == c.player_pilot().id
    planner.release_player(c, f.id)
    assert f.pilot_ids == original and f.status is FlightStatus.PLANNED


def test_store_roundtrip_and_rollback(camp, tmp_path, monkeypatch):
    monkeypatch.setenv("CWDC_CAMPAIGNS_DIR", str(tmp_path))
    store.save(camp, snapshot=True)
    turn.end_turn(camp)
    store.save(camp, snapshot=True)
    assert store.load().turn == 2
    back = store.rollback(camp.id, 1)
    assert back.turn == 1 and store.load().turn == 1
    assert back.model_dump() == store.load(camp.id).model_dump()


def test_cli_end_to_end(tmp_path, monkeypatch, capsys):
    from cwdc.cli import main
    from cwdc import dcs_paths
    monkeypatch.setenv("CWDC_CAMPAIGNS_DIR", str(tmp_path / "campaigns"))
    monkeypatch.setenv("CWDC_DCS_WRITEDIR", str(tmp_path / "dcs"))
    main(["new", "--name", "E2E", "--seed", "3", "--pilot", "Capt Test"])
    main(["ato"])
    c = store.load()
    f = _flight(c, Task.STRIKE)
    main(["fly", f.id])
    c = store.load()
    assert c.pending_mission and (tmp_path / "dcs" / "Missions" / "ColdWarDynamic").is_dir()
    res_dir = dcs_paths.results_dir()
    res_dir.mkdir(parents=True, exist_ok=True)
    (res_dir / f"{c.pending_mission.mission_id}.json").write_text(
        json.dumps(_crafted_result(c, c.pending_mission)))
    main(["ingest"])
    out = capsys.readouterr().out
    assert "flown in DCS" in out and "POW" in out
    main(["skip"])
    main(["status"])
    main(["roster"])
    assert store.load().turn == 3


@pytest.mark.parametrize("start", ["runway", "ramp", "air"])
@pytest.mark.parametrize("task", [Task.CAP, Task.STRIKE])
def test_spawns_deconflicted_and_waves_timed(camp, tmp_path, start, task):
    """Regression (first live flight): two AI flights spawned on the same spot and collided."""
    import itertools
    f = _flight(camp, task)
    planner.assign_player(camp, f.id)
    rec = builder.build(camp, f.id, tmp_path, start=start)
    m = dcs.Mission()
    m.load_file(rec.miz_path)
    air, enemy_caps = [], 0
    for coal in m.coalition.values():
        for co in coal.countries.values():
            for g in co.plane_group:
                assert g.points[0].ETA == g.start_time       # DCS spawns on wp0 ETA
                if g.points[0].type == "Turning Point":
                    air += [(u.position, u.alt) for u in g.units]
                fl = camp.flights[g.name.split()[0]]
                if fl.side is Side.RED and fl.task is Task.CAP:
                    enemy_caps += 1
                if fl.side is Side.BLUE:
                    assert g.units[0].callsign_dict["name"].startswith(
                        camp.squadrons[fl.squadron_id].callsign)
    for (p1, a1), (p2, a2) in itertools.combinations(air, 2):
        assert p1.distance_to_point(p2) > 150 or abs(a1 - a2) > 100
    assert 1 <= enemy_caps <= builder.MAX_ENEMY_CAP


def test_collision_and_fratricide_not_credited(camp, tmp_path):
    f = _flight(camp, Task.STRIKE)
    planner.assign_player(camp, f.id)
    rec = builder.build(camp, f.id, tmp_path)
    blue = [n for n, e in rec.manifest.items()
            if e["kind"] == "airframe" and camp.flights[e["flight"]].side is Side.BLUE]
    a, b = blue[0], blue[-1]
    data = {"events": [
        {"t": 1, "event": "S_EVENT_KILL", "initiator": _unit(a, "F-4E-45MC"),
         "target": _unit(b, "F-4E-45MC"), "weapon": "F-4E-45MC"},          # collision
        {"t": 2, "event": "S_EVENT_KILL", "initiator": _unit(b, "F-4E-45MC"),
         "target": _unit(a, "F-4E-45MC"), "weapon": "AIM-7E-2"},           # blue on blue
        {"t": 3, "event": "S_EVENT_UNIT_LOST", "initiator": _unit(b, "F-4E-45MC")},
    ], "snapshot": {}}
    results, _ = results_from_dcs(camp, rec, data)
    assert not [k for r in results for k in r.kills]
    assert sum(len(r.airframes_lost) for r in results) == 1
