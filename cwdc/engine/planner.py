"""Air tasking: both sides plan every sortie of the turn (extensibility principle 1).

Phase 2 step 5, "air commanders take 2". Each side:
  1. mans its barrier-CAP stations (doctrine decides how many);
  2. scores its *needs* - CAS where an enemy attack threatens a sector, BAI/strike on
     enemy formations elsewhere, interdiction of the supply lines feeding enemy
     offensives, SEAD and strikes on air-defence and radar sites;
  3. fills the needs in priority order from squadrons that can fly them, building
     **packages**: the main flight plus a SEAD flight if the target sits under an
     enemy SAM umbrella, and an escort if enemy fighters can reach it.

Step 6 adds OCA: strikes on enemy airfields that host squadrons (engine.airbases).
Sortie rate is limited by each squadron's `sorties`, its ready aircraft and rested crews
(pilots, and WSOs for two-seat types - engine.crews), its
airbase's damage (a closed base flies nothing) and the period (night: CAP only).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from cwdc import theater as T
from cwdc.engine import airbases, crews, rng
from cwdc.model import (Campaign, CrewRole, Flight, FlightStatus, Objective, ObjectiveKind, Package,
                        PilotStatus, Pos, Side, Squadron, Task)

FLIGHT_SIZE = 2
SORTIES = {"player": 3, "default": 2}
STATION_NAMES = ["Alpha", "Bravo", "Charlie", "Delta"]
ENEMY_FIGHTER_REACH = 120_000     # enemy CAP stations this close to a target justify an escort
OCA_BASE, OCA_PER_SQUADRON = 2.0, 1.5

# Kept for callers that ask which objective kinds a task can attack.
TARGET_KINDS = {
    Task.STRIKE: {ObjectiveKind.DEPOT, ObjectiveKind.ARTILLERY, ObjectiveKind.ARMOR,
                  ObjectiveKind.COMMAND, ObjectiveKind.EWR, ObjectiveKind.RAIL,
                  ObjectiveKind.AIRBASE},
    Task.INTERDICTION: {ObjectiveKind.RAIL, ObjectiveKind.CONVOY, ObjectiveKind.DEPOT},
    Task.CAS: {ObjectiveKind.ARMOR, ObjectiveKind.ARTILLERY},
    Task.BAI: {ObjectiveKind.ARMOR, ObjectiveKind.ARTILLERY, ObjectiveKind.DEPOT,
               ObjectiveKind.CONVOY},
    Task.SEAD: {ObjectiveKind.SAM},
}

# Which squadron tasks can fly a need, in order of preference.
CAN_FLY = {
    Task.CAS: [Task.CAS, Task.BAI, Task.STRIKE],
    Task.BAI: [Task.BAI, Task.STRIKE, Task.CAS],
    Task.STRIKE: [Task.STRIKE, Task.BAI, Task.INTERDICTION],
    Task.INTERDICTION: [Task.INTERDICTION, Task.STRIKE, Task.BAI],
    Task.SEAD: [Task.SEAD],
}

DOCTRINE = {
    # NATO: one barrier CAP (the Hawk belt does the rest), escorts and SEAD for packages
    Side.BLUE: {"cap_stations": 1, "cas": 1.0, "bai": 1.0, "interdiction": 1.0, "strike": 1.0, "oca": 0.8,
                "escort": True, "sead_support": True},
    # Warsaw Pact: GCI-directed fighters on CAP, mass on the battlefield, few escorts,
    # and a doctrine of hitting NATO airfields to win the air battle on the ground
    Side.RED: {"cap_stations": 2, "cas": 1.2, "bai": 1.0, "interdiction": 0.8, "strike": 0.8, "oca": 1.0,
               "escort": False, "sead_support": True},
}


@dataclass
class Need:
    task: Task
    target: Objective
    score: float
    label: Optional[str] = None        # e.g. "OCA" for a strike on an airfield
    reason: str = ""                   # plain words for the player: why this target


# ----------------------------------------------------------------------------- helpers

def period(c: Campaign) -> str:
    h = c.time.hour
    if 5 <= h < 11:
        return "dawn"
    if 11 <= h < 17:
        return "day"
    if 17 <= h < 21:
        return "dusk"
    return "night"


def _flyable(sq: Squadron) -> bool:
    return bool(T.airframe_catalogue().get(sq.aircraft_type, {}).get("flyable"))


def _feeds_attack(c: Campaign, o: Objective) -> bool:
    """Does this logistics target supply a sector where its side is attacking?"""
    nodes = set()
    if o.supply_node:
        nodes.add(o.supply_node)
    if o.supply_link:
        lk = next((l for l in c.supply_links if l.id == o.supply_link), None)
        if lk:
            nodes.add(lk.b)
    return any(sec.attacker is o.side and sec.depots.get(o.side) in nodes
               for sec in c.sectors.values())


def _sam_cover(c: Campaign, target: Objective, enemy: Side) -> list[Objective]:
    return [o for o in c.objectives.values() if o.side is enemy and o.kind is ObjectiveKind.SAM
            and not o.destroyed and o.id != target.id and o.pos.dist(target.pos) <= o.threat_range_m]


def _enemy_fighters_near(c: Campaign, pos: Pos, enemy: Side) -> bool:
    return any(p.dist(pos) <= ENEMY_FIGHTER_REACH for p in c.cap_stations.get(enemy, []))


# ----------------------------------------------------------------------------- needs

def needs(c: Campaign, side: Side) -> list[Need]:
    from cwdc.engine.ground import power
    doc = DOCTRINE[side]
    enemy = side.enemy
    out: list[Need] = []
    live = [o for o in c.objectives.values() if o.side is enemy and not o.destroyed]

    for o in live:
        frac = len(o.alive_units) / len(o.units)
        if o.formation_id and o.kind in (ObjectiveKind.ARMOR, ObjectiveKind.ARTILLERY):
            sec = c.sectors.get(o.sector_id) if o.sector_id else None
            if sec is not None:
                own, theirs = power(c, sec.id, side), power(c, sec.id, enemy)
                if sec.attacker is enemy:                       # they are attacking us here
                    crit = theirs / max(own, 1.0)
                    out.append(Need(Task.CAS, o, (4 + 3 * crit) * frac * doc["cas"],
                                    reason=f"they are attacking in the {sec.name} "
                                           f"({crit:.1f} : 1 against our troops)"))
                    continue
            push = 2.0 if sec is not None and sec.attacker is side else 0.0   # support our offensive
            why = (f"supports our own attack in the {sec.name}" if push
                   else f"wears down their forces in the {sec.name}" if sec else "enemy formation")
            out.append(Need(Task.BAI, o, (2 + 0.5 * o.value + push) * frac * doc["bai"], reason=why))
        elif o.kind in (ObjectiveKind.RAIL, ObjectiveKind.CONVOY, ObjectiveKind.DEPOT):
            feeds = _feeds_attack(c, o)
            s = 2 + 0.5 * o.value + (4 if feeds else 0)
            out.append(Need(Task.INTERDICTION, o, s * frac * doc["interdiction"],
                            reason="cuts the supplies feeding their offensive" if feeds
                            else "cuts an enemy supply line"))
        elif o.kind is ObjectiveKind.SAM:
            out.append(Need(Task.SEAD, o, (1.5 + 0.3 * o.value) * frac,
                            reason="this missile site threatens our aircraft"))
        elif o.kind in (ObjectiveKind.EWR, ObjectiveKind.COMMAND):
            out.append(Need(Task.STRIKE, o, (1 + 0.3 * o.value) * frac * doc["strike"],
                            reason="blinds their radar / command network" if o.kind is ObjectiveKind.EWR
                            else "disrupts their command"))
        elif o.kind is ObjectiveKind.AIRBASE and o.airbase_id:
            ab = c.airbases[o.airbase_id]
            based = sum(1 for sq in c.squadrons.values() if sq.base_id == ab.id and sq.side is enemy)
            if based and ab.damage < airbases.CLOSED:            # nothing to gain on an empty / shut base
                s = (OCA_BASE + OCA_PER_SQUADRON * based) * (1 - ab.damage) * doc["oca"]
                out.append(Need(Task.STRIKE, o, s, label="OCA",
                                reason=f"{based} enemy squadron(s) fly from here"))
    return sorted(out, key=lambda n: -n.score)


# ----------------------------------------------------------------------------- planning

class _Pool:
    """Remaining sorties, aircraft and crews of one side's squadrons this turn."""

    def __init__(self, c: Campaign, side: Side, r):
        self.c = c
        self.left: dict[str, int] = {}
        self.airframes: dict[str, list] = {}
        self.pilots: dict[str, list] = {}
        self.wsos: dict[str, list] = {}
        for sq in sorted((s for s in c.squadrons.values() if s.side is side), key=lambda s: s.id):
            afs = c.squadron_airframes(sq.id)
            r.shuffle(afs)
            pls = sorted(crews.available(c, sq.id, CrewRole.PILOT), key=crews.schedule_order)
            wsos = sorted(crews.available(c, sq.id, CrewRole.WSO), key=crews.schedule_order)                 if crews.two_seat(sq) else None
            self.airframes[sq.id], self.pilots[sq.id], self.wsos[sq.id] = afs, pls, wsos
            n = sq.sorties or (SORTIES["player"] if sq.player_squadron else SORTIES["default"])
            if wsos is not None:
                n = min(n, len(wsos) // FLIGHT_SIZE)
            n = airbases.sorties(c, sq.base_id, n)                # a damaged base launches less
            self.left[sq.id] = min(n, len(afs) // FLIGHT_SIZE, len(pls) // FLIGHT_SIZE)

    def pick(self, tasks: list[Task], near: Pos | None = None, exclude: set[str] = frozenset()):
        """Best squadron able to fly one of `tasks`: specialists first, then nearest."""
        best = None
        for sq_id, n in self.left.items():
            if n <= 0 or sq_id in exclude:
                continue
            sq = self.c.squadrons[sq_id]
            # role discipline: only a need's first two acceptable tasks (no strike squadron on CAS)
            options = [t for t in tasks[:2] if t in sq.tasks]
            if not options:
                continue
            spec = sq.tasks.index(options[0])                   # lower = more of a specialist
            dist = self.c.airbases[sq.base_id].pos.dist(near) if near else 0.0
            key = (tasks.index(options[0]), spec, dist)
            if best is None or key < best[0]:
                best = (key, sq, options[0])
        return (best[1], best[2]) if best else (None, None)

    def take(self, sq: Squadron) -> tuple[list, list, list]:
        self.left[sq.id] -= 1
        afs = [self.airframes[sq.id].pop(0) for _ in range(FLIGHT_SIZE)]
        pls = [self.pilots[sq.id].pop(0) for _ in range(FLIGHT_SIZE)]
        wsos = [self.wsos[sq.id].pop(0) for _ in range(FLIGHT_SIZE)] if self.wsos[sq.id] is not None else []
        return afs, pls, wsos


def plan_turn(c: Campaign) -> None:
    """Replace the current ATO with a fresh plan for this turn."""
    c.packages.clear()
    c.flights.clear()
    r = rng(c.seed, c.turn, "plan")
    night = period(c) == "night"

    for side in Side:
        doc = DOCTRINE[side]
        pool = _Pool(c, side, r)

        # 1. barrier CAP
        stations = c.cap_stations.get(side, [])[:doc["cap_stations"]]
        for idx, st in enumerate(stations):
            sq, task = pool.pick([Task.CAP], near=st)
            if sq:
                pkg = _package(c, side, Task.CAP, f"Barrier CAP, station {STATION_NAMES[idx % 4]}", area=st)
                pkg.reason = "keeps enemy aircraft away from our side of the front"
                _flight(c, pool, sq, Task.CAP, pkg, area=st)
        if night or not c.at_war:
            continue      # the F-4E/MiGs of 1976 do little at night; no strikes before the war

        # 2. needs, in priority order, each with its escort and SEAD
        taken: set[str] = set()
        for need in needs(c, side):
            if need.target.id in taken:
                continue
            sq, task = pool.pick(CAN_FLY[need.task], near=need.target.pos)
            if sq is None:
                continue
            taken.add(need.target.id)
            pkg = _package(c, side, task, f"{need.label or task.value} - {need.target.name}",
                           target=need.target)
            pkg.reason = need.reason
            main = _flight(c, pool, sq, task, pkg, target=need.target)
            if task is Task.SEAD:
                continue
            if doc["sead_support"]:
                for sam in _sam_cover(c, need.target, side.enemy)[:1]:
                    ssq, _ = pool.pick([Task.SEAD], near=sam.pos)
                    if ssq and sam.id not in taken:
                        taken.add(sam.id)
                        _flight(c, pool, ssq, Task.SEAD, pkg, target=sam, supports=main.id)
            if (doc["escort"] or need.target.value >= 4) and _enemy_fighters_near(c, need.target.pos, side.enemy):
                esq, _ = pool.pick([Task.ESCORT, Task.CAP], near=need.target.pos)
                if esq:
                    _flight(c, pool, esq, Task.ESCORT, pkg, area=need.target.pos, supports=main.id)
            _describe(c, pkg)


def _package(c: Campaign, side: Side, task: Task, desc: str, target: Objective | None = None,
             area: Pos | None = None) -> Package:
    pid = c.next_id("P")
    pkg = Package(id=pid, side=side, task=task, description=desc, flight_ids=[],
                  target_id=target.id if target else None, area=area)
    c.packages[pid] = pkg
    return pkg


def _flight(c: Campaign, pool: _Pool, sq: Squadron, task: Task, pkg: Package,
            target: Objective | None = None, area: Pos | None = None,
            supports: str | None = None) -> Flight:
    afs, pls, wsos = pool.take(sq)
    n = c.counters.get(f"cs:{sq.id}:{c.turn}", 0) + 1
    c.counters[f"cs:{sq.id}:{c.turn}"] = n
    fid = c.next_id("F")
    loadouts = T.airframe_catalogue().get(sq.aircraft_type, {}).get("loadouts", {})
    f = Flight(id=fid, package_id=pkg.id, side=sq.side, squadron_id=sq.id, task=task,
               callsign=f"{sq.callsign} {n}", airframe_ids=[a.id for a in afs],
               pilot_ids=[p.id for p in pls], wso_ids=[p.id for p in wsos], target_id=target.id if target else None, area=area,
               loadout=loadouts.get(task.value) or loadouts.get("CAP" if task is Task.ESCORT else ""),
               player_eligible=sq.player_squadron and _flyable(sq), supports=supports)
    c.flights[fid] = f
    pkg.flight_ids.append(fid)
    return f


def _describe(c: Campaign, pkg: Package) -> None:
    support = [c.flights[f] for f in pkg.flight_ids[1:]]
    if support:
        pkg.description += " (with " + ", ".join(
            f"{'escort' if f.task is Task.ESCORT else f.task.value} {f.callsign}" for f in support) + ")"


def package_flights(c: Campaign, flight_id: str) -> list[Flight]:
    f = c.flights[flight_id]
    return [c.flights[x] for x in c.packages[f.package_id].flight_ids]


# ----------------------------------------------------------------------------- player seat

def assign_player(c: Campaign, flight_id: str) -> Flight:
    """Put the player pilot in the lead seat of a flight; the displaced pilot stands down."""
    f = c.flights[flight_id]
    if not f.player_eligible:
        raise ValueError(f"{flight_id} ({f.callsign}) is not a flight you can fly")
    player = c.player_pilot()
    if player.status is not PilotStatus.ACTIVE:
        raise ValueError(f"{player.name} is {player.status.value} and cannot fly")
    for other in c.flights.values():          # leave any flight chosen earlier this turn
        if other.id != f.id:
            release_player(c, other.id)
    if player.id not in f.pilot_ids:
        f.displaced_pilot_id = f.pilot_ids[0]
        f.pilot_ids[0] = player.id
    f.status = FlightStatus.ASSIGNED_PLAYER
    return f


def release_player(c: Campaign, flight_id: str) -> None:
    """Undo assign_player: the stood-down pilot takes the lead seat back."""
    f = c.flights[flight_id]
    player = c.player_pilot()
    if player.id in f.pilot_ids and f.displaced_pilot_id:
        f.pilot_ids[f.pilot_ids.index(player.id)] = f.displaced_pilot_id
        f.displaced_pilot_id = None
    if f.status is FlightStatus.ASSIGNED_PLAYER:
        f.status = FlightStatus.PLANNED
