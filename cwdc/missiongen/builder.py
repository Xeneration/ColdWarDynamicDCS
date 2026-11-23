"""Build a DCS mission for the player's chosen flight from the campaign state.

Extensibility principle 3: the mission contains the player's flight plus everything
that overlaps it in time and space (enemy CAP that makes contact, the target and
nearby SAMs, enemy strikes into the player's CAP sector, friendly flights on nearby
targets or covering the route). Everything else stays abstract and is resolved by
the engine.
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from pathlib import Path

from dcs import task as dtask
from dcs.mission import Mission, StartType
from dcs.planes import plane_map
from dcs.unit import Skill
from dcs.vehicles import vehicle_map

from cwdc import theater as T
from cwdc.engine import rng
from cwdc.missiongen import coalitions, loadouts
from cwdc.missiongen.runtime_inject import inject_runtime
from cwdc.model import Campaign, Flight, MissionRecord, Objective, ObjectiveKind, Pos, Side, Task

ENEMY_CAP_RADIUS = 90_000       # enemy CAP stations this close to the route may make contact
P_ENEMY_CAP_CONTACT = 0.5       # chance each such CAP flight is actually in the player's fight
MAX_ENEMY_CAP = 2               # never more than this many enemy CAP flights in one mission
CAP_WAVE_DELAY = 600            # s; the second enemy CAP flight arrives as a later wave
FRIENDLY_CAP_RADIUS = 60_000    # friendly CAP stations this close to the route join the mission
ENEMY_STRIKE_RADIUS = 60_000    # enemy strikes on targets this close to a player CAP station
OBJECTIVE_RADIUS = 25_000       # objectives this close to the player's area/target are spawned
AIR_START_DIST = 90_000         # m from the destination for `--start air`
EWR_RADIUS = 200_000            # radar sites this close to the destination are always spawned
FORWARD_CAP_DIST = 30_000       # friendly CAP covering a strike orbits this far short of the target
CAP_ENGAGE_RADIUS = 60_000      # ...and engages enemy aircraft within this radius of the target
TAXI_TIME = {"ramp": 600, "runway": 90, "air": 0}   # s before the player is en route
FLIGHT_SPACING = 3_000          # m lateral separation between spawned flights
UNIT_SPACING = 250              # m between aircraft of one flight

START_TYPES = {"ramp": StartType.Warm, "runway": StartType.Runway}

DCS_TASK = {Task.CAP: dtask.CAP, Task.ESCORT: dtask.Escort, Task.STRIKE: dtask.GroundAttack,
            Task.BAI: dtask.GroundAttack, Task.INTERDICTION: dtask.GroundAttack,
            Task.CAS: dtask.CAS, Task.SEAD: dtask.SEAD}

# DCS western aircraft callsigns (index used in the unit callsign table)
WESTERN_CALLSIGNS = {"Enfield": 1, "Springfield": 2, "Uzi": 3, "Colt": 4, "Dodge": 5,
                     "Ford": 6, "Chevy": 7, "Pontiac": 8}


@dataclass
class Plan:
    flights: list[Flight] = field(default_factory=list)       # all flights to spawn (player first)
    objectives: list[Objective] = field(default_factory=list)
    delay: dict[str, int] = field(default_factory=dict)        # flight id -> spawn delay (s)


def _base(c: Campaign, f: Flight) -> Pos:
    return c.airbases[c.squadrons[f.squadron_id].base_id].pos


def _dest(c: Campaign, f: Flight) -> Pos:
    return f.area if f.area else c.objectives[f.target_id].pos


def _route(c: Campaign, f: Flight) -> list[Pos]:
    base, dest = _base(c, f), _dest(c, f)
    n = max(1, int(base.dist(dest) // 20_000))
    return [Pos(x=base.x + (dest.x - base.x) * i / n, z=base.z + (dest.z - base.z) * i / n)
            for i in range(n + 1)]


def _toward(a: Pos, b: Pos, dist: float) -> Pos:
    """Point `dist` metres from a towards b (clamped to b)."""
    d = a.dist(b)
    if d <= dist or d == 0:
        return b
    return Pos(x=a.x + (b.x - a.x) * dist / d, z=a.z + (b.z - a.z) * dist / d)


def _offset(p: Pos, toward: Pos, lateral: float) -> Pos:
    """Shift p sideways (perpendicular to the p->toward line)."""
    d = p.dist(toward) or 1.0
    ux, uz = (toward.x - p.x) / d, (toward.z - p.z) / d
    return Pos(x=p.x - uz * lateral, z=p.z + ux * lateral)


def select(c: Campaign, player: Flight) -> Plan:
    plan = Plan(flights=[player])
    if player.package_id in c.packages:             # the player's whole package flies together
        plan.flights += [c.flights[x] for x in c.packages[player.package_id].flight_ids
                         if x != player.id and x in c.flights]
    route = _route(c, player)
    dest = _dest(c, player)
    enemy = player.side.enemy
    objs: dict[str, Objective] = {}
    r = rng(c.seed, c.turn, f"contact:{player.id}")

    # enemy CAP: those near the route roll for contact; at most MAX_ENEMY_CAP, nearest first
    caps = sorted((f for f in c.flights.values() if f.side is enemy and f.task is Task.CAP
                   and min(p.dist(f.area) for p in route) <= ENEMY_CAP_RADIUS),
                  key=lambda f: f.area.dist(dest))
    contact = [f for f in caps if r.random() < P_ENEMY_CAP_CONTACT][:MAX_ENEMY_CAP]
    if caps and not contact:
        contact = caps[:1]          # always some opposition
    for i, f in enumerate(contact):
        plan.flights.append(f)
        if i:
            plan.delay[f.id] = CAP_WAVE_DELAY * i

    for f in c.flights.values():
        if f.id == player.id or f in plan.flights:
            continue
        if f.side is enemy and f.target_id and player.task is Task.CAP:
            if c.objectives[f.target_id].pos.dist(dest) <= ENEMY_STRIKE_RADIUS:
                plan.flights.append(f)
                objs[f.target_id] = c.objectives[f.target_id]
        elif f.side is player.side and f.target_id and player.target_id:
            if c.objectives[f.target_id].pos.dist(dest) <= OBJECTIVE_RADIUS:
                plan.flights.append(f)
                objs[f.target_id] = c.objectives[f.target_id]
        elif f.side is player.side and f.task is Task.CAP and player.task is not Task.CAP:
            if min(p.dist(f.area) for p in route) <= FRIENDLY_CAP_RADIUS:
                plan.flights.append(f)

    if player.target_id:
        objs[player.target_id] = c.objectives[player.target_id]
    for o in c.objectives.values():
        if o.destroyed:
            continue
        near = o.pos.dist(dest) <= OBJECTIVE_RADIUS
        covers_route = o.threat_range_m and any(p.dist(o.pos) <= o.threat_range_m for p in route)
        radar = o.kind is ObjectiveKind.EWR and o.pos.dist(dest) <= EWR_RADIUS
        if near or covers_route or radar:
            objs[o.id] = o
    for f in plan.flights:                          # escorted / SEAD-supported targets
        if f.target_id and f.target_id not in objs and not c.objectives[f.target_id].destroyed:
            objs[f.target_id] = c.objectives[f.target_id]
    plan.objectives = list(objs.values())
    # spawn order: player first, then main flights, then the flights that support them
    plan.flights.sort(key=lambda f: (f is not player, f.supports is not None))
    return plan


def _cap_station(c: Campaign, f: Flight, player: Flight) -> Pos:
    """Where a CAP flight orbits in this mission. Friendly CAP covering a player strike moves
    forward to just short of the target; everyone else flies their planned station."""
    if f is not player and f.side is player.side and (player.target_id or player.task is Task.ESCORT):
        return _toward(_dest(c, player), _base(c, player), FORWARD_CAP_DIST)
    return f.area


def _unit_name(f: Flight, i: int) -> str:
    return f"{f.id}-{i + 1} {f.callsign}"


def _apply_loadout(group, f: Flight, type_id: str, dcs_task) -> None:
    if f.loadout and f.loadout != "default" and loadouts.loadouts(type_id):
        loadouts.apply(group, f.loadout, type_id)
        return
    plane = plane_map[type_id]
    for tk in (dcs_task, dtask.GroundAttack, dtask.CAS, dtask.CAP):
        if plane.loadout(tk):
            group.load_task_default_loadout(tk)
            return


def _set_callsign(group, f: Flight, squadron_callsign: str) -> None:
    """DCS radio callsign, e.g. Colt 2-1, matching the campaign callsign."""
    idx = WESTERN_CALLSIGNS.get(squadron_callsign)
    if idx is None:
        return
    try:
        flight_no = int(f.callsign.rsplit(" ", 1)[1])
    except (IndexError, ValueError):
        flight_no = 1
    for i, u in enumerate(group.units):
        u.callsign_dict = {1: idx, 2: flight_no, 3: i + 1,
                           "name": f"{squadron_callsign}{flight_no}{i + 1}"}


def _delay(group, seconds: int) -> None:
    """DCS takes an air group's spawn time from its first waypoint's ETA."""
    group.start_time = seconds
    group.points[0].ETA = seconds
    group.points[0].ETA_locked = True


def _spread_units(group, toward: Pos, alt_step: float = 30) -> None:
    """Air-started units sit in echelon instead of on top of each other."""
    for i, u in enumerate(group.units):
        if i == 0:
            continue
        here = Pos(x=u.position.x, z=u.position.y)
        u.position = T.to_point(_offset(here, toward, UNIT_SPACING * i))
        u.alt += alt_step * i


BOT_TIME_LIMIT = 2 * 3600       # s; bot missions end at the latest after this much mission time


def build(c: Campaign, player_flight_id: str, out_dir: Path, start: str = "runway",
          bot: bool = False) -> MissionRecord:
    """Build the mission around `player_flight_id`.

    bot=True: an AI-only mission. The focus flight is flown by its AI crew, there is no
    player slot (DCS starts in spectator view), and the mission ends itself once the
    focus flight has landed or been lost, or after BOT_TIME_LIMIT.
    """
    import logging
    logging.getLogger().setLevel(logging.CRITICAL)
    if start not in TAXI_TIME:
        raise ValueError(f"start must be one of {sorted(TAXI_TIME)}")
    player = c.flights[player_flight_id]
    plan = select(c, player)
    terrain = T.terrain()
    m = Mission(terrain)
    coalitions.setup(m)
    m.start_time = c.time
    m.weather.clouds_density = 3
    m.weather.clouds_base = 2500
    m.weather.wind_at_ground.speed = 3

    tag = f"-{c.instance}" if c.instance else ""
    mission_id = f"{c.id}{tag}-t{c.turn:03d}-{player.id}"
    manifest: dict[str, dict] = {}
    tracked: list[str] = []
    obj_groups: dict[str, object] = {}
    tracked_statics: list[str] = []
    static_targets: dict[str, Pos] = {}
    groups: dict[str, object] = {}
    spawn_at: dict[str, Pos] = {}

    # time for the player to reach the destination: AI strikes are synchronised to it
    pspec = T.airframe_catalogue().get(c.squadrons[player.squadron_id].aircraft_type, {})
    p_speed = pspec.get("cruise_kmh", 800) / 3.6
    p_dist = min(AIR_START_DIST, _base(c, player).dist(_dest(c, player))) if start == "air" \
        else _base(c, player).dist(_dest(c, player))
    player_eta = TAXI_TIME[start] + p_dist / p_speed
    player_base = _base(c, player)
    player_spawn = _toward(_dest(c, player), player_base, AIR_START_DIST) if start == "air" else player_base

    # --- objectives (spawn first so strike flights can target their group ids)
    for o in plan.objectives:
        alive = [(i, u) for i, u in enumerate(o.units) if u.alive]
        if not alive:
            continue
        country = m.country(o.country)
        gname = f"{o.id} {o.name}"
        vehicles = [(i, u) for i, u in alive if u.type in vehicle_map]
        structures = [(i, u) for i, u in alive if u.type not in vehicle_map]
        if vehicles:
            i0, u0 = vehicles[0]
            g = m.vehicle_group(country, gname, vehicle_map[u0.type],
                                T.to_point(Pos(x=o.pos.x + u0.dx, z=o.pos.z + u0.dz)), heading=0)
            g.units[0].name = f"{o.id}#{i0}"
            for i, u in vehicles[1:]:
                v = m.vehicle(f"{o.id}#{i}", vehicle_map[u.type])
                v.position = T.to_point(Pos(x=o.pos.x + u.dx, z=o.pos.z + u.dz))
                g.add_unit(v)
            for unit in g.units:
                idx = int(unit.name.split("#")[1])
                manifest[unit.name] = {"kind": "objective", "objective": o.id, "unit": idx}
            obj_groups[o.id] = g
            tracked.append(gname)
        for i, u in structures:              # rail yards, depots: one static per structure
            key = f"{o.id}#{i}"
            sg = m.static_group(country, key, static_type(u.type),
                                T.to_point(Pos(x=o.pos.x + u.dx, z=o.pos.z + u.dz)))
            sg.units[0].name = key
            manifest[key] = {"kind": "objective", "objective": o.id, "unit": i, "static": True}
            tracked_statics.append(key)
        if structures:
            static_targets[o.id] = o.pos

    # --- flights
    for k, f in enumerate(plan.flights):
        sq = c.squadrons[f.squadron_id]
        spec = T.airframe_catalogue().get(sq.aircraft_type, {})
        type_cls = plane_map[sq.aircraft_type]
        country = m.country(sq.country)
        base = c.airbases[sq.base_id]
        airport = terrain.airports[base.dcs_name]
        dcs_task = DCS_TASK.get(f.task, dtask.CAP)
        gname = f"{f.id} {f.callsign}"
        alt = spec.get("cruise_alt_m", 5000) + 300 * (k % 4)     # deconflict altitudes
        speed = spec.get("cruise_kmh", 800)
        is_player = f is player
        dest = _dest(c, f)
        n = len(f.airframe_ids)

        if is_player and start in START_TYPES:
            slots = _adjacent_slots(airport, type_cls, n) if start == "ramp" else None
            g = m.flight_group_from_airport(country, gname, type_cls, airport, maintask=dcs_task,
                                            start_type=START_TYPES[start], group_size=n,
                                            parking_slots=slots)
        else:
            main = c.flights.get(f.supports) if f.supports else None
            if is_player:                                   # air start en route
                spawn = _toward(dest, base.pos, AIR_START_DIST)
            elif f.id == player.supports:
                # the player escorts / supports this flight: it launches with the player
                spawn = player_spawn
                if start in START_TYPES:
                    plan.delay.setdefault(f.id, TAXI_TIME[start])
            elif f.task is Task.ESCORT and main is not None and main.id in spawn_at:
                spawn = spawn_at[main.id]                   # escort forms up on its package
                if main.id in plan.delay:
                    plan.delay.setdefault(f.id, plan.delay[main.id])
            elif f.task in (Task.CAP, Task.ESCORT):
                spawn = _cap_station(c, f, player)
            else:                                           # AI strike: arrive with the player
                ai_dist = (speed / 3.6) * player_eta
                spawn = _toward(dest, base.pos, ai_dist)
            spawn_at[f.id] = spawn
            spawn = _offset(spawn, dest, FLIGHT_SPACING * k)
            g = m.flight_group_inflight(country, gname, type_cls, T.to_point(spawn), altitude=alt,
                                        speed=speed, maintask=dcs_task, group_size=n)
            _spread_units(g, dest)
        groups[f.id] = g
        if f.id in plan.delay:
            _delay(g, plan.delay[f.id])
        _apply_loadout(g, f, sq.aircraft_type, dcs_task)
        _set_callsign(g, f, sq.callsign)

        for i, unit in enumerate(g.units):
            unit.name = _unit_name(f, i)
            af = c.airframes[f.airframe_ids[i]]
            manifest[unit.name] = {"kind": "airframe", "flight": f.id, "airframe": af.id,
                                   "pilot": f.pilot_ids[i]}
            if sq.aircraft_type == "F-4E-45MC":
                unit.set_property("Quality", af.quality)
            if bot or not (is_player and i == 0):
                unit.skill = Skill.Good if sq.side is Side.BLUE else Skill.Average
        if is_player and not bot:
            g.units[0].set_player()
        ai_flown = bot or not is_player      # in a bot mission the focus flight is AI as well

        # AI bombers stay on task: no jettison, attack only their assigned target, evade threats
        if ai_flown and f.target_id:
            opts = g.points[0].tasks
            # assigned target first, but free to shoot back at fighters (they keep their bombs)
            opts.append(dtask.OptROE(dtask.OptROE.Values.OpenFireWeaponFree))
            opts.append(dtask.OptReactOnThreat(dtask.OptReactOnThreat.Values.EvadeFire))
            opts.append(dtask.OptRestrictJettison(True))

        # waypoints
        escorted = groups.get(f.supports) if f.task is Task.ESCORT and f.supports else None
        if ai_flown and escorted is not None and (bot or c.flights[f.supports] is not player):
            # AI escort: stay with the package and engage fighters that threaten it
            g.points[0].tasks.append(dtask.EscortTaskAction(escorted.id, engagement_max_dist=60_000,
                                                             lastwpt=3))
            g.add_waypoint(T.to_point(dest), alt, speed, name="TGT")
        elif f.task is Task.CAP or (f.task is Task.ESCORT and ai_flown):
            station_pos = _cap_station(c, f, player)
            station = T.to_point(station_pos)
            wp = g.add_waypoint(station, alt, speed, name="CAP")
            if ai_flown and f.side is player.side and (player.target_id or player.task is Task.ESCORT):
                # covering the player's strike: engage anything approaching the target area
                wp.tasks.append(dtask.EngageTargetsInZone(T.to_point(_dest(c, player)),
                                                          CAP_ENGAGE_RADIUS, [dtask.Targets.All.Air]))
            if is_player:
                wp.tasks.append(dtask.OrbitAction(alt, speed, dtask.OrbitAction.OrbitPattern.RaceTrack))
                g.add_waypoint(T.to_point(Pos(x=f.area.x + 30_000, z=f.area.z)), alt, speed, name="CAP-2")
            else:
                wp.tasks.append(dtask.OrbitAction(alt, speed, dtask.OrbitAction.OrbitPattern.Circle))
        else:
            ip = _toward(dest, base.pos, 20_000)
            low = alt if f.task is Task.ESCORT else spec.get("low_alt_m", 500)   # escorts stay high
            ipwp = g.add_waypoint(T.to_point(ip), low, speed, name="IP")
            twp = g.add_waypoint(T.to_point(dest), low, speed, name="TGT")
            for wp in (ipwp, twp):
                if f.task is not Task.ESCORT:
                    wp.alt_type = "RADIO"  # low level means height above ground, not sea level
            if ai_flown and f.target_id in static_targets:
                # structures (rail yard, depot): bomb the site
                twp.tasks.append(dtask.Bombing(T.to_point(static_targets[f.target_id]), group_attack=True))
            elif ai_flown and f.target_id in obj_groups:
                twp.tasks.append(dtask.AttackGroup(obj_groups[f.target_id].id))
            g.add_waypoint(T.to_point(ip), alt, speed, name="EGRESS")
        if not (f.task in (Task.CAP, Task.ESCORT) and ai_flown):
            g.land_at(airport)
        tracked.append(gname)

    # --- briefing
    sq = c.squadrons[player.squadron_id]
    lines = (["BOT MISSION (AI only): spectate with F10 / F2 and speed up with Ctrl+Z. It ends "
              f"itself when {player.callsign} has landed or been lost, or after "
              f"{BOT_TIME_LIMIT // 3600} h.", ""] if bot else [])
    lines += [f"{c.name} - turn {c.turn}, {c.time:%d %b %Y %H:%M} local",
             f"Flight: {player.callsign} ({sq.name}), {len(player.airframe_ids)}x {sq.aircraft_type}"
             f" from {c.airbases[sq.base_id].dcs_name} ({start} start)",
             f"Tasking: {c.packages[player.package_id].description}"]
    if player.target_id:
        tgt = c.objectives[player.target_id]
        lat, lon = T.pos_to_latlon(tgt.pos)
        lines.append(f"Target: {tgt.name} at {lat:.3f}N {lon:.3f}E "
                     f"({len(tgt.alive_units)} vehicles reported)")
    threats = [o for o in plan.objectives if o.side is player.side.enemy and o.kind.value == "sam"]
    if threats:
        lines.append("Known SAM threats: " + ", ".join(o.name for o in threats))
    friendly = [f for f in plan.flights if f.side is player.side and f is not player]
    if friendly:
        lines.append("Friendly flights in the area: " + ", ".join(
            f"{f.callsign} ({f.task.value})" for f in friendly))
    n_enemy = sum(1 for f in plan.flights if f.side is player.side.enemy and f.task is Task.CAP)
    lines.append(f"Intel: expect {n_enemy} enemy fighter flight(s) in your area."
                 if n_enemy else "Intel: no enemy fighters expected in your area.")
    lines.append("Exit the mission normally when done; run `cwdc ingest` afterwards.")
    m.set_description_text("\n".join(lines))
    m.set_description_bluetask_text(c.packages[player.package_id].description)

    end_when = {"groups": [f"{player.id} {player.callsign}"], "time_limit": BOT_TIME_LIMIT}         if bot else None
    inject_runtime(m, mission_id, tracked, end_when=end_when, tracked_statics=tracked_statics)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / mission_filename(c, player, bot)
    m.save(str(path))
    return MissionRecord(mission_id=mission_id, turn=c.turn, player_flight_id=player.id,
                         miz_path=str(path), generated_at=time.time(), manifest=manifest,
                         spawned_flights=[f.id for f in plan.flights], bot=bot)


def static_type(type_id: str):
    """pydcs class for a static object type id (fortifications, warehouses, ...)."""
    from dcs import statics
    for table in (statics.fortification_map, statics.warehouse_map, statics.groundobject_map,
                  statics.cargo_map):
        if type_id in table:
            return table[type_id]
    raise KeyError(f"unknown unit or static type {type_id!r}")


def mission_filename(c: Campaign, f: Flight, bot: bool = False) -> str:
    """e.g. 'CWDC Fulda Gap 1976 - T001 14Aug 0600 - Colt 2 Strike.miz' (unique per flight/turn)."""
    import re
    raw = f"CWDC {c.name} - T{c.turn:03d} {c.time:%d%b %H%M} - {f.callsign} {f.task.value}"
    if bot:
        raw += " (BOT)"
    return re.sub(r'[<>:"/\\|?*]', "", raw) + ".miz"


def _adjacent_slots(airport, type_cls, n: int):
    """Parking slots close together, so an AI wingman does not taxi across the base."""
    free = airport.free_parking_slots(type_cls)
    if len(free) < n:
        return None
    best, best_d = None, math.inf
    for s in free:
        near = sorted(free, key=lambda o: s.position.distance_to_point(o.position))[:n]
        d = sum(s.position.distance_to_point(o.position) for o in near)
        if d < best_d:
            best, best_d = near, d
    return best
