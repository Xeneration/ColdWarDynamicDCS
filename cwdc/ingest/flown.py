"""Turn a DCS mission result (hook file / dcs.log) into SortieResults.

Uses the MissionRecord manifest to map DCS unit names back to campaign airframes,
pilots and objective units. Every flight that was spawned in DCS gets a result
from here; the engine resolves the rest abstractly.
"""
from __future__ import annotations

from cwdc import theater as T
from cwdc.ingest.results import is_scenery, kill_weapon
from cwdc.model import (Campaign, Kill, MissionRecord, PilotFate, PilotStatus, Pos, SortieResult)

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
    chute: dict[str, Pos | None] = {}
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
            chute[e["place"]] = _pos(ini)
        if ev == "S_EVENT_PILOT_DEAD" and iname in man:
            pilot_dead.add(iname)
        if ev == "S_EVENT_HIT" and tname in man:
            hit.add(tname)
        if ev == "S_EVENT_KILL" and tname in man and man[tname]["kind"] == "objective":
            obj_dead.add(tname)
        if ev == "S_EVENT_KILL" and iname in man and man[iname]["kind"] == "airframe" \
                and not is_scenery(tgt) and tname:
            k_entry = man.get(tname)
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
    if snap:
        for uname, entry in man.items():
            if entry["kind"] == "objective" and uname not in present:
                obj_dead.add(uname)

    # airframe losses, damage and pilot fates
    for uname, entry in man.items():
        if entry["kind"] != "airframe":
            continue
        res = by_flight[entry["flight"]]
        pilot = entry["pilot"]
        if uname in lost or uname in ejected or uname in pilot_dead:
            res.airframes_lost.append(entry["airframe"])
            res.pilot_fates.append(_fate(c, pilot, uname, ejected, chute, pilot_dead))
        else:
            u = present.get(uname)
            if (u and u.get("life0") and u["life"] / u["life0"] < DAMAGE_THRESHOLD) or uname in hit:
                res.airframes_damaged.append(entry["airframe"])

    destroyed = sorted({f"{man[n]['objective']}#{man[n]['unit']}" for n in obj_dead})
    for res in by_flight.values():
        f = c.flights[res.flight_id]
        res.summary = _summary(c, f.callsign, res)
    return list(by_flight.values()), destroyed


def _fate(c: Campaign, pilot_id: str, uname: str, ejected, chute, pilot_dead) -> PilotFate:
    side = c.pilots[pilot_id].side
    if uname in pilot_dead or uname not in ejected:
        return PilotFate(pilot_id=pilot_id, status=PilotStatus.KIA, note="killed in action")
    where = chute.get(uname) or ejected.get(uname)
    if where is None:
        return PilotFate(pilot_id=pilot_id, status=PilotStatus.MIA, note="ejected, position unknown")
    lat, lon = T.pos_to_latlon(where)
    if T.side_at(c.front_line, lat, lon) is side:
        return PilotFate(pilot_id=pilot_id, status=PilotStatus.ACTIVE,
                         note=f"ejected over friendly territory and was recovered ({lat:.2f}N {lon:.2f}E)")
    return PilotFate(pilot_id=pilot_id, status=PilotStatus.POW,
                     note=f"ejected over enemy territory and was captured ({lat:.2f}N {lon:.2f}E)")


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
