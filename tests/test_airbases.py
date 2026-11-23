import dcs
import pytest

from cwdc import theater as T
from cwdc.engine import airbases, apply, ground, planner, turn
from cwdc.engine.factory import new_campaign, sync_theater
from cwdc.missiongen import builder
from cwdc.model import AirframeStatus, ObjectiveKind, Pos, Side, Task


@pytest.fixture
def camp():
    return new_campaign("Airbases", seed=6)


def _ab_obj(c, ab_id):
    return c.objectives[airbases.objective_id(ab_id)]


def _ready(c, sq_id):
    return len(c.squadron_airframes(sq_id))


def test_every_airbase_has_an_airfield_objective_on_its_parking_slots(camp):
    terrain = T.terrain()
    for ab in camp.airbases.values():
        o = _ab_obj(camp, ab.id)
        assert o.kind is ObjectiveKind.AIRBASE and o.side is ab.side and o.airbase_id == ab.id
        parked = [u for u in o.units if airbases.is_parked(u.type)]
        assert len(parked) == airbases.N_PARKED
        resident = [s for s in camp.squadrons.values() if s.base_id == ab.id]
        if resident:                                     # the aircraft on the ramp are the residents'
            assert {u.type for u in parked} == {resident[0].aircraft_type}
        slots = [s.position for s in terrain.airports[ab.dcs_name].parking_slots]
        for u in parked:
            x, z = o.pos.x + u.dx, o.pos.z + u.dz
            assert min(((p.x - x) ** 2 + (p.y - z) ** 2) ** 0.5 for p in slots) < 1.0


def test_destroying_parked_aircraft_damages_base_and_kills_a_resident(camp):
    ab = camp.airbases["rheinmain"]
    sq = next(s for s in camp.squadrons.values() if s.base_id == "rheinmain")
    before = _ready(camp, sq.id)
    apply.destroy_unit(camp, f"{airbases.objective_id('rheinmain')}#0")
    assert ab.damage == pytest.approx(airbases.DAMAGE_PER_HIT) and ab.hit_turn == camp.turn
    assert _ready(camp, sq.id) == before - 1
    apply.destroy_unit(camp, f"{airbases.objective_id('rheinmain')}#4")     # an AAA gun: no damage
    assert ab.damage == pytest.approx(airbases.DAMAGE_PER_HIT)


def test_damage_cuts_sortie_rate_and_a_closed_base_flies_nothing(camp):
    camp.airbases["spangdahlem"].damage = 0.5
    camp.airbases["rheinmain"].damage = airbases.CLOSED
    planner.plan_turn(camp)
    by_sq = {}
    for f in camp.flights.values():
        by_sq[f.squadron_id] = by_sq.get(f.squadron_id, 0) + 1
    for sq in camp.squadrons.values():
        if sq.base_id == "rheinmain":
            assert by_sq.get(sq.id, 0) == 0
        if sq.base_id == "spangdahlem":
            assert by_sq.get(sq.id, 0) <= airbases.sorties(camp, "spangdahlem", sq.sorties or 2)
    assert airbases.sorties(camp, "spangdahlem", 3) == 2


def test_base_repairs_over_turns_and_parked_aircraft_return(camp):
    ab, o = camp.airbases["merseburg"], _ab_obj(camp, "merseburg")
    for i in range(3):
        apply.destroy_unit(camp, f"{o.id}#{i}")
    hit = ab.damage
    airbases.airbase_turn(camp)                          # hit this turn: no repair yet
    assert ab.damage == pytest.approx(hit)
    camp.turn += 1
    for _ in range(5):
        airbases.airbase_turn(camp)
    assert ab.damage == 0
    assert all(u.alive for u in o.units)


def test_warsaw_pact_flies_oca_against_occupied_nato_bases(camp):
    seen = set()
    for _ in range(6):
        for p in camp.packages.values():
            if p.side is Side.RED and p.description.startswith("OCA"):
                tgt = camp.objectives[p.target_id]
                assert tgt.kind is ObjectiveKind.AIRBASE and tgt.side is Side.BLUE
                main = camp.flights[p.flight_ids[0]]
                assert main.task in (Task.STRIKE, Task.BAI)
                seen.add(tgt.airbase_id)
        turn.end_turn(camp)
    assert seen and "hahn" not in seen                  # no squadron at Hahn: nothing to hit


def test_overrun_airfield_changes_hands_wrecked(camp):
    ab = camp.airbases["merseburg"]
    sq = next(s for s in camp.squadrons.values() if s.base_id == "merseburg")
    ground._overrun_airbase(camp, ab, Side.BLUE)
    o = _ab_obj(camp, "merseburg")
    assert ab.side is Side.BLUE and o.side is Side.BLUE and o.country == "USA"
    assert ab.damage >= airbases.CLOSED and o.destroyed
    assert sq.base_id != "merseburg"                    # the MiGs evacuated


def test_sync_adds_airfields_to_older_campaigns(camp):
    for ab in camp.airbases.values():
        del camp.objectives[airbases.objective_id(ab.id)]
    added = sync_theater(camp)
    assert sum(1 for a in added if a.endswith("airfield")) == len(camp.airbases)


def test_oca_mission_spawns_parked_statics_and_bombs_them(camp, tmp_path, monkeypatch):
    monkeypatch.setitem(planner.DOCTRINE[Side.BLUE], "oca", 50.0)    # force a NATO OCA package
    planner.plan_turn(camp)
    f = next(fl for fl in camp.flights.values() if fl.side is Side.BLUE and fl.target_id
             and camp.objectives[fl.target_id].kind is ObjectiveKind.AIRBASE and fl.supports is None)
    o = camp.objectives[f.target_id]
    rec = builder.build(camp, f.id, tmp_path, bot=True)
    statics = sorted(n for n, e in rec.manifest.items() if e.get("static") and e["objective"] == o.id)
    assert len(statics) == airbases.N_PARKED
    m = dcs.Mission()
    m.load_file(rec.miz_path)
    planes = {u.name: u for coal in m.coalition.values() for co in coal.countries.values()
              for g in co.static_group for u in g.units if u.name in statics}
    assert set(planes) == set(statics)
    assert all(u.category == "Planes" for u in planes.values())
    g = m.find_group(f"{f.id} {f.callsign}")
    bomb = [t for p in g.points for t in p.tasks if getattr(t, "Id", "") == "Bombing"]
    assert bomb
    cx = sum(u.position.x for u in planes.values()) / len(planes)
    cz = sum(u.position.y for u in planes.values()) / len(planes)
    assert abs(bomb[0].params["x"] - cx) < 1 and abs(bomb[0].params["y"] - cz) < 1


def test_player_ramp_slots_avoid_parked_statics():
    airport = T.terrain().airports["Frankfurt"]
    free = airport.free_parking_slots(dcs.planes.F_4E_45MC)
    blocked = [Pos(x=s.position.x, z=s.position.y) for s in free[:20]]
    slots = builder._adjacent_slots(airport, dcs.planes.F_4E_45MC, 2, blocked)
    assert slots and all(s not in free[:20] for s in slots)
