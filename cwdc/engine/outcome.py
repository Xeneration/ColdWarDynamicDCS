"""How the war ends (Phase 2 step 8).

Checked at the end of every turn, using the theater's `victory` block:

  Warsaw Pact victory - the front reaches the Main (any `red_goals` city in Warsaw Pact
      hands), a sector breaks through to `breakthrough_km`, or NATO air power is exhausted
      (serviceable aircraft below `air_exhausted` of establishment).
  NATO victory - after `nato_hold_days`, every sector is back on or beyond the border.
  Ceasefire - after `max_days` the war is stopped where it stands and judged by how deep
      the Warsaw Pact holds West German territory.

Once `Campaign.outcome` is set the campaign is over: no further turns are planned.
"""
from __future__ import annotations

import math
from collections import Counter

from cwdc import theater as T
from cwdc.model import AirframeStatus, Campaign, Outcome, PilotStatus, Pos, Side

CEASEFIRE_WP_KM = 20       # mean penetration at the ceasefire that counts as a Warsaw Pact gain
CEASEFIRE_NATO_KM = 5      # ... and at or below which NATO is judged to have held


def rules(c: Campaign) -> dict:
    from cwdc.engine.factory import THEATERS
    return T.load_data(THEATERS[c.theater][0]).get("victory", {})


def war_day(c: Campaign) -> int:
    """Day of the war (1 = its first day; 0 during the pre-war crisis)."""
    if not c.at_war:
        return 0
    return (c.war_turn - 1) * c.turn_hours // 24 + 1


def turns_per_day(c: Campaign) -> int:
    return max(1, 24 // c.turn_hours)


def air_strength(c: Campaign, side: Side) -> float:
    """Serviceable (ready or in repair) aircraft as a share of establishment."""
    est = sum(sq.establishment_airframes for sq in c.squadrons.values() if sq.side is side)
    have = sum(1 for a in c.airframes.values() if a.status is not AirframeStatus.DESTROYED
               and c.squadrons[a.squadron_id].side is side)
    return have / est if est else 1.0


def _goal_pos(g: dict) -> Pos:
    return T.latlon_to_pos(*g["latlon"])


def front_distance_km(c: Campaign, pos: Pos) -> float:
    """Shortest distance from a point to the current front polyline."""
    pts = [T.latlon_to_pos(lat, lon) for lat, lon in c.front_line]
    best = math.inf
    for a, b in zip(pts, pts[1:]):
        dx, dz = b.x - a.x, b.z - a.z
        L = dx * dx + dz * dz or 1.0
        t = max(0.0, min(1.0, ((pos.x - a.x) * dx + (pos.z - a.z) * dz) / L))
        best = min(best, math.hypot(pos.x - (a.x + t * dx), pos.z - (a.z + t * dz)))
    return best / 1000


def check(c: Campaign) -> Outcome | None:
    """Decide the war if it is over at the end of this turn; sets and returns c.outcome."""
    if c.outcome is not None:
        return c.outcome
    if not c.at_war:
        return None
    v = rules(c)
    if not v:
        return None
    day = war_day(c)
    end_of_day = c.war_turn % turns_per_day(c) == 0

    def done(winner, title, reason, decisive=True):
        c.outcome = Outcome(winner=winner, decisive=decisive, title=title, reason=reason,
                            turn=c.turn, time=c.time)
        from cwdc.engine import news
        news.post(c, f"{title}: {reason}", "military")
        return c.outcome

    for g in v.get("red_goals", []):
        if T.side_at_pos(c.front_line, _goal_pos(g)) is Side.RED:
            return done(Side.RED, "Warsaw Pact victory",
                        f"Soviet forces have reached {g['name']} on the Main. NATO's forward "
                        "defence has collapsed.")
    deep = max(c.sectors.values(), key=lambda s: s.offset_km, default=None)
    if deep and deep.offset_km >= v.get("breakthrough_km", math.inf):
        return done(Side.RED, "Warsaw Pact victory",
                    f"a breakthrough in the {deep.name}, {deep.offset_km:.0f} km deep, has "
                    "torn open NATO's front.")
    if air_strength(c, Side.BLUE) < v.get("air_exhausted", 0.0):
        return done(Side.RED, "Warsaw Pact victory",
                    f"NATO air power is exhausted ({air_strength(c, Side.BLUE):.0%} of its "
                    "aircraft left); the ground forces cannot hold without it.")
    if day > v.get("nato_hold_days", math.inf) and c.sectors \
            and all(s.offset_km <= 0 for s in c.sectors.values()):
        return done(Side.BLUE, "NATO victory",
                    f"after {day - 1} days NATO has thrown the Warsaw Pact back across the "
                    "inner German border everywhere. Moscow accepts a ceasefire.")
    if day >= v.get("max_days", math.inf) and end_of_day:
        mean = sum(max(0.0, s.offset_km) for s in c.sectors.values()) / max(1, len(c.sectors))
        if mean >= CEASEFIRE_WP_KM:
            return done(Side.RED, "Ceasefire - Warsaw Pact gains", _ceasefire(c, day, mean), False)
        if mean <= CEASEFIRE_NATO_KM:
            return done(Side.BLUE, "Ceasefire - NATO holds", _ceasefire(c, day, mean), False)
        return done(None, "Ceasefire - stalemate", _ceasefire(c, day, mean), False)
    return None


def _ceasefire(c: Campaign, day: int, mean: float) -> str:
    return (f"after {day} days the superpowers agree a ceasefire. The Warsaw Pact holds on "
            f"average {mean:.0f} km of West German territory along the front.")


# ----------------------------------------------------------------------------- reports

def situation(c: Campaign) -> list[str]:
    """Where the war stands against the victory conditions (status, while it lasts)."""
    v = rules(c)
    if not v:
        return []
    lines = [f"  War day {war_day(c)} (ceasefire talks by day {v.get('max_days', '?')})"]
    goals = v.get("red_goals", [])
    if goals:
        near = min(goals, key=lambda g: front_distance_km(c, _goal_pos(g)))
        lines.append(f"  Nearest Warsaw Pact objective: {near['name']}, "
                     f"{front_distance_km(c, _goal_pos(near)):.0f} km behind the front")
    deep = max(c.sectors.values(), key=lambda s: s.offset_km, default=None)
    if deep:
        lines.append(f"  Deepest penetration: {deep.name} {max(0.0, deep.offset_km):.0f} km "
                     f"(breakthrough at {v.get('breakthrough_km', '?')} km)")
    lines.append(f"  NATO air strength {air_strength(c, Side.BLUE):.0%} "
                 f"(exhausted below {v.get('air_exhausted', 0):.0%}), "
                 f"Warsaw Pact {air_strength(c, Side.RED):.0%}")
    if all(s.offset_km <= 0 for s in c.sectors.values()):
        lines.append(f"  The border is held: NATO wins if it still is after day "
                     f"{v.get('nato_hold_days', '?')}")
    return lines


def situation_data(c: Campaign) -> dict:
    """The victory conditions as numbers (web UI gauges)."""
    v = rules(c)
    out = {"war_day": war_day(c), "max_days": v.get("max_days"), "hold_days": v.get("nato_hold_days"),
           "breakthrough_km": v.get("breakthrough_km"), "air_exhausted": v.get("air_exhausted", 0.0),
           "air": {s.value: round(air_strength(c, s), 3) for s in Side},
           "border_held": bool(c.sectors) and all(s.offset_km <= 0 for s in c.sectors.values())}
    goals = v.get("red_goals", [])
    if goals:
        border = [T.latlon_to_pos(lat, lon) for lat, lon in c.base_front_line]

        def from_border(p: Pos) -> float:
            return min(math.hypot(p.x - b.x, p.z - b.z) for b in border) / 1000

        near = min(goals, key=lambda g: front_distance_km(c, _goal_pos(g)))
        out["goal"] = {"name": near["name"], "km": round(front_distance_km(c, _goal_pos(near))),
                       "start_km": round(from_border(_goal_pos(near)))}
    deep = max(c.sectors.values(), key=lambda s: s.offset_km, default=None)
    if deep:
        out["deepest"] = {"name": deep.name, "km": round(max(0.0, deep.offset_km), 1)}
    return out


def summary(c: Campaign) -> list[str]:
    """The war summary shown once the campaign is over."""
    o = c.outcome
    lines = []
    if o:
        lines += [f"*** {o.title.upper()} ***", f"  {o.reason[0].upper()}{o.reason[1:]}",
                  f"  The war lasted {war_day(c)} days ({o.time:%d %b %Y %H:%M}, turn {o.turn})."]
    lines.append("  Front at the end: " + ", ".join(
        f"{s.name} {'+' if s.offset_km > 0 else ''}{s.offset_km:.0f} km" for s in c.sectors.values())
        + "  (+ = into West Germany)")
    for side, name in ((Side.BLUE, "NATO"), (Side.RED, "Warsaw Pact")):
        afs = [a for a in c.airframes.values() if c.squadrons[a.squadron_id].side is side]
        crew = Counter(p.status for p in c.pilots.values() if p.side is side)
        kills = sum(p.air_kills for p in c.pilots.values() if p.side is side and p.role.value == "pilot")
        power = sum(f.strength for f in c.formations.values() if f.side is side and f.arrival_turn <= c.turn)
        full = sum(f.max_strength for f in c.formations.values() if f.side is side and f.arrival_turn <= c.turn)
        lines += [f"  {name}:",
                  f"    sorties {sum(a.sorties for a in afs)}, aircraft lost "
                  f"{sum(1 for a in afs if a.status is AirframeStatus.DESTROYED)}, air kills {kills}",
                  f"    aircrew killed {crew[PilotStatus.KIA]}, missing {crew[PilotStatus.MIA]}, "
                  f"captured {crew[PilotStatus.POW]}",
                  f"    ground forces at {power / full:.0%} of full strength" if full else "    no ground forces"]
        lost = [o for o in c.objectives.values() if o.side is side and o.destroyed]
        if lost:
            lines.append(f"    {len(lost)} objectives destroyed or lost")
    me = c.player_pilot()
    lines.append(f"  You: {me.rank} {me.name}, {me.sorties} sorties, {me.air_kills} air and "
                 f"{me.ground_kills} ground kills, {me.status.value}; "
                 f"{c.counters.get('flown_in_dcs', 0)} missions flown in DCS.")
    return lines
