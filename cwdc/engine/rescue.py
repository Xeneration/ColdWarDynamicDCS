"""Downed crews and their rescue (RESCAP missions).

A NATO crew that ejects over enemy territory either is captured at once or evades (MIA, with
the position it came down at). Each turn an evader may be captured; after EVADE_TURNS they are.
A RESCAP flight covers a rescue helicopter: simulated, it succeeds with a chance that drops with
the threats around the survivor; flown in DCS, the helicopter must land at the survivor
(the mission runtime records a CWDC_PICKUP event).
"""
from __future__ import annotations

from cwdc.engine import news, rng
from cwdc.model import Campaign, PilotStatus, Side, Task

EVADE_TURNS = 4            # turns an evader can stay free
P_CAPTURE = 0.25           # per turn, without a rescue
P_RESCUE = 0.6             # simulated rescue under RESCAP, before threats
SAM_PENALTY = 0.15         # per enemy SAM covering the survivor
FIGHTER_PENALTY = 0.10     # enemy fighter patrol within reach
FIGHTER_REACH = 120_000
P_WOUNDED = 0.3            # rescued crews who need hospital time


def evaders(c: Campaign, side: Side = Side.BLUE):
    return sorted((p for p in c.pilots.values() if p.side is side and p.status is PilotStatus.MIA
                   and p.down_pos is not None), key=lambda p: -p.down_turn)


def _rescued(c: Campaign, pilot, how: str) -> str:
    from cwdc.engine.apply import WIA_RECOVERY_TURNS
    r = rng(c.seed, c.turn, f"rescued:{pilot.id}")
    hours = (c.turn - pilot.down_turn) * c.turn_hours
    if r.random() < P_WOUNDED:
        pilot.status, pilot.recovery_turns = PilotStatus.WIA, WIA_RECOVERY_TURNS
    else:
        pilot.status = PilotStatus.ACTIVE
    pilot.down_pos = None
    text = (f"RESCUED: {pilot.rank} {pilot.name} ({c.squadrons[pilot.squadron_id].name}) was picked up "
            f"{how} after {hours} hours on the run.")
    news.post(c, text, "squadron")
    return text


def threat(c: Campaign, pos) -> float:
    """How much harder the threats around `pos` make a rescue (0 = none)."""
    sams = sum(1 for o in c.objectives.values() if o.side is Side.RED and o.kind.value == "sam"
               and not o.destroyed and o.pos.dist(pos) <= o.threat_range_m)
    fighters = any(s.dist(pos) <= FIGHTER_REACH for s in c.cap_stations.get(Side.RED, []))
    return SAM_PENALTY * sams + (FIGHTER_PENALTY if fighters else 0.0)


def rescue_turn(c: Campaign, flights) -> list[str]:
    """End of turn: simulated RESCAPs (flights not flown in DCS), then captures."""
    r = rng(c.seed, c.turn, "rescue")
    lines = []
    for f in flights:
        if f.task is not Task.RESCAP or not f.rescue_of:
            continue
        p = c.pilots.get(f.rescue_of)
        if p is None or p.status is not PilotStatus.MIA or p.down_pos is None:
            continue
        if r.random() < max(0.1, P_RESCUE - threat(c, p.down_pos)):
            lines.append(_rescued(c, p, f"under {f.callsign}'s cover"))
        else:
            lines.append(f"{f.callsign} covered a rescue attempt for {p.rank} {p.name}; it failed.")
    for p in evaders(c):
        age = c.turn - p.down_turn
        if age >= EVADE_TURNS or (age > 0 and r.random() < P_CAPTURE):
            p.status, p.down_pos = PilotStatus.POW, None
            text = f"{p.rank} {p.name} ({c.squadrons[p.squadron_id].name}) has been captured."
            news.post(c, text, "squadron")
            lines.append(text)
    return lines


def dcs_pickups(c: Campaign, mission, data: dict) -> list[str]:
    """A RESCAP flown in DCS: the helicopter landed at the survivor -> rescued."""
    if mission is None:
        return []
    player = c.flights.get(mission.player_flight_id)
    if player is None or player.task is not Task.RESCAP or not player.rescue_of:
        return []
    picked = any(e.get("event") == "CWDC_PICKUP" for e in data.get("events", []) or [])
    p = c.pilots.get(player.rescue_of)
    if not picked or p is None or p.status is not PilotStatus.MIA:
        return [f"The rescue helicopter did not reach {p.rank} {p.name}."] if p else []
    return [_rescued(c, p, "by Dustoff 1 under your cover")]
