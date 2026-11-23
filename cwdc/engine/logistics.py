"""Logistics: supply flows along the network to the formations (Phase 2 step 4).

Each turn:
  1. rail heads receive new supply (`production`);
  2. supply flows downstream along links, limited by capacity x (1 - damage) and the
     space at the receiving node; a node shares what it has between its outgoing
     links in proportion to their capacity;
  3. every sector's formations draw from their side's sector depot (attacking
     costs more). What they get, and how far the depot is from the fighting,
     sets each formation's `supply` (0..1), which the ground war uses;
  4. engineers repair link damage; interdiction targets (rail yards, columns)
     are rebuilt as their link is repaired.

Interdiction: destroying the units of an objective with `supply_link` damages
that link in proportion; hitting a depot (`supply_node`) burns part of its stock.
"""
from __future__ import annotations

import math
from collections import deque

from cwdc import theater as T
from cwdc.engine import rng
from cwdc.model import Campaign, Objective, Pos, Sector, Side, SupplyNode

CONSUMPTION = 0.15            # supply per strength point per turn when defending
ATTACK_FACTOR = 1.5           # attacking burns more
REPAIR_PER_TURN = 0.15        # link damage repaired per turn
DEPOT_REACH_KM = 60.0         # beyond this the depot-to-front distance starts to hurt
DEPOT_FALLOFF_KM = 150.0
MIN_DISTANCE_FACTOR = 0.4
SMOOTHING = 0.5               # supply state changes gradually (stocks in the field)
DEPOT_HIT_STOCK_LOSS = 0.5    # share of a depot's stock lost when *all* its units are destroyed


def _order(c: Campaign) -> list[str]:
    """Nodes in upstream-to-downstream order (BFS from the producing nodes)."""
    down: dict[str, list[str]] = {}
    for l in c.supply_links:
        down.setdefault(l.a, []).append(l.b)
    q = deque(n.id for n in c.supply_nodes.values() if n.production > 0)
    seen, order = set(q), []
    while q:
        n = q.popleft()
        order.append(n)
        for m in down.get(n, []):
            if m not in seen:
                seen.add(m)
                q.append(m)
    order += [n for n in c.supply_nodes if n not in seen]
    return order


def flow(c: Campaign) -> None:
    for n in c.supply_nodes.values():
        n.stock = min(n.capacity, n.stock + n.production)
    for l in c.supply_links:
        l.flow = 0.0
    for nid in _order(c):
        node = c.supply_nodes[nid]
        outs = [l for l in c.supply_links if l.a == nid]
        requests = []
        for l in outs:
            dst = c.supply_nodes[l.b]
            if dst.side is not node.side:          # cut by the front
                requests.append(0.0)
                continue
            requests.append(max(0.0, min(l.capacity * (1.0 - l.damaged), dst.capacity - dst.stock)))
        total = sum(requests)
        if total <= 0:
            continue
        scale = min(1.0, node.stock / total)
        for l, req in zip(outs, requests):
            moved = req * scale
            node.stock -= moved
            c.supply_nodes[l.b].stock += moved
            l.flow = moved


def _sector_front_center(c: Campaign, sec: Sector) -> Pos:
    pts = [T.latlon_to_pos(*c.front_line[i]) for i in sec.front_points]
    return Pos(x=sum(p.x for p in pts) / len(pts), z=sum(p.z for p in pts) / len(pts))


def sector_depot(c: Campaign, sec: Sector, side: Side) -> SupplyNode | None:
    """The sector's own depot, or the nearest friendly node if it has been lost."""
    nid = sec.depots.get(side)
    if nid in c.supply_nodes and c.supply_nodes[nid].side is side:
        return c.supply_nodes[nid]
    own = [n for n in c.supply_nodes.values() if n.side is side]
    if not own:
        return None
    centre = _sector_front_center(c, sec)
    return min(own, key=lambda n: n.pos.dist(centre))


def distance_factor(depot: SupplyNode, front: Pos) -> float:
    d_km = depot.pos.dist(front) / 1000
    return max(MIN_DISTANCE_FACTOR, min(1.0, 1.0 - max(0.0, d_km - DEPOT_REACH_KM) / DEPOT_FALLOFF_KM))


def consume(c: Campaign) -> list[str]:
    from cwdc.engine.ground import active
    from cwdc.model import Posture
    lines = []
    for sec in c.sectors.values():
        centre = _sector_front_center(c, sec)
        for side in Side:
            forms = [f for f in c.formations.values()
                     if f.side is side and f.sector_id == sec.id and active(c, f)]
            if not forms:
                continue
            demand = sum(f.strength * CONSUMPTION * (ATTACK_FACTOR if f.posture is Posture.ATTACK else 1.0)
                         for f in forms)
            depot = sector_depot(c, sec, side)
            if depot is None or demand <= 0:
                got, dist = 0.0, MIN_DISTANCE_FACTOR
            else:
                delivered = min(depot.stock, demand)
                depot.stock -= delivered
                got, dist = delivered / demand, distance_factor(depot, centre)
            level = got * dist
            for f in forms:
                f.supply = (1 - SMOOTHING) * f.supply + SMOOTHING * level
            if level < 0.6:
                who = "NATO" if side is Side.BLUE else "Warsaw Pact"
                lines.append(f"  {sec.name}: {who} supply short ({level:.0%} of needs"
                             + (f", depot {depot.name}" if depot else ", no depot") + ")")
    return lines


def repair(c: Campaign) -> None:
    for l in c.supply_links:
        l.damaged = max(0.0, l.damaged - REPAIR_PER_TURN)


def link(c: Campaign, link_id: str):
    return next((l for l in c.supply_links if l.id == link_id), None)


def on_unit_destroyed(c: Campaign, o: Objective) -> None:
    """Interdiction effect of a vehicle/structure destroyed at a logistics target."""
    if o.supply_link:
        lk = link(c, o.supply_link)
        if lk:
            dead = sum(1 for u in o.units if not u.alive) / len(o.units)
            lk.damaged = max(lk.damaged, min(1.0, dead))
    if o.supply_node and o.supply_node in c.supply_nodes:
        node = c.supply_nodes[o.supply_node]
        node.stock *= 1.0 - DEPOT_HIT_STOCK_LOSS / len(o.units)


def sync_interdiction(c: Campaign) -> None:
    """Rail yards and columns are rebuilt as engineers repair their link."""
    r = rng(c.seed, c.turn, "interdiction")
    for o in c.objectives.values():
        if not o.supply_link:
            continue
        lk = link(c, o.supply_link)
        if lk is None or T.side_at_pos(c.front_line, o.pos) is not o.side:
            continue
        target = max(0, math.ceil(len(o.units) * (1.0 - lk.damaged)))
        dead = [u for u in o.units if not u.alive]
        alive = len(o.units) - len(dead)
        if alive < target:
            r.shuffle(dead)
            for u in dead[:target - alive]:
                u.alive = True


def logistics_turn(c: Campaign) -> list[str]:
    if not c.supply_links or not all(l.id for l in c.supply_links):
        return []
    flow(c)
    lines = consume(c)
    repair(c)
    sync_interdiction(c)
    return lines


def report(c: Campaign) -> list[str]:
    out = []
    for sec in c.sectors.values():
        parts = []
        for side in Side:
            d = sector_depot(c, sec, side)
            forms = [f for f in c.formations.values() if f.side is side and f.sector_id == sec.id]
            sup = sum(f.supply for f in forms) / len(forms) if forms else 0
            parts.append(f"{side.value} {d.name if d else '-'} {d.stock if d else 0:.0f} ({sup:.0%})")
        out.append(f"  {sec.name:<22} " + " | ".join(parts))
    cut = [l for l in c.supply_links if l.damaged > 0.05]
    if cut:
        out.append("  Damaged links: " + ", ".join(
            f"{c.supply_nodes[l.a].name} -> {c.supply_nodes[l.b].name} {l.damaged:.0%}" for l in cut))
    return out
