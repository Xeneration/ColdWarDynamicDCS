import dcs
import pytest

from cwdc.engine import apply, crews, planner, resolver, turn
from cwdc.engine.factory import new_campaign, sync_theater
from cwdc.ingest.flown import results_from_dcs
from cwdc.missiongen import builder
from cwdc.model import CrewRole, Kill, PilotStatus, Side, SortieResult, Task


@pytest.fixture
def camp():
    return new_campaign("Crews", seed=7)


def _f4_flights(c):
    return [f for f in c.flights.values() if c.squadrons[f.squadron_id].aircraft_type == "F-4E-45MC"]


def test_phantoms_fly_with_a_wso_in_every_back_seat(camp):
    for sq in camp.squadrons.values():
        wsos = camp.squadron_pilots(sq.id, role=CrewRole.WSO)
        assert bool(wsos) == crews.two_seat(sq) == (sq.aircraft_type == "F-4E-45MC")
    flights = _f4_flights(camp)
    assert flights
    for f in flights:
        assert len(f.wso_ids) == len(f.airframe_ids) == len(f.pilot_ids)
        assert all(camp.pilots[w].role is CrewRole.WSO for w in f.wso_ids)
        assert not set(f.wso_ids) & set(f.pilot_ids)
    for f in camp.flights.values():
        if not crews.two_seat(camp.squadrons[f.squadron_id]):
            assert f.wso_ids == []
    seats = [p for f in camp.flights.values() for p in crews.crew_of(f)]
    assert len(seats) == len(set(seats))                 # nobody in two cockpits


def test_flying_tires_crews_and_rest_recovers_them(camp):
    f = _f4_flights(camp)[0]
    crew = crews.crew_of(f)
    turn.end_turn(camp)
    assert all(camp.pilots[p].fatigue == pytest.approx(crews.FATIGUE_PER_SORTIE) for p in crew)
    crews.rest(camp, night=False)                         # a turn on the ground
    assert all(camp.pilots[p].fatigue == pytest.approx(crews.FATIGUE_PER_SORTIE - crews.REST_PER_TURN)
               for p in crew)


def test_exhausted_crews_are_not_scheduled(camp):
    sq = next(s for s in camp.squadrons.values() if s.player_squadron)
    for p in camp.squadron_pilots(sq.id, role=None):
        p.fatigue = 0.95
    planner.plan_turn(camp)
    assert not [f for f in camp.flights.values() if f.squadron_id == sq.id]


def test_fatigue_limits_sorties_per_day():
    c = new_campaign("Tempo", seed=8)
    start = {p.id: p.sorties for p in c.pilots.values()}
    for _ in range(4):                                    # one day: dawn, day, dusk, night
        turn.end_turn(c)
    per_day = [p.sorties - start.get(p.id, 0) for p in c.pilots.values()]
    assert max(per_day) <= 3


def test_tired_crews_fight_worse(camp):
    f = _f4_flights(camp)[0]
    fresh = crews.performance(camp, f)
    for p in crews.crew_of(f):
        camp.pilots[p].fatigue = 1.0
    assert fresh == 1.0 and crews.performance(camp, f) == pytest.approx(1 - crews.PERFORMANCE_LOSS)


def test_lost_phantom_costs_both_seats_who_eject_together():
    for seed in range(30):
        c = new_campaign("Losses", seed=seed)
        res = resolver.resolve(c, list(c.flights.values()))
        for r in res:
            f = c.flights[r.flight_id]
            if not f.wso_ids:
                continue
            fates = {x.pilot_id: x for x in r.pilot_fates}
            for aid in r.airframes_lost:
                slot = f.airframe_ids.index(aid)
                pilot, wso = fates[f.pilot_ids[slot]], fates[f.wso_ids[slot]]
                assert (pilot.note == "did not eject") == (wso.note == "did not eject")
                return
    pytest.fail("no Phantom lost in 30 seeds")


def test_shared_kill_credit(camp):
    f = _f4_flights(camp)[0]
    res = SortieResult(flight_id=f.id, source="sim",
                       kills=[Kill(pilot_id=f.pilot_ids[0], target="x", target_type="MiG-21Bis")])
    apply.apply_results(camp, [res])
    assert camp.pilots[f.pilot_ids[0]].air_kills == 1 == camp.pilots[f.wso_ids[0]].air_kills
    assert camp.pilots[f.wso_ids[0]].sorties == 1


def _player_mission(c, tmp_path):
    f = next(fl for fl in c.flights.values() if fl.player_eligible and fl.target_id)
    planner.assign_player(c, f.id)
    rec = builder.build(c, f.id, tmp_path, start="runway")
    return f, rec


def test_mission_manifest_and_briefing_name_the_wso(camp, tmp_path):
    f, rec = _player_mission(camp, tmp_path)
    lead = next(n for n, e in rec.manifest.items() if e["kind"] == "airframe" and e["flight"] == f.id)
    assert rec.manifest[lead]["wso"] == f.wso_ids[0]
    m = dcs.Mission()
    m.load_file(rec.miz_path)
    w = camp.pilots[f.wso_ids[0]]
    assert f"{w.name} (WSO)" in m.description_text()


def test_dcs_ingest_gives_each_seat_its_own_parachute(camp, tmp_path):
    f, rec = _player_mission(camp, tmp_path)
    lead = next(n for n, e in rec.manifest.items() if e["kind"] == "airframe" and e["flight"] == f.id)
    home = camp.airbases["rheinmain"].pos
    enemy = camp.airbases["merseburg"].pos
    events = [
        {"event": "S_EVENT_EJECTION", "initiator": {"name": lead, "pos": {"x": home.x, "z": home.z}}},
        {"event": "S_EVENT_LANDING_AFTER_EJECTION", "place": lead,
         "initiator": {"name": "F-4E_Parachute_Pilot", "pos": {"x": home.x, "z": home.z}}},
        {"event": "S_EVENT_LANDING_AFTER_EJECTION", "place": lead,
         "initiator": {"name": "F-4E_Parachute_WSO", "pos": {"x": enemy.x, "z": enemy.z}}},
    ]
    results, _ = results_from_dcs(camp, rec, {"events": events, "snapshot": {}})
    fates = {x.pilot_id: x.status for r in results for x in r.pilot_fates}
    assert fates[f.pilot_ids[0]] is PilotStatus.ACTIVE      # came down at home
    assert fates[f.wso_ids[0]] is PilotStatus.POW           # drifted over the GDR


def test_sync_gives_older_campaigns_their_wsos(camp):
    for p in [p for p in camp.pilots.values() if p.role is CrewRole.WSO]:
        del camp.pilots[p.id]
    for sq in camp.squadrons.values():
        sq.establishment_wsos = 0
    added = sync_theater(camp)
    assert any(a.endswith("WSOs") for a in added)
    sq = next(s for s in camp.squadrons.values() if s.player_squadron)
    assert len(camp.squadron_pilots(sq.id, role=CrewRole.WSO)) == sq.establishment_pilots
