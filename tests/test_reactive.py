"""Phase E: missions in the enemy's turn - troops in contact, holding a river line, counter-battery,
helicopter hunts and named scrambles - and their effect on the ground war."""
import logging
import tempfile
from pathlib import Path

import dcs
import pytest

from cwdc.engine import ground, planner, reactive, turn
from cwdc.engine.factory import new_campaign
from cwdc.ingest.flown import results_from_dcs
from cwdc.missiongen import builder
from cwdc.model import MissionRecord, ObjectiveKind, Side, Task


def _attacking(seed=4, sector=None):
    """A war turn where the Warsaw Pact attacked last turn (sector forced if given), re-planned."""
    c = new_campaign("React", seed=seed)
    turn.end_turn(c)
    if sector:
        for s in c.sectors.values():
            s.attacker = Side.RED if s.id == sector else None
        planner.plan_turn(c)
    return c


def _reaction_pkg(c, kinds):
    return next((p for p in c.packages.values() if p.reaction in kinds), None)


def test_troops_in_contact_answers_the_attack_and_goes_to_the_players_wing():
    c = _attacking(sector="fulda")
    if planner.period(c) == "night":
        pytest.skip("night turn")
    pkg = _reaction_pkg(c, ("tic", "hold"))
    assert pkg is not None and pkg.task is Task.CAS
    tgt = c.objectives[pkg.target_id]
    assert tgt.kind is ObjectiveKind.ARMOR and tgt.side is Side.RED and tgt.sector_id == "fulda"
    main = c.flights[pkg.flight_ids[0]]
    sq = c.squadrons[main.squadron_id]
    assert (sq.player_squadron or sq.player_wing) and main.player_eligible
    x = next(r for r in reactive.this_turn(c) if r.package_id == pkg.id)
    assert x.sector_id == "fulda" and x.target_id == tgt.id
    assert len([r for r in reactive.this_turn(c) if r.kind in ("tic", "hold", "battery")]) == 1


def test_hold_the_river_line_near_a_chokepoint():
    c = _attacking()
    sec = c.sectors["fulda"]
    sec.offset_km = 22.0                     # the Fulda river at Fulda is at 25 km
    assert reactive.chokepoint(c, sec) == "Fulda river at Fulda"
    sec.offset_km = 2.0
    assert reactive.chokepoint(c, sec) is None


def test_air_kills_on_the_lead_battalion_stall_the_attack():
    c = _attacking(sector="fulda")
    x = next((r for r in reactive.this_turn(c) if r.kind in ("tic", "hold")), None)
    if x is None:
        pytest.skip("no troops-in-contact this turn (night)")
    o = c.objectives[x.target_id]
    sec = c.sectors[x.sector_id]
    assert reactive.sector_factor(c, sec)[0] == 1.0
    for i in range(len(o.units)):            # destroy the battalion from the air
        o.units[i].alive = False
        ground.destroy_from_air(c, o, i)
    assert x.score >= x.needed
    f, lines = reactive.sector_factor(c, sec)
    assert f == reactive.STALL[x.kind] and "stalls" in lines[0]


def test_helicopter_hunt_and_assault(monkeypatch):
    monkeypatch.setattr(reactive, "P_HELO", 1.0)
    c = _attacking(sector="rhoen")
    if planner.period(c) == "night":
        pytest.skip("night turn")
    x = reactive.this_turn(c, "helo")[0]
    pkg = c.packages[x.package_id]
    assert pkg.reaction == "helo" and pkg.description.startswith("Helicopter hunt")
    assert c.flights[pkg.flight_ids[0]].task is Task.CAP
    sec = c.sectors["rhoen"]
    assert reactive.sector_factor(c, sec)[0] == reactive.HELO_LIFT          # unopposed so far
    x.score = 1.0
    assert reactive.sector_factor(c, sec)[0] == 1.0
    assert reactive.helo_types(1976) == ["Mi-24V", "Mi-24V", "Mi-8MT", "Mi-8MT"]
    assert reactive.helo_types(1968) == ["Mi-8MT", "Mi-8MT"]


def test_no_helicopter_assaults_before_the_mi8(monkeypatch):
    from cwdc.engine import generate
    monkeypatch.setattr(reactive, "P_HELO", 1.0)
    c = new_campaign("Old", seed=4, setup=generate.Setup(year=1960, owned=["F-100D"]))
    for s in c.sectors.values():
        s.attacker = Side.RED
    import random
    assert reactive.helo_assault(c, random.Random(1)) is None


def test_scrambles_are_named_by_what_they_meet():
    seen = set()
    for seed in range(1, 6):
        c = new_campaign("Raids", seed=seed)
        for _ in range(6):
            seen |= {p.reaction for p in c.packages.values() if p.task is Task.INTERCEPT}
            turn.end_turn(c)
    assert "hunt" in seen
    assert any(p.description.startswith("Hunt the raid") for p in c.packages.values()) or "hunt" in seen


def test_mission_scenes_and_dcs_results(monkeypatch):
    logging.disable(logging.CRITICAL)
    monkeypatch.setattr(reactive, "P_HELO", 1.0)
    c = _attacking(sector="fulda")
    if planner.period(c) == "night":
        pytest.skip("night turn")
    tic = _reaction_pkg(c, ("tic", "hold"))
    rec = builder.build(c, tic.flight_ids[0], Path(tempfile.mkdtemp()), start="air")
    assert any(l.startswith(("TROOPS IN CONTACT", "HOLD THE")) for l in rec.briefing)
    m = dcs.Mission()
    m.load_file(rec.miz_path)
    names = {g.name for co in m.coalition.values() for cc in co.countries.values() for g in cc.vehicle_group}
    assert "Friendly armour in contact" in names
    helo = _reaction_pkg(c, ("helo",))
    rec = builder.build(c, helo.flight_ids[0], Path(tempfile.mkdtemp()), start="air")
    assert any(l.startswith("HELICOPTER HUNT") for l in rec.briefing)
    x = reactive.this_turn(c, "helo")[0]
    assert x.helos and all(rec.manifest[n]["kind"] == "reactive" for n in x.helos)
    # DCS: half the wave shot down (missing from the final snapshot), one kill credited
    mission = MissionRecord(mission_id="m", turn=c.turn, player_flight_id=helo.flight_ids[0],
                            miz_path=rec.miz_path, generated_at=0, manifest=rec.manifest,
                            spawned_flights=rec.spawned_flights)
    alive = x.helos[len(x.helos) // 2:]
    pilot_unit = next(n for n, e in rec.manifest.items() if e["kind"] == "airframe"
                      and e["flight"] == helo.flight_ids[0])
    data = {"events": [{"event": "S_EVENT_KILL", "initiator": {"name": pilot_unit},
                        "target": {"name": x.helos[0], "type": "Mi-24V", "category": 1, "coalition": 1}}],
            "snapshot": {"groups": [{"units": [{"name": n} for n in alive]}]}}
    lines = reactive.dcs_results(c, mission, data)
    assert x.flown and x.score == 0.5 and "broken up" in lines[0]
    results, _ = results_from_dcs(c, mission, data)      # reactive units do not trip the ingest
    assert any(k.target == x.helos[0] for r in results for k in r.kills)
