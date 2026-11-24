"""Phase F (sea): warships by era, anti-ship strikes, and US Navy squadrons flying off a carrier."""
import logging
import random
import tempfile
from pathlib import Path

import dcs
import pytest

from cwdc import era
from cwdc.engine import generate, ground, planner, resolver, turn
from cwdc.engine.factory import new_campaign
from cwdc.missiongen import builder
from cwdc.model import AirframeStatus, ObjectiveKind, Side, Task

OWNED = ["F-100D", "F-4E-45MC", "A-10A", "A-4E-C", "F-5E-3", "F-14A-135-GR", "F-14B"]


def _syria(year, seed=5, **kw):
    return new_campaign(f"Navy {year}", seed=seed, setup=generate.Setup(
        theatre="syria", year=year, owned=OWNED, mods=["A-4E-C"], **kw))


@pytest.fixture(scope="module")
def navy():
    return _syria(1980, player_airframe="F-14A")


def _ships(c):
    return {o.id: o for o in c.objectives.values() if o.kind is ObjectiveKind.SHIP}


def test_warships_follow_the_year():
    old, new = _ships(_syria(1966)), _ships(_syria(1985))
    assert [u.type for u in old["r_med_squadron"].units][:3] == ["REZKY"] * 3        # Kotlin stand-ins
    assert {"MOSCOW", "PIOTR"} & {u.type for u in new["r_med_squadron"].units}
    assert old["b_cvbg"].units[0].type == "Forrestal"
    assert new["r_med_squadron"].threat_range_m >= 75_000
    assert generate.carrier_spec(generate.Setup(theatre="syria", year=1952, owned=OWNED)) is None
    assert generate.carrier_spec(generate.Setup(theatre="syria", year=1952, owned=OWNED,
                                                tech=["WWII Units"]))["carrier_of"] == "b_cvbg"


def test_us_navy_squadrons_fly_from_the_carrier(navy):
    deck = navy.airbases["cvbg"]
    assert deck.carrier_of == "b_cvbg" and deck.side is Side.BLUE
    on_deck = [s for s in navy.squadrons.values() if s.base_id == "cvbg"]
    assert any(s.player_squadron for s in on_deck) and all(era.airframes()[s.airframe].carrier for s in on_deck)
    assert not any(o.kind is ObjectiveKind.AIRBASE and o.airbase_id == "cvbg" for o in navy.objectives.values())
    assert "Anti-ship" in {t.value for s in navy.squadrons.values() for t in s.tasks}


def test_carrier_mission_launches_and_recovers_on_deck(navy):
    logging.disable(logging.CRITICAL)
    f = next(x for x in navy.flights.values() if x.player_eligible
             and navy.airbases[navy.squadrons[x.squadron_id].base_id].carrier_of)
    rec = builder.build(navy, f.id, Path(tempfile.mkdtemp()), start="runway")
    assert any("from the carrier" in l for l in rec.briefing)
    m = dcs.Mission()
    m.load_file(rec.miz_path)
    ships = {g.name: g for co in m.coalition.values() for cc in co.countries.values() for g in cc.ship_group}
    cv = ships["b_cvbg 6th Fleet carrier group"]
    assert cv.units[0].type == "Forrestal" and len(cv.points) >= 2               # steaming into the wind
    g = next(g for co in m.coalition.values() for cc in co.countries.values() for g in cc.plane_group
             if g.name == f"{f.id} {f.callsign}")
    assert g.points[0].type == "TakeOff" and g.points[0].link_unit == cv.units[0].id
    assert g.points[-1].type == "Land" and g.points[-1].link_unit == cv.units[0].id
    assert all(rec.manifest[u.name]["kind"] == "objective" for u in cv.units)


def test_daily_naval_strikes_both_ways():
    c = _syria(1980, seed=6)
    seen = set()
    for _ in range(8):
        for p in c.packages.values():
            if p.target_id and c.objectives[p.target_id].kind is ObjectiveKind.SHIP:
                seen.add(p.side)
        turn.end_turn(c)
    assert seen == {Side.BLUE, Side.RED}


def test_ships_sink_escorts_first_and_the_deck_is_tough(navy):
    cv = navy.objectives["b_cvbg"]
    sim = resolver._Sim(navy, [], random.Random(1))
    assert sim.sink(cv, resolver.SHIP_HITS * 3 + resolver.SHIP_HITS) == 3     # the screen goes first
    assert f"{cv.id}#0" not in sim.destroyed
    assert sim.sink(cv, resolver.SHIP_HITS * 2) == 1 and f"{cv.id}#0" in sim.destroyed


def test_a_sunk_carrier_sends_its_wing_ashore():
    c = _syria(1980, player_airframe="F-14A", seed=7)
    cv = c.objectives["b_cvbg"]
    wing = [s for s in c.squadrons.values() if s.base_id == "cvbg"]
    before = sum(len(c.squadron_airframes(s.id, None)) for s in wing)
    for u in cv.units:
        u.alive = False
    lines = ground.check_overruns(c)
    assert any("sunk" in l for l in lines)
    assert all(not c.airbases[s.base_id].carrier_of for s in wing)
    lost = sum(1 for s in wing for a in c.squadron_airframes(s.id, None) if a.status is AirframeStatus.DESTROYED)
    assert 0 < lost <= before


def test_the_central_front_has_no_navy():
    c = new_campaign("Fulda navy", seed=3)
    assert not _ships(c) and not any(ab.carrier_of for ab in c.airbases.values())
    assert Task.ANTISHIP not in {t for s in c.squadrons.values() for t in s.tasks}
