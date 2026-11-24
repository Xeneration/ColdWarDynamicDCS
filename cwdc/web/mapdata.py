"""Map layers for the web UI, with a plain-English explanation on everything drawn.

Everything is from the player's (NATO) point of view: "enemy" is the Warsaw Pact.
"""
from __future__ import annotations

import math

from cwdc import theater as T
from cwdc.engine import ground
from cwdc.model import Campaign, Formation, Objective, ObjectiveKind, Pos, Sector, Side

NM = 1.852
SIDE_NAME = {Side.BLUE: "NATO", Side.RED: "Warsaw Pact"}
KIND_WORD = {"armor": "armoured", "mech": "mechanised", "cavalry": "armoured cavalry", "infantry": "infantry"}


def ll(p: Pos) -> list[float]:
    lat, lon = T.pos_to_latlon(p)
    return [round(lat, 5), round(lon, 5)]


def whose(side: Side) -> str:
    return "Friendly" if side is Side.BLUE else "Enemy"


def echelon(name: str) -> str:
    n = name.lower()
    if "brigade" in n or "brig" in n or "battle group" in n:
        return "brigade"
    if "regiment" in n:
        return "regiment"
    return "division"


# ----------------------------------------------------------------------------- sectors

def sector_geometry(c: Campaign, sec: Sector) -> dict:
    """Front midpoint of a sector and the unit normal pointing into NATO territory."""
    pts = ground._base_points(c)
    norms = ground._normals(c, pts)
    idx = sec.front_points
    nx = sum(norms[i][0] for i in idx) / len(idx)
    nz = sum(norms[i][1] for i in idx) / len(idx)
    n = math.hypot(nx, nz) or 1.0
    nx, nz = nx / n, nz / n
    dx, dz = ground.sector_shift(c, sec)
    mid = Pos(x=sum(pts[i].x for i in idx) / len(idx) + dx, z=sum(pts[i].z for i in idx) / len(idx) + dz)
    return {"mid": mid, "normal": (nx, nz), "tangent": (-nz, nx)}


def sector_odds(c: Campaign, sec: Sector) -> dict:
    """Who attacks here and how it is going, in the same terms the ground war uses."""
    R, B = ground.power(c, sec.id, Side.RED), ground.power(c, sec.id, Side.BLUE)
    if sec.attacker is Side.RED:
        odds = R / max(B * ground.defence(sec, Side.BLUE) * sec.terrain, 1.0)
    elif sec.attacker is Side.BLUE:
        odds = B / max(R * ground.defence(sec, Side.RED) * sec.terrain, 1.0)
    else:
        odds = 0.0
    gain = min(ground.MAX_ADVANCE_KM, ground.ADVANCE_PER_RATIO_KM * (odds - ground.GRIND_ODDS)) \
        if odds > ground.GRIND_ODDS else 0.0
    if sec.attacker is None:
        text = (f"{sec.name}: both sides are dug in - no major attack this period "
                f"(Warsaw Pact {R:.0f} vs NATO {B:.0f} combat power).")
    else:
        who = SIDE_NAME[sec.attacker]
        pace = f"gaining about {gain:.0f} km per 6 hours" if gain >= 0.5 else "making little headway"
        text = f"{sec.name}: {who} attacking at {odds:.1f} : 1 odds, {pace}."
    off = sec.offset_km
    where = ("on the pre-war border" if abs(off) < 0.5 else
             f"{off:.0f} km inside West Germany" if off > 0 else f"{-off:.0f} km inside East Germany")
    return {"attacker": sec.attacker.value if sec.attacker else None, "odds": round(odds, 2),
            "gain_km": round(gain, 1), "red": round(R), "blue": round(B),
            "text": text, "where": f"The front here is {where}."}


def captured_strip(c: Campaign, sec: Sector) -> list[list[float]] | None:
    """Ground taken since the war began in this sector: between the border and the front."""
    if abs(sec.offset_km) < 0.5:
        return None
    lo, hi = min(sec.front_points), max(sec.front_points)
    rng = range(max(0, lo - 1), min(len(c.base_front_line) - 1, hi + 1) + 1)
    border = [list(c.base_front_line[i]) for i in rng]
    front = [list(c.front_line[i]) if i in sec.front_points else list(c.base_front_line[i]) for i in rng]
    return border + front[::-1]


# ----------------------------------------------------------------------------- formations

def formation_positions(c: Campaign) -> dict[str, Pos]:
    """Where to draw each active formation: behind its own side of its sector's front."""
    out = {}
    for sec in c.sectors.values():
        g = sector_geometry(c, sec)
        (nx, nz), (tx, tz), mid = g["normal"], g["tangent"], g["mid"]
        for side, sign in ((Side.BLUE, 1.0), (Side.RED, -1.0)):
            forms = [f for f in c.formations.values()
                     if f.side is side and f.sector_id == sec.id and ground.active(c, f)]
            n = len(forms)
            for k, f in enumerate(sorted(forms, key=lambda f: f.id)):
                depth = 14_000 + 9_000 * (k % 2)               # first line, second line
                lateral = (k - (n - 1) / 2) * 14_000
                out[f.id] = Pos(x=mid.x + sign * nx * depth + tx * lateral,
                                z=mid.z + sign * nz * depth + tz * lateral)
    return out


def formation_info(c: Campaign, f: Formation, at: Pos) -> dict:
    ratio = f.strength / f.max_strength if f.max_strength else 0
    state = ("at full strength" if ratio > 0.9 else "battered" if ratio < 0.5 else "worn down")
    posture = "attacking" if f.posture.value == "attack" else "defending"
    return {"id": f.id, "name": f.name, "side": f.side.value, "kind": f.kind, "echelon": echelon(f.name),
            "at": ll(at), "strength": round(ratio, 2), "supply": round(f.supply, 2),
            "readiness": round(f.readiness, 2), "posture": f.posture.value,
            "text": (f"{whose(f.side)} {KIND_WORD.get(f.kind, f.kind)} {echelon(f.name)}: {f.name}. "
                     f"{posture.capitalize()} in the {c.sectors[f.sector_id].name}, {state} "
                     f"({ratio:.0%} strength, supply {f.supply:.0%}). Ground formations are abstract: "
                     "only their forward elements appear in DCS.")}


# ----------------------------------------------------------------------------- objectives

def objective_text(c: Campaign, o: Objective) -> str:
    w = whose(o.side)
    alive, total = len(o.alive_units), len(o.units)
    state = "destroyed" if not alive else f"{alive}/{total} still working"
    if o.kind is ObjectiveKind.SAM:
        km = o.threat_range_m / 1000
        return (f"{w} surface-to-air missile site: {o.name}. Dangerous within about {km:.0f} km "
                f"({km / NM:.0f} nm) - stay low or outside the ring. {state}.")
    if o.kind is ObjectiveKind.EWR:
        return (f"{w} early-warning radar: {o.name}. Spots aircraft at medium and high altitude far "
                f"across the front and directs fighters onto them. {state}.")
    if o.kind is ObjectiveKind.AIRBASE:
        ab = c.airbases.get(o.airbase_id) if o.airbase_id else None
        dmg = f", {ab.damage:.0%} damaged" if ab and ab.damage > 0 else ""
        return (f"{w} airfield defences and parked aircraft at {o.name}{dmg}. Hits here cut the "
                "squadrons' sortie rate.")
    if o.formation_id and o.formation_id in c.formations:
        f = c.formations[o.formation_id]
        return (f"{w} forward element of {f.name} ({o.kind.value}). This is what you would see and "
                f"attack in DCS - every vehicle destroyed weakens the whole formation. {state}.")
    if o.kind is ObjectiveKind.RAIL:
        lk = next((l for l in c.supply_links if l.id == o.supply_link), None)
        line = (f" on the {c.supply_nodes[lk.a].name} - {c.supply_nodes[lk.b].name} line"
                if lk and lk.a in c.supply_nodes and lk.b in c.supply_nodes else "")
        return (f"{w} rail yard{line}: {o.name}. Damage here chokes the supplies moving to the front "
                f"until engineers repair it. {state}.")
    if o.kind is ObjectiveKind.CONVOY:
        return f"{w} supply column: {o.name}. Destroying it cuts supply on its road. {state}."
    if o.kind is ObjectiveKind.DEPOT:
        return f"{w} supply depot: {o.name}. Feeds the troops in its sector; hits burn stock. {state}."
    if o.kind is ObjectiveKind.ARTILLERY:
        return f"{w} artillery: {o.name}. {state}."
    return f"{w} {o.kind.value}: {o.name}. {state}."


def objective_info(c: Campaign, o: Objective) -> dict:
    return {"id": o.id, "name": o.name, "kind": o.kind.value, "side": o.side.value, "at": ll(o.pos),
            "alive": len(o.alive_units), "total": len(o.units), "threat_m": o.threat_range_m,
            "element": bool(o.formation_id), "text": objective_text(c, o)}


# ----------------------------------------------------------------------------- airbases

def airbase_info(c: Campaign, ab) -> dict:
    from cwdc.engine import airbases
    sqs = [s for s in c.squadrons.values() if s.base_id == ab.id]
    state = airbases.describe(ab).split(":", 1)[1].strip()
    units = ", ".join(f"{s.name} ({s.aircraft_type})" for s in sqs) or "no squadron based here"
    name = base_name(c, ab)
    dcs = f" (in DCS: {ab.dcs_name})" if name.split()[0] != ab.dcs_name else ""
    return {"id": ab.id, "name": name, "side": ab.side.value, "at": ll(ab.pos),
            "damage": round(ab.damage, 2), "squadrons": [s.name for s in sqs],
            "player": any(s.player_squadron for s in sqs),
            "text": f"{whose(ab.side)} airbase {name}{dcs}: {state}. Home of: {units}."}


def base_name(c: Campaign, ab) -> str:
    return T.airbase_name(_theater_file(c), ab)


# ----------------------------------------------------------------------------- everything

def map_layers(c: Campaign) -> dict:
    from cwdc.web.views import flight_route
    th = T.load_data(_theater_file(c))
    terr = T.territory_polygons(c.front_line)
    sectors, strips = [], []
    for sec in c.sectors.values():
        g = sector_geometry(c, sec)
        odds = sector_odds(c, sec)
        nx, nz = g["normal"]
        # the arrow points the way the attacker pushes: into NATO ground (+normal) for the WP
        sign = 1.0 if sec.attacker is Side.RED else -1.0
        tail = Pos(x=g["mid"].x - sign * nx * 12_000, z=g["mid"].z - sign * nz * 12_000)
        head = Pos(x=g["mid"].x + sign * nx * 12_000, z=g["mid"].z + sign * nz * 12_000)
        sectors.append({"id": sec.id, "name": sec.name, "offset_km": round(sec.offset_km, 1),
                        "at": ll(g["mid"]), "arrow": [ll(tail), ll(head)], **odds})
        strip = captured_strip(c, sec)
        if strip:
            gainer = "Warsaw Pact" if sec.offset_km > 0 else "NATO"
            strips.append({"sector": sec.id, "side": "red" if sec.offset_km > 0 else "blue",
                           "polygon": strip, "km": round(abs(sec.offset_km)),
                           "text": f"Ground taken by the {gainer} in the {sec.name}: "
                                   f"{abs(sec.offset_km):.0f} km beyond the pre-war border."})
    pos = formation_positions(c)
    labels = th.get("map_labels", {})
    return {
        "front": [[round(a, 5), round(b, 5)] for a, b in c.front_line],
        "border": [[round(a, 5), round(b, 5)] for a, b in c.base_front_line],
        "territory": {k: [[round(a, 5), round(b, 5)] for a, b in v] for k, v in terr.items()},
        "captured": strips,
        "sectors": sectors,
        "formations": [formation_info(c, c.formations[fid], p) for fid, p in pos.items()],
        "airbases": [airbase_info(c, ab) for ab in c.airbases.values()],
        "objectives": [objective_info(c, o) for o in c.objectives.values()
                       if o.kind is not ObjectiveKind.AIRBASE],
        "cap_stations": {s.value: [ll(p) for p in pts] for s, pts in c.cap_stations.items()},
        "flights": [flight_route(c, f) for f in c.flights.values()],
        "supply_nodes": [{"id": n.id, "name": n.name, "side": n.side.value, "at": ll(n.pos),
                          "stock": round(n.stock), "capacity": round(n.capacity)}
                         for n in c.supply_nodes.values()],
        "supply_links": [{"id": l.id, "a": ll(c.supply_nodes[l.a].pos), "b": ll(c.supply_nodes[l.b].pos),
                          "side": c.supply_nodes[l.a].side.value, "damaged": round(l.damaged, 2),
                          "flow": round(l.flow), "capacity": round(l.capacity),
                          "text": f"{whose(c.supply_nodes[l.a].side)} supply line "
                                  f"{c.supply_nodes[l.a].name} - {c.supply_nodes[l.b].name}: carrying "
                                  f"{l.flow:.0f} of {l.capacity:.0f}"
                                  + (f", {l.damaged:.0%} damaged" if l.damaged > 0.05 else "")}
                         for l in c.supply_links if l.a in c.supply_nodes and l.b in c.supply_nodes],
        "places": labels.get("places", []),
        "countries": labels.get("countries", []),
    }


def _theater_file(c: Campaign) -> str:
    from cwdc.engine.factory import THEATERS
    return THEATERS[c.theater][0]
