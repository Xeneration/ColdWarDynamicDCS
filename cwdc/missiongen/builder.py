"""Build a DCS mission for the player's chosen flight from the campaign state.

Extensibility principle 3: the mission contains the player's flight plus everything
that overlaps it in time and space (enemy CAP on the route, the target and nearby
SAMs, enemy strikes into the player's CAP sector, friendly flights on the same
target). Everything else stays abstract and is resolved by the engine.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path

from dcs import task as dtask
from dcs.mission import Mission, StartType
from dcs.planes import plane_map
from dcs.unit import Skill
from dcs.vehicles import vehicle_map

from cwdc import theater as T
from cwdc.missiongen import coalitions, loadouts
from cwdc.missiongen.runtime_inject import inject_runtime
from cwdc.model import Campaign, Flight, MissionRecord, Objective, Pos, Side, Task

ENEMY_CAP_RADIUS = 90_000       # enemy CAP stations this close to the player's route are spawned
ENEMY_STRIKE_RADIUS = 60_000    # enemy strikes on targets this close to a player CAP station
OBJECTIVE_RADIUS = 25_000       # objectives this close to the player's area/target are spawned
AI_LAUNCH_DELAY = 420           # s; AI flights starting over their base wait for the player's taxi

DCS_TASK = {Task.CAP: dtask.CAP, Task.ESCORT: dtask.Escort, Task.STRIKE: dtask.GroundAttack,
            Task.BAI: dtask.GroundAttack, Task.INTERDICTION: dtask.GroundAttack,
            Task.CAS: dtask.CAS, Task.SEAD: dtask.SEAD}


@dataclass
class Plan:
    flights: list[Flight] = field(default_factory=list)       # all flights to spawn (player first)
    objectives: list[Objective] = field(default_factory=list)


def _route(c: Campaign, f: Flight) -> list[Pos]:
    base = c.airbases[c.squadrons[f.squadron_id].base_id].pos
    dest = f.area if f.area else c.objectives[f.target_id].pos
    pts = []
    n = max(1, int(base.dist(dest) // 20_000))
    for i in range(n + 1):
        t = i / n
        pts.append(Pos(x=base.x + (dest.x - base.x) * t, z=base.z + (dest.z - base.z) * t))
    return pts


def _dest(c: Campaign, f: Flight) -> Pos:
    return f.area if f.area else c.objectives[f.target_id].pos


def select(c: Campaign, player: Flight) -> Plan:
    plan = Plan(flights=[player])
    route = _route(c, player)
    dest = _dest(c, player)
    enemy = player.side.enemy
    objs: dict[str, Objective] = {}

    for f in c.flights.values():
        if f.id == player.id:
            continue
        if f.side is enemy and f.task is Task.CAP:
            if any(p.dist(f.area) <= ENEMY_CAP_RADIUS for p in route):
                plan.flights.append(f)
        elif f.side is enemy and f.target_id and player.task is Task.CAP:
            if c.objectives[f.target_id].pos.dist(dest) <= ENEMY_STRIKE_RADIUS:
                plan.flights.append(f)
                objs[f.target_id] = c.objectives[f.target_id]
        elif f.side is player.side and f.target_id and player.target_id:
            if c.objectives[f.target_id].pos.dist(dest) <= OBJECTIVE_RADIUS:
                plan.flights.append(f)
                objs[f.target_id] = c.objectives[f.target_id]

    if player.target_id:
        objs[player.target_id] = c.objectives[player.target_id]
    for o in c.objectives.values():
        if o.destroyed:
            continue
        near = o.pos.dist(dest) <= OBJECTIVE_RADIUS
        covers_route = o.threat_range_m and any(p.dist(o.pos) <= o.threat_range_m for p in route)
        if near or covers_route:
            objs[o.id] = o
    plan.objectives = list(objs.values())
    return plan


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


def build(c: Campaign, player_flight_id: str, out_dir: Path) -> MissionRecord:
    import logging
    logging.getLogger().setLevel(logging.CRITICAL)
    player = c.flights[player_flight_id]
    plan = select(c, player)
    terrain = T.terrain()
    m = Mission(terrain)
    coalitions.setup(m)
    m.start_time = c.time
    m.weather.clouds_density = 3
    m.weather.clouds_base = 2500
    m.weather.wind_at_ground.speed = 3

    mission_id = f"{c.id}-t{c.turn:03d}-{player.id}"
    manifest: dict[str, dict] = {}
    tracked: list[str] = []
    obj_groups: dict[str, object] = {}

    # --- objectives (spawn first so strike flights can target their group ids)
    for o in plan.objectives:
        alive = [(i, u) for i, u in enumerate(o.units) if u.alive]
        if not alive:
            continue
        country = m.country(o.country)
        gname = f"{o.id} {o.name}"
        i0, u0 = alive[0]
        g = m.vehicle_group(country, gname, vehicle_map[u0.type],
                            T.to_point(Pos(x=o.pos.x + u0.dx, z=o.pos.z + u0.dz)), heading=0)
        g.units[0].name = f"{o.id}#{i0}"
        for i, u in alive[1:]:
            v = m.vehicle(f"{o.id}#{i}", vehicle_map[u.type])
            v.position = T.to_point(Pos(x=o.pos.x + u.dx, z=o.pos.z + u.dz))
            g.add_unit(v)
        for unit in g.units:
            idx = int(unit.name.split("#")[1])
            manifest[unit.name] = {"kind": "objective", "objective": o.id, "unit": idx}
        obj_groups[o.id] = g
        tracked.append(gname)

    # --- flights
    for f in plan.flights:
        sq = c.squadrons[f.squadron_id]
        spec = T.airframe_catalogue().get(sq.aircraft_type, {})
        type_cls = plane_map[sq.aircraft_type]
        country = m.country(sq.country)
        base = c.airbases[sq.base_id]
        airport = terrain.airports[base.dcs_name]
        dcs_task = DCS_TASK.get(f.task, dtask.CAP)
        gname = f"{f.id} {f.callsign}"
        alt = spec.get("cruise_alt_m", 5000)
        speed = spec.get("cruise_kmh", 800)
        is_player = f is player
        dest = _dest(c, f)

        if is_player:
            g = m.flight_group_from_airport(country, gname, type_cls, airport, maintask=dcs_task,
                                            start_type=StartType.Warm, group_size=len(f.airframe_ids))
        elif f.task is Task.CAP:
            g = m.flight_group_inflight(country, gname, type_cls, T.to_point(f.area), altitude=alt,
                                        speed=speed, maintask=dcs_task, group_size=len(f.airframe_ids))
        else:
            g = m.flight_group_inflight(country, gname, type_cls, T.to_point(base.pos), altitude=alt,
                                        speed=speed, maintask=dcs_task, group_size=len(f.airframe_ids))
            g.start_time = AI_LAUNCH_DELAY
        _apply_loadout(g, f, sq.aircraft_type, dcs_task)

        for i, unit in enumerate(g.units):
            unit.name = _unit_name(f, i)
            af = c.airframes[f.airframe_ids[i]]
            manifest[unit.name] = {"kind": "airframe", "flight": f.id, "airframe": af.id,
                                   "pilot": f.pilot_ids[i]}
            if sq.aircraft_type == "F-4E-45MC":
                unit.set_property("Quality", af.quality)
            if not (is_player and i == 0):
                unit.skill = Skill.Good if sq.side is Side.BLUE else Skill.Average
        if is_player:
            g.units[0].set_player()

        # waypoints
        if f.task is Task.CAP:
            station = T.to_point(f.area)
            wp = g.add_waypoint(station, alt, speed, name="CAP")
            if is_player:
                # race-track between the station and a point 30 km north
                wp.tasks.append(dtask.OrbitAction(alt, speed, dtask.OrbitAction.OrbitPattern.RaceTrack))
                g.add_waypoint(T.to_point(Pos(x=f.area.x + 30_000, z=f.area.z)), alt, speed, name="CAP-2")
            else:
                wp.tasks.append(dtask.OrbitAction(alt, speed, dtask.OrbitAction.OrbitPattern.Circle))
        else:
            start = base.pos
            d = max(1.0, start.dist(dest))
            ip = Pos(x=dest.x + (start.x - dest.x) * 20_000 / d, z=dest.z + (start.z - dest.z) * 20_000 / d)
            low = spec.get("low_alt_m", 500)
            ipwp = g.add_waypoint(T.to_point(ip), low, speed, name="IP")
            twp = g.add_waypoint(T.to_point(dest), low, speed, name="TGT")
            for wp in (ipwp, twp):
                wp.alt_type = "RADIO"      # low level means height above ground, not sea level
            if not is_player and f.target_id in obj_groups:
                twp.tasks.append(dtask.AttackGroup(obj_groups[f.target_id].id))
            g.add_waypoint(T.to_point(ip), alt, speed, name="EGRESS")
        if not (f.task is Task.CAP and not is_player):
            g.land_at(airport)
        tracked.append(gname)

    # --- briefing
    sq = c.squadrons[player.squadron_id]
    lines = [f"{c.name} - turn {c.turn}, {c.time:%d %b %Y %H:%M} local",
             f"Flight: {player.callsign} ({sq.name}), {len(player.airframe_ids)}x {sq.aircraft_type}",
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
    lines.append("Expect enemy fighters. Exit the mission normally when done; "
                 "run `cwdc ingest` afterwards.")
    m.set_description_text("\n".join(lines))
    m.set_description_bluetask_text(c.packages[player.package_id].description)

    inject_runtime(m, mission_id, tracked)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"CWDC_{c.id}_t{c.turn:03d}.miz"
    m.save(str(path))
    return MissionRecord(mission_id=mission_id, turn=c.turn, player_flight_id=player.id,
                         miz_path=str(path), generated_at=time.time(), manifest=manifest,
                         spawned_flights=[f.id for f in plan.flights])
