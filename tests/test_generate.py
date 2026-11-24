"""Campaign maker Phase B: the war generated from a year (order of battle, air defences, ground
forces), the Fulda 1976 preset unchanged, presets and the year-aware loadout pick."""
import logging
import tempfile
from pathlib import Path

import dcs
import pytest

from cwdc import era
from cwdc import theater as T
from cwdc.engine import factory, generate, turn
from cwdc.engine.factory import new_campaign
from cwdc.missiongen import builder
from cwdc.model import ObjectiveKind, Side

OWNED = ["F-100D", "F-4E-45MC", "A-10A", "A-4E-C", "AJS37", "F-5E-3", "F-14B", "UH-1H", "Su-25T"]


def _setup(year, **kw):
    return generate.Setup(year=year, owned=OWNED, mods=["A-4E-C"], tech=["WWII Units"], **kw)


@pytest.fixture(scope="module")
def wars():
    return {y: new_campaign(f"Gen {y}", seed=5, setup=_setup(y)) for y in (1957, 1966, 1976, 1985)}


def test_fulda_preset_is_untouched():
    c = new_campaign("Preset", seed=1976)
    oob = T.load_data("oob_fulda_1976.yaml")
    assert c.era is None
    assert sorted(c.squadrons) == sorted(s["id"] for s in oob["squadrons"])
    assert c.time.year == 1976 and c.squadrons["b_496tfs"].player_squadron


def test_every_squadron_flies_what_its_nation_had_that_year(wars):
    for year, c in wars.items():
        sides = {s.side for s in c.squadrons.values()}
        assert sides == {Side.BLUE, Side.RED}
        names = [s.name for s in c.squadrons.values()]
        assert len(names) == len(set(names)), year
        for sq in c.squadrons.values():
            af = era.airframes()[sq.airframe]
            nation = generate.nation_of_country(sq.country)
            assert af.nations_in(year, [nation]), (year, sq.name, af.id)
            assert c.airbases[sq.base_id].side is sq.side
            if sq.side is Side.BLUE:
                assert sq.callsign in builder.WESTERN_CALLSIGNS, sq.callsign
        player = next(s for s in c.squadrons.values() if s.player_squadron)
        assert player.aircraft_type in OWNED and player.side is Side.BLUE


def test_player_airframe_by_year(wars):
    ptype = {y: next(s for s in c.squadrons.values() if s.player_squadron).aircraft_type for y, c in wars.items()}
    assert ptype[1957] == "F-100D" and ptype[1976] == "F-4E-45MC"
    c = new_campaign("A4", seed=5, setup=_setup(1966, player_airframe="A-4E"))
    assert next(s for s in c.squadrons.values() if s.player_squadron).aircraft_type == "A-4E-C"


def test_air_defences_and_vehicles_fit_the_year(wars):
    idx = generate._unit_index()
    for year, c in wars.items():
        for o in c.objectives.values():
            for u in o.units:
                if u.type in idx:
                    section, key = idx[u.type]
                    a, b = era._ground()[section][key]["years"]
                    if not a <= year <= b:          # the type may also belong to another (older) entry
                        assert any(u.type in v["units"] and v["years"][0] <= year <= v["years"][1]
                                   for v in era._ground()[section].values()), (year, o.name, u.type)
    sams57 = [o for o in wars[1957].objectives.values() if o.kind is ObjectiveKind.SAM]
    assert sams57 and all(o.name.startswith("AAA") for o in sams57)     # no SAMs before 1959
    assert any(o.name.startswith("SA-") for o in wars[1976].objectives.values())
    types85 = {u.type for o in wars[1985].objectives.values() for u in o.units}
    assert {"T-34-85", "M4_Sherman"}.isdisjoint(types85)
    types57 = {u.type for o in wars[1957].objectives.values() for u in o.units}
    assert {"T-72B", "BMP-1", "M-1 Abrams"}.isdisjoint(types57)


def test_dates_follow_the_year(wars):
    assert all(c.time.year == y for y, c in wars.items())
    c = new_campaign("Crisis", seed=5, setup=_setup(1966), crisis=True)
    assert c.phase == "crisis" and c.time.year == 1966


def test_generation_is_deterministic_and_the_theatre_does_not_drift(wars):
    again = new_campaign("Gen 1957", seed=5, setup=_setup(1957))
    assert [(s.name, s.aircraft_type, s.base_id) for s in again.squadrons.values()] == \
           [(s.name, s.aircraft_type, s.base_id) for s in wars[1957].squadrons.values()]
    assert factory.sync_theater(again) == []        # no 1976 SA-2 reappears in 1957


def test_setup_errors():
    with pytest.raises(generate.SetupError):
        generate.validate(_setup(1951))                       # nothing owned flies in 1951
    with pytest.raises(generate.SetupError):
        generate.validate(_setup(1976, side="east"))
    with pytest.raises(generate.SetupError):
        generate.validate(_setup(1960, player_airframe="A-10A"))


def test_presets():
    assert generate.preset_setup("fulda-1976", None) is None
    inst = _setup(1976).installed()
    s = generate.preset_setup("phantoms-and-viggens-1975", inst)
    assert s.year == 1975 and s.sweden and "Sweden" in s.active_nations()
    c = new_campaign("PV", seed=2, setup=s)
    assert next(x for x in c.squadrons.values() if x.player_squadron).aircraft_type == "F-4E-45MC"


def test_year_aware_payload_pick():
    p = era.pick_payload("F-100D", 1957, "ground")
    assert p and all(era.weapon_year(c) <= 1957 for c in p["pylons"].values())
    a2a = era.pick_payload("F-4E-45MC", 1976, "air")
    fams = {era.weapon_info(c)[1] for c in a2a["pylons"].values()}
    assert any(f.startswith("AIM-") for f in fams) and not fams & {"GP bombs", "Rockeye"}
    sead = era.pick_payload("F-4E-45MC", 1977, "sead")     # shipped Shrike loads carry ALQ-131
    assert sead and any(era.weapon_info(c)[1] in era.ARM for c in sead["pylons"].values())
    assert era.pick_payload("F-100D", 1960, "sead") is None    # no Shrike before 1965


def test_generated_wars_run_and_build_missions(wars):
    logging.disable(logging.CRITICAL)
    for year in (1957, 1985):
        c = new_campaign(f"Run {year}", seed=9, setup=_setup(year))
        for _ in range(8):
            turn.end_turn(c)
        f = next((x for x in c.flights.values() if x.player_eligible), None)
        if f is None:
            continue
        rec = builder.build(c, f.id, Path(tempfile.mkdtemp()), start="air")
        m = dcs.Mission()
        m.load_file(rec.miz_path)
        g = next(g for co in m.coalition.values() for cc in co.countries.values() for g in cc.plane_group
                 if g.name == f"{f.id} {f.callsign}")
        for p in g.units[0].pylons.values():
            y = era.weapon_year(p["CLSID"])
            assert y is None or y <= year, (year, p["CLSID"])
