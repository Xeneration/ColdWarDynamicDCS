"""Airbases as targets: offensive counter-air (Phase 2 step 6).

Every airbase has an *airfield objective*: parked aircraft (statics on real parking
slots, so they never block a runway) and a pair of AAA guns. Destroying a parked
aircraft - in DCS or in the resolver - is the same event:

  * the base takes DAMAGE_PER_HIT damage (ramp, fuel, ground crew, runway repair crews);
  * one ready aircraft of a squadron based there is destroyed on the ground.

Damage cuts the squadrons' sortie rate (`sortie_factor`); at CLOSED the base flies
nothing. Repair crews take REPAIR_PER_TURN off each turn the base is not hit again,
and the parked aircraft / guns reappear as the base recovers, so it can be hit again.

Runway craters from bombs that miss everything are not visible to the mission runtime
(no event), so a flown strike only counts what it destroys.
"""
from __future__ import annotations

import math

from cwdc import theater as T
from cwdc.engine import rng
from cwdc.model import (AirframeStatus, Airbase, Campaign, ObjUnit, Objective, ObjectiveKind, Pos,
                        Side)

DAMAGE_PER_HIT = 0.15
REPAIR_PER_TURN = 0.10
CLOSED = 0.7
N_PARKED = 4
SLOT_SPACING = 60            # m between chosen parking slots
AAA_OUTSIDE = 120            # m beyond the outermost parked aircraft
PARKED_TYPE = {Side.BLUE: "F-4E-45MC", Side.RED: "MiG-21Bis"}
AAA_TYPE = {Side.BLUE: "Vulcan", Side.RED: "ZSU-23-4 Shilka"}
COUNTRY = {Side.BLUE: "USA", Side.RED: "USSR"}


def objective_id(ab_id: str) -> str:
    return f"ab_{ab_id}"


def objective(c: Campaign, ab: Airbase) -> Objective | None:
    return c.objectives.get(objective_id(ab.id))


def is_parked(type_id: str) -> bool:
    from dcs.planes import plane_map
    return type_id in plane_map


def _resident(c: Campaign, ab: Airbase, side: Side):
    return next((s for s in sorted(c.squadrons.values(), key=lambda s: s.id)
                 if s.base_id == ab.id and s.side is side), None)


def _units(c: Campaign, ab: Airbase, side: Side) -> list[ObjUnit]:
    """Parked aircraft on the outermost parking slots (well clear of the runway), AAA outside."""
    from dcs.planes import plane_map
    sq = _resident(c, ab, side)
    parked = sq.aircraft_type if sq else PARKED_TYPE[side]
    airport = T.terrain().airports[ab.dcs_name]
    ref = airport.position
    slots = sorted(airport.free_parking_slots(plane_map[parked]),
                   key=lambda s: -s.position.distance_to_point(ref))
    chosen = []
    for s in slots:
        if all(s.position.distance_to_point(o.position) >= SLOT_SPACING for o in chosen):
            chosen.append(s)
        if len(chosen) == N_PARKED:
            break
    units = [ObjUnit(type=parked, dx=s.position.x - ab.pos.x, dz=s.position.y - ab.pos.z)
             for s in chosen]
    for s in chosen[:2]:
        dx, dz = s.position.x - ref.x, s.position.y - ref.y
        d = math.hypot(dx, dz) or 1.0
        k = (d + AAA_OUTSIDE) / d
        units.append(ObjUnit(type=AAA_TYPE[side], dx=ref.x + dx * k - ab.pos.x,
                             dz=ref.y + dz * k - ab.pos.z))
    return units


def make_objective(c: Campaign, ab: Airbase) -> Objective:
    sq = _resident(c, ab, ab.side)
    return Objective(id=objective_id(ab.id), name=f"{ab.dcs_name} airfield", kind=ObjectiveKind.AIRBASE,
                     side=ab.side, country=sq.country if sq else COUNTRY[ab.side],
                     pos=Pos(x=ab.pos.x, z=ab.pos.z), units=_units(c, ab, ab.side), value=4,
                     airbase_id=ab.id)


def ensure_objectives(c: Campaign) -> list[str]:
    added = []
    for ab in c.airbases.values():
        if objective(c, ab) is None:
            o = make_objective(c, ab)
            c.objectives[o.id] = o
            added.append(o.name)
    return added


def sortie_factor(c: Campaign, base_id: str) -> float:
    ab = c.airbases.get(base_id)
    if ab is None:
        return 1.0
    return 0.0 if ab.damage >= CLOSED else 1.0 - ab.damage


def sorties(c: Campaign, base_id: str, planned: int) -> int:
    return math.floor(planned * sortie_factor(c, base_id) + 0.5)


# ----------------------------------------------------------------------------- damage & repair

def on_unit_destroyed(c: Campaign, o: Objective, idx: int) -> None:
    """A parked aircraft on an airfield was destroyed (flown or simulated)."""
    if o.kind is not ObjectiveKind.AIRBASE or not o.airbase_id or not is_parked(o.units[idx].type):
        return
    ab = c.airbases[o.airbase_id]
    ab.damage = min(1.0, ab.damage + DAMAGE_PER_HIT)
    ab.hit_turn = c.turn
    r = rng(c.seed, c.turn, f"oca:{o.id}:{idx}")
    ready = [a for sq in sorted(c.squadrons.values(), key=lambda s: s.id)
             if sq.base_id == ab.id and sq.side is o.side for a in c.squadron_airframes(sq.id)]
    if ready:
        a = r.choice(ready)
        a.status = AirframeStatus.DESTROYED
        c.add_news(f"{c.squadrons[a.squadron_id].name} loses {a.id} on the ground at {ab.dcs_name}.")


def overrun(c: Campaign, ab: Airbase, owner: Side) -> None:
    """The base changed hands: the new owner inherits a wrecked airfield."""
    ab.damage, ab.hit_turn = max(ab.damage, CLOSED), c.turn
    o = objective(c, ab)
    if o is None:
        return
    o.side, o.country = owner, COUNTRY[owner]
    o.units = _units(c, ab, owner)
    for u in o.units:
        u.alive = False


def airbase_turn(c: Campaign) -> list[str]:
    """Repair crews work on every base not hit this turn; returns status lines for damaged bases."""
    lines = []
    for ab in c.airbases.values():
        if ab.damage > 0 and ab.hit_turn != c.turn:
            ab.damage = max(0.0, round(ab.damage - REPAIR_PER_TURN, 4))
        o = objective(c, ab)
        if o is not None and ab.hit_turn != c.turn:
            _restore(o, ab)
        if ab.damage > 0:
            lines.append("  " + describe(ab))
    return lines


def _restore(o: Objective, ab: Airbase) -> None:
    parked = [u for u in o.units if is_parked(u.type)]
    still_dead = min(len(parked), round(ab.damage / DAMAGE_PER_HIT))
    dead = [u for u in parked if not u.alive]
    for u in dead[still_dead:]:
        u.alive = True
    if ab.damage < CLOSED:
        for u in o.units:
            if not is_parked(u.type):
                u.alive = True                 # replacement guns


def describe(ab: Airbase) -> str:
    side = "NATO" if ab.side is Side.BLUE else "Warsaw Pact"
    if ab.damage >= CLOSED:
        state = "CLOSED"
    elif ab.damage > 0:
        state = f"{ab.damage:.0%} damaged, sortie rate {1 - ab.damage:.0%}"
    else:
        state = "operational"
    return f"{ab.dcs_name:<12} ({side}): {state}"
