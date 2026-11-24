"""Turn a DCS mission result (hook file / dcs.log) into SortieResults.

Uses the MissionRecord manifest to map DCS unit names back to campaign airframes,
pilots and objective units. Every flight that was spawned in DCS gets a result
from here; the engine resolves the rest abstractly.
"""
from __future__ import annotations

from cwdc import theater as T
from cwdc.ingest.results import is_collision, is_scenery, kill_weapon
from cwdc.model import (Campaign, Kill, MissionRecord, PilotFate, PilotStatus, Pos, Side,
                        SortieResult)

LOSS_EVENTS = {"S_EVENT_UNIT_LOST", "S_EVENT_CRASH", "S_EVENT_DEAD", "S_EVENT_EJECTION",
               "S_EVENT_PILOT_DEAD"}
DAMAGE_THRESHOLD = 0.9


def _name(obj) -> str | None:
    return (obj or {}).get("name")


def _pos(obj) -> Pos | None:
    p = (obj or {}).get("pos")
    return Pos(x=p["x"], z=p["z"]) if p else None


def results_from_dcs(c: Campaign, rec: MissionRecord, data: dict) -> tuple[list[SortieResult], list[str]]:
    """Return (results for spawned flights, objective units destroyed as 'obj#idx')."""
    man = rec.manifest
    events = data.get("events", [])
    by_flight = {fid: SortieResult(flight_id=fid, source="dcs") for fid in rec.spawned_flights}

    lost: set[str] = set()
    ejected: dict[str, Pos | None] = {}
    chutes: dict[str, list[tuple[str, Pos | None]]] = {}   # unit -> [(parachute name, landing)]
    pilot_dead: set[str] = set()
    hit: set[str] = set()
    obj_dead: set[str] = set()

    for e in events:
        ev = e["event"]
        ini, tgt = e.get("initiator"), e.get("target")
        iname, tname = _name(ini), _name(tgt)
        if ev in LOSS_EVENTS and iname in man:
            if man[iname]["kind"] == "airframe":
                lost.add(iname)
            else:
                obj_dead.add(iname)
        if ev == "S_EVENT_EJECTION" and iname in man:
            ejected[iname] = _pos(ini)
        if ev == "S_EVENT_LANDING_AFTER_EJECTION" and e.get("place") in man:
            chutes.setdefault(e["place"], []).append((_name(ini) or "", _pos(ini)))
        if ev == "S_EVENT_PILOT_DEAD" and iname in man:
            pilot_dead.add(iname)
        if ev == "S_EVENT_HIT" and tname in man:
            hit.add(tname)
        if ev == "S_EVENT_KILL" and tname in man and man[tname]["kind"] == "objective":
            obj_dead.add(tname)
        if ev == "S_EVENT_KILL" and iname in man and man[iname]["kind"] == "airframe" \
                and not is_scenery(tgt) and tname and not is_collision(e):
            k_entry = man.get(tname)
            killer_side = c.flights[man[iname]["flight"]].side
            if _side_of(c, k_entry, tgt) is killer_side:
                continue                      # fratricide: no credit
            if k_entry and k_entry["kind"] == "airframe":
                target, ttype, air = k_entry["airframe"], c.airframes[k_entry["airframe"]].type, True
            elif k_entry:
                target, ttype, air = f"{k_entry['objective']}#{k_entry['unit']}", tgt.get("type"), False
            else:
                target, ttype, air = tname, tgt.get("type"), tgt.get("category") == 1 and \
                    tgt.get("type") in _plane_types()
            by_flight[man[iname]["flight"]].kills.append(
                Kill(pilot_id=man[iname]["pilot"], target=target, target_type=ttype or "?",
                     weapon=kill_weapon(events, e), air=air))

    # ground units missing from the final snapshot are dead (they never despawn)
    snap = data.get("snapshot") or {}
    present = {}
    for g in snap.get("groups", []):
        for u in g.get("units", []):
            present[u["name"]] = u
    statics = {s["name"]: s for s in snap.get("statics", [])}
    if snap:
        for uname, entry in man.items():
            if entry["kind"] != "objective":
                continue
            if entry.get("static"):
                st = statics.get(uname)
                if "statics" in snap and (st is None or not st.get("exists") or st.get("life", 1) <= 0):
                    obj_dead.add(uname)
            elif uname not in present:
                obj_dead.add(uname)

    # airframe losses, damage and pilot fates
    for uname, entry in man.items():
        if entry["kind"] != "airframe":
            continue
        res = by_flight[entry["flight"]]
        pilot = entry["pilot"]
        if uname in lost or uname in ejected or uname in pilot_dead:
            res.airframes_lost.append(entry["airframe"])
            seats = _seat_landings(chutes.get(uname, []))
            res.pilot_fates.append(_fate(c, pilot, uname, ejected, seats["pilot"],
                                         uname in pilot_dead))
            if entry.get("wso"):
                res.pilot_fates.append(_fate(c, entry["wso"], uname, ejected, seats["wso"], False))
        else:
            u = present.get(uname)
            if (u and u.get("life0") and u["life"] / u["life0"] < DAMAGE_THRESHOLD) or uname in hit:
                res.airframes_damaged.append(entry["airframe"])

    destroyed = sorted({f"{man[n]['objective']}#{man[n]['unit']}" for n in obj_dead})
    for res in by_flight.values():
        f = c.flights[res.flight_id]
        res.summary = _summary(c, f.callsign, res)
    return list(by_flight.values()), destroyed


def _seat_landings(chutes: list[tuple[str, Pos | None]]) -> dict[str, Pos | None]:
    """Which parachute is whose. The F-4E's are named F-4E_Parachute_Pilot / _WSO; single
    seaters' have other names (e.g. pilot_f15_parachute) and belong to the pilot."""
    out: dict[str, Pos | None] = {"pilot": None, "wso": None}
    rest = []
    for name, pos in chutes:
        if "wso" in name.lower() and out["wso"] is None:
            out["wso"] = pos
        elif "pilot" in name.lower() and out["pilot"] is None:
            out["pilot"] = pos
        else:
            rest.append(pos)
    for seat in ("pilot", "wso"):
        if out[seat] is None and rest:
            out[seat] = rest.pop(0)
    return out


def _fate(c: Campaign, pilot_id: str, uname: str, ejected, landed: Pos | None,
          dead: bool) -> PilotFate:
    side = c.pilots[pilot_id].side
    if dead or uname not in ejected:
        return PilotFate(pilot_id=pilot_id, status=PilotStatus.KIA, note="killed in action")
    where = landed or ejected.get(uname)
    if where is None:
        return PilotFate(pilot_id=pilot_id, status=PilotStatus.MIA, note="ejected, position unknown")
    lat, lon = T.pos_to_latlon(where)
    if T.side_at(c.front_line, lat, lon) is side:
        return PilotFate(pilot_id=pilot_id, status=PilotStatus.ACTIVE,
                         note=f"ejected over friendly territory and was recovered ({lat:.2f}N {lon:.2f}E)")
    return PilotFate(pilot_id=pilot_id, status=PilotStatus.MIA, pos=where,
                     note=f"ejected over enemy territory - evading capture ({lat:.2f}N {lon:.2f}E)")


def _side_of(c: Campaign, entry: dict | None, obj: dict) -> Side | None:
    if entry and entry["kind"] == "airframe":
        return c.squadrons[c.airframes[entry["airframe"]].squadron_id].side
    if entry:
        return c.objectives[entry["objective"]].side
    return {1: Side.RED, 2: Side.BLUE}.get((obj or {}).get("coalition"))


def _plane_types() -> set[str]:
    from dcs.planes import plane_map
    return set(plane_map)


def _summary(c: Campaign, callsign: str, r: SortieResult) -> str:
    parts = [callsign]
    air = [k for k in r.kills if k.air]
    gnd = [k for k in r.kills if not k.air]
    if air:
        parts.append(f"{len(air)} air kill(s) ({', '.join(k.target_type for k in air)})")
    if gnd:
        parts.append(f"{len(gnd)} ground kill(s)")
    if r.airframes_lost:
        parts.append(f"lost {len(r.airframes_lost)} aircraft")
    if r.airframes_damaged:
        parts.append(f"{len(r.airframes_damaged)} damaged")
    if len(parts) == 1:
        parts.append("returned without incident")
    return ": ".join([parts[0], ", ".join(parts[1:])])
