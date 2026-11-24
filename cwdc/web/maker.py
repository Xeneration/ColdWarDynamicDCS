"""Campaign maker (Phase D): the data behind the year-slider screen.

    info(installed, theatre)     bars for the slider, nations, presets, theatres, what you own
    preview(installed, req)      "what's in this war": your aircraft and wing, both air forces,
                                 air defences, ground forces and your weapons for that year
    setup(installed, req)        the engine.generate.Setup a request describes
"""
from __future__ import annotations

from collections import defaultdict

from cwdc import era
from cwdc.engine import generate

THEATRES = [
    {"id": "germany", "name": "Central Front", "map": "Cold War Germany", "terrain": "GermanyColdWar",
     "blurb": "The Fulda Gap: NATO against the Warsaw Pact across the inner German border."},
    {"id": "syria", "name": "Southern Flank", "map": "Syria", "terrain": "Syria", "coming": True,
     "blurb": "NATO from Turkey and Cyprus against Syria and a Soviet expeditionary force (coming later)."},
]
ROLE_PLAIN = {"tank": "tanks", "ifv": "infantry fighting vehicles", "apc": "personnel carriers",
              "recon": "recon vehicles", "artillery": "artillery", "mlrs": "rocket launchers", "truck": "trucks"}
SETUP_KEYS = ("theatre", "year", "side", "nations", "sweden", "strict", "mixed_wing", "player_airframe")


def info(installed, theatre: str = "germany") -> dict:
    theatres = []
    for t in THEATRES:
        avail = t["id"] in generate.THEATRE_FILES and (not installed.terrains or t["terrain"] in installed.terrains)
        theatres.append({**t, "available": avail})
    nations = [{"name": n, "side": s["side"], "toggle": s.get("toggle"), "default": s.get("default", True),
                "default_on": s.get("default_on", [era.FIRST_YEAR, era.LAST_YEAR])}
               for n, s in era.nations().items() if theatre in s["theatres"]]
    presets = [{"id": k, "name": v["name"], "blurb": v.get("blurb", ""), "fixed": bool(v.get("fixed")),
                **({"setup": v["setup"]} if "setup" in v else {})} for k, v in generate.presets().items()]
    return {"years": [era.FIRST_YEAR, era.LAST_YEAR], "theatres": theatres, "nations": nations,
            "presets": presets, "bars": era.slider(installed, theatre=theatre),
            "owned": sorted(installed.flyable)}


def setup(installed, req: dict) -> generate.Setup:
    kw = {k: req[k] for k in SETUP_KEYS if req.get(k) is not None}
    return generate.setup_from_installed(installed, **kw)


def _player_options(s: generate.Setup) -> list[dict]:
    inst = s.installed()
    seen, out = set(), []
    for x in era.airframes_in(s.year, s.active_nations(), side=s.side, strict=s.strict, installed=inst):
        types = era.player_types(x.airframe, inst)
        if not types or x.airframe.id in seen or x.airframe.role not in generate.FIGHTER_ROLES + generate.STRIKE_ROLES:
            continue
        seen.add(x.airframe.id)
        out.append({"id": x.airframe.id, "name": x.airframe.name, "nation": x.nation, "types": types,
                    "stand_in": x.airframe.stand_in, "carrier": x.airframe.carrier, "role": x.airframe.role})
    return out


def preview(installed, req: dict) -> dict:
    s = setup(installed, req)
    seed = int(req.get("seed") or 1976)
    out = {"year": s.year, "nations": s.active_nations(), "player_options": _player_options(s),
           "ok": True, "error": None, "notes": []}
    try:
        oob = generate.oob(s, seed)
        th = generate.adapt_theatre(s, seed)
    except generate.SetupError as e:
        out.update(ok=False, error=str(e))
        return out
    names = {a.id: a for a in era.airframes().values()}
    sq_out = defaultdict(list)
    for sq in oob["squadrons"]:
        af = names[sq["airframe"]]
        sq_out["west" if sq["side"] == "blue" else "east"].append({
            "name": sq["name"], "nation": sq["nation"], "airframe": af.name, "type": sq["aircraft_type"],
            "stand_in": af.stand_in, "role": af.role, "home": bool(sq.get("player_squadron")),
            "wing": bool(sq.get("player_wing")), "base": sq["base_spec"]["name"]})
    out["squadrons"] = dict(sq_out)
    out["wing"] = [x for x in sq_out["west"] if x["home"] or x["wing"]]
    out["player"] = next(x for x in out["wing"] if x["home"])
    out["sites"] = sorted(o["name"] for o in th["objectives"] if o["kind"] == "sam")
    inst = s.installed()
    out["air_defence"] = {side: sorted({v["name"] for v in era.air_defence_in(s.year, side, s.active_nations(), inst).values()
                                        if v["kind"] != "ewr"}) for side in era.SIDES}
    ground = {}
    for side in era.SIDES:
        by_role = defaultdict(list)
        for k, v in era.ground_in(s.year, side, s.active_nations(), inst).items():
            by_role[v["role"]].append(k)
        ground[side] = [{"role": ROLE_PLAIN.get(r, r), "types": sorted(ts)} for r, ts in by_role.items()
                        if r in ("tank", "ifv", "apc", "artillery", "mlrs")]
    out["ground"] = ground
    out["weapons"] = [{"type": w["type"], "airframe": w["airframe"],
                       "families": [b for b in era.weapon_bars(w["type"]) if b["year"] <= s.year]}
                      for w in out["wing"]]
    if s.year < 1959:
        out["notes"].append("No surface-to-air missiles yet: air defence is flak. The SA-2 arrives in 1959.")
    if s.year < 1956:
        out["notes"].append("No guided air-to-air missiles: guns only until the AIM-9B (1956).")
    if not any(x["home"] for x in out["wing"]):
        out["notes"].append("You fly none of these: pick a year where one of your modules is in service.")
    stand_ins = sorted({f"{x['airframe']} (flown by the {x['type']})" for side in sq_out.values() for x in side
                        if x["stand_in"]})
    if stand_ins:
        out["notes"].append("Stand-ins: " + "; ".join(stand_ins) + ".")
    return out
