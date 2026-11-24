"""Build a DCS mission for the player's chosen flight from the campaign state.

Extensibility principle 3: the mission contains the player's flight plus everything
that overlaps it in time and space (enemy CAP that makes contact, the target and
nearby SAMs, enemy strikes into the player's CAP sector, friendly flights on nearby
targets or covering the route). Everything else stays abstract and is resolved by
the engine.
"""
from __future__ import annotations

import math
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

from dcs import task as dtask
from dcs.mapping import Vector2
from dcs.mission import Mission, StartType
from dcs.planes import plane_map
from dcs.unit import Skill
from dcs.ships import ship_map
from dcs.vehicles import vehicle_map

from cwdc import theater as T
from cwdc.engine import rng
from cwdc.engine.airbases import RUNWAY_LOADOUT
from cwdc import era
from cwdc.missiongen import coalitions, kneeboard, loadouts
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
SAM_MARGIN = 1.15               # routes keep this multiple of a SAM's engagement radius away
TARGET_DEFENCE_SLACK = 10_000   # m; SAM rings this close to the target defend it: no detour
DETOUR_FACTOR = 1.25            # detour turning points sit this far out (x ring radius)
MAX_DETOUR = 1.6                # never accept a route longer than this x the straight line
MIN_CONTACT_DIST = 50_000       # m; enemy fighters never spawn closer than this to the player
SWEEP_DELAY = 900               # s; a quiet CAP gets an enemy fighter sweep after this long
SWEEP_START_DIST = 80_000       # m; ...which starts this far out on the enemy side
SWEEP_MAX_DIST = 250_000        # m; only enemy CAP flights based this close can sweep
PROBE_RADIUS = 80_000           # m; crisis probes this close to the player's area are in the mission
PROBE_RUN_IN = (40_000, 150_000)  # m east of its turn point a probe starts (scaled to the QRA's ETA)
QRA_SHADOW_S = 15 * 60          # s an AI QRA follows its probe before resuming its own route
INTERCEPT_RANGE = 3_700         # m (2 nm) an interceptor must hold...
INTERCEPT_HOLD = 60             # ...for this long (s) to count as an intercept
ON_STATION_S = 30 * 60          # s an AI-flown focus CAP / escort orbits before going home
ESCORT_HOLD_S = 8 * 60          # s an escort holds outside the target's SAM cover, then home
TAXI_TIME = {"ramp": 600, "runway": 90, "air": 0}   # s before the player is en route
FLIGHT_SPACING = 3_000          # m lateral separation between spawned flights
UNIT_SPACING = 250              # m between aircraft of one flight

START_TYPES = {"ramp": StartType.Warm, "runway": StartType.Runway}

DCS_TASK = {Task.CAP: dtask.CAP, Task.ESCORT: dtask.Escort, Task.STRIKE: dtask.GroundAttack,
            Task.BAI: dtask.GroundAttack, Task.INTERDICTION: dtask.GroundAttack,
            Task.CAS: dtask.CAS, Task.SEAD: dtask.SEAD, Task.QRA: dtask.Intercept, Task.RECON: dtask.CAP,
            Task.INTERCEPT: dtask.Intercept, Task.SWEEP: dtask.FighterSweep, Task.RESCAP: dtask.CAS,
            Task.ARMED_RECON: dtask.CAS, Task.ANTISHIP: dtask.AntishipStrike}
FIGHTER_TASKS = (Task.CAP, Task.QRA, Task.INTERCEPT, Task.SWEEP, Task.RESCAP)   # fly to an area and orbit
SWEEP_TARGET_RADIUS = 30_000    # m: enemy CAP flights this close to a sweep's area are its quarry

# DCS western aircraft callsigns (index used in the unit callsign table)
WESTERN_CALLSIGNS = {"Enfield": 1, "Springfield": 2, "Uzi": 3, "Colt": 4, "Dodge": 5,
                     "Ford": 6, "Chevy": 7, "Pontiac": 8}


@dataclass
class Plan:
    flights: list[Flight] = field(default_factory=list)       # all flights to spawn (player first)
    objectives: list[Objective] = field(default_factory=list)
    delay: dict[str, int] = field(default_factory=dict)        # flight id -> spawn delay (s)
    sweep: set[str] = field(default_factory=set)               # enemy CAP sent at the player's station


# campaign weather kind -> DCS cloud presets (pydcs dcs.cloud_presets.Clouds)
CLOUD_PRESETS = {"scattered": ("Scattered1", "Scattered2", "LightScattered1"),
                 "broken": ("Broken1", "Broken3", "Broken4"),
                 "overcast": ("Overcast1", "Overcast2", "Overcast3"),
                 "rain": ("OvercastAndRain1", "OvercastAndRain2", "OvercastAndRain3")}


def apply_weather(m: Mission, c: Campaign) -> None:
    """Map the campaign's weather onto the mission (cloud preset, fog, visibility, wind)."""
    from dcs.cloud_presets import Clouds
    from dcs.weather import Weather, Wind
    w = c.weather
    wx = m.weather
    wx.season_temperature = w.temp_c
    wx.visibility_distance = min(80_000, w.visibility_m)
    kt = 0.514444
    wx.wind_at_ground = Wind(w.wind_dir, round(w.wind_kts * kt))
    wx.wind_at_2000 = Wind((w.wind_dir + 10) % 360, round(w.wind_kts * 1.8 * kt))
    wx.wind_at_8000 = Wind((w.wind_dir + 20) % 360, round(w.wind_kts * 3.0 * kt))
    names = CLOUD_PRESETS.get(w.kind)
    if names:
        preset = Clouds[names[rng(c.seed, c.turn, "clouds").randrange(len(names))]].value
        wx.clouds_preset = preset
        wx.clouds_base = max(preset.min_base, min(preset.max_base, w.cloud_base_m))
        if w.kind == "rain":
            wx.clouds_iprecptns = Weather.Preceptions.Rain
    else:
        wx.clouds_preset = None
        wx.clouds_density = 0
    if w.kind == "fog":
        wx.enable_fog = True
        wx.fog_visibility = w.visibility_m
        wx.fog_thickness = 300


CODEWORDS = ("BLUE MOON", "TIN CAN", "ROADHOUSE", "HAYSTACK", "LANTERN", "PEPPERMILL", "SNOWCAP",
             "TEAPOT", "BRASS RING", "OLD MILL", "STORMDRAIN", "FENCEPOST")


def _divert(c: Campaign, player: Flight):
    from cwdc.engine.airbases import CLOSED
    home = c.airbases[c.squadrons[player.squadron_id].base_id]
    others = [ab for ab in c.airbases.values() if ab.side is player.side and ab.id != home.id
              and ab.damage < CLOSED and not ab.carrier_of]
    return min(others, key=lambda ab: ab.pos.dist(home.pos), default=None)


def _situation_lines(c: Campaign, player: Flight, plan: Plan) -> list[str]:
    """SITUATION / AAA / DIVERT / CODEWORDS briefing lines (also on the kneeboard)."""
    from cwdc.engine import outcome
    from cwdc.engine.resolver import AAA_TYPES
    out = []
    if c.at_war:
        deep = max(c.sectors.values(), key=lambda s: s.offset_km, default=None)
        front = (f" Deepest Warsaw Pact gain: {deep.offset_km:.0f} km ({deep.name})."
                 if deep and deep.offset_km > 0 else " The front is holding at the border.")
        out.append(f"SITUATION: war day {outcome.war_day(c)}.{front}")
    if player.target_id:
        tgt = c.objectives[player.target_id]
        aaa = sum(1 for u in tgt.units if u.alive and any(a in u.type for a in AAA_TYPES))
        if aaa:
            out.append(f"Target defences: {aaa} AAA / short-range SAM vehicle(s) reported.")
    div = _divert(c, player)
    if div is not None:
        out.append(f"Divert: {div.dcs_name}.")
    r = rng(c.seed, c.turn, f"codewords:{player.id}")
    ok, abort = r.sample(CODEWORDS, 2)
    out.append(f"Codewords: mission complete {ok}, abort {abort}.")
    return out


def _kneeboard_info(c: Campaign, player: Flight, plan: Plan, group, lines: list[str],
                    magic: dict | None) -> "kneeboard.KneeboardInfo":
    wps = []
    route = []
    for i, p in enumerate(group.points):
        pos = Pos(x=p.position.x, z=p.position.y)
        lat, lon = T.pos_to_latlon(pos)
        name = p.name or ("START" if i == 0 else "LAND" if p.type == "Land" else f"WP{i}")
        wps.append((name, lat, lon, round(p.alt * 3.28084), "AGL" if p.alt_type == "RADIO" else "MSL"))
        route.append(pos)
    enemy_sams = [o for o in plan.objectives if o.side is player.side.enemy
                  and o.kind in (ObjectiveKind.SAM, ObjectiveKind.SHIP)
                  and o.threat_range_m and not o.destroyed]
    threats = [f"{o.name}: {o.threat_range_m / 1852:.0f} nm ring" for o in enemy_sams]
    threats += [l for l in lines if l.startswith(("Intel:", "Intruders", "Target defences"))]
    comms = [l for l in lines if l.startswith(("GCI:", "Friendly flights", "Divert:", "Codewords:", "Weather:"))]
    head = [l for l in lines if l.startswith(("Flight:", "Loadout:", "Crews:", "Tasking:", "Target:", "CRISIS", "WAR:",
                                              "SITUATION:"))]
    bull = None
    if magic:
        bull = Pos(x=magic["bullseye"]["x"], z=magic["bullseye"]["z"])
    centre = route[len(route) // 2] if route else None
    front = [T.latlon_to_pos(la, lo) for la, lo in c.front_line]
    if centre is not None:
        front = [p for p in front if p.dist(centre) < 300_000]
    return kneeboard.KneeboardInfo(
        title=f"{player.callsign.upper()} - {player.task.value}",
        subtitle=f"{c.time:%d %b %Y %H%M}L, turn {c.turn}", lines=head, waypoints=wps,
        threats=threats, comms=comms, route=route,
        rings=[(o.pos, o.threat_range_m, o.name.split()[0]) for o in enemy_sams], front=front,
        bullseye=bull, target=c.objectives[player.target_id].pos if player.target_id else None)


PRECISION_LOADOUT = "PRECISION_GBU8"
ARMED_RECON_BOX = 30_000          # m: the road-hunting box around the column
FAC_CALLSIGN = "Wildcat 21"       # airborne forward air controller (fictional callsign)
FAC_RANGE = 27_800                # m (15 nm): the FAC checks the flight in and marks from here
FAC_OFFSET = 400                  # m: the smoke goes down this far from the target
FAC_INTERVAL = 300                # s between marks (smoke lasts about 5 min)


CONTACT_DIST = 2_500              # m: friendly armour in contact, this far toward our lines from the enemy
HELO_START_DIST = 35_000          # m east of the hunt area (which is 10 km on our side) the wave starts
HELO_LZ_DIST = 4_000              # m west of the hunt area it lands its troops
HELO_SPEED_KMH = 200


def _reaction_scene(m: Mission, c: Campaign, player: Flight, player_eta: float, obj_groups: dict,
                    manifest: dict, tracked: list) -> dict | None:
    """Set the scene for an answer to the enemy's move (engine.reactive): the enemy battalion
    pushes on with our armour in contact, or a helicopter assault wave crosses the front."""
    from dcs.helicopters import helicopter_map
    from dcs.point import PointAction
    from cwdc.engine import reactive
    pkg = c.packages[player.package_id]
    x = next((r for r in reactive.this_turn(c) if r.package_id == pkg.id), None)
    if x is None:
        return None
    ref = player.area or (c.objectives[player.target_id].pos if player.target_id else None)
    if ref is None:
        return None
    blue_home = min((ab.pos for ab in c.airbases.values() if ab.side is Side.BLUE), key=lambda p: p.dist(ref))
    if x.kind in ("tic", "hold") and player.target_id in obj_groups:
        g = obj_groups[player.target_id]
        here = Pos(x=g.units[0].position.x, z=g.units[0].position.y)
        g.add_waypoint(T.to_point(_toward(here, blue_home, CONTACT_DIST + 1_500)), PointAction.OffRoad, 20)
        friends = [f for f in c.formations.values() if f.side is Side.BLUE and f.sector_id == x.sector_id]
        country = max(friends, key=lambda f: f.strength).country if friends else "USA"
        types = ["M-60", "M-60", "M-113"]
        if c.era:
            from cwdc import era
            avail = era.ground_in(c.time.year, "west")

            def newest(role):
                return max((v for v in avail.values() if v["role"] == role and not v.get("requires")),
                           key=lambda v: v["years"][0], default=None)
            tank, ifv = newest("tank"), newest("ifv") or newest("apc")
            types = [tank["units"][0]] * 2 if tank else types[:2]
            types.append(ifv["units"][0] if ifv else "M-113")
        at = _toward(here, blue_home, CONTACT_DIST)
        vg = m.vehicle_group(m.country(country), "Friendly armour in contact", vehicle_map[types[0]],
                             T.to_point(at), group_size=1)
        for i, t in enumerate(types[1:], 1):
            v = m.vehicle(f"Friendly armour in contact {i + 1}", vehicle_map[t])
            v.position = T.to_point(Pos(x=at.x + 40 * i, z=at.z + 25 * i))
            vg.add_unit(v)
        tracked.append(vg.name)
        form = c.formations.get(c.objectives[player.target_id].formation_id or "")
        best = max(friends, key=lambda f: f.strength) if friends else None
        return {"kind": x.kind, "name": x.name, "enemy": form.name if form else "the enemy",
                "friends": best.name if best else "our troops"}
    if x.kind == "helo" and player.area is not None:
        area = player.area
        start = T.toward_enemy(area, HELO_START_DIST)
        lz = T.toward_nato(area, HELO_LZ_DIST)
        delay = int(player_eta - HELO_START_DIST / (HELO_SPEED_KMH / 3.6))
        names = []
        types = reactive.helo_types(c.time.year)
        for k, t in enumerate(dict.fromkeys(types)):
            n = types.count(t)
            hg = m.flight_group_inflight(m.country("USSR"), f"Assault wave {k + 1}", helicopter_map[t],
                                         T.to_point(Pos(x=start.x + 600 * k, z=start.z)), altitude=60,
                                         speed=HELO_SPEED_KMH, maintask=dtask.Transport, group_size=n)
            if delay > 0:
                _delay(hg, delay)
            wp = hg.add_waypoint(T.to_point(lz), 40, HELO_SPEED_KMH, name="LZ")
            wp.tasks.append(dtask.Land(T.to_point(lz), duration=300))
            for i, u in enumerate(hg.units):
                u.name = f"Assault wave {k + 1}-{i + 1}"
                manifest[u.name] = {"kind": "reactive", "reaction": x.kind}
                names.append(u.name)
            tracked.append(hg.name)
        x.helos = names
        return {"kind": "helo", "count": len(names),
                "types": ", ".join(f"{types.count(t)}x {t}" for t in dict.fromkeys(types))}
    return None


def reactive_behind() -> int:
    from cwdc.engine import reactive
    return reactive.HUNT_BEHIND


def _move_toward_front(c: Campaign, group, dist: float = 15_000) -> None:
    """Send a vehicle group down the road toward the nearest point of the front."""
    from dcs.point import PointAction
    here = Pos(x=group.units[0].position.x, z=group.units[0].position.y)
    front = [T.latlon_to_pos(la, lo) for la, lo in c.front_line]
    goal = min(front, key=lambda p: p.dist(here))
    group.add_waypoint(T.to_point(_toward(here, goal, dist)), PointAction.OnRoad, 30)


RESCAP_ENGAGE_RADIUS = 10_000     # m around the survivor an AI RESCAP attacks ground units
HELO_SPEED = 180                  # km/h
HELO_START = 60_000               # m from the survivor the rescue helicopter starts (our side)
SEARCH_PARTY_DIST = 3_000         # m from the survivor the enemy search party starts
PICKUP_RADIUS, PICKUP_HOLD = 300, 20


def _rescue_party(m: Mission, c: Campaign, player: Flight, player_eta: float, tracked: list) -> dict | None:
    """RESCAP: the rescue helicopter (from our nearest airfield's side) and an enemy search party
    closing on the survivor. Returns the runtime's pickup watch config (+ briefing names)."""
    from dcs.helicopters import helicopter_map
    from dcs.point import PointAction
    pilot = c.pilots.get(player.rescue_of) if player.rescue_of else None
    if pilot is None:
        return None
    pickup = player.area
    home = min((ab for ab in c.airbases.values() if ab.side is player.side and not ab.carrier_of),
               key=lambda ab: ab.pos.dist(pickup))
    start = _toward(pickup, home.pos, HELO_START)
    sq = c.squadrons[player.squadron_id]
    hg = m.flight_group_inflight(m.country(sq.country), "Dustoff 1", helicopter_map["UH-1H"],
                                 T.to_point(start), altitude=150, speed=HELO_SPEED,
                                 maintask=dtask.Transport, group_size=1)
    delay = int(player_eta + 300 - start.dist(pickup) / (HELO_SPEED / 3.6))
    if delay > 0:
        _delay(hg, delay)                              # arrives once the RESCAP is on station
    wp = hg.add_waypoint(T.to_point(pickup), 100, HELO_SPEED, name="PICKUP")
    wp.tasks.append(dtask.Land(T.to_point(pickup), duration=120))
    hg.add_waypoint(T.to_point(start), 150, HELO_SPEED, name="EXIT")
    hg.land_at(m.terrain.airports[home.dcs_name])
    tracked.append(hg.name)
    red = next(s for s in c.squadrons.values() if s.side is player.side.enemy)
    party_at = T.toward_enemy(pickup, SEARCH_PARTY_DIST if player.side is Side.BLUE else -SEARCH_PARTY_DIST)
    vg = m.vehicle_group(m.country(red.country), "Search party", vehicle_map["BRDM-2"],
                         T.to_point(party_at), group_size=2)
    v = m.vehicle("Search party AAA", vehicle_map["Ural-375 ZU-23"])
    v.position = T.to_point(Pos(x=party_at.x + 60, z=party_at.z))
    vg.add_unit(v)
    vg.add_waypoint(T.to_point(pickup), PointAction.OffRoad, 15)
    tracked.append(vg.name)
    return {"helo": hg.name, "x": pickup.x, "z": pickup.z, "radius": PICKUP_RADIUS, "hold": PICKUP_HOLD,
            "who": f"{pilot.rank} {pilot.name}", "callsign": "Dustoff 1"}


def _theater(c: Campaign) -> dict:
    from cwdc.engine.factory import THEATERS
    return T.load_data(THEATERS[c.theater][0])


CARRIER_TRACK = 60_000            # m the carrier steams west during the mission
CARRIER_SPEED_KMH = 46            # 25 knots of wind over the deck


def _base_label(c: Campaign, base) -> str:
    """'Incirlik', or for a carrier 'the 6th Fleet carrier (Forrestal-class carrier)'."""
    if not base.carrier_of:
        return base.dcs_name
    o = c.objectives.get(base.carrier_of)
    return f"the carrier ({o.units[0].type})" if o else "the carrier"


def _land(g, airport, deck) -> None:
    """Land at the home airfield, or recover aboard the carrier."""
    if deck is None:
        g.land_at(airport)
        return
    from dcs.point import MovingPoint, PointAction, PointProperties
    mp = MovingPoint(deck.units[0].position)
    mp.type, mp.action = "Land", PointAction.Landing
    mp.helipad_id = mp.link_unit = deck.units[0].id
    mp.alt, mp.speed, mp.properties = 0, 0, PointProperties()
    g.add_point(mp)


def _base(c: Campaign, f: Flight) -> Pos:
    return c.airbases[c.squadrons[f.squadron_id].base_id].pos


def _dest(c: Campaign, f: Flight) -> Pos:
    return f.area if f.area else c.objectives[f.target_id].pos


def _rings(c: Campaign, side: Side, keep_clear_of: Pos | None = None,
           exempt: tuple[Pos, ...] = ()) -> list[tuple[Pos, float]]:
    """Enemy SAM engagement rings (with margin) that a `side` flight should route around.
    Rings around a point in `exempt` (the flight's target, its own base) are left out: there is
    no way round those."""
    out = []
    for o in c.objectives.values():
        if o.side is side or o.destroyed or o.kind is not ObjectiveKind.SAM or not o.threat_range_m:
            continue
        r = o.threat_range_m * SAM_MARGIN
        if any(o.pos.dist(p) <= r + TARGET_DEFENCE_SLACK for p in exempt):
            continue
        out.append((o.pos, r))
    return out


def _closest_on(a: Pos, b: Pos, p: Pos) -> tuple[Pos, float]:
    dx, dz = b.x - a.x, b.z - a.z
    l2 = dx * dx + dz * dz
    t = 0.0 if l2 == 0 else max(0.0, min(1.0, ((p.x - a.x) * dx + (p.z - a.z) * dz) / l2))
    return Pos(x=a.x + dx * t, z=a.z + dz * t), t


def _avoid(a: Pos, b: Pos, rings: list[tuple[Pos, float]], depth: int = 0) -> list[Pos]:
    """Turning points after `a` up to and including `b` that keep the legs out of `rings`."""
    hits = []
    for centre, r in rings:
        q, t = _closest_on(a, b, centre)
        if q.dist(centre) < r and a.dist(centre) > r and b.dist(centre) > r:
            hits.append((t, centre, r, q))
    if not hits or depth >= 4:
        return [b]
    _, centre, r, q = min(hits, key=lambda h: h[0])     # the first ring along the leg
    d = q.dist(centre)
    if d < 1.0:                                         # dead centre: go round the left side
        L = a.dist(b) or 1.0
        ux, uz = -(b.z - a.z) / L, (b.x - a.x) / L
    else:
        ux, uz = (q.x - centre.x) / d, (q.z - centre.z) / d
    w = Pos(x=centre.x + ux * r * DETOUR_FACTOR, z=centre.z + uz * r * DETOUR_FACTOR)
    return _avoid(a, w, rings, depth + 1) + _avoid(w, b, rings, depth + 1)


def _path(c: Campaign, f: Flight) -> list[Pos]:
    """Turning points from the flight's base to its destination, bent round enemy SAM rings
    that don't defend the destination itself. Falls back to the straight line when the
    detour would be too long."""
    base, dest = _base(c, f), _dest(c, f)
    pts = [base] + _avoid(base, dest, _rings(c, f.side, exempt=(dest, base)))
    length = sum(p.dist(q) for p, q in zip(pts, pts[1:]))
    if length > MAX_DETOUR * base.dist(dest):
        return [base, dest]
    return pts


def _route(c: Campaign, f: Flight) -> list[Pos]:
    """The flight's path as points about every 20 km (for 'what is near the route' checks)."""
    pts = _path(c, f)
    out = [pts[0]]
    for a, b in zip(pts, pts[1:]):
        n = max(1, int(a.dist(b) // 20_000))
        out += [Pos(x=a.x + (b.x - a.x) * i / n, z=a.z + (b.z - a.z) * i / n) for i in range(1, n + 1)]
    return out


def _back_along(path: list[Pos], dist: float) -> tuple[Pos, int]:
    """The point `dist` metres before the end of `path`, and the index of the next turning
    point after it (so the remaining route is path[index:])."""
    left = dist
    for i in range(len(path) - 1, 0, -1):
        leg = path[i - 1].dist(path[i])
        if leg >= left:
            return _toward(path[i], path[i - 1], left), i
        left -= leg
    return path[0], 1


def _clear(p: Pos, rings: list[tuple[Pos, float]]) -> Pos:
    """Move p out of any ring it sits in (radially, to the ring's edge)."""
    for centre, r in rings:
        d = p.dist(centre)
        if d < r:
            if d < 1.0:
                p = Pos(x=centre.x + r, z=centre.z)
            else:
                p = Pos(x=centre.x + (p.x - centre.x) * r / d, z=centre.z + (p.z - centre.z) * r / d)
    return p


def _beyond(a: Pos, b: Pos, dist: float) -> Pos:
    """Point `dist` metres from a in the direction of b (not clamped to b)."""
    d = a.dist(b) or 1.0
    return Pos(x=a.x + (b.x - a.x) * dist / d, z=a.z + (b.z - a.z) * dist / d)


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

    # scramble: the raid it is sent against (and its package); sweep: the patrol it hunts
    if player.task is Task.INTERCEPT and player.intercepts in c.flights:
        raid_pkg = c.packages.get(c.flights[player.intercepts].package_id)
        for fid in (raid_pkg.flight_ids if raid_pkg else [player.intercepts]):
            if c.flights[fid] not in plan.flights:
                plan.flights.append(c.flights[fid])
    if player.task is Task.SWEEP:
        for f in c.flights.values():
            if f.side is enemy and f.task is Task.CAP and f not in plan.flights \
                    and f.area.dist(dest) <= SWEEP_TARGET_RADIUS:
                plan.flights.append(f)

    # crisis: the border probes near the player's area are what the mission is about
    if not c.at_war:
        for f in c.flights.values():
            if f.side is enemy and f.task is Task.RECON and f not in plan.flights and (
                    f.id == player.intercepts or f.area.dist(dest) <= PROBE_RADIUS):
                plan.flights.append(f)

    # a CAP with nobody to meet is a dull hour: the nearest enemy CAP flight sweeps the station
    if c.at_war and player.task is Task.CAP and not any(f.side is enemy for f in plan.flights):
        sweepers = sorted((f for f in c.flights.values() if f.side is enemy and f.task is Task.CAP
                           and f.area and f.area.dist(dest) <= SWEEP_MAX_DIST),
                          key=lambda f: f.area.dist(dest))
        if sweepers:
            plan.flights.append(sweepers[0])
            plan.delay[sweepers[0].id] = SWEEP_DELAY
            plan.sweep.add(sweepers[0].id)

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
        deck = c.airbases[c.squadrons[f.squadron_id].base_id].carrier_of
        if deck and deck in c.objectives:            # carrier flights need their ship
            objs[deck] = c.objectives[deck]
    plan.objectives = list(objs.values())
    # spawn order: player first, then main flights, then the flights that support them
    plan.flights.sort(key=lambda f: (f is not player, f.supports is not None))
    return plan


def _cap_station(c: Campaign, f: Flight, player: Flight) -> Pos:
    """Where a CAP flight orbits in this mission. Friendly CAP covering a player strike moves
    forward to just short of the target; everyone else flies their planned station."""
    if f is not player and f.side is player.side and (player.target_id or player.task is Task.ESCORT):
        station = _toward(_dest(c, player), _base(c, player), FORWARD_CAP_DIST)
    else:
        station = f.area
    return _clear(station, _rings(c, f.side, exempt=(_base(c, f),)))     # orbit outside SAM cover


def _skill(c: Campaign, f: Flight, i: int) -> Skill:
    """AI skill: Good for NATO crews and Warsaw Pact fighter pilots, Average for Warsaw Pact
    strike crews; veterans one level up, tired crews one level down."""
    from cwdc.engine.crews import GROUNDED
    sq = c.squadrons[f.squadron_id]
    level = 1 if sq.side is Side.BLUE or f.task in (Task.CAP, Task.ESCORT) else 0
    p = c.pilots.get(f.pilot_ids[i]) if i < len(f.pilot_ids) else None
    if p is not None:
        if p.sorties >= 15 or p.air_kills >= 3:
            level += 1
        if p.fatigue >= 0.6 * GROUNDED:
            level -= 1
    return [Skill.Average, Skill.Good, Skill.High][max(0, min(2, level))]


def _unit_name(f: Flight, i: int) -> str:
    return f"{f.id}-{i + 1} {f.callsign}"


def _apply_loadout(group, f: Flight, type_id: str, dcs_task, c: Campaign | None = None) -> None:
    if c is not None:
        pay = loadouts.flight_payload(c, f)
        if pay["pylons"] is not None:
            for unit in group.units:
                unit.pylons = {n: {"CLSID": cl} for n, cl in pay["pylons"].items()}
            return
    elif f.loadout and f.loadout != "default" and loadouts.loadouts(type_id):
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


def _ahead(path: list[Pos], spawn: Pos | None) -> list[Pos]:
    """The intermediate turning points of `path` still ahead of a flight spawning at `spawn`."""
    if spawn is None or len(path) <= 2:
        return []
    leg = min(range(len(path) - 1), key=lambda i: _closest_on(path[i], path[i + 1], spawn)[0].dist(spawn))
    return path[leg + 1:-1]


def _wp_index(group, name: str) -> int:
    """Index of the group's waypoint called `name` (its last waypoint if there is none)."""
    return next((i for i, p in enumerate(group.points) if p.name == name), len(group.points) - 1)


def _pos_of(point) -> Pos:
    return Pos(x=point.position.x, z=point.position.y)


BOT_TIME_LIMIT = 2 * 3600       # s; bot missions end at the latest after this much mission time


def build(c: Campaign, player_flight_id: str, out_dir: Path, start: str = "runway",
          bot: bool = False, time_limit: int = BOT_TIME_LIMIT) -> MissionRecord:
    """Build the mission around `player_flight_id`.

    bot=True: an AI-only mission. The focus flight is flown by its AI crew, there is no
    player slot (DCS starts in spectator view), and the mission ends itself once the
    focus flight has landed or been lost, or after BOT_TIME_LIMIT.
    """
    T.use(c)
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
    apply_weather(m, c)

    tag = f"-{c.instance}" if c.instance else ""
    mission_id = f"{c.id}{tag}-t{c.turn:03d}-{player.id}"
    manifest: dict[str, dict] = {}
    tracked: list[str] = []
    obj_groups: dict[str, object] = {}
    tracked_statics: list[str] = []
    static_targets: dict[str, Pos] = {}
    blocked: list[Pos] = []                   # statics on parking slots: keep the player's slots clear
    groups: dict[str, object] = {}
    spawn_at: dict[str, Pos] = {}

    # time for the player to reach the destination: AI strikes are synchronised to it
    pspec = T.airframe_catalogue().get(c.squadrons[player.squadron_id].aircraft_type, {})
    p_speed = pspec.get("cruise_kmh", 800) / 3.6
    paths = {f.id: _path(c, f) for f in plan.flights}
    p_path = paths[player.id]
    p_len = sum(a.dist(b) for a, b in zip(p_path, p_path[1:]))
    p_dist = min(AIR_START_DIST, p_len) if start == "air" else p_len
    player_eta = TAXI_TIME[start] + p_dist / p_speed
    player_base = _base(c, player)
    player_spawn = _back_along(p_path, AIR_START_DIST)[0] if start == "air" else player_base
    player_station = _cap_station(c, player, player) if player.task is Task.CAP else _dest(c, player)

    # --- objectives (spawn first so strike flights can target their group ids)
    for o in plan.objectives:
        alive = [(i, u) for i, u in enumerate(o.units) if u.alive]
        if not alive:
            continue
        country = m.country(o.country)
        gname = f"{o.id} {o.name}"
        ships = [(i, u) for i, u in alive if u.type in ship_map]
        vehicles = [(i, u) for i, u in alive if u.type in vehicle_map]
        structures = [(i, u) for i, u in alive if u.type not in vehicle_map and u.type not in ship_map]
        if ships:                            # a naval group at sea (a carrier group steams into the wind)
            i0, u0 = ships[0]
            sgp = m.ship_group(country, gname, ship_map[u0.type],
                               T.to_point(Pos(x=o.pos.x + u0.dx, z=o.pos.z + u0.dz)))
            sgp.units[0].name = f"{o.id}#{i0}"
            for i, u in ships[1:]:
                sh = m.ship(f"{o.id}#{i}", ship_map[u.type])
                sh.position = T.to_point(Pos(x=o.pos.x + u.dx, z=o.pos.z + u.dz))
                sgp.add_unit(sh)
            if any(ab.carrier_of == o.id for ab in c.airbases.values()):
                sgp.add_waypoint(T.to_point(Pos(x=o.pos.x, z=o.pos.z - CARRIER_TRACK)), CARRIER_SPEED_KMH)
            for unit in sgp.units:
                manifest[unit.name] = {"kind": "objective", "objective": o.id, "unit": int(unit.name.split("#")[1])}
            obj_groups[o.id] = sgp
            tracked.append(gname)
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
        for i, u in structures:              # rail yards, depots, parked aircraft: one static each
            key = f"{o.id}#{i}"
            where = Pos(x=o.pos.x + u.dx, z=o.pos.z + u.dz)
            sg = m.static_group(country, key, static_type(u.type), T.to_point(where))
            blocked.append(where)
            sg.units[0].name = key
            manifest[key] = {"kind": "objective", "objective": o.id, "unit": i, "static": True}
            tracked_statics.append(key)
        if structures:                       # aim at the structures, not the reference point
            static_targets[o.id] = Pos(x=sum(o.pos.x + u.dx for _, u in structures) / len(structures),
                                       z=sum(o.pos.z + u.dz for _, u in structures) / len(structures))

    # --- flights
    order = list(plan.flights)
    escorted_by_player = c.flights.get(player.supports) if player.supports else None
    probe_of_player = c.flights.get(player.intercepts) if player.intercepts else None
    for first in (escorted_by_player, probe_of_player):
        if bot and first in order:
            # an AI-flown focus escort / QRA needs the group it escorts / follows to exist first
            order.remove(first)
            order.insert(0, first)
    for k, f in enumerate(order):
        sq = c.squadrons[f.squadron_id]
        spec = T.airframe_catalogue().get(sq.aircraft_type, {})
        type_cls = plane_map[sq.aircraft_type]
        country = m.country(sq.country)
        base = c.airbases[sq.base_id]
        deck = obj_groups.get(base.carrier_of) if base.carrier_of else None
        airport = None if base.carrier_of else terrain.airports[base.dcs_name]
        dcs_task = DCS_TASK.get(f.task, dtask.CAP)
        gname = f"{f.id} {f.callsign}"
        alt = spec.get("cruise_alt_m", 5000) + 300 * (k % 4)     # deconflict altitudes
        speed = spec.get("cruise_kmh", 800)
        is_player = f is player
        dest = _dest(c, f)
        n = len(f.airframe_ids)

        if is_player and start in START_TYPES and deck is not None:      # off the carrier's deck
            g = m.flight_group_from_unit(country, gname, type_cls, deck, maintask=dcs_task,
                                         start_type=START_TYPES[start], group_size=n)
        elif is_player and start in START_TYPES:
            slots = _adjacent_slots(airport, type_cls, n, blocked) if start == "ramp" else None
            g = m.flight_group_from_airport(country, gname, type_cls, airport, maintask=dcs_task,
                                            start_type=START_TYPES[start], group_size=n,
                                            parking_slots=slots)
        else:
            main = c.flights.get(f.supports) if f.supports else None
            if is_player:                                   # air start en route
                spawn = player_spawn
            elif f.id == player.supports:
                # the player escorts / supports this flight: it launches with the player
                spawn = player_spawn
                if start in START_TYPES:
                    plan.delay.setdefault(f.id, TAXI_TIME[start])
            elif f.task is Task.ESCORT and main is not None and main.id in spawn_at:
                spawn = spawn_at[main.id]                   # escort forms up on its package
                if main.id in plan.delay:
                    plan.delay.setdefault(f.id, plan.delay[main.id])
            elif f.task is Task.RECON:                      # border probe: runs in from the east
                if player.task is Task.QRA:                 # already inbound when QRA scrambles
                    run_in = min(max((speed / 3.6) * player_eta, PROBE_RUN_IN[0]), PROBE_RUN_IN[1])
                else:
                    run_in = 70_000
                    plan.delay.setdefault(f.id, max(0, int(player_eta - run_in / (speed / 3.6))))
                spawn = T.toward_enemy(f.area, run_in)
            elif f.id in plan.sweep:                        # sweep in from the enemy side
                spawn = _beyond(player_station, f.area, SWEEP_START_DIST)
            elif f.task in (Task.CAP, Task.ESCORT):
                spawn = _cap_station(c, f, player)
            else:                                           # AI strike: arrive with the player
                ai_dist = (speed / 3.6) * player_eta
                raid = c.flights.get(player.intercepts) if player.task is Task.INTERCEPT else None
                if raid is not None and f.package_id == raid.package_id:
                    ai_dist += _dest(c, player).dist(dest)  # ...at the scramble point, not the target
                spawn = _back_along(paths[f.id], ai_dist)[0]
            if f.side is not player.side and spawn.dist(player_spawn) < MIN_CONTACT_DIST:
                spawn = _beyond(player_spawn, spawn, MIN_CONTACT_DIST)   # no point-blank merges
            spawn_at[f.id] = spawn
            spawn = _offset(spawn, dest, FLIGHT_SPACING * k)
            g = m.flight_group_inflight(country, gname, type_cls, T.to_point(spawn), altitude=alt,
                                        speed=speed, maintask=dcs_task, group_size=n)
            _spread_units(g, dest)
        groups[f.id] = g
        if f.id in plan.delay:
            _delay(g, plan.delay[f.id])
        _apply_loadout(g, f, sq.aircraft_type, dcs_task, c)
        _set_callsign(g, f, sq.callsign)

        for i, unit in enumerate(g.units):
            unit.name = _unit_name(f, i)
            af = c.airframes[f.airframe_ids[i]]
            manifest[unit.name] = {"kind": "airframe", "flight": f.id, "airframe": af.id,
                                   "pilot": f.pilot_ids[i]}
            if i < len(f.wso_ids):
                manifest[unit.name]["wso"] = f.wso_ids[i]
            if sq.aircraft_type == "F-4E-45MC":
                unit.set_property("Quality", af.quality)
            if bot or not (is_player and i == 0):
                unit.skill = _skill(c, f, i)
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
        if ai_flown and not c.at_war:                       # crisis: nobody shoots first
            g.points[0].tasks.append(dtask.OptROE(dtask.OptROE.Values.ReturnFire))

        # waypoints: SAM detours still ahead of the spawn point, then the task's own points
        path = paths.get(f.id) or _path(c, f)
        detours = path[1:-1] if (is_player and start in START_TYPES) else _ahead(path, spawn_at.get(f.id))
        escorted = groups.get(f.supports) if f.task is Task.ESCORT and f.supports else None
        if ai_flown and escorted is not None and (bot or c.flights[f.supports] is not player):
            # AI escort: stay with the package and engage fighters that threaten it
            g.points[0].tasks = [t for t in g.points[0].tasks if not isinstance(t, dtask.EscortTaskAction)]
            # ...as far as its IP; then it holds outside the target's SAM cover and goes home,
            # instead of circling high over the SA-6 that defends the target
            ip_i = _wp_index(escorted, "IP")
            g.points[0].tasks.append(dtask.EscortTaskAction(escorted.id, engagement_max_dist=60_000,
                                                             lastwpt=ip_i))
            for p in detours:
                g.add_waypoint(T.to_point(p), alt, speed, name="DETOUR")
            hold = _clear(_pos_of(escorted.points[ip_i]), _rings(c, f.side, exempt=(_base(c, f),)))
            hwp = g.add_waypoint(T.to_point(hold), alt, speed, name="HOLD")
            orbit = dtask.ControlledTask(dtask.OrbitAction(alt, speed, dtask.OrbitAction.OrbitPattern.Circle))
            orbit.stop_after_duration(ESCORT_HOLD_S)
            hwp.tasks.append(orbit)
        elif f.task is Task.RECON:
            # probe: up to (just over) the border, along it, and back home
            g.add_waypoint(T.to_point(f.area), alt, speed, name="PROBE")
            nx, nz = T.nato_dir()
            along = Pos(x=f.area.x - nz * 20_000, z=f.area.z + nx * 20_000)     # parallel to the border
            g.add_waypoint(T.to_point(along), alt, speed, name="PROBE-2")
            g.add_waypoint(T.to_point(T.toward_enemy(along, 60_000)), alt, speed, name="EXIT")
        elif f.task in FIGHTER_TASKS or (f.task is Task.ESCORT and ai_flown):
            probe_g = groups.get(f.intercepts) if f.task is Task.QRA and f.intercepts else None
            if ai_flown and probe_g is not None:
                # AI QRA: vectored onto the probe, then shadows it
                follow = dtask.ControlledTask(dtask.Follow(probe_g.id, Vector2(-1_000, 300), 0))
                follow.stop_after_duration(QRA_SHADOW_S)
                g.points[0].tasks.append(follow)
            station_pos = player_station if f.id in plan.sweep else _cap_station(c, f, player)
            if f.id not in plan.sweep:
                for p in detours:
                    g.add_waypoint(T.to_point(p), alt, speed, name="DETOUR")
            station = T.to_point(station_pos)
            wp = g.add_waypoint(station, alt, speed, name="CAP")
            g.points[0].tasks = [t for t in g.points[0].tasks       # pydcs' empty default escort task
                                 if not isinstance(t, dtask.EscortTaskAction)]
            if ai_flown and f.task is Task.RESCAP:
                # cover the survivor: hit anything on the ground closing in
                wp.tasks.append(dtask.OptROE(dtask.OptROE.Values.OpenFireWeaponFree))
                wp.tasks.append(dtask.EngageTargetsInZone(station, RESCAP_ENGAGE_RADIUS,
                                                          [dtask.Targets.All.GroundUnits]))
            if ai_flown and f.task in (Task.INTERCEPT, Task.SWEEP):
                # scramble / sweep: engage whatever comes into the area
                wp.tasks.append(dtask.EngageTargetsInZone(station, CAP_ENGAGE_RADIUS, [dtask.Targets.All.Air]))
            if ai_flown and f.side is not player.side and c.at_war:
                # enemy fighters in this mission are in the player's fight: they come to it
                wp.tasks.append(dtask.EngageTargetsInZone(T.to_point(player_station),
                                                          CAP_ENGAGE_RADIUS, [dtask.Targets.All.Air]))
            if ai_flown and f.side is player.side and (player.target_id or player.task is Task.ESCORT):
                # covering the player's strike: engage anything approaching the target area
                wp.tasks.append(dtask.EngageTargetsInZone(T.to_point(_dest(c, player)),
                                                          CAP_ENGAGE_RADIUS, [dtask.Targets.All.Air]))
            if is_player:
                orbit = dtask.OrbitAction(alt, speed, dtask.OrbitAction.OrbitPattern.RaceTrack)
                if ai_flown:                     # AI crews go home after their time on station
                    orbit = dtask.ControlledTask(orbit)
                    orbit.stop_after_duration(ON_STATION_S)
                wp.tasks.append(orbit)
                leg2 = _clear(Pos(x=station_pos.x + 30_000, z=station_pos.z), _rings(c, f.side, exempt=(base.pos,)))
                g.add_waypoint(T.to_point(leg2), alt, speed, name="CAP-2")
            else:
                wp.tasks.append(dtask.OrbitAction(alt, speed, dtask.OrbitAction.OrbitPattern.Circle))
        else:
            ip = _toward(dest, detours[-1] if detours else spawn_at.get(f.id, base.pos), 20_000)
            low = alt if f.task is Task.ESCORT else spec.get("low_alt_m", 500)   # escorts stay high
            ipwp = g.add_waypoint(T.to_point(ip), low, speed, name="IP")
            twp = g.add_waypoint(T.to_point(dest), low, speed, name="TGT")
            for wp in (ipwp, twp):
                if f.task is not Task.ESCORT:
                    wp.alt_type = "RADIO"  # low level means height above ground, not sea level
            if f is player and f.task is Task.ARMED_RECON and f.target_id in obj_groups:
                _move_toward_front(c, obj_groups[f.target_id])   # the column is on the move
            if ai_flown and f.target_id in static_targets:
                # structures (rail yard, depot): bomb the site
                twp.tasks.append(dtask.Bombing(T.to_point(static_targets[f.target_id]), group_attack=True))
            elif ai_flown and f.target_id in obj_groups:
                twp.tasks.append(dtask.AttackGroup(obj_groups[f.target_id].id))
            g.add_waypoint(T.to_point(ip), alt, speed, name="EGRESS")
        if is_player or escorted is not None or not (f.task in (Task.CAP, Task.ESCORT) and ai_flown):
            for p in reversed(path[1:-1]):       # home the same way round the SAMs
                g.add_waypoint(T.to_point(p), alt, speed, name="RETURN")
            _land(g, airport, deck)
        tracked.append(gname)

    rescue_cfg = _rescue_party(m, c, player, player_eta, tracked) if player.task is Task.RESCAP else None
    scene = None
    if c.packages[player.package_id].reaction:
        scene = _reaction_scene(m, c, player, player_eta, obj_groups, manifest, tracked)
    fac_cfg = None
    if player.task is Task.CAS and player.target_id in obj_groups:
        fac_cfg = {"callsign": FAC_CALLSIGN, "target": obj_groups[player.target_id].name,
                   "focus": [f"{player.id} {player.callsign}"], "range": FAC_RANGE,
                   "offset": FAC_OFFSET, "interval": FAC_INTERVAL, "menu": not bot}

    # --- briefing
    sq = c.squadrons[player.squadron_id]
    lines = (["BOT MISSION (AI only): spectate with F10 / F2 and speed up with Ctrl+Z. It ends "
              f"itself when {player.callsign} has landed or been lost, or after "
              f"{time_limit // 60} min.", ""] if bot else [])
    lines += [f"{c.name} - turn {c.turn}, {c.time:%d %b %Y %H:%M} local",
             f"Flight: {player.callsign} ({sq.name}), {len(player.airframe_ids)}x {sq.aircraft_type}"
             f" from {_base_label(c, c.airbases[sq.base_id])} ({start} start)",
             f"Loadout: {loadouts.flight_payload(c, player)['text']}",
             f"Tasking: {c.packages[player.package_id].description}"]
    from cwdc.engine import weather as wxmod
    lines.append("Weather: " + wxmod.describe(c.weather) + ".")
    if player.task is Task.INTERCEPT and player.intercepts in c.flights:
        raid = c.flights[player.intercepts]
        rtgt = c.objectives.get(raid.target_id) if raid.target_id else None
        lines.append(f"SCRAMBLE: {raid.callsign} ({len(raid.airframe_ids)}x "
                     f"{c.squadrons[raid.squadron_id].aircraft_type}) inbound"
                     + (f" to {rtgt.name}" if rtgt else "") + ". Meet it at the scramble point; "
                     "MAGIC (F10) will vector you.")
    if player.loadout == RUNWAY_LOADOUT and player.target_id:
        lines.append(f"RUNWAY ATTACK: crater the runway at {c.objectives[player.target_id].name}. Cross it "
                     "at an angle and string your M117s along it; every hit near the runway counts.")
    if player.loadout == PRECISION_LOADOUT and player.target_id:
        lines.append(f"PRECISION STRIKE: GBU-8 HOBOS (TV-guided) on {c.objectives[player.target_id].name}. "
                     "The WSO locks the target on the scope before release; the Pave Spike pod is carried.")
    if player.task is Task.SEAD and player.target_id and \
            c.packages[player.package_id].description.startswith("Iron Hand"):
        lines.append(f"IRON HAND: hunt {c.objectives[player.target_id].name} with Shrikes. Fire while its "
                     "radar is up; it may shut down to hide - press on and it will come back up.")
    if scene and scene["kind"] in ("tic", "hold"):
        head = f"HOLD THE {scene['name'].upper()}" if scene["kind"] == "hold" else "TROOPS IN CONTACT"
        lines.append(f"{head}: {scene['enemy']} is attacking {scene['friends']}. Friendly armour is "
                     f"{CONTACT_DIST / 1000:.1f} km west of the enemy column - check your target before you "
                     "roll in. Stop the lead battalion and the attack in this sector stalls.")
    if scene and scene["kind"] == "helo":
        lines.append(f"HELICOPTER HUNT: an assault wave ({scene['types']}) is crossing the front low and "
                     f"heading for a landing zone {(HELO_LZ_DIST + reactive_behind()) // 1000} km behind our lines. Shoot down at "
                     "least half and the assault is broken up.")
    reaction = c.packages[player.package_id].reaction
    if reaction in ("hunt", "bomber", "airfield"):
        lines.append({"hunt": "HUNT THE RAID: Warsaw Pact attack aircraft are going for our troops. Catch "
                              "them low over the battlefield before they reach the front line.",
                      "bomber": "BOMBER INTERCEPT: a bomber raid is heading deep into our rear. Get to it "
                                "before it reaches the target - mind its escorts.",
                      "airfield": "AIRFIELD DEFENCE: a raid is inbound to your own airfield. Get airborne now "
                                  "and meet it before it is over the runway."}[reaction])
    if player.task is Task.ARMED_RECON and player.target_id:
        lines.append(f"ARMED RECON: hunt along the roads within {ARMED_RECON_BOX // 1000} km of "
                     f"{c.objectives[player.target_id].name} - the column is moving up to the front. "
                     "Anything military in the box is fair game; mind the column's flak.")
    if fac_cfg:
        lines.append(f"FAC: {fac_cfg['callsign']} is working the target area. Check in on the F10 menu; "
                     "he marks the target with white smoke and talks you on.")
    if rescue_cfg:
        lat, lon = T.pos_to_latlon(player.area)
        lines.append(f"RESCAP: {rescue_cfg['who']} is down and evading at {lat:.3f}N {lon:.3f}E. "
                     f"{rescue_cfg['callsign']} (UH-1H) is inbound for the pickup; an enemy search party "
                     "is closing in. Hold the area, keep the helicopter alive - the survivor pops green "
                     "smoke when it is close.")
    if player.task is Task.SWEEP:
        lines.append("SWEEP: clear the enemy fighter patrol from the area before our strike arrives. "
                     "Don't get drawn deep into their SAM cover.")
    if c.at_war and c.war_turn == 1:
        lines.append(f"WAR: the Warsaw Pact attacked at {c.time:%H%M} today. Expect raids on our "
                     "airfields. ROE: WEAPONS FREE.")
    if not c.at_war:
        lines.append(f"CRISIS - tension {c.tension:.0f}/100. ROE: WEAPONS TIGHT. Identify and shadow; "
                     "do NOT fire unless fired upon. If a Warsaw Pact aircraft fires, you are cleared "
                     "to return fire. Firing first means an inquiry and could start the war.")
        probes = [f for f in plan.flights if f.task is Task.RECON]
        if probes:
            lines.append("Intruders expected: " + ", ".join(
                f"{f.callsign} ({c.squadrons[f.squadron_id].aircraft_type})" for f in probes)
                + f". An intercept counts once you hold within {INTERCEPT_RANGE / 1852:.0f} nm "
                  f"for {INTERCEPT_HOLD} s.")
    crew = []
    for i, pid in enumerate(player.pilot_ids):
        p = c.pilots[pid]
        seat = f"{p.rank} {p.name}"
        if i < len(player.wso_ids):
            w = c.pilots[player.wso_ids[i]]
            seat += f" / {w.rank} {w.name} (WSO)"
        crew.append(f"{player.callsign}-{i + 1} {seat}")
    lines.append("Crews: " + "; ".join(crew))
    if player.target_id:
        tgt = c.objectives[player.target_id]
        lat, lon = T.pos_to_latlon(tgt.pos)
        if tgt.kind is ObjectiveKind.AIRBASE:
            from collections import Counter
            kinds = Counter(u.type for u in tgt.units if u.alive)
            lines.append(f"Target: {tgt.name} at {lat:.3f}N {lon:.3f}E - airfield attack (OCA). "
                         "Reported: " + ", ".join(f"{n}x {t}" for t, n in kinds.items())
                         + ". Low-level run-in; hit the parked aircraft, not the runway.")
        else:
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
    magic = None
    gci = _theater(c).get("gci")
    if gci and not bot:
        bull = T.latlon_to_pos(*gci["bullseye"]["latlon"])
        radars = [f"{o.id} {o.name}" for o in plan.objectives if o.side is player.side
                  and o.kind in (ObjectiveKind.EWR, ObjectiveKind.SAM) and o.id in obj_groups]
        radars += [f"{f.id} {f.callsign}" for f in plan.flights if f.side is player.side and f is not player
                   and f.task in (Task.CAP, Task.ESCORT, Task.QRA)]
        magic = {"callsign": gci.get("callsign", "MAGIC"), "side": 2 if player.side is Side.BLUE else 1,
                 "groups": [f"{player.id} {player.callsign}"], "radars": radars,
                 "bullseye": {"x": bull.x, "z": bull.z, "name": gci["bullseye"]["name"]}, "threat_nm": 25}
        bc = m.coalition["blue" if player.side is Side.BLUE else "red"]
        bc.bullseye = {"x": bull.x, "y": bull.z}
        lines.append(f"GCI: {magic['callsign']} on the F10 radio menu (bogey dope, picture, threat "
                     f"calls); it only sees what our radars hold. Bullseye: {gci['bullseye']['name']}.")
    lines += _situation_lines(c, player, plan)
    lines.append("Exit the mission normally when done; run `cwdc ingest` afterwards.")
    m.set_description_text("\n".join(lines))
    briefing = list(lines)
    m.set_description_bluetask_text(c.packages[player.package_id].description)

    end_when = {"groups": [f"{player.id} {player.callsign}"], "time_limit": time_limit}         if bot else None
    probes = [f"{f.id} {f.callsign}" for f in plan.flights if f.task is Task.RECON]
    intercept = ({"focus": [f"{player.id} {player.callsign}"], "targets": probes,
                  "range": INTERCEPT_RANGE, "hold": INTERCEPT_HOLD} if probes else None)
    inject_runtime(m, mission_id, tracked, end_when=end_when, tracked_statics=tracked_statics,
                   roe="weapons_free" if c.at_war else "weapons_tight", intercept=intercept, magic=magic,
                   rescue=rescue_cfg and {k: rescue_cfg[k] for k in ("helo", "x", "z", "radius", "hold")},
                   fac=fac_cfg,
                   impacts={"groups": [f"{player.id} {player.callsign}"]} if player.loadout == RUNWAY_LOADOUT else None)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / mission_filename(c, player, bot)
    with tempfile.TemporaryDirectory() as tmp:
        if not bot:                                   # kneeboard pages for the player's aircraft
            info = _kneeboard_info(c, player, plan, groups[player.id], lines, magic)
            for page in kneeboard.render(info, Path(tmp), mission_id):
                m.add_aircraft_kneeboard(plane_map[sq.aircraft_type], page)
        m.save(str(path))
    return MissionRecord(mission_id=mission_id, turn=c.turn, player_flight_id=player.id,
                         miz_path=str(path), generated_at=time.time(), manifest=manifest,
                         spawned_flights=[f.id for f in plan.flights], bot=bot, briefing=briefing)


def static_type(type_id: str):
    """pydcs class for a static object type id (fortifications, warehouses, ...)."""
    from dcs import planes, statics
    for table in (statics.fortification_map, statics.warehouse_map, statics.groundobject_map,
                  statics.cargo_map, planes.plane_map):
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


def _adjacent_slots(airport, type_cls, n: int, blocked: list[Pos] = ()):
    """Parking slots close together, so an AI wingman does not taxi across the base."""
    free = [s for s in airport.free_parking_slots(type_cls)
            if all(math.hypot(s.position.x - b.x, s.position.y - b.z) > 40 for b in blocked)]
    if len(free) < n:
        return None
    best, best_d = None, math.inf
    for s in free:
        near = sorted(free, key=lambda o: s.position.distance_to_point(o.position))[:n]
        d = sum(s.position.distance_to_point(o.position) for o in near)
        if d < best_d:
            best, best_d = near, d
    return best
