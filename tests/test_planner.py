import dcs
import pytest

from cwdc.engine import planner, resolver, turn
from cwdc.engine.factory import new_campaign
from cwdc.missiongen import builder
from cwdc.model import ObjectiveKind, Side, Task


@pytest.fixture
def camp():
    c = new_campaign("Packages", seed=4)
    turn.end_turn(c)                    # offensives under way: CAS / interdiction needs exist
    return c


def _packages(c, side):
    return [p for p in c.packages.values() if p.side is side]


def test_packages_have_escort_and_sead_support(camp):
    blue = _packages(camp, Side.BLUE)
    support = [camp.flights[f] for p in blue for f in p.flight_ids[1:]]
    assert any(f.task is Task.ESCORT for f in support)
    for f in support:
        main = camp.flights[f.supports]
        assert main.package_id == f.package_id and main.supports is None
        if f.task is Task.SEAD:                   # suppresses a SAM covering the package target
            sam, tgt = camp.objectives[f.target_id], camp.objectives[main.target_id]
            assert sam.kind is ObjectiveKind.SAM and sam.pos.dist(tgt.pos) <= sam.threat_range_m


def test_both_sides_man_their_cap_stations(camp):
    for side, n in ((Side.BLUE, 1), (Side.RED, 2)):
        caps = [f for f in camp.flights.values() if f.side is side and f.task is Task.CAP]
        assert len(caps) == n == planner.DOCTRINE[side]["cap_stations"]


def test_role_discipline_no_strike_squadron_on_cas(camp):
    for f in camp.flights.values():
        sq = camp.squadrons[f.squadron_id]
        if f.task is Task.CAS:
            assert Task.CAS in sq.tasks or Task.BAI in sq.tasks


def test_cas_goes_to_sectors_under_attack(camp):
    for f in camp.flights.values():
        if f.task is Task.CAS:
            tgt = camp.objectives[f.target_id]
            assert camp.sectors[tgt.sector_id].attacker is tgt.side


def test_sortie_limits_respected(camp):
    for sq in camp.squadrons.values():
        n = sum(1 for f in camp.flights.values() if f.squadron_id == sq.id)
        limit = sq.sorties or (planner.SORTIES["player"] if sq.player_squadron else planner.SORTIES["default"])
        assert n <= limit
        used = [a for f in camp.flights.values() if f.squadron_id == sq.id for a in f.airframe_ids]
        assert len(used) == len(set(used))          # no airframe in two flights


def test_night_is_cap_only():
    c = new_campaign("Night", seed=4)
    for _ in range(3):                               # 06 -> 12 -> 18 -> 00
        turn.end_turn(c)
    assert planner.period(c) == "night"
    assert {f.task for f in c.flights.values()} == {Task.CAP}


def test_escort_reduces_strike_losses():
    lost = {True: 0, False: 0}
    for seed in range(40):
        c = new_campaign("Esc", seed=seed)
        turn.end_turn(c)
        pkg = next((p for p in c.packages.values() if p.side is Side.BLUE and p.target_id
                    and any(c.flights[f].task is Task.ESCORT for f in p.flight_ids)), None)
        if pkg is None:
            continue
        main = c.flights[pkg.flight_ids[0]]
        escort = next(c.flights[f] for f in pkg.flight_ids if c.flights[f].task is Task.ESCORT)
        red_cap = [f for f in c.flights.values() if f.side is Side.RED and f.task is Task.CAP]
        base = c.turn
        for roll in range(5):                     # several resolver rolls per campaign
            c.turn = base + roll
            for with_escort in (True, False):
                flights = [main] + ([escort] if with_escort else []) + red_cap
                res = resolver.resolve(c, flights)
                lost[with_escort] += sum(len(r.airframes_lost) for r in res if r.flight_id == main.id)
        c.turn = base
    assert lost[True] < lost[False]


def _package_with(c, task):
    for p in c.packages.values():
        fl = [c.flights[f] for f in p.flight_ids]
        if p.side is Side.BLUE and any(f.task is task and f.player_eligible for f in fl):
            return fl
    pytest.skip(f"no blue package with a player-eligible {task.value} flight this turn")


def test_player_escort_mission_launches_bombers_with_player(camp, tmp_path):
    flights = _package_with(camp, Task.ESCORT)
    escort = next(f for f in flights if f.task is Task.ESCORT)
    main = camp.flights[escort.supports]
    planner.assign_player(camp, escort.id)
    rec = builder.build(camp, escort.id, tmp_path, start="runway")
    assert main.id in rec.spawned_flights
    m = dcs.Mission()
    m.load_file(rec.miz_path)
    g = m.find_group(f"{main.id} {main.callsign}")
    base = camp.airbases[camp.squadrons[escort.squadron_id].base_id].pos
    p0 = g.units[0].position
    assert ((p0.x - base.x) ** 2 + (p0.y - base.z) ** 2) ** 0.5 < 10_000   # overhead the player's base
    assert g.points[0].ETA == builder.TAXI_TIME["runway"]                  # launches after the taxi


def test_ai_escort_follows_ai_package(camp, tmp_path):
    flights = _package_with(camp, Task.ESCORT)
    escort = next(f for f in flights if f.task is Task.ESCORT)
    main = camp.flights[escort.supports]
    if not main.player_eligible:
        pytest.skip("main flight not player-eligible")
    planner.assign_player(camp, main.id)
    rec = builder.build(camp, main.id, tmp_path, bot=True)
    m = dcs.Mission()
    m.load_file(rec.miz_path)
    g = m.find_group(f"{escort.id} {escort.callsign}")
    main_group = m.find_group(f"{main.id} {main.callsign}")
    follows = [t for t in g.points[0].tasks if getattr(t, "Id", "") == "Escort"
               and t.params.get("groupId") == main_group.id]
    assert follows, "AI escort must be tied to its package's main flight"
