"""Aircrew: two-seat crews and fatigue (Phase 2 step 7).

Two-seat types (airframes.yaml `crew: 2`, the F-4E) fly with a pilot and a WSO, each
a persistent person with their own sorties, kills and fate. Front seats are
`Flight.pilot_ids`, back seats `Flight.wso_ids` in the same order.

Fatigue: every sortie adds FATIGUE_PER_SORTIE; every turn takes off REST_PER_TURN
(double at night, when the 1976 air war mostly stands down). Crew at GROUNDED or above
are not scheduled. Tired crews fight worse (`performance`).
"""
from __future__ import annotations

from cwdc import theater as T
from cwdc.model import Campaign, CrewRole, Flight, Pilot, PilotStatus, Squadron

FATIGUE_PER_SORTIE = 0.30
REST_PER_TURN = 0.15
NIGHT_REST = 2.0              # rest multiplier for a night turn
GROUNDED = 0.80
PERFORMANCE_LOSS = 0.25       # at fatigue 1.0 a crew fights 25% worse


def crew_size(type_id: str) -> int:
    return int(T.airframe_catalogue().get(type_id, {}).get("crew", 1))


def two_seat(sq: Squadron) -> bool:
    return crew_size(sq.aircraft_type) >= 2


def available(c: Campaign, sq_id: str, role: CrewRole) -> list[Pilot]:
    """Active, rested enough to fly, and not the player (the player chooses their own sorties)."""
    return [p for p in c.squadron_pilots(sq_id, role=role)
            if not p.is_player and p.fatigue < GROUNDED]


def schedule_order(p: Pilot) -> tuple:
    """Freshest first, then the most experienced."""
    return (round(p.fatigue, 1), -p.sorties, p.id)


def crew_of(f: Flight) -> list[str]:
    return list(f.pilot_ids) + list(f.wso_ids)


def wso_for(f: Flight, pilot_id: str) -> str | None:
    """The back-seater flying with this front-seater, if any."""
    if pilot_id in f.pilot_ids and f.wso_ids:
        i = f.pilot_ids.index(pilot_id)
        return f.wso_ids[i] if i < len(f.wso_ids) else None
    return None


def seat_crew(f: Flight, slot: int) -> list[str]:
    out = [f.pilot_ids[slot]]
    if slot < len(f.wso_ids):
        out.append(f.wso_ids[slot])
    return out


def performance(c: Campaign, f: Flight) -> float:
    crew = [c.pilots[p] for p in crew_of(f)]
    if not crew:
        return 1.0
    return 1.0 - PERFORMANCE_LOSS * sum(min(1.0, p.fatigue) for p in crew) / len(crew)


def flew(c: Campaign, f: Flight) -> None:
    for pid in crew_of(f):
        p = c.pilots[pid]
        p.fatigue = min(1.0, p.fatigue + FATIGUE_PER_SORTIE)


def rest(c: Campaign, night: bool, exclude: set[str] = frozenset()) -> None:
    """Crews who did not fly this turn recover."""
    amount = REST_PER_TURN * (NIGHT_REST if night else 1.0)
    for p in c.pilots.values():
        if p.id not in exclude and p.status in (PilotStatus.ACTIVE, PilotStatus.WIA) and p.fatigue > 0:
            p.fatigue = max(0.0, round(p.fatigue - amount, 4))


def describe_fatigue(p: Pilot) -> str:
    if p.fatigue >= GROUNDED:
        return "exhausted"
    if p.fatigue >= 0.5:
        return "tired"
    return "fresh"


def ensure_wsos(c: Campaign) -> list[str]:
    """Older campaigns: give two-seat squadrons their back-seaters."""
    from cwdc.engine import rng
    names = T.load_data("names.yaml")
    added = []
    for sq in c.squadrons.values():
        if not two_seat(sq) or sq.establishment_wsos:
            continue
        sq.establishment_wsos = sq.establishment_pilots
        r = rng(c.seed, 0, f"wsos:{sq.id}")
        _recruit(c, sq, sq.establishment_wsos, r, names, CrewRole.WSO)
        added.append(f"{sq.name} WSOs")
    return added


def _recruit(c: Campaign, sq: Squadron, n: int, r, names, role: CrewRole, ranks=None) -> list[Pilot]:
    pool = names[sq.side.value]
    used = {p.name for p in c.pilots.values()}
    out = []
    for _ in range(n):
        for _attempt in range(50):
            nm = f"{r.choice(pool['first'])} {r.choice(pool['last'])}"
            if nm not in used:
                break
        used.add(nm)
        pid = c.next_id(f"{sq.side.value[0]}{'w' if role is CrewRole.WSO else 'p'}")
        p = Pilot(id=pid, name=nm, rank=r.choice(ranks or pool["ranks"]), side=sq.side,
                  squadron_id=sq.id, role=role)
        c.pilots[pid] = p
        out.append(p)
    return out
