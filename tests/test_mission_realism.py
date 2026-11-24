"""Fixes from the first overnight soak (2026-11-24): SAM-aware routes, no point-blank fighter
spawns, AI skill, CAP/escort flights that go home, and no empty CAPs."""
import logging

import dcs
import pytest
from dcs import task as dtask

from cwdc import theater as T
from cwdc.engine.factory import new_campaign
from cwdc.missiongen import builder
from cwdc.model import Pos, Side, Task


@pytest.fixture(scope="module")
def camp():
    return new_campaign("Realism", seed=11)


def _load(rec):
    logging.disable(logging.CRITICAL)
    m = dcs.Mission()
    m.load_file(rec.miz_path)
    return {g.name: g for co in m.coalition.values() for c in co.countries.values() for g in c.plane_group}


def _pos(p) -> Pos:
    return Pos(x=p.position.x, z=p.position.y)


def test_avoid_bends_round_a_ring():
    a, b, centre = Pos(x=0, z=0), Pos(x=200_000, z=0), Pos(x=100_000, z=5_000)
    pts = [a] + builder._avoid(a, b, [(centre, 30_000)])
    assert len(pts) > 2
    for p, q in zip(pts, pts[1:]):
        near, _ = builder._closest_on(p, q, centre)
        assert near.dist(centre) >= 30_000 - 1


def test_back_along_and_ahead():
    path = [Pos(x=0, z=0), Pos(x=100_000, z=0), Pos(x=100_000, z=100_000)]
    p, i = builder._back_along(path, 150_000)
    assert abs(p.x - 50_000) < 1 and i == 1
    assert builder._ahead(path, p) == [path[1]]
    assert builder._ahead(path, Pos(x=100_000, z=50_000)) == []


def test_bot_missions_route_round_sams_and_go_home(camp, tmp_path):
    seen = set()
    for f in [x for x in camp.flights.values() if x.player_eligible]:
        if f.task in seen:
            continue
        seen.add(f.task)
        rec = builder.build(camp, f.id, tmp_path, start="air", bot=True, time_limit=4500)
        groups = _load(rec)
        g = groups[f"{f.id} {f.callsign}"]
        names = [p.name for p in g.points]
        # the focus flight always goes home, so the mission can end before the time limit
        assert any(p.type == "Land" for p in g.points), (f.task, names)
        # no leg of its route crosses a SAM ring it could have avoided
        dest = builder._dest(camp, f)
        rings = builder._rings(camp, f.side, exempt=(dest, builder._base(camp, f)))
        pts = [_pos(p) for p in g.points if p.type != "Land" and p.name not in ("IP", "TGT", "EGRESS")]
        for a, b in zip(pts, pts[1:]):
            for centre, r in rings:
                if a.dist(centre) > r and b.dist(centre) > r:
                    assert builder._closest_on(a, b, centre)[0].dist(centre) >= r * 0.95, (f.task, names)
        # enemy fighters don't start inside missile range of the focus flight
        spawn = _pos(g.points[0])
        for name, eg in groups.items():
            fid = name.split(" ")[0]
            ef = camp.flights.get(fid)
            if ef and ef.side is not f.side:
                assert _pos(eg.points[0]).dist(spawn) >= builder.MIN_CONTACT_DIST - builder.FLIGHT_SPACING * 8
        if f.task is Task.CAP:
            orbit = [t for p in g.points for t in p.tasks if isinstance(t, dtask.ControlledTask)]
            assert orbit and orbit[0].params["stopCondition"]["duration"] == builder.ON_STATION_S
            assert any(camp.flights[n.split(" ")[0]].side is not f.side
                       for n in groups if n.split(" ")[0] in camp.flights), "CAP with no enemy at all"
        all_rings = builder._rings(camp, Side.BLUE)
        for name, eg in groups.items():
            assert sum(isinstance(t, dtask.EscortTaskAction) for t in eg.points[0].tasks) <= 1
            ef = camp.flights.get(name.split(" ")[0])
            if ef and ef.side is Side.BLUE and ef.task in (Task.CAP, Task.ESCORT):
                # escorts hold and covering CAPs orbit outside the enemy's SAM cover
                for p in eg.points:
                    if p.name in ("HOLD", "CAP"):
                        assert all(_pos(p).dist(cn) >= r - 1 for cn, r in all_rings), (name, p.name)
    assert Task.CAP in seen


def test_skill_by_side_task_experience_and_fatigue(camp):
    from dcs.unit import Skill
    blue = next(f for f in camp.flights.values() if f.side is Side.BLUE and f.pilot_ids)
    red_cap = next(f for f in camp.flights.values() if f.side is Side.RED and f.task is Task.CAP)
    assert builder._skill(camp, blue, 0) is Skill.Good
    assert builder._skill(camp, red_cap, 0) is Skill.Good
    p = camp.pilots[blue.pilot_ids[0]]
    old = (p.sorties, p.fatigue)
    p.sorties = 20
    assert builder._skill(camp, blue, 0) is Skill.High
    p.fatigue = 0.7
    assert builder._skill(camp, blue, 0) is Skill.Good
    p.sorties, p.fatigue = old
