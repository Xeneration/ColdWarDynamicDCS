"""JSON views of a campaign for the web UI. Read-only: nothing here changes the campaign."""
from __future__ import annotations

from collections import Counter

from cwdc import theater as T
from cwdc.model import AirframeStatus, Campaign, CrewRole, Flight, FlightStatus, PilotStatus, Pos, Side


def _ll(p: Pos) -> list[float]:
    lat, lon = T.pos_to_latlon(p)
    return [round(lat, 5), round(lon, 5)]


def version(c: Campaign) -> str:
    """Changes whenever anything the UI shows may have changed."""
    pm = c.pending_mission.mission_id if c.pending_mission else "-"
    return f"{c.turn}:{pm}:{len(c.news)}:{bool(c.outcome)}"


def _person(c: Campaign, pid: str) -> dict:
    from cwdc.engine.crews import describe_fatigue
    p = c.pilots[pid]
    return {"id": p.id, "name": p.name, "rank": p.rank, "role": p.role.value, "status": p.status.value,
            "fatigue": round(p.fatigue, 2), "rest": describe_fatigue(p), "sorties": p.sorties,
            "air_kills": p.air_kills, "ground_kills": p.ground_kills, "is_player": p.is_player}


def overview(c: Campaign) -> dict:
    from cwdc.actions import result_status
    from cwdc.engine import airbases, ground, logistics, outcome
    from cwdc.engine.planner import period
    me = c.player_pilot()
    pm = c.pending_mission
    pending = None
    if pm:
        f = c.flights.get(pm.player_flight_id)
        pending = {"mission_id": pm.mission_id, "flight_id": pm.player_flight_id,
                   "callsign": f.callsign if f else "?", "miz_path": pm.miz_path, "bot": pm.bot,
                   "briefing": pm.briefing, "result": result_status(c),
                   "others": [c.flights[x].callsign for x in pm.spawned_flights
                              if x in c.flights and x != pm.player_flight_id]}
    squadrons = []
    for sq in c.squadrons.values():
        afs = Counter(a.status for a in c.airframes.values() if a.squadron_id == sq.id)
        squadrons.append({
            "id": sq.id, "name": sq.name, "side": sq.side.value, "type": sq.aircraft_type,
            "base": base_name(c, c.airbases[sq.base_id]), "player": sq.player_squadron,
            "ready": afs[AirframeStatus.READY], "repair": afs[AirframeStatus.DAMAGED],
            "lost": afs[AirframeStatus.DESTROYED], "pilots": len(c.squadron_pilots(sq.id)),
            "wsos": len(c.squadron_pilots(sq.id, role=CrewRole.WSO)) if sq.establishment_wsos else None})
    sectors = []
    for sec in c.sectors.values():
        forms = [{"id": f.id, "name": f.name, "side": f.side.value, "strength": round(f.strength),
                  "max": round(f.max_strength), "supply": round(f.supply, 2),
                  "readiness": round(f.readiness, 2), "posture": f.posture.value}
                 for f in c.formations.values() if f.sector_id == sec.id and ground.active(c, f)]
        sectors.append({"id": sec.id, "name": sec.name, "offset_km": round(sec.offset_km, 1),
                        "attacker": sec.attacker.value if sec.attacker else None,
                        "red": round(ground.power(c, sec.id, Side.RED)),
                        "blue": round(ground.power(c, sec.id, Side.BLUE)), "formations": forms})
    coming = sorted((f for f in c.formations.values() if f.side is Side.BLUE and f.arrival_turn > c.turn),
                    key=lambda f: f.arrival_turn)
    return {
        "version": version(c), "id": c.id, "name": c.name, "turn": c.turn, "time": c.time.isoformat(),
        "period": period(c), "war_day": outcome.war_day(c),
        "player": _person(c, me.id) | {"squadron": c.squadrons[me.squadron_id].name,
                                       "base": base_name(c, c.airbases[c.squadrons[me.squadron_id].base_id])},
        "pending": pending,
        "outcome": ({"title": c.outcome.title, "reason": c.outcome.reason,
                     "winner": c.outcome.winner.value if c.outcome.winner else None,
                     "summary": outcome.summary(c)} if c.outcome else None),
        "situation": outcome.situation(c),
        "war": outcome.situation_data(c),
        "squadrons": squadrons, "sectors": sectors,
        "reinforcements": [{"name": f.name, "turn": f.arrival_turn} for f in coming],
        "supply": [l.strip() for l in logistics.report(c)],
        "airfields": [{"id": ab.id, "name": base_name(c, ab), "side": ab.side.value,
                       "damage": round(ab.damage, 2), "state": airbases.describe(ab)}
                      for ab in c.airbases.values()],
        "news": [{"turn": n.turn, "time": n.time.isoformat(), "text": n.text, "category": n.category,
                  "source": n.source} for n in c.news[-30:]][::-1],
    }


TASK_PLAIN = {
    "CAP": "Combat air patrol: guard an area and shoot down enemy aircraft that come near",
    "Escort": "Escort: fly with a strike package and fight off enemy fighters that go for it",
    "Strike": "Strike: bomb a fixed target such as a radar, a depot, a rail yard or an airfield",
    "CAS": "Close air support: attack enemy troops that are fighting ours right now",
    "BAI": "Battlefield interdiction: hit enemy units just behind the front before they reach it",
    "SEAD": "Suppression of air defences: knock out the missile site that threatens the package",
    "Interdiction": "Interdiction: cut the enemy's supply lines (rail yards, convoys, depots)",
    "Recon": "Reconnaissance: photograph a target area",
    "Airlift": "Airlift: carry supplies or troops",
}


def base_name(c: Campaign, ab) -> str:
    from cwdc.web.mapdata import base_name as _bn
    return _bn(c, ab)


def _flight_dest(c: Campaign, f: Flight) -> Pos:
    return f.area if f.area else c.objectives[f.target_id].pos


def _distance(c: Campaign, f: Flight) -> tuple[float, float]:
    """(nm one way, minutes one way) from the flight's base to its target or station."""
    base = c.airbases[c.squadrons[f.squadron_id].base_id].pos
    km = base.dist(_flight_dest(c, f)) / 1000
    kmh = T.airframe_catalogue().get(c.squadrons[f.squadron_id].aircraft_type, {}).get("cruise_kmh", 800)
    return km / 1.852, km / kmh * 60


LONG_TRANSIT_NM = 120          # beyond this the ATO suggests an air start


def flight_route(c: Campaign, f: Flight) -> dict:
    nm, minutes = _distance(c, f)
    base = c.airbases[c.squadrons[f.squadron_id].base_id]
    return {"id": f.id, "side": f.side.value, "callsign": f.callsign, "task": f.task.value,
            "from": _ll(base.pos), "to": _ll(_flight_dest(c, f)), "eligible": f.player_eligible,
            "player": f.status is FlightStatus.ASSIGNED_PLAYER, "package": f.package_id,
            "supports": f.supports, "distance_nm": round(nm), "eta_min": round(minutes),
            "base": base_name(c, base)}


def threats(c: Campaign, f: Flight) -> dict:
    """What stands in this flight's way, from the planning data (no dice rolled)."""
    from cwdc.engine.resolver import AAA_TYPES
    from cwdc.missiongen import builder
    route = builder._route(c, f)
    enemy = f.side.enemy
    sams = [o for o in c.objectives.values() if o.side is enemy and o.kind.value == "sam" and not o.destroyed
            and any(p.dist(o.pos) <= o.threat_range_m for p in route)]
    tgt = c.objectives.get(f.target_id) if f.target_id else None
    aaa = sum(1 for u in tgt.units if u.alive and any(a in u.type for a in AAA_TYPES)) if tgt else 0
    fighters = [x for x in c.flights.values() if x.side is enemy and x.task.value == "CAP"
                and min(p.dist(x.area) for p in route) <= builder.ENEMY_CAP_RADIUS]
    level = min(3, len(sams) + (1 if aaa else 0) + len(fighters))
    return {"sams": [o.name for o in sams], "aaa": aaa, "fighters": len(fighters),
            "level": ["low", "moderate", "high", "very high"][level]}


def _loadout_text(c: Campaign, f: Flight) -> str:
    from cwdc.missiongen import loadouts
    name = f.loadout or "default"
    spec = loadouts.loadouts(c.squadrons[f.squadron_id].aircraft_type).get(name, {})
    return spec.get("description") or ("standard load for the task" if name == "default" else name)


def ato(c: Campaign) -> dict:
    from cwdc.engine.planner import period
    rows = []
    for f in sorted(c.flights.values(), key=lambda f: (f.side.value, not f.player_eligible, f.id)):
        if f.side is not Side.BLUE:
            continue
        pk = c.packages[f.package_id]
        tgt = c.objectives.get(f.target_id) if f.target_id else None
        r = flight_route(c, f)
        rows.append({
            "id": f.id, "side": f.side.value, "callsign": f.callsign,
            "squadron": c.squadrons[f.squadron_id].name, "type": c.squadrons[f.squadron_id].aircraft_type,
            "base": r["base"], "task": f.task.value, "task_plain": TASK_PLAIN.get(f.task.value, f.task.value),
            "package": pk.id, "mission": pk.description, "reason": pk.reason,
            "target": tgt.name if tgt else None, "loadout": f.loadout or "default",
            "loadout_text": _loadout_text(c, f),
            "eligible": f.player_eligible, "assigned": f.status is FlightStatus.ASSIGNED_PLAYER,
            "supports": f.supports, "supports_callsign": c.flights[f.supports].callsign if f.supports else None,
            "aircraft": len(f.airframe_ids), "distance_nm": r["distance_nm"], "eta_min": r["eta_min"],
            "suggested_start": "air" if (tgt is not None and tgt.kind.value == "airbase")
                               or r["distance_nm"] > LONG_TRANSIT_NM else "runway",
            "threats": threats(c, f),
            "crews": [[_person(c, p)["name"] for p in seat]
                      for seat in ([f.pilot_ids[i]] + ([f.wso_ids[i]] if i < len(f.wso_ids) else [])
                                   for i in range(len(f.pilot_ids)))]})
    packages = []
    for pk in c.packages.values():
        if pk.side is not Side.BLUE:
            continue
        members = [r for r in rows if r["package"] == pk.id]
        packages.append({"id": pk.id, "task": pk.task.value, "description": pk.description,
                         "reason": pk.reason, "flights": members,
                         "flyable": any(m["eligible"] for m in members)})
    packages.sort(key=lambda p: (not p["flyable"], p["id"]))
    return {"turn": c.turn, "time": c.time.isoformat(), "period": period(c), "packages": packages,
            "flights": rows,
            "enemy_expected": sum(1 for f in c.flights.values() if f.side is Side.RED)}


def flight_preview(c: Campaign, flight_id: str) -> dict:
    """Who and what a generated mission for this flight would contain. Changes nothing."""
    from cwdc.missiongen import builder
    f = c.flights[flight_id]
    plan = builder.select(c, f)
    friendly = [{"callsign": x.callsign, "task": x.task.value,
                 "role": ("your flight" if x is f else "your package" if x.package_id == f.package_id
                          else "nearby")} for x in plan.flights if x.side is f.side]
    enemy = [x for x in plan.flights if x.side is not f.side]
    sites = [{"name": o.name, "kind": o.kind.value, "side": o.side.value} for o in plan.objectives]
    r = flight_route(c, f)
    return {"flight": flight_id, "callsign": f.callsign, "task_plain": TASK_PLAIN.get(f.task.value),
            "distance_nm": r["distance_nm"], "eta_min": r["eta_min"], "friendly": friendly,
            "enemy_fighters": sum(1 for x in enemy if x.task.value == "CAP"),
            "enemy_strikers": sum(1 for x in enemy if x.task.value != "CAP"),
            "sites": sites, "threats": threats(c, f), "loadout_text": _loadout_text(c, f)}


def history(c: Campaign) -> list[dict]:
    return [h.model_dump(mode="json") for h in c.history]


def squadron(c: Campaign) -> dict:
    sq = c.squadrons[c.player_pilot().squadron_id]
    crew = [_person(c, p.id) for p in c.pilots.values() if p.squadron_id == sq.id]
    crew.sort(key=lambda p: (p["status"] != PilotStatus.ACTIVE.value, -p["sorties"], p["name"]))
    afs = [{"id": a.id, "status": a.status.value, "quality": a.quality, "sorties": a.sorties,
            "repair_turns": a.repair_turns}
           for a in c.airframes.values() if a.squadron_id == sq.id]
    afs.sort(key=lambda a: (a["status"] == "destroyed", a["id"]))
    return {"name": sq.name, "type": sq.aircraft_type, "base": base_name(c, c.airbases[sq.base_id]),
            "pilots": [p for p in crew if p["role"] == CrewRole.PILOT.value],
            "wsos": [p for p in crew if p["role"] == CrewRole.WSO.value], "airframes": afs}


def debriefs(c: Campaign) -> list[dict]:
    return [{"turn": d.turn, "time": d.time.isoformat(), "flown_in_dcs": d.flown_in_dcs, "lines": d.lines,
             "stats": d.stats} for d in reversed(c.debriefs)]
