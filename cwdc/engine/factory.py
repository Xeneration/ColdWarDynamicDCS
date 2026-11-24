"""Create a new campaign from theater + order-of-battle data."""
from __future__ import annotations

import re
import secrets
from datetime import datetime

from cwdc import theater as T
from cwdc.engine import airbases, crews, crisis as crisis_mod, planner, rng, weather
from cwdc.model import (Airbase, Airframe, Campaign, Formation, ObjUnit, Objective, ObjectiveKind,
                        Pilot, Pos, Sector, Side, Squadron, SupplyLink, SupplyNode, Task)

THEATERS = {"fulda_1976": ("theater_fulda_1976.yaml", "oob_fulda_1976.yaml")}


def slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "campaign"


def new_campaign(name: str, seed: int = 1976, theater: str = "fulda_1976",
                 player_name: str = "Player", crisis: bool = False) -> Campaign:
    """crisis=True opens with the pre-war crisis (engine.crisis); False starts on day 1 of the war
    (tests, balance runs)."""
    th_file, oob_file = THEATERS[theater]
    th = T.load_data(th_file)
    oob = T.load_data(oob_file)
    names = T.load_data("names.yaml")
    terrain = T.terrain()
    r = rng(seed, 0, "factory")

    c = Campaign(id=slug(name), name=name, seed=seed, theater=theater,
                 time=datetime.fromisoformat(th["start"]), turn_hours=th["turn_hours"])
    c.front_line = [tuple(p) for p in th["front_line"]]
    c.base_front_line = list(c.front_line)
    c.cap_stations = {Side(s): [T.latlon_to_pos(*ll) for ll in pts]
                      for s, pts in th["cap_stations"].items()}

    for ab in th["airbases"]:
        ap = terrain.airports[ab["dcs_name"]]
        c.airbases[ab["id"]] = Airbase(id=ab["id"], dcs_name=ab["dcs_name"], side=Side(ab["side"]),
                                       pos=Pos(x=ap.position.x, z=ap.position.y))

    for o in th["objectives"]:
        c.objectives[o["id"]] = _objective(o)

    _load_network(c, th)

    _add_ground_war(c, th, oob)

    used_names: set[str] = set()
    for sq in oob["squadrons"]:
        side = Side(sq["side"])
        s = Squadron(id=sq["id"], name=sq["name"], side=side, country=sq["country"],
                     aircraft_type=sq["aircraft_type"], base_id=sq["base"], callsign=sq["callsign"],
                     tasks=[Task(t) for t in sq["tasks"]],
                     player_squadron=sq.get("player_squadron", False), sorties=sq.get("sorties", 0),
                     establishment_airframes=sq["airframes"], establishment_pilots=sq["pilots"])
        c.squadrons[s.id] = s
        tails = r.sample(range(100, 999), sq["airframes"])
        for t in sorted(tails):
            aid = f"{sq['tail_prefix']}{t}"
            c.airframes[aid] = Airframe(id=aid, type=s.aircraft_type, squadron_id=s.id,
                                        quality=r.randint(70, 100))
        pool = names[side.value]
        for i in range(sq["pilots"]):
            while True:
                nm = f"{r.choice(pool['first'])} {r.choice(pool['last'])}"
                if nm not in used_names:
                    used_names.add(nm)
                    break
            pid = c.next_id(f"{side.value[0]}p")
            c.pilots[pid] = Pilot(id=pid, name=nm, rank=r.choice(pool["ranks"]), side=side,
                                  squadron_id=s.id)

    airbases.ensure_objectives(c)          # after the squadrons: parked aircraft match the residents
    crews.ensure_wsos(c)                   # back-seaters for two-seat squadrons
    c.player_name = player_name
    c.instance = secrets.token_hex(2)
    add_player_pilot(c, player_name)
    if crisis and crisis_mod.crisis_rules(c):
        crisis_mod.begin(c)
    else:
        c.add_news(f"{c.time:%d %b %Y}: Warsaw Pact forces have crossed the inner German border. "
                   "USAFE squadrons are committed to the defence of the Fulda Gap.")
    weather.initial(c)
    planner.plan_turn(c)
    return c


def _objective(o: dict) -> Objective:
    pos = T.latlon_to_pos(*o["latlon"])
    return Objective(
        id=o["id"], name=o["name"], kind=ObjectiveKind(o["kind"]), side=Side(o["side"]),
        country=o["country"], pos=pos, value=o.get("value", 1),
        threat_range_m=o.get("threat_range_km", 0) * 1000, supply_node=o.get("supply_node"),
        supply_link=o.get("supply_link"),
        formation_id=o.get("formation"), sector_id=o.get("sector"),
        base_pos=pos if o.get("sector") else None,
        units=[ObjUnit(**u) for u in o["units"]])


def _add_ground_war(c: Campaign, th: dict, oob: dict) -> list[str]:
    """Add sectors and formations that are not in the campaign yet. Returns names added."""
    added = []
    for s in th.get("sectors", []):
        if s["id"] not in c.sectors:
            c.sectors[s["id"]] = Sector(id=s["id"], name=s["name"], front_points=s["front_points"],
                                        terrain=s.get("terrain", 1.0),
                                        depots={Side(k): v for k, v in s.get("depots", {}).items()})
            added.append(s["name"])
        elif not c.sectors[s["id"]].depots and s.get("depots"):
            c.sectors[s["id"]].depots = {Side(k): v for k, v in s["depots"].items()}
    plan = th.get("war_plan", {})
    for f in oob.get("formations", []):
        if f["id"] not in c.formations:
            sector = f["sector"]
            if sector == "main":
                sector = main_effort(c, plan)
            arrival = f.get("arrival_turn", 1)
            if arrival > 1:                     # mobilisation and REFORGER timetables slip
                lo, hi = plan.get("arrival_jitter", {}).get(f["side"], (0, 0))
                arrival = max(2, arrival + rng(c.seed, 0, f"arrival:{f['id']}").randint(lo, hi))
            c.formations[f["id"]] = Formation(
                id=f["id"], name=f["name"], side=Side(f["side"]), country=f["country"],
                sector_id=sector, kind=f.get("kind", "armor"), strength=f["strength"],
                max_strength=f["strength"], arrival_turn=arrival)
            added.append(f["name"])
    return added


def main_effort(c: Campaign, plan: dict) -> str:
    """The Warsaw Pact's main axis for this campaign (rolled once, weighted by the theater)."""
    if c.main_effort is None:
        weights = {k: v for k, v in plan.get("red_main_effort", {}).items() if k in c.sectors}
        if not weights:
            weights = {next(iter(c.sectors)): 1.0}
        r = rng(c.seed, 0, "war_plan")
        c.main_effort = r.choices(list(weights), weights=list(weights.values()))[0]
    return c.main_effort


def _load_network(c: Campaign, th: dict) -> None:
    c.supply_nodes = {}
    for n in th.get("supply_nodes", []):
        c.supply_nodes[n["id"]] = SupplyNode(id=n["id"], name=n["name"], side=Side(n["side"]),
                                             pos=T.latlon_to_pos(*n["latlon"]),
                                             stock=n.get("stock", 100), capacity=n.get("capacity", 100),
                                             production=n.get("production", 0))
    c.supply_links = [SupplyLink(**l) for l in th.get("supply_links", [])]


def sync_theater(c: Campaign) -> list[str]:
    """Add objectives that were added to the theater data after the campaign was created.

    Existing objectives (and their damage) are never touched. Returns names added.
    """
    th = T.load_data(THEATERS[c.theater][0])
    oob = T.load_data(THEATERS[c.theater][1])
    added = []
    if not c.base_front_line:
        c.base_front_line = [tuple(p) for p in th["front_line"]]
    if not c.supply_links or not all(l.id for l in c.supply_links):
        _load_network(c, th)                    # Phase 1 network was storage-only: replace it
        added.append("logistics network")
    added += _add_ground_war(c, th, oob)
    for o in th["objectives"]:
        if o["id"] not in c.objectives:
            c.objectives[o["id"]] = _objective(o)
            added.append(o["name"])
        else:                                   # link older objectives to the ground war
            obj = c.objectives[o["id"]]
            if o.get("formation") and not obj.formation_id:
                obj.formation_id, obj.sector_id = o["formation"], o.get("sector")
                obj.base_pos = obj.base_pos or obj.pos
            if o.get("supply_link") and not obj.supply_link:
                obj.supply_link = o["supply_link"]
    added += airbases.ensure_objectives(c)
    added += crews.ensure_wsos(c)
    return added


def add_player_pilot(c: Campaign, name: str) -> Pilot:
    sq = next(s for s in c.squadrons.values() if s.player_squadron)
    for p in c.pilots.values():
        p.is_player = False
    pid = c.next_id("bp")
    p = Pilot(id=pid, name=name, rank="Capt", side=sq.side, squadron_id=sq.id, is_player=True)
    c.pilots[pid] = p
    return p
