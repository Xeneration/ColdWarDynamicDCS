"""Create a new campaign from theater + order-of-battle data."""
from __future__ import annotations

import re
from datetime import datetime

from cwdc import theater as T
from cwdc.engine import planner, rng
from cwdc.model import (Airbase, Airframe, Campaign, ObjUnit, Objective, ObjectiveKind, Pilot,
                        Pos, Side, Squadron, SupplyLink, SupplyNode, Task)

THEATERS = {"fulda_1976": ("theater_fulda_1976.yaml", "oob_fulda_1976.yaml")}


def slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "campaign"


def new_campaign(name: str, seed: int = 1976, theater: str = "fulda_1976",
                 player_name: str = "Player") -> Campaign:
    th_file, oob_file = THEATERS[theater]
    th = T.load_data(th_file)
    oob = T.load_data(oob_file)
    names = T.load_data("names.yaml")
    terrain = T.terrain()
    r = rng(seed, 0, "factory")

    c = Campaign(id=slug(name), name=name, seed=seed, theater=theater,
                 time=datetime.fromisoformat(th["start"]), turn_hours=th["turn_hours"])
    c.front_line = [tuple(p) for p in th["front_line"]]
    c.cap_stations = {Side(s): [T.latlon_to_pos(*ll) for ll in pts]
                      for s, pts in th["cap_stations"].items()}

    for ab in th["airbases"]:
        ap = terrain.airports[ab["dcs_name"]]
        c.airbases[ab["id"]] = Airbase(id=ab["id"], dcs_name=ab["dcs_name"], side=Side(ab["side"]),
                                       pos=Pos(x=ap.position.x, z=ap.position.y))

    for o in th["objectives"]:
        c.objectives[o["id"]] = Objective(
            id=o["id"], name=o["name"], kind=ObjectiveKind(o["kind"]), side=Side(o["side"]),
            country=o["country"], pos=T.latlon_to_pos(*o["latlon"]), value=o.get("value", 1),
            threat_range_m=o.get("threat_range_km", 0) * 1000, supply_node=o.get("supply_node"),
            units=[ObjUnit(**u) for u in o["units"]])

    for n in th.get("supply_nodes", []):
        c.supply_nodes[n["id"]] = SupplyNode(id=n["id"], name=n["name"], side=Side(n["side"]),
                                             pos=T.latlon_to_pos(*n["latlon"]),
                                             stock=n.get("stock", 100), capacity=n.get("capacity", 100))
    c.supply_links = [SupplyLink(**l) for l in th.get("supply_links", [])]

    used_names: set[str] = set()
    for sq in oob["squadrons"]:
        side = Side(sq["side"])
        s = Squadron(id=sq["id"], name=sq["name"], side=side, country=sq["country"],
                     aircraft_type=sq["aircraft_type"], base_id=sq["base"], callsign=sq["callsign"],
                     tasks=[Task(t) for t in sq["tasks"]],
                     player_squadron=sq.get("player_squadron", False),
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

    c.player_name = player_name
    add_player_pilot(c, player_name)
    c.add_news(f"{c.time:%d %b %Y}: Warsaw Pact forces have crossed the inner German border. "
               "USAFE squadrons are committed to the defence of the Fulda Gap.")
    planner.plan_turn(c)
    return c


def add_player_pilot(c: Campaign, name: str) -> Pilot:
    sq = next(s for s in c.squadrons.values() if s.player_squadron)
    for p in c.pilots.values():
        p.is_player = False
    pid = c.next_id("bp")
    p = Pilot(id=pid, name=name, rank="Capt", side=sq.side, squadron_id=sq.id, is_player=True)
    c.pilots[pid] = p
    return p
