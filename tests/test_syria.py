"""Phase F: the Southern Flank theatre on the DCS Syria map, and the engine switching theatres."""
import logging
import tempfile
from pathlib import Path

import dcs
import pytest

from cwdc import theater as T
from cwdc.engine import generate, reactive, turn
from cwdc.engine.factory import new_campaign
from cwdc.missiongen import builder
from cwdc.model import Side
from cwdc.web import mapdata

OWNED = ["F-100D", "F-4E-45MC", "A-10A", "A-4E-C", "F-5E-3", "F-14B"]


def _syria(year=1976, seed=5, **kw):
    return new_campaign(f"Flank {year}", seed=seed, setup=generate.Setup(
        theatre="syria", year=year, owned=OWNED, mods=["A-4E-C"], tech=["WWII Units"], **kw))


@pytest.fixture(scope="module")
def flank():
    return _syria()


def test_geography_north_south_front_and_islands(flank):
    T.use(flank)
    assert T.geometry()["dcs_terrain"] == "Syria" and T.nato_dir() == (1.0, 0.0)
    side = lambda la, lo: T.side_at(flank.front_line, la, lo)          # noqa: E731
    assert side(36.20, 36.16) is Side.BLUE        # Antakya
    assert side(37.00, 35.43) is Side.BLUE        # Incirlik
    assert side(36.20, 37.16) is Side.RED         # Aleppo
    assert side(35.52, 35.78) is Side.RED         # Latakia
    assert side(34.59, 32.99) is Side.BLUE        # Akrotiri, Cyprus: off the front, NATO's
    for ab in flank.airbases.values():
        assert T.side_at_pos(flank.front_line, ab.pos) is ab.side, ab.id
    for o in flank.objectives.values():
        assert T.side_at_pos(flank.front_line, o.pos) is o.side, o.name


def test_air_forces_of_the_theatre(flank):
    countries = {(s.side, s.country) for s in flank.squadrons.values()}
    assert (Side.BLUE, "Turkey") in countries and (Side.RED, "Syria") in countries
    player = next(s for s in flank.squadrons.values() if s.player_squadron)
    assert player.base_id == "incirlik" and player.aircraft_type in OWNED
    old = _syria(1957)
    types = {s.aircraft_type for s in old.squadrons.values() if s.side is Side.RED}
    assert types <= {"MiG-15bis", "MiG-19P", "Su-24M", "Tu-22M3"}      # MiG-15/17 era (and stand-ins)


def test_crisis_and_dates_follow_the_theatre():
    c = new_campaign("Flank crisis", seed=5, crisis=True,
                     setup=generate.Setup(theatre="syria", year=1983, owned=OWNED))
    assert c.phase == "crisis" and c.time.year == 1983 and c.time.month == 10
    assert "southern flank" in c.news[-1].text.lower()


def test_war_runs_and_missions_build_on_the_syria_map():
    logging.disable(logging.CRITICAL)
    c = _syria(seed=6)
    for _ in range(8):
        turn.end_turn(c)
    f = next(x for x in c.flights.values() if x.player_eligible)
    rec = builder.build(c, f.id, Path(tempfile.mkdtemp()), start="runway")
    m = dcs.Mission()
    m.load_file(rec.miz_path)
    assert m.terrain.name == "Syria"
    assert any("into Turkey" in l or "on the border" in l or "into Syria" in l
               for d in c.debriefs for l in d.lines)


def test_chokepoints_of_the_theatre(flank):
    T.use(flank)
    sec = flank.sectors["hatay"]
    sec.offset_km = 15
    assert reactive.chokepoint(flank, sec) == "Orontes bridges at Antakya"
    sec.offset_km = 0


def test_switching_back_to_the_central_front(flank):
    g = new_campaign("Back to Fulda", seed=1976)
    assert T.geometry()["dcs_terrain"] == "Germany" and T.nato_dir() == (0.0, -1.0)
    assert T.side_at(g.front_line, 50.55, 9.68) is Side.BLUE          # Fulda
    T.use(flank)
    assert T.side_at(flank.front_line, 36.20, 37.16) is Side.RED      # Aleppo again


def test_map_data_follows_the_theatre(flank):
    T.use(flank)
    m = mapdata.map_layers(flank)
    assert m["theater"] == "syria" and "Turkish-Syrian" in m["border_note"]
    assert {c["name"] for c in m["countries"]} >= {"TURKEY", "SYRIA"}
