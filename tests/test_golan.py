"""The Golan Front: a compact theatre on the south of the Syria map, where every mission the
player's squadron and wing are given lies within 50 nm of home."""
import logging
import tempfile
from pathlib import Path

import pytest

from cwdc import theater as T
from cwdc.engine import generate, planner, turn
from cwdc.engine.factory import new_campaign
from cwdc.missiongen import builder
from cwdc.model import Side
from cwdc.web import maker

OWNED = ["F-100D", "F-4E-45MC", "A-10A", "A-4E-C", "F-5E-3", "F-14B"]
LIMIT_M = 50 * 1852


def _golan(year=1976, seed=5, **kw):
    return new_campaign(f"Golan {year}", seed=seed, setup=generate.Setup(
        theatre="golan", year=year, owned=OWNED, mods=["A-4E-C"], tech=["WWII Units"], **kw))


@pytest.fixture(scope="module")
def golan():
    return _golan()


def _player_flights(c):
    for f in c.flights.values():
        sq = c.squadrons[f.squadron_id]
        if sq.player_squadron or sq.player_wing:
            at = c.objectives[f.target_id].pos if f.target_id else f.area
            yield f, c.airbases[sq.base_id].pos.dist(at) if at else 0.0


def test_geography_west_east_front(golan):
    T.use(golan)
    assert T.geometry()["dcs_terrain"] == "Syria" and T.nato_dir() == (0.0, -1.0)
    assert T.geometry()["player_range_nm"] == 50
    side = lambda la, lo: T.side_at(golan.front_line, la, lo)          # noqa: E731
    assert side(32.97, 35.57) is Side.BLUE        # Rosh Pina
    assert side(33.51, 36.29) is Side.RED         # Damascus
    for ab in golan.airbases.values():
        assert T.side_at_pos(golan.front_line, ab.pos) is ab.side, ab.id
    for o in golan.objectives.values():
        assert T.side_at_pos(golan.front_line, o.pos) is o.side, o.id


def test_home_field_is_ramat_david_and_the_wing_shares_it(golan):
    ps = [s for s in golan.squadrons.values() if s.player_squadron or s.player_wing]
    assert ps and {s.base_id for s in ps} == {"ramat_david"}


def test_player_missions_stay_within_50_nm_through_the_war():
    logging.disable(logging.CRITICAL)
    for year in (1966, 1985):
        c = _golan(year, seed=3)
        eligible = 0
        for _ in range(12):                       # three days of war
            for f, d in _player_flights(c):
                assert d <= LIMIT_M, (year, c.turn, f.task, d / 1852)
                eligible += f.player_eligible
            if c.outcome:
                break
            turn.end_turn(c)
        assert eligible >= 12, (year, eligible)  # there's plenty to fly


def test_the_ai_still_strikes_deep(golan):
    T.use(golan)
    deep = [f for f in golan.flights.values() if f.side is Side.BLUE and f.target_id
            and golan.airbases[golan.squadrons[f.squadron_id].base_id].pos.dist(
                golan.objectives[f.target_id].pos) > LIMIT_M]
    assert all(not f.player_eligible for f in deep)


def test_range_limit_only_on_the_golan():
    c = new_campaign("Flank", seed=5, setup=generate.Setup(
        theatre="syria", year=1976, owned=OWNED, mods=["A-4E-C"], tech=["WWII Units"]))
    T.use(c)
    assert T.geometry()["player_range_nm"] is None and T.geometry()["advance_scale"] == 1.0
    sq = next(s for s in c.squadrons.values() if s.player_squadron)
    far = next(o for o in c.objectives.values() if o.side is Side.RED)
    assert planner.player_in_range(c, sq, far.pos)


def test_maker_offers_the_golan_and_builds_a_mission(golan):
    logging.disable(logging.CRITICAL)
    t = next(t for t in maker.THEATRES if t["id"] == "golan")
    assert "50 nm" in t["blurb"]
    T.use(golan)
    f = next(f for f in golan.flights.values() if f.player_eligible)
    rec = builder.build(golan, f.id, Path(tempfile.mkdtemp()), start="runway")
    assert Path(rec.miz_path).is_file()
