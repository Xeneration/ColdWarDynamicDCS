"""The ground war: sector combat, the moving front, overruns, visible formation elements.

Abstract by design (decision: "abstract front + key units"). Each sector holds
formations with a combat strength. Every turn the side with the better odds
attacks; Lanchester-style attrition is applied to both sides and the attacker
gains ground in proportion to its advantage. The sector's front offset moves
the front-line polyline, which in turn decides territory (`theater.side_at`):
airbases, depots and static sites that end up behind enemy lines are overrun.

The few *visible* elements of a formation (objectives with `formation_id`) are
what DCS spawns and air power hits. Destroying them costs the formation strength;
while the formation still has strength they are rebuilt (the next battalion moves
up), so ground targets never run dry while the war goes on.
"""
from __future__ import annotations

import math
import random

from cwdc import theater as T
from cwdc.engine import airbases, news, rng
from cwdc.model import (AirframeStatus, Campaign, Formation, Objective, ObjectiveKind, Pos, Posture,
                        Sector, Side)

K_ATTRITION = 0.03           # share of the opposing force's power lost per 6 h of fighting
DEFENDER_BONUS = 1.3         # prepared defence multiplier (on top of sector terrain)
HASTY_DEFENCE = 1.05         # ... when defending ground taken during this war (no prepared positions)
# Commanders attack on *expected* odds: attacker power / (defender power x defence x terrain)
RED_MIN_ODDS = 0.85          # Soviet doctrine: keep the tempo up at modest odds
BLUE_MIN_ODDS = 0.9          # NATO counter-attacks when it expects to hold its own
PENETRATION_KM = 5.0         # a breach this deep draws NATO reinforcements
ATTACK_STRAIN = 0.04         # readiness an attacking formation loses per turn (fuel, ammo, exhaustion)
READINESS_RECOVERY = 0.03    # readiness regained per turn not attacking
MIN_READINESS = 0.4
ADVANCE_PER_RATIO_KM = 5.0   # km gained per turn for each 1.0 of odds above GRIND_ODDS
GRIND_ODDS = 0.8             # below 1:1 an attacker still grinds forward slowly
MAX_ADVANCE_KM = 8.0         # km per turn
MIN_OFFSET_KM = -10.0        # forward defence: NATO restores the border, it does not invade the GDR
REGEN_PER_TURN = 0.006       # of max strength, scaled by supply (replacements)
REVIVE_SHARE = 0.5           # share of dead visible units rebuilt per turn
AIR_KILL_SCALE = 4.0         # strength lost per point of a visible vehicle destroyed from the air
                             # (each visible element stands for a company-sized slice)
SUPPLY_FALLOFF_KM = 250.0    # stand-in until Phase 2 step 4 logistics: supply fades with distance
MIN_SUPPLY = 0.3             # ...advanced beyond the pre-war border
ON_GROUND_LOSS = 0.25        # share of ready aircraft lost when their airbase is overrun

UNIT_POINTS = [("T-", 1.0), ("Leopard", 1.0), ("M-60", 1.0), ("BMP", 0.6), ("Marder", 0.6),
               ("M-113", 0.4), ("BTR", 0.4), ("SAU", 0.8), ("M-109", 0.8), ("ZSU", 0.5),
               ("Vulcan", 0.5), ("Gepard", 0.5), ("Strela", 0.5), ("Chaparral", 0.5),
               ("Kub", 0.8), ("Ural", 0.2), ("M 818", 0.2)]


def unit_points(type_id: str) -> float:
    for key, pts in UNIT_POINTS:
        if type_id.startswith(key) or key in type_id:
            return pts
    return 0.5


def active(c: Campaign, f: Formation) -> bool:
    return f.arrival_turn <= c.turn and f.strength > 0


def power(c: Campaign, sector_id: str, side: Side) -> float:
    return sum(f.strength * f.readiness * (0.5 + 0.5 * f.supply)
               for f in c.formations.values()
               if f.side is side and f.sector_id == sector_id and active(c, f))


# ----------------------------------------------------------------------------- combat

def _apply_losses(forms: list[Formation], loss: float) -> None:
    total = sum(f.strength for f in forms)
    if total <= 0:
        return
    for f in forms:
        f.strength = max(0.0, f.strength - loss * f.strength / total)


def resolve_ground(c: Campaign) -> list[str]:
    """One turn of fighting in every sector. Returns debrief lines."""
    r = rng(c.seed, c.turn, "ground")
    lines = []
    for f in c.formations.values():
        if f.arrival_turn == c.turn and c.turn > c.war_start_turn:
            if f.sector_id == "auto":
                f.sector_id = _commit_sector(c, f.side)
            news.headline(c, "formation_arrived", "ground", name=f.name, sector=c.sectors[f.sector_id].name)
            lines.append(f"  Reinforcement: {f.name} arrives in {c.sectors[f.sector_id].name}")
    if not (c.supply_links and all(l.id for l in c.supply_links)):
        _supply_proxy(c)                 # old campaigns without a logistics network

    for sec in c.sectors.values():
        reds = [f for f in c.formations.values() if f.side is Side.RED and f.sector_id == sec.id and active(c, f)]
        blues = [f for f in c.formations.values() if f.side is Side.BLUE and f.sector_id == sec.id and active(c, f)]
        R, B = power(c, sec.id, Side.RED), power(c, sec.id, Side.BLUE)
        if R <= 0 and B <= 0:
            continue
        attacker = None
        red_odds = R / max(B * defence(sec, Side.BLUE) * sec.terrain, 1e-6)
        blue_odds = B / max(R * defence(sec, Side.RED) * sec.terrain, 1e-6)
        if B <= 0 or red_odds >= RED_MIN_ODDS:
            attacker = Side.RED
        elif R <= 0 or blue_odds >= BLUE_MIN_ODDS:
            attacker = Side.BLUE
        sec.attacker = attacker
        noise = r.uniform(0.85, 1.15)

        if attacker is None:                       # both sides dig in: skirmishing only
            _apply_losses(reds, K_ATTRITION * 0.3 * B)
            _apply_losses(blues, K_ATTRITION * 0.3 * R)
            for f in reds + blues:
                f.posture = Posture.DEFEND
                f.readiness = min(1.0, f.readiness + READINESS_RECOVERY)
            lines.append(f"  {sec.name}: front static ({_fmt(sec)}), red {R:.0f} vs blue {B:.0f}")
            continue

        att, dfn = (reds, blues) if attacker is Side.RED else (blues, reds)
        A, D = (R, B) if attacker is Side.RED else (B, R)
        d_eff = D * defence(sec, attacker.enemy) * sec.terrain
        odds = A * noise / max(d_eff, 1.0)
        # a prepared defence in good terrain takes fewer losses and inflicts more
        _apply_losses(dfn, K_ATTRITION * A * noise / sec.terrain)
        _apply_losses(att, K_ATTRITION * d_eff)
        gain = min(MAX_ADVANCE_KM, ADVANCE_PER_RATIO_KM * (odds - GRIND_ODDS)) if odds > GRIND_ODDS else 0.0
        if D <= 0:
            gain = MAX_ADVANCE_KM
        sec.offset_km += gain if attacker is Side.RED else -gain
        sec.offset_km = max(MIN_OFFSET_KM, sec.offset_km)
        for f in att:
            f.posture = Posture.ATTACK
            f.readiness = max(MIN_READINESS, f.readiness - ATTACK_STRAIN)
        for f in dfn:
            f.posture = Posture.DEFEND
            f.readiness = min(1.0, f.readiness + READINESS_RECOVERY)
        who = "Warsaw Pact" if attacker is Side.RED else "NATO"
        moved = f"advances {gain:.1f} km" if gain > 0.05 else "is held"
        lines.append(f"  {sec.name}: {who} attack (odds {odds:.1f}:1) {moved}; "
                     f"front {_fmt(sec)}, red {R:.0f} vs blue {B:.0f}")

    for f in c.formations.values():
        if active(c, f):
            f.strength = min(f.max_strength, f.strength + REGEN_PER_TURN * f.max_strength * f.supply)
        if f.strength <= 0.5 and f.arrival_turn <= c.turn:
            if f.strength > 0:
                news.headline(c, "formation_destroyed", "ground", name=f.name)
            f.strength = 0.0
    return lines


def defence(sec: Sector, defender: Side) -> float:
    """Prepared positions on your own side of the border; hasty ones on ground just taken."""
    on_taken_ground = sec.offset_km > 0.5 if defender is Side.RED else sec.offset_km < -0.5
    return HASTY_DEFENCE if on_taken_ground else DEFENDER_BONUS


def _commit_sector(c: Campaign, side: Side) -> str:
    """NATO reinforces the deepest penetration (to restore the border there), else the most
    threatened sector; the Warsaw Pact exploits its best one."""
    def odds(sec: Sector) -> float:
        own, enemy = power(c, sec.id, side), power(c, sec.id, side.enemy)
        return enemy / max(own, 1.0)
    secs = list(c.sectors.values())
    if side is Side.BLUE:
        deep = max(secs, key=lambda s: s.offset_km)
        if deep.offset_km > PENETRATION_KM:
            return deep.id
        return max(secs, key=lambda s: (odds(s), s.offset_km)).id
    return max(secs, key=lambda s: (s.offset_km, -odds(s))).id


def _supply_proxy(c: Campaign) -> None:
    """Simple stand-in for logistics: the deeper an advance, the thinner the supply."""
    for f in c.formations.values():
        if f.sector_id not in c.sectors:
            continue
        off = c.sectors[f.sector_id].offset_km
        depth = off if f.side is Side.RED else -off
        f.supply = max(MIN_SUPPLY, min(1.0, 1.0 - max(0.0, depth) / SUPPLY_FALLOFF_KM))


def _fmt(sec: Sector) -> str:
    if abs(sec.offset_km) < 0.5:
        return "on the border"
    if sec.offset_km > 0:
        return f"{sec.offset_km:.0f} km into West Germany"
    return f"{-sec.offset_km:.0f} km into East Germany"


# ----------------------------------------------------------------------------- front geometry

def _base_points(c: Campaign) -> list[Pos]:
    return [T.latlon_to_pos(lat, lon) for lat, lon in c.base_front_line]


def _normals(c: Campaign, pts: list[Pos]) -> list[tuple[float, float]]:
    """Unit normal at each front point, pointing into NATO territory."""
    out = []
    for i, p in enumerate(pts):
        a, b = pts[max(0, i - 1)], pts[min(len(pts) - 1, i + 1)]
        tx, tz = b.x - a.x, b.z - a.z
        n = math.hypot(tx, tz) or 1.0
        nx, nz = -tz / n, tx / n
        probe = Pos(x=p.x + nx * 2000, z=p.z + nz * 2000)
        if T.side_at_pos(c.base_front_line, probe) is not Side.BLUE:
            nx, nz = -nx, -nz
        out.append((nx, nz))
    return out


def sector_shift(c: Campaign, sec: Sector) -> tuple[float, float]:
    """Displacement (m) of everything in a sector caused by its front offset."""
    pts = _base_points(c)
    norms = _normals(c, pts)
    sel = [norms[i] for i in sec.front_points]
    nx = sum(n[0] for n in sel) / len(sel)
    nz = sum(n[1] for n in sel) / len(sel)
    n = math.hypot(nx, nz) or 1.0
    return nx / n * sec.offset_km * 1000, nz / n * sec.offset_km * 1000


def update_front(c: Campaign) -> None:
    """Rebuild the front polyline from the sector offsets."""
    pts = _base_points(c)
    norms = _normals(c, pts)
    offset = [0.0] * len(pts)
    for sec in c.sectors.values():
        for i in sec.front_points:
            offset[i] = sec.offset_km
    front = []
    for p, (nx, nz), off in zip(pts, norms, offset):
        moved = Pos(x=p.x + nx * off * 1000, z=p.z + nz * off * 1000)
        front.append(T.pos_to_latlon(moved))
    c.front_line = front


def reposition_objectives(c: Campaign) -> None:
    shifts = {s.id: sector_shift(c, s) for s in c.sectors.values()}
    for o in c.objectives.values():
        if o.sector_id and o.base_pos and o.sector_id in shifts:
            dx, dz = shifts[o.sector_id]
            o.pos = Pos(x=o.base_pos.x + dx, z=o.base_pos.z + dz)


# ----------------------------------------------------------------------------- overruns

def check_overruns(c: Campaign) -> list[str]:
    lines = []
    for ab in c.airbases.values():
        owner = T.side_at_pos(c.front_line, ab.pos)
        if owner is not ab.side:
            lines += _overrun_airbase(c, ab, owner)
    for node in c.supply_nodes.values():
        owner = T.side_at_pos(c.front_line, node.pos)
        if owner is not node.side:
            node.side, node.stock, node.production = owner, 0.0, 0.0
            msg = f"{node.name} has been overrun."
            news.post(c, msg, "ground")
            lines.append("  " + msg)
    for o in c.objectives.values():
        if o.sector_id or o.destroyed or o.kind is ObjectiveKind.AIRBASE:
            continue                       # formation elements move with the front; airfields change hands
        if T.side_at_pos(c.front_line, o.pos) is not o.side:
            for u in o.units:
                u.alive = False
            msg = f"{o.name} has been overrun and is lost."
            news.post(c, msg, "ground")
            lines.append("  " + msg)
    return lines


def _overrun_airbase(c: Campaign, ab, owner: Side) -> list[str]:
    lines = [f"  {ab.dcs_name} has been overrun by the {'Warsaw Pact' if owner is Side.RED else 'NATO'}."]
    news.post(c, lines[0].strip(), "ground")
    r = rng(c.seed, c.turn, f"overrun:{ab.id}")
    for sq in [s for s in c.squadrons.values() if s.base_id == ab.id]:
        ready = c.squadron_airframes(sq.id)
        r.shuffle(ready)
        lost = ready[:math.ceil(len(ready) * ON_GROUND_LOSS)]
        for a in lost:
            a.status = AirframeStatus.DESTROYED
        for a in c.squadron_airframes(sq.id, AirframeStatus.DAMAGED):
            a.status = AirframeStatus.DESTROYED      # can't fly out
        options = [b for b in c.airbases.values() if b.side is sq.side and b.id != ab.id]
        if options:
            new = min(options, key=lambda b: b.pos.dist(ab.pos))
            sq.base_id = new.id
            lines.append(f"  {sq.name} evacuates to {new.dcs_name}, losing {len(lost)} aircraft on the ground.")
        else:
            for a in c.squadron_airframes(sq.id, None):
                a.status = AirframeStatus.DESTROYED
            lines.append(f"  {sq.name} has nowhere to go and is lost.")
        news.post(c, lines[-1].strip(), "ground")
    ab.side = owner
    airbases.overrun(c, ab, owner)
    return lines


# ----------------------------------------------------------------------------- visible elements

def destroy_from_air(c: Campaign, o: Objective, idx: int) -> None:
    """A visible vehicle was destroyed by air power: the parent formation pays for it."""
    from cwdc.engine import logistics
    airbases.on_unit_destroyed(c, o, idx)
    if o.formation_id and o.formation_id in c.formations:
        f = c.formations[o.formation_id]
        f.strength = max(0.0, f.strength - unit_points(o.units[idx].type) * AIR_KILL_SCALE)
    logistics.on_unit_destroyed(c, o)


def sync_visible(c: Campaign) -> None:
    """Visible elements mirror their formation: rebuilt while it has strength, gone when it is."""
    r = rng(c.seed, c.turn, "visible")
    for o in c.objectives.values():
        if not o.formation_id or o.formation_id not in c.formations:
            continue
        f = c.formations[o.formation_id]
        if not active(c, f):
            for u in o.units:
                u.alive = False
            continue
        target = max(1, round(len(o.units) * min(1.0, f.ratio + 0.2)))   # forward elements stay strong
        alive = [u for u in o.units if u.alive]
        dead = [u for u in o.units if not u.alive]
        if len(alive) < target and dead:
            n = min(target - len(alive), max(1, math.ceil(len(dead) * REVIVE_SHARE)))
            r.shuffle(dead)
            for u in dead[:n]:
                u.alive = True
        elif len(alive) > target:
            r.shuffle(alive)
            for u in alive[:len(alive) - target]:
                u.alive = False


def ground_turn(c: Campaign) -> list[str]:
    """Everything the ground war does at the end of a turn."""
    if not c.sectors:
        return []
    lines = resolve_ground(c)
    update_front(c)
    reposition_objectives(c)
    lines += check_overruns(c)
    sync_visible(c)
    return lines
