"""End-of-turn processing: results in, simulate the rest, maintain, advance, re-plan."""
from __future__ import annotations

from datetime import timedelta

from cwdc.engine import apply, factory, ground, logistics, planner, resolver
from cwdc.model import (Airframe, AirframeStatus, Campaign, FlightStatus, Pilot, PilotStatus,
                        SortieResult)
from cwdc.engine import rng

REPLACEMENT_EVERY_TURNS = 4      # once a day
REPLACEMENT_AIRFRAMES = 2
REPLACEMENT_PILOTS = 2


def end_turn(c: Campaign, dcs_results: list[SortieResult] | None = None,
             dcs_destroyed: list[str] | None = None) -> list[str]:
    """Close the current turn. Returns debrief lines."""
    debrief = [f"=== Turn {c.turn} debrief - {c.time:%d %b %Y %H:%M} ==="]
    flown = set()
    if dcs_results is not None:
        flown = {r.flight_id for r in dcs_results}
        debrief += ["-- Flown in DCS --",
                    *apply.apply_results(c, dcs_results, dcs_destroyed or [])]

    remaining = [f for f in c.flights.values()
                 if f.id not in flown and f.status is not FlightStatus.DONE]
    sim = resolver.resolve(c, remaining)
    debrief += ["-- Rest of the air war --", *apply.apply_results(c, sim)]
    supply_lines = logistics.logistics_turn(c)
    if supply_lines:
        debrief += ["-- Logistics --", *supply_lines]
    ground_lines = ground.ground_turn(c)
    if ground_lines:
        debrief += ["-- Ground war --", *ground_lines]

    player = c.player_pilot()
    if player.status is not PilotStatus.ACTIVE:
        c.add_news(f"{player.rank} {player.name} is {player.status.value.upper()}. "
                   f"A new pilot joins the {c.squadrons[player.squadron_id].name}.")
        new = factory.add_player_pilot(c, c.player_name)
        debrief.append(f"** Your pilot is {player.status.value.upper()}. "
                       f"You continue as {new.rank} {new.name} (new crew). **")

    c.pending_mission = None
    c.add_news(f"Turn {c.turn}: " + "; ".join(l for l in debrief if l.startswith("[")))
    _advance(c)
    planner.plan_turn(c)
    return debrief


def _advance(c: Campaign) -> None:
    for af in c.airframes.values():
        if af.status is AirframeStatus.DAMAGED:
            af.repair_turns -= 1
            if af.repair_turns <= 0:
                af.status = AirframeStatus.READY
    for p in c.pilots.values():
        if p.status is PilotStatus.WIA:
            p.recovery_turns -= 1
            if p.recovery_turns <= 0:
                p.status = PilotStatus.ACTIVE
    c.turn += 1
    c.time += timedelta(hours=c.turn_hours)
    if (c.turn - 1) % REPLACEMENT_EVERY_TURNS == 0:
        _replacements(c)


def _replacements(c: Campaign) -> None:
    from cwdc import theater as T
    names = T.load_data("names.yaml")
    r = rng(c.seed, c.turn, "replacements")
    for sq in c.squadrons.values():
        have = len([a for a in c.airframes.values()
                    if a.squadron_id == sq.id and a.status is not AirframeStatus.DESTROYED])
        for _ in range(min(REPLACEMENT_AIRFRAMES, sq.establishment_airframes - have)):
            prefix = next(a.id for a in c.airframes.values() if a.squadron_id == sq.id).rsplit(" ", 1)[0]
            aid = f"{prefix} {c.next_id('R')}"
            c.airframes[aid] = Airframe(id=aid, type=sq.aircraft_type, squadron_id=sq.id,
                                        quality=r.randint(80, 100))
        have_p = len(c.squadron_pilots(sq.id))
        pool = names[sq.side.value]
        for _ in range(min(REPLACEMENT_PILOTS, sq.establishment_pilots - have_p)):
            pid = c.next_id(f"{sq.side.value[0]}p")
            c.pilots[pid] = Pilot(id=pid, name=f"{r.choice(pool['first'])} {r.choice(pool['last'])}",
                                  rank=pool["ranks"][0], side=sq.side, squadron_id=sq.id)
