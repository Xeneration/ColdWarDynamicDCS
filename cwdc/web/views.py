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
            "base": c.airbases[sq.base_id].dcs_name, "player": sq.player_squadron,
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
        "player": _person(c, me.id) | {"squadron": c.squadrons[me.squadron_id].name},
        "pending": pending,
        "outcome": ({"title": c.outcome.title, "reason": c.outcome.reason,
                     "winner": c.outcome.winner.value if c.outcome.winner else None,
                     "summary": outcome.summary(c)} if c.outcome else None),
        "situation": outcome.situation(c),
        "squadrons": squadrons, "sectors": sectors,
        "reinforcements": [{"name": f.name, "turn": f.arrival_turn} for f in coming],
        "supply": [l.strip() for l in logistics.report(c)],
        "airfields": [{"id": ab.id, "name": ab.dcs_name, "side": ab.side.value,
                       "damage": round(ab.damage, 2), "state": airbases.describe(ab)}
                      for ab in c.airbases.values()],
        "news": [{"turn": n.turn, "time": n.time.isoformat(), "text": n.text} for n in c.news[-20:]][::-1],
    }


def _flight_dest(c: Campaign, f: Flight) -> Pos:
    return f.area if f.area else c.objectives[f.target_id].pos


def map_layers(c: Campaign, show_enemy_flights: bool = True) -> dict:
    flights = []
    for f in c.flights.values():
        if f.side is Side.RED and not show_enemy_flights:
            continue
        base = c.airbases[c.squadrons[f.squadron_id].base_id].pos
        flights.append({"id": f.id, "side": f.side.value, "callsign": f.callsign, "task": f.task.value,
                        "from": _ll(base), "to": _ll(_flight_dest(c, f)), "eligible": f.player_eligible,
                        "player": f.status is FlightStatus.ASSIGNED_PLAYER, "package": f.package_id,
                        "supports": f.supports})
    return {
        "front": [[round(a, 5), round(b, 5)] for a, b in c.front_line],
        "border": [[round(a, 5), round(b, 5)] for a, b in c.base_front_line],
        "sectors": [{"id": s.id, "name": s.name, "offset_km": round(s.offset_km, 1),
                     "attacker": s.attacker.value if s.attacker else None,
                     "at": [round(sum(c.front_line[i][0] for i in s.front_points) / len(s.front_points), 5),
                            round(sum(c.front_line[i][1] for i in s.front_points) / len(s.front_points), 5)]}
                    for s in c.sectors.values()],
        "airbases": [{"id": ab.id, "name": ab.dcs_name, "side": ab.side.value, "at": _ll(ab.pos),
                      "damage": round(ab.damage, 2),
                      "squadrons": [s.name for s in c.squadrons.values() if s.base_id == ab.id]}
                     for ab in c.airbases.values()],
        "objectives": [{"id": o.id, "name": o.name, "kind": o.kind.value, "side": o.side.value,
                        "at": _ll(o.pos), "alive": len(o.alive_units), "total": len(o.units),
                        "threat_m": o.threat_range_m, "value": o.value}
                       for o in c.objectives.values()],
        "cap_stations": {s.value: [_ll(p) for p in pts] for s, pts in c.cap_stations.items()},
        "flights": flights,
        "supply_nodes": [{"id": n.id, "name": n.name, "side": n.side.value, "at": _ll(n.pos),
                          "stock": round(n.stock), "capacity": round(n.capacity)}
                         for n in c.supply_nodes.values()],
        "supply_links": [{"id": l.id, "a": _ll(c.supply_nodes[l.a].pos), "b": _ll(c.supply_nodes[l.b].pos),
                          "side": c.supply_nodes[l.a].side.value, "damaged": round(l.damaged, 2),
                          "flow": round(l.flow), "capacity": round(l.capacity)}
                         for l in c.supply_links if l.a in c.supply_nodes and l.b in c.supply_nodes],
    }


def ato(c: Campaign) -> dict:
    rows = []
    for f in sorted(c.flights.values(), key=lambda f: (f.side.value, not f.player_eligible, f.id)):
        pk = c.packages[f.package_id]
        tgt = c.objectives.get(f.target_id) if f.target_id else None
        rows.append({
            "id": f.id, "side": f.side.value, "callsign": f.callsign,
            "squadron": c.squadrons[f.squadron_id].name, "type": c.squadrons[f.squadron_id].aircraft_type,
            "task": f.task.value, "package": pk.id, "mission": pk.description,
            "target": tgt.name if tgt else None, "loadout": f.loadout or "default",
            "eligible": f.player_eligible, "assigned": f.status is FlightStatus.ASSIGNED_PLAYER,
            "supports": f.supports, "aircraft": len(f.airframe_ids),
            "crews": [[_person(c, p)["name"] for p in seat]
                      for seat in ([f.pilot_ids[i]] + ([f.wso_ids[i]] if i < len(f.wso_ids) else [])
                                   for i in range(len(f.pilot_ids)))]})
    blue = [r for r in rows if r["side"] == "blue"]
    return {"turn": c.turn, "time": c.time.isoformat(), "flights": blue,
            "enemy_expected": sum(1 for r in rows if r["side"] == "red"),
            "enemy": [r for r in rows if r["side"] == "red"]}


def squadron(c: Campaign) -> dict:
    sq = c.squadrons[c.player_pilot().squadron_id]
    crew = [_person(c, p.id) for p in c.pilots.values() if p.squadron_id == sq.id]
    crew.sort(key=lambda p: (p["status"] != PilotStatus.ACTIVE.value, -p["sorties"], p["name"]))
    afs = [{"id": a.id, "status": a.status.value, "quality": a.quality, "sorties": a.sorties,
            "repair_turns": a.repair_turns}
           for a in c.airframes.values() if a.squadron_id == sq.id]
    afs.sort(key=lambda a: (a["status"] == "destroyed", a["id"]))
    return {"name": sq.name, "type": sq.aircraft_type, "base": c.airbases[sq.base_id].dcs_name,
            "pilots": [p for p in crew if p["role"] == CrewRole.PILOT.value],
            "wsos": [p for p in crew if p["role"] == CrewRole.WSO.value], "airframes": afs}


def debriefs(c: Campaign) -> list[dict]:
    return [{"turn": d.turn, "time": d.time.isoformat(), "flown_in_dcs": d.flown_in_dcs, "lines": d.lines}
            for d in reversed(c.debriefs)]
