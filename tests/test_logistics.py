import json

import dcs
import pytest

from cwdc.engine import apply
from cwdc.engine import ground, logistics, planner, turn
from cwdc.engine.factory import new_campaign
from cwdc.ingest.flown import results_from_dcs
from cwdc.missiongen import builder
from cwdc.missiongen.runtime_inject import config_lua
from cwdc.model import Side, Task
from tests.luamock import mission_runtime


@pytest.fixture
def camp():
    return new_campaign("Logistics", seed=5)


def _link(c, lid):
    return logistics.link(c, lid)


def test_supply_flows_from_rail_heads_to_depots(camp):
    eis = camp.supply_nodes["rn_eisenach"]
    eis.stock = 0
    logistics.flow(camp)
    lk = _link(camp, "l_erf_eis")
    assert eis.stock == pytest.approx(lk.capacity)          # one turn of rail capacity
    assert _link(camp, "l_lei_erf").flow > 0                  # hub restocked from the rail head


def test_damaged_link_carries_less_and_is_repaired(camp):
    lk = _link(camp, "l_erf_eis")
    camp.supply_nodes["rn_eisenach"].stock = 0
    lk.damaged = 0.75
    logistics.flow(camp)
    assert lk.flow == pytest.approx(lk.capacity * 0.25)
    logistics.repair(camp)
    assert lk.damaged == pytest.approx(0.75 - logistics.REPAIR_PER_TURN)


def test_bombing_a_rail_yard_cuts_its_link_and_it_rebuilds(camp):
    yard = camp.objectives["r_yard_gotha"]
    for i in range(3):
        apply.destroy_unit(camp, f"r_yard_gotha#{i}")
    assert _link(camp, "l_erf_eis").damaged == pytest.approx(3 / len(yard.units))
    for _ in range(5):
        logistics.repair(camp)
        logistics.sync_interdiction(camp)
    assert _link(camp, "l_erf_eis").damaged == 0
    assert len(yard.alive_units) == len(yard.units)


def test_hitting_a_depot_burns_stock(camp):
    node = camp.supply_nodes["rn_meiningen"]
    before = node.stock
    apply.destroy_unit(camp, "r_depot_meiningen#0")
    assert node.stock < before


def test_empty_depot_starves_formations_and_weakens_the_attack(camp):
    full = new_campaign("Logistics", seed=5)
    for _ in range(4):                       # Eisenach depot empty and its rail line cut
        camp.supply_nodes["rn_eisenach"].stock = 0
        _link(camp, "l_erf_eis").damaged = 1.0
        for c in (camp, full):
            logistics.flow(c)
            logistics.consume(c)
    assert camp.formations["r_39gmrd"].supply < 0.2
    assert full.formations["r_39gmrd"].supply > 0.9
    assert ground.power(camp, "fulda", Side.RED) < ground.power(full, "fulda", Side.RED)


def test_lost_depot_falls_back_to_nearest_friendly_node(camp):
    camp.supply_nodes["bn_hersfeld"].side = Side.RED
    depot = logistics.sector_depot(camp, camp.sectors["fulda"], Side.BLUE)
    assert depot.side is Side.BLUE and depot.id in ("bn_alsfeld", "bn_kassel")


def _at_war(c, turns=1):
    """Interdiction is planned once offensives are under way (they decide what to cut)."""
    for _ in range(turns):
        turn.end_turn(c)
    return c


def test_planner_flies_interdiction_against_logistics(camp):
    _at_war(camp)
    inter = [f for f in camp.flights.values() if f.task is Task.INTERDICTION]
    assert Side.BLUE in {f.side for f in inter}
    for f in inter:
        assert camp.objectives[f.target_id].kind.value in ("rail", "convoy", "depot")


def test_rail_yard_spawns_statics_and_ingest_reads_them(camp, tmp_path):
    _at_war(camp)
    f = next(fl for fl in camp.flights.values()
             if fl.side is Side.BLUE and fl.task is Task.INTERDICTION
             and camp.objectives[fl.target_id].kind.value == "rail")
    rec = builder.build(camp, f.id, tmp_path, bot=True)
    statics = [n for n, e in rec.manifest.items() if e.get("static")]
    assert statics and all(n.startswith(f.target_id) for n in statics)
    m = dcs.Mission()
    m.load_file(rec.miz_path)
    names = {u.name for coal in m.coalition.values() for co in coal.countries.values()
             for g in co.static_group for u in g.units}
    assert set(statics) <= names
    g = m.find_group(f"{f.id} {f.callsign}")
    assert any(getattr(t, "Id", "") == "Bombing" for p in g.points for t in p.tasks)

    # snapshot: one structure destroyed (gone), the others standing
    vehicles = [{"name": n, "life": 10, "life0": 10} for n, e in rec.manifest.items()
                if e["kind"] == "objective" and not e.get("static")]
    snap = {"groups": [{"name": "all", "units": vehicles}],
            "statics": [{"name": n, "exists": True, "life": 10} for n in statics[1:]]}
    _, destroyed = results_from_dcs(camp, rec, {"events": [], "snapshot": snap})
    assert destroyed == [statics[0]]          # only the missing structure


def test_runtime_snapshots_statics():
    lua = mission_runtime(config_lua("s-1", [], tracked_statics=["yard#0", "yard#1"]),
                          pre_runtime='mkstatic("yard#0", 10)')
    snap = json.loads(lua.eval("CWDC_export()"))
    lua.execute("CWDC.emit('SNAP', CWDC.snapshot())")
    data = json.loads(lua.eval("CWDC.encode(CWDC.snapshot())"))
    by = {s["name"]: s for s in data["statics"]}
    assert by["yard#0"]["exists"] is True and by["yard#0"]["life"] == 10
    assert by["yard#1"]["exists"] is False
    assert snap["mission_id"] == "s-1"


def test_logistics_turn_runs_in_end_turn(camp):
    lines = turn.end_turn(camp)
    assert any(l.flow > 0 for l in camp.supply_links)
    assert isinstance(lines, list)
