"""Air tasking: both sides plan every sortie of the turn (extensibility principle 1)."""
from __future__ import annotations

from cwdc import theater as T
from cwdc.engine import rng
from cwdc.model import (Campaign, Flight, FlightStatus, Objective, ObjectiveKind, Package,
                        PilotStatus, Side, Squadron, Task)

FLIGHT_SIZE = 2
SORTIES = {"player": 3, "default": 2}
STATION_NAMES = ["Alpha", "Bravo", "Charlie", "Delta"]

TARGET_KINDS = {
    Task.STRIKE: {ObjectiveKind.DEPOT, ObjectiveKind.ARTILLERY, ObjectiveKind.ARMOR,
                  ObjectiveKind.COMMAND, ObjectiveKind.EWR},
    Task.CAS: {ObjectiveKind.ARMOR, ObjectiveKind.ARTILLERY},
    Task.BAI: {ObjectiveKind.ARMOR, ObjectiveKind.ARTILLERY, ObjectiveKind.DEPOT,
               ObjectiveKind.CONVOY},
    Task.SEAD: {ObjectiveKind.SAM},
}


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


def _pick_target(c: Campaign, side: Side, task: Task, taken: set[str],
                 prefer_near: list[Objective]) -> Objective | None:
    kinds = TARGET_KINDS[task]
    cands = [o for o in c.objectives.values()
             if o.side is side.enemy and o.kind in kinds and not o.destroyed and o.id not in taken]
    if not cands:
        return None

    def score(o: Objective) -> float:
        s = o.value * len(o.alive_units) / len(o.units)
        if task is Task.SEAD and prefer_near:
            # SAMs covering targets we are already hitting come first
            if any(o.pos.dist(t.pos) <= o.threat_range_m for t in prefer_near):
                s += 10
        return s

    return max(cands, key=score)


def plan_turn(c: Campaign) -> None:
    """Replace the current ATO with a fresh plan for this turn."""
    c.packages.clear()
    c.flights.clear()
    r = rng(c.seed, c.turn, "plan")
    night = period(c) == "night"

    for side in Side:
        taken: set[str] = set()
        cap_count = 0
        deferred: list[tuple[Squadron, Task, list, list]] = []
        pools = {}
        for sq in sorted((s for s in c.squadrons.values() if s.side is side), key=lambda s: s.id):
            airframes = c.squadron_airframes(sq.id)
            pilots = [p for p in c.squadron_pilots(sq.id) if not p.is_player]
            r.shuffle(airframes)
            pilots.sort(key=lambda p: -p.sorties)
            pools[sq.id] = (airframes, pilots)
            tasks = [Task.CAP] if night else [t for t in sq.tasks if t in TARGET_KINDS or t is Task.CAP]
            if night and Task.CAP not in sq.tasks:
                continue
            n = SORTIES["player"] if sq.player_squadron else SORTIES["default"]
            for k in range(n):
                task = tasks[k % len(tasks)]
                if task is Task.SEAD:
                    deferred.append((sq, task, airframes, pilots))
                    continue
                if task is Task.CAP:
                    stations = c.cap_stations.get(side, [])
                    if not stations:
                        continue
                    idx = cap_count % len(stations)
                    cap_count += 1
                    _add_flight(c, sq, task, airframes, pilots, area_idx=idx)
                else:
                    tgt = _pick_target(c, side, task, taken, [])
                    if tgt:
                        taken.add(tgt.id)
                        _add_flight(c, sq, task, airframes, pilots, target=tgt)

        hit = [c.objectives[t] for t in taken]
        for sq, task, airframes, pilots in deferred:
            tgt = _pick_target(c, side, task, taken, hit)
            if tgt:
                taken.add(tgt.id)
                _add_flight(c, sq, task, airframes, pilots, target=tgt)


def _add_flight(c: Campaign, sq: Squadron, task: Task, airframes: list, pilots: list,
                target: Objective | None = None, area_idx: int | None = None) -> Flight | None:
    if len(airframes) < FLIGHT_SIZE or len(pilots) < FLIGHT_SIZE:
        return None
    afs = [airframes.pop(0) for _ in range(FLIGHT_SIZE)]
    pls = [pilots.pop(0) for _ in range(FLIGHT_SIZE)]
    n = c.counters.get(f"cs:{sq.id}:{c.turn}", 0) + 1
    c.counters[f"cs:{sq.id}:{c.turn}"] = n
    fid = c.next_id("F")
    pid = c.next_id("P")
    area = None
    if task is Task.CAP:
        area = c.cap_stations[sq.side][area_idx]
        desc = f"Barrier CAP, station {STATION_NAMES[area_idx % 4]}"
    else:
        desc = f"{task.value} - {target.name}"
    f = Flight(id=fid, package_id=pid, side=sq.side, squadron_id=sq.id, task=task,
               callsign=f"{sq.callsign} {n}", airframe_ids=[a.id for a in afs],
               pilot_ids=[p.id for p in pls], target_id=target.id if target else None, area=area,
               loadout=T.airframe_catalogue().get(sq.aircraft_type, {}).get("loadouts", {}).get(task.value),
               player_eligible=sq.player_squadron and _flyable(sq))
    c.flights[fid] = f
    c.packages[pid] = Package(id=pid, side=sq.side, task=task, description=desc, flight_ids=[fid],
                              target_id=f.target_id, area=area)
    return f


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
