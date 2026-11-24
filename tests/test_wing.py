"""Campaign maker Phase C: the player's wing (several owned airframes), one payload decision per
flight for any type and year, and AI loadouts that fit the year."""
import logging
import tempfile
from pathlib import Path

import dcs
import pytest

from cwdc import era
from cwdc.engine import generate, planner, turn
from cwdc.engine.factory import new_campaign
from cwdc.era.payloads import harvested_types
from cwdc.missiongen import builder, loadouts

OWNED = ["F-100D", "F-4E-45MC", "A-10A", "A-4E-C", "AJS37", "F-5E-3", "F-14B", "F-14A-135-GR", "UH-1H"]


def _setup(year, **kw):
    return generate.Setup(year=year, owned=OWNED, mods=["A-4E-C"], tech=["WWII Units"], **kw)


def _wing(c):
    return [s for s in c.squadrons.values() if s.player_squadron or s.player_wing]


def _offered(c, turns=8):
    types = set()
    for _ in range(turns):
        types |= {c.squadrons[f.squadron_id].aircraft_type for f in c.flights.values() if f.player_eligible}
        turn.end_turn(c)
    return types


def test_wing_offers_several_owned_airframes():
    c = new_campaign("Wing", seed=4, setup=_setup(1966))
    wing = _wing(c)
    assert 2 <= len(wing) <= 1 + generate.WING_EXTRA
    home = next(s for s in wing if s.player_squadron)
    assert all(s.base_id == home.base_id for s in wing)
    types = [s.aircraft_type for s in wing]
    assert len(set(types)) == len(types) and set(types) <= set(OWNED)
    offered = _offered(c)
    assert len(offered) >= 2 and offered <= set(types)


def test_unowned_and_non_wing_squadrons_are_never_offered():
    c = new_campaign("Wing", seed=4, setup=_setup(1976))
    for _ in range(4):
        for f in c.flights.values():
            sq = c.squadrons[f.squadron_id]
            if f.player_eligible:
                assert (sq.player_squadron or sq.player_wing) and sq.aircraft_type in OWNED
        turn.end_turn(c)


def test_unmixed_wing_keeps_to_the_players_nation_and_land_bases():
    c = new_campaign("Wing", seed=4, setup=_setup(1976, mixed_wing=False))
    for s in _wing(c):
        af = era.airframes()[s.airframe]
        assert s.country == "USA" and not af.carrier
    mixed = new_campaign("Wing", seed=4, setup=_setup(1976))
    assert any(s.country != "USA" for s in _wing(mixed))              # e.g. a French F-100D squadron
    assert not any(era.airframes()[s.airframe].carrier for s in _wing(mixed))   # land-based first
    navy = new_campaign("Navy", seed=4, setup=_setup(1976, player_airframe="F-14A"))
    home = next(s for s in navy.squadrons.values() if s.player_squadron)
    assert home.name.startswith("VF-")                                # US Navy squadrons named as such
    pv = new_campaign("PV", seed=4, setup=_setup(1975, sweden=True, player_airframe="F-4E"))
    assert "AJS37" in {s.aircraft_type for s in _wing(pv)}            # Phantoms and Viggens


def test_preset_offers_only_the_home_squadron():
    c = new_campaign("Preset", seed=1976)
    assert not any(s.player_wing for s in c.squadrons.values())
    assert {c.squadrons[f.squadron_id].id for f in c.flights.values() if f.player_eligible} <= {"b_496tfs"}


def test_player_flies_a_wing_squadrons_aircraft():
    logging.disable(logging.CRITICAL)
    c = new_campaign("Wing", seed=4, setup=_setup(1966))
    home = next(s for s in c.squadrons.values() if s.player_squadron)
    for _ in range(12):
        f = next((x for x in c.flights.values() if x.player_eligible and x.squadron_id != home.id), None)
        if f is not None:
            break
        turn.end_turn(c)
    else:
        pytest.skip("no wing flight in the sample")
    planner.assign_player(c, f.id)
    assert c.player_pilot().id in f.pilot_ids
    rec = builder.build(c, f.id, Path(tempfile.mkdtemp()), start="runway")
    m = dcs.Mission()
    m.load_file(rec.miz_path)
    g = next(g for co in m.coalition.values() for cc in co.countries.values() for g in cc.plane_group
             if g.name == f"{f.id} {f.callsign}")
    assert g.units[0].skill.value == "Client" or g.units[0].skill.value == "Player"
    assert g.units[0].type == c.squadrons[f.squadron_id].aircraft_type
    assert any(l.startswith("Loadout:") for l in rec.briefing)
    for p in g.units[0].pylons.values():
        assert era.weapon_year(p["CLSID"]) <= 1966


def test_flight_payload_named_and_era():
    c = new_campaign("Preset", seed=1976)
    f = next(x for x in c.flights.values() if x.loadout and c.squadrons[x.squadron_id].aircraft_type == "F-4E-45MC")
    pay = loadouts.flight_payload(c, f)
    assert pay["source"] == "named" and pay["pylons"]
    old = new_campaign("Old", seed=3, setup=_setup(1970, player_airframe="F-4E"))
    x = next(x for x in old.flights.values() if old.squadrons[x.squadron_id].aircraft_type == "F-4E-45MC")
    x.loadout = "BAI_MAVERICK"                         # a named load; Mavericks arrive in 1972
    pay = loadouts.flight_payload(old, x)
    assert pay["source"] == "era"
    assert all(era.weapon_year(cl) <= 1970 for cl in pay["pylons"].values())
    x.loadout = "STRIKE_M117"                          # M117s, AIM-7E-2s, ALE-40: all there by 1970
    assert loadouts.flight_payload(old, x)["source"] == "named"


def test_picks_never_carry_training_stores_or_too_new_weapons():
    for t in harvested_types():
        for year in (1957, 1966, 1976, 1985):
            for kind in ("air", "ground", "sead", "clean"):
                p = era.pick_payload(t, year, kind)
                if p is None:
                    continue
                for cl in p["pylons"].values():
                    y, fam = era.weapon_info(cl)
                    assert fam != "training" and y <= year, (t, year, kind, cl)


def test_trimmed_payloads_keep_period_weapons():
    p = era.pick_payload("F-14A-135-GR", 1976, "air")
    fams = {era.weapon_info(c)[1] for c in p["pylons"].values()}
    assert "AIM-54" in fams and "AIM-9L/M" not in fams
    mig = era.pick_payload("MiG-21Bis", 1966, "air")
    assert mig and "R-3S" in {era.weapon_info(c)[1] for c in mig["pylons"].values()}
    assert era.pick_payload("MiG-15bis", 1957, "air") is None
    assert era.pick_payload("MiG-15bis", 1957, "clean") is not None        # guns and tanks


def test_every_generated_ai_type_has_a_period_load():
    for year in (1957, 1966, 1976, 1985):
        c = new_campaign(f"AI {year}", seed=6, setup=_setup(year))
        for _ in range(2):
            for f in c.flights.values():
                pay = loadouts.flight_payload(c, f)
                assert pay["pylons"] is not None                   # never a DCS default in an era war
                assert all(era.weapon_year(cl) <= year for cl in pay["pylons"].values())
            turn.end_turn(c)
