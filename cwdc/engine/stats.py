"""Numbers for the web UI: per-turn history (charts) and structured debrief summaries."""
from __future__ import annotations

from cwdc.model import AirframeStatus, Campaign, PilotStatus, Side, SortieResult, TurnStats

LOST_CREW = (PilotStatus.KIA, PilotStatus.MIA, PilotStatus.POW)


def counts(c: Campaign) -> dict:
    """Cumulative losses and the front, to diff a turn against."""
    out = {"offsets": {s.id: s.offset_km for s in c.sectors.values()}}
    for side in Side:
        out[f"aircraft:{side.value}"] = sum(1 for a in c.airframes.values()
                                            if a.status is AirframeStatus.DESTROYED
                                            and c.squadrons[a.squadron_id].side is side)
        out[f"aircrew:{side.value}"] = sum(1 for p in c.pilots.values()
                                           if p.side is side and p.status in LOST_CREW)
    return out


def turn_stats(c: Campaign) -> TurnStats:
    from cwdc.engine import ground, outcome
    now = counts(c)
    return TurnStats(
        turn=c.turn, time=c.time,
        offsets={s.id: round(s.offset_km, 2) for s in c.sectors.values()},
        ground_power={side.value: round(sum(ground.power(c, s.id, side) for s in c.sectors.values()), 1)
                      for side in Side},
        air_strength={side.value: round(outcome.air_strength(c, side), 3) for side in Side},
        aircraft_lost={side.value: now[f"aircraft:{side.value}"] for side in Side},
        aircrew_lost={side.value: now[f"aircrew:{side.value}"] for side in Side})


def debrief_stats(c: Campaign, before: dict, results: list[SortieResult],
                  player_flight_id: str | None, flown_in_dcs: bool) -> dict:
    now = counts(c)
    kills = {side.value: {"air": 0, "ground": 0} for side in Side}
    sorties = {side.value: 0 for side in Side}
    yours = None
    for r in results:
        f = c.flights.get(r.flight_id)
        if f is None:
            continue
        sorties[f.side.value] += len(f.airframe_ids)
        for k in r.kills:
            kills[f.side.value]["air" if k.air else "ground"] += 1
        if r.flight_id == player_flight_id:
            yours = {"callsign": f.callsign, "task": f.task.value, "summary": r.summary,
                     "flown_in_dcs": flown_in_dcs, "lost": len(r.airframes_lost)}
    front = []
    for s in c.sectors.values():
        b = before["offsets"].get(s.id, s.offset_km)
        front.append({"id": s.id, "name": s.name, "before": round(b, 1), "after": round(s.offset_km, 1),
                      "change": round(s.offset_km - b, 1),
                      "attacker": s.attacker.value if s.attacker else None})
    return {
        "sorties": sorties,
        "aircraft_lost": {side.value: now[f"aircraft:{side.value}"] - before[f"aircraft:{side.value}"]
                          for side in Side},
        "aircrew_lost": {side.value: now[f"aircrew:{side.value}"] - before[f"aircrew:{side.value}"]
                         for side in Side},
        "kills": kills,
        "front": front,
        "airfields_hit": [{"name": ab.dcs_name, "side": ab.side.value, "damage": round(ab.damage, 2)}
                          for ab in c.airbases.values() if ab.hit_turn == c.turn],
        "your_flight": yours,
    }
