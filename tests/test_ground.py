import pytest

from cwdc import theater as T
from cwdc.engine import ground, turn
from cwdc.engine.factory import new_campaign, sync_theater
from cwdc.model import AirframeStatus, Side


@pytest.fixture
def camp():
    return new_campaign("Ground", seed=3)


def test_campaign_has_ground_war(camp):
    assert set(camp.sectors) == {"goettingen", "fulda", "rhoen", "hof"}
    active = [f for f in camp.formations.values() if ground.active(camp, f)]
    assert {f.side for f in active} == {Side.BLUE, Side.RED}
    for sec in camp.sectors.values():
        assert ground.power(camp, sec.id, Side.BLUE) > 0 and ground.power(camp, sec.id, Side.RED) > 0
    assert camp.objectives["r_tank_vacha"].formation_id == "r_39gmrd"


@pytest.mark.parametrize("sector,point", [("fulda", (50.63, 9.90)),     # OP Alpha, west of border
                                          ("hof", (50.25, 11.45))])     # Bavaria, south of border
def test_positive_offset_moves_front_into_nato_territory(camp, sector, point):
    assert T.side_at(camp.front_line, *point) is Side.BLUE
    camp.sectors[sector].offset_km = 20
    ground.update_front(camp)
    assert T.side_at(camp.front_line, *point) is Side.RED
    camp.sectors[sector].offset_km = -20                  # NATO counter-offensive into the GDR
    ground.update_front(camp)
    assert T.side_at(camp.front_line, *point) is Side.BLUE


def test_formation_elements_move_with_the_front(camp):
    camp.sectors["fulda"].offset_km = 15
    ground.update_front(camp)
    ground.reposition_objectives(camp)
    for oid in ("b_acr_alpha", "r_tank_vacha", "r_arty_geisa"):
        o = camp.objectives[oid]
        assert 14_000 < o.pos.dist(o.base_pos) < 16_000
        assert T.side_at_pos(camp.front_line, o.pos) is o.side, oid


def test_deep_breakthrough_overruns_airbase_and_static_sites(camp):
    for sec in camp.sectors.values():
        sec.offset_km = 160
    ground.update_front(camp)
    ground.reposition_objectives(camp)
    sq = camp.squadrons["b_496tfs"]
    lines = ground.check_overruns(camp)
    assert camp.airbases["rheinmain"].side is Side.RED
    assert sq.base_id != "rheinmain" and camp.airbases[sq.base_id].side is Side.BLUE
    assert any(a.status is AirframeStatus.DESTROYED for a in camp.airframes.values()
               if a.squadron_id == sq.id)
    assert camp.objectives["b_hawk_fulda"].destroyed
    assert any("overrun" in line for line in lines)


def test_air_kills_cost_formation_strength_and_elements_rebuild(camp):
    from cwdc.engine.apply import destroy_unit
    f = camp.formations["r_39gmrd"]
    before = f.strength
    for i in range(4):
        destroy_unit(camp, f"r_tank_vacha#{i}")
    assert f.strength < before
    assert len(camp.objectives["r_tank_vacha"].alive_units) == 3
    ground.sync_visible(camp)
    assert len(camp.objectives["r_tank_vacha"].alive_units) > 3       # next battalion moves up
    f.strength = 0
    ground.sync_visible(camp)
    assert camp.objectives["r_tank_vacha"].destroyed                   # formation gone: element gone


def test_reinforcements_arrive_and_are_committed(camp):
    rf = camp.formations["b_8id"]
    assert not ground.active(camp, rf) and rf.sector_id == "auto"
    while camp.turn < rf.arrival_turn:
        turn.end_turn(camp)
    turn.end_turn(camp)
    assert rf.sector_id in camp.sectors and ground.active(camp, rf)


def test_ground_war_is_deterministic_and_bounded():
    a, b = new_campaign("D", seed=9), new_campaign("D", seed=9)
    for _ in range(24):                  # six days
        turn.end_turn(a)
        turn.end_turn(b)
    assert [s.offset_km for s in a.sectors.values()] == [s.offset_km for s in b.sectors.values()]
    for s in a.sectors.values():
        assert ground.MIN_OFFSET_KM <= s.offset_km < 150, s.id
    assert all(f.strength >= 0 for f in a.formations.values())


def test_sync_theater_adds_ground_war_to_older_campaigns(camp):
    camp.sectors.clear()
    camp.formations.clear()
    camp.base_front_line = []
    o = camp.objectives["r_tank_vacha"]
    o.formation_id = o.sector_id = o.base_pos = None
    added = sync_theater(camp)
    assert "Fulda Gap" in added and camp.formations and camp.base_front_line
    assert o.formation_id == "r_39gmrd" and o.base_pos is not None
