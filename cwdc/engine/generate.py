"""Campaign maker: assemble the war from a year (theatre, year, nations, player airframe).

The theatre file gives the geography: front, sectors, cities, objective sites, the logistics
network and the candidate airfields (`generator` section). This module fills it with the forces of
the chosen year:

  * oob(setup)            squadrons (airframe by year, nation, base, name, callsign, tasks) plus the
                          ground formations, handed to nations that are in the war
  * adapt_theatre(setup)  objective sites re-equipped for the year: SAM/AAA sites get a system in
                          service (or fall back to AAA), armour and convoys get the year's vehicles

The Fulda 1976 preset does not come through here: it keeps its hand-written order of battle.
"""
from __future__ import annotations

import copy
import random
from dataclasses import asdict, dataclass, field
from functools import lru_cache

from cwdc import era
from cwdc import theater as T
from cwdc.engine import rng

THEATRE_FILES = {"germany": "theater_fulda_1976.yaml", "syria": "theater_syria.yaml"}
CAMPAIGN_THEATER = {"germany": "fulda_1976", "syria": "syria"}      # Setup.theatre -> Campaign.theater
NATION_WEIGHT = {"USA": 4, "USSR": 5, "West Germany": 2, "UK": 2, "GDR": 2, "France": 1.5}
FIGHTER_ROLES = ("fighter", "interceptor", "multirole")
STRIKE_ROLES = ("attack", "bomber", "multirole")
ROLE_TASKS = {"fighter": ["CAP"], "interceptor": ["CAP"], "multirole": ["CAP", "Strike", "CAS", "BAI"],
              "attack": ["CAS", "BAI", "Strike"], "bomber": ["Strike", "Interdiction"]}
ARM_FAMILIES = {"AGM-45 Shrike", "AGM-78 Standard ARM", "AGM-88 HARM", "Kh-58", "Kh-28"}
AD_FALLBACK = {"sam_long": ["sam_medium", "sam_short", "aaa"], "sam_medium": ["sam_short", "aaa"],
               "sam_short": ["aaa"], "aaa": [], "ewr": []}
GROUND_FALLBACK = {"tank": [], "ifv": ["apc"], "apc": ["truck"], "recon": ["apc", "truck"],
                   "artillery": ["mlrs"], "mlrs": ["artillery"], "truck": [],
                   # warships (section "naval")
                   "carrier": [], "cruiser": ["destroyer", "frigate", "corvette"],
                   "destroyer": ["frigate", "corvette"], "frigate": ["destroyer", "corvette"],
                   "corvette": ["frigate", "fac"], "fac": ["corvette"], "amphibious": ["supply"], "supply": []}
WING_EXTRA = 2              # squadrons besides the player's home squadron flying owned types
P_UPGRADE = 0.35            # a SAM site still in service is re-equipped with a newer system
P_MODERNISE = 0.7           # a vehicle type with a successor 8+ years newer is replaced by it


class SetupError(ValueError):
    pass


@dataclass
class Setup:
    theatre: str = "germany"
    year: int = 1976
    side: str = "west"
    nations: list[str] | None = None       # None = era.default_nations(theatre, year, sweden)
    sweden: bool = False
    strict: bool = False                   # no stand-ins
    player_airframe: str | None = None     # era airframe id; None = first owned one in service
    player_type: str | None = None         # flyable DCS variant; None = first owned
    mixed_wing: bool = True                # wing squadrons from any nation (and carrier types) of the side
    owned: list[str] = field(default_factory=list)   # owned DCS types (era.modules.detect)
    mods: list[str] = field(default_factory=list)
    tech: list[str] = field(default_factory=list)

    def active_nations(self) -> list[str]:
        return self.nations or era.default_nations(self.theatre, self.year, self.sweden)

    def installed(self):
        from cwdc.era.modules import Installed
        inst = Installed(tech=set(self.tech), mods={f"mod:{t}" for t in self.mods})
        for t in self.owned:
            inst.flyable[t] = f"mod:{t}" if t in self.mods else t
        return inst

    def as_dict(self) -> dict:
        return asdict(self)


def setup_from_installed(installed, **kw) -> Setup:
    """A Setup carrying what is installed on this machine."""
    return Setup(owned=sorted(installed.flyable), mods=sorted(t for t in installed.flyable if installed.is_mod(t)),
                 tech=sorted(installed.tech), **kw)


@lru_cache(maxsize=1)
def _units() -> dict:
    return T.load_data("era/units.yaml")


def theatre_data(setup: Setup) -> dict:
    if setup.theatre not in THEATRE_FILES:
        raise SetupError(f"theatre {setup.theatre!r} is not available yet")
    return T.load_data(THEATRE_FILES[setup.theatre])


def nation_of_country(country: str) -> str | None:
    return next((n for n, s in era.nations().items() if s["country"] == country), None)


def country_of(nation: str) -> str:
    return era.nations()[nation]["country"]


def presets() -> dict[str, dict]:
    return T.load_data("era/presets.yaml")["presets"]


def preset_setup(preset_id: str, installed) -> Setup | None:
    """The Setup of a preset (None for a fixed, hand-written preset such as Fulda 1976)."""
    try:
        p = presets()[preset_id]
    except KeyError:
        raise SetupError(f"unknown preset {preset_id!r}; have {', '.join(presets())}") from None
    if p.get("fixed"):
        return None
    return setup_from_installed(installed, **p["setup"])


# ---------------------------------------------------------------------------- validation
def validate(setup: Setup) -> None:
    if not era.FIRST_YEAR <= setup.year <= era.LAST_YEAR:
        raise SetupError(f"year must be {era.FIRST_YEAR}-{era.LAST_YEAR}")
    if setup.side != "west":
        raise SetupError("flying for the East arrives in a later phase; choose the West for now")
    nations_on = setup.active_nations()
    for n in nations_on:
        if n not in era.nations():
            raise SetupError(f"unknown nation {n!r}")
    for side in era.SIDES:
        if not any(era.nations()[n]["side"] == side for n in nations_on):
            raise SetupError(f"no {side} nation is in the war")
    player_choice(setup)


def player_choice(setup: Setup) -> tuple[era.InService, str]:
    """(airframe in service, flyable DCS type) the player flies."""
    inst = setup.installed()
    cands = [s for s in era.airframes_in(setup.year, setup.active_nations(), side=setup.side,
                                         strict=setup.strict, installed=inst)
             if era.player_types(s.airframe, inst) and s.airframe.role in FIGHTER_ROLES + STRIKE_ROLES]
    if setup.player_airframe:
        cands = [s for s in cands if s.airframe.id == setup.player_airframe]
        if not cands:
            raise SetupError(f"{setup.player_airframe} is not flown by your side in {setup.year}, "
                             "or you do not own a module that flies it")
    if not cands:
        raise SetupError(f"none of your aircraft modules is in service with the {setup.side} in {setup.year}")
    # prefer land-based real airframes, multirole first, then the lead nation
    role_pref = {"multirole": 0, "attack": 1, "fighter": 2, "interceptor": 3, "bomber": 4}
    nw = weights(setup)
    cands.sort(key=lambda s: (s.airframe.carrier, s.airframe.stand_in, role_pref[s.airframe.role],
                              -nw.get(s.nation, 1), s.airframe.id))
    pick = cands[0]
    types = era.player_types(pick.airframe, inst)
    ptype = setup.player_type if setup.player_type in types else types[0]
    return pick, ptype


def wing_choice(setup: Setup, pick: era.InService, ptype: str) -> list[tuple[era.InService, str]]:
    """Up to WING_EXTRA more squadrons for the player's wing, each flying another owned type in
    service with the side that year: land-based and real types first, the newest of them first (the
    year's signature aircraft), then the player's nation."""
    inst = setup.installed()
    cands = [s for s in era.airframes_in(setup.year, setup.active_nations(), side=setup.side,
                                         strict=setup.strict, installed=inst)
             if s.airframe.role in FIGHTER_ROLES + STRIKE_ROLES and era.player_types(s.airframe, inst)]
    if not setup.mixed_wing:
        cands = [s for s in cands if s.nation == pick.nation and not s.airframe.carrier]
    cands.sort(key=lambda s: (s.airframe.carrier, s.airframe.stand_in, -s.airframe.years[0],
                              s.nation != pick.nation,
                              _group(s.airframe) == _group(pick.airframe), -NATION_WEIGHT.get(s.nation, 1),
                              s.airframe.id))
    out, types = [], {ptype}
    for s in cands:
        t = era.player_types(s.airframe, inst)[0]
        if s.airframe.id == pick.airframe.id or t in types:
            continue
        out.append((s, t))
        types.add(t)
        if len(out) == WING_EXTRA:
            break
    return out


def carrier_spec(setup: Setup, th: dict | None = None) -> dict | None:
    """The theatre's carrier at sea, as a base spec, when a carrier is in service that year."""
    th = th or theatre_data(setup)
    specs = th.get("generator", {}).get("carriers", [])
    if not specs:
        return None
    spec = specs[0]
    decks = [v for v in era.naval_in(setup.year, "west", setup.active_nations(), setup.installed()).values()
             if v["role"] == "carrier" and set(v.get("nations", spec["nations"])) & set(spec["nations"])]
    if not decks:
        return None
    obj = next(o for o in th["objectives"] if o["id"] == spec["objective"])
    return {"id": spec["id"], "name": spec["name"], "dcs_name": "", "carrier_of": spec["objective"],
            "latlon": obj["latlon"], "nations": spec["nations"]}


def _group(af: era.Airframe) -> str:
    return "fighter" if af.role in ("fighter", "interceptor") else "strike"


# ---------------------------------------------------------------------------- air order of battle
def _tasks(af: era.Airframe, dcs_type: str, year: int) -> list[str]:
    tasks = list(ROLE_TASKS[af.role])
    fams = {w["family"] for w in era.weapon_bars(dcs_type) if w["year"] <= year}
    if fams & ARM_FAMILIES and af.role in ("multirole", "attack"):
        tasks.append("SEAD")
    if (fams & era.ANTISHIP and af.role in ("multirole", "attack")) or af.role == "bomber":
        tasks.append("Anti-ship")
    return tasks


def ordinal(n) -> str:
    if not isinstance(n, int):
        return str(n)
    suffix = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def _name(nation: str, group: str, used: set, r: random.Random) -> str:
    spec = _units()["units"][nation]
    fmt = spec[group]

    def fill(n):
        return fmt.replace("{n}th", ordinal(n)).format(n=n)
    nums = [n for n in spec["numbers"] if fill(n) not in used]
    name = fill(r.choice(nums)) if nums else f"{nation} {group} squadron {len(used) + 1}"
    used.add(name)
    return name


def weights(setup: Setup) -> dict[str, float]:
    """How much of each air force a theatre sees (the Central Front: USAF and the Soviets)."""
    return theatre_data(setup)["generator"].get("weights", NATION_WEIGHT)


def _pick(cands: list[era.InService], used_af: set, r: random.Random,
          nation_weight: dict | None = None) -> era.InService | None:
    if not cands:
        return None
    nw = nation_weight or NATION_WEIGHT
    w = [nw.get(s.nation, 1) * (0.5 if s.airframe.stand_in else 1)
         * (0.5 if s.airframe.role == "bomber" else 1)
         * (0.25 if s.airframe.id in used_af else 1) for s in cands]
    return r.choices(cands, weights=w)[0]


def oob(setup: Setup, seed: int) -> dict:
    """Generated order of battle in the oob_*.yaml shape (squadrons + formations)."""
    validate(setup)
    th = theatre_data(setup)
    gen = th["generator"]
    r = rng(seed, 0, f"generate:{setup.year}")
    inst = setup.installed()
    nations_on = setup.active_nations()
    sides = {"west": "blue", "east": "red"}
    squadrons, used_names, base_load = [], set(), {}
    callsigns = {"west": list(_units()["west_callsigns"]), "east": list(_units()["east_callsigns"])}
    for k in callsigns:
        r.shuffle(callsigns[k])

    def base_for(side: str, nation: str, near_front: bool, at: dict | None = None) -> dict:
        if at is not None:                     # the player's wing shares the home airfield
            base_load[at["id"]] = base_load.get(at["id"], 0) + 1
            return at
        bases = gen["bases"][side]
        fits = [b for b in bases if nation in b.get("nations", [])] or bases
        free = [b for b in fits if base_load.get(b["id"], 0) < 2] or [b for b in bases if base_load.get(b["id"], 0) < 2]
        b = free[0] if near_front else r.choice(free[:4])
        base_load[b["id"]] = base_load.get(b["id"], 0) + 1
        return b

    def add(s: era.InService, side: str, group: str, player: bool = False, dcs_type: str | None = None,
            wing: bool = False):
        dcs_type = dcs_type or s.dcs_type
        deck = deck_for(s)
        b = deck or base_for(side, s.nation, near_front=player,
                             at=squadrons[0]["base_spec"] if wing and not squadrons[0]["base_spec"].get("carrier_of")
                             else None)
        cs = callsigns[side].pop(0) if callsigns[side] else f"{side.title()} {len(squadrons)}"
        size = 18 if s.nation not in ("USSR", "GDR", "Poland", "Czechoslovakia", "Hungary") else 24
        sq = {"id": f"{'b' if side == 'west' else 'r'}_{len(squadrons) + 1:02d}_{s.airframe.id.lower()}",
              "name": _name("USN" if s.airframe.carrier and s.nation == "USA" else s.nation, group,
                            used_names, r), "side": sides[side],
              "country": country_of(s.nation), "aircraft_type": dcs_type, "base": b["id"],
              "callsign": cs, "tasks": _tasks(s.airframe, dcs_type, setup.year), "airframes": size, "pilots": size + 2,
              "tail_prefix": _units()["units"]["USN" if s.airframe.carrier and s.nation == "USA" else s.nation]["tail"], "airframe": s.airframe.id,
              "air_rating": max(s.airframe.air, 0.05), "strike_rating": max(s.airframe.strike, 0.05), "nation": s.nation,
              "base_spec": b}
        if player:
            sq["player_squadron"] = True
        if wing:
            sq["player_wing"] = True
        squadrons.append(sq)
        return sq

    carrier = carrier_spec(setup, th)

    def deck_for(s: era.InService) -> dict | None:
        return carrier if carrier and s.airframe.carrier and s.nation in carrier["nations"] else None

    pick, ptype = player_choice(setup)
    used_af = {pick.airframe.id}
    slots = {side: dict(gen["squadrons"][side]) for side in era.SIDES}
    add(pick, setup.side, _group(pick.airframe), player=True, dcs_type=ptype)
    for s, t in wing_choice(setup, pick, ptype):
        used_af.add(s.airframe.id)
        g = _group(s.airframe)
        if slots[setup.side].get(g, 0) > 0:        # a wing squadron takes an AI slot of its kind
            slots[setup.side][g] -= 1
        add(s, setup.side, g, dcs_type=t, wing=True)
    for side in era.SIDES:
        all_in = [s for s in era.airframes_in(setup.year, nations_on, side=side, strict=setup.strict, installed=inst)
                  if not s.airframe.carrier]
        if side == "west" and carrier:          # the carrier air wing
            navy = [s for s in era.airframes_in(setup.year, nations_on, side=side, strict=setup.strict,
                                                installed=inst)
                    if deck_for(s) and s.airframe.role in FIGHTER_ROLES + STRIKE_ROLES]
            for group, roles in (("fighter", FIGHTER_ROLES), ("strike", STRIKE_ROLES)):
                for _ in range(gen.get("carrier_wing", {}).get(group, 0)):
                    s = _pick([x for x in navy if x.airframe.role in roles and x.airframe.id not in used_af],
                              used_af, r, weights(setup))
                    if s is None:
                        break
                    used_af.add(s.airframe.id)
                    add(s, side, group)
        for group, roles in (("fighter", FIGHTER_ROLES), ("strike", STRIKE_ROLES)):
            cands = [s for s in all_in if s.airframe.role in roles
                     and (group == "fighter" or s.airframe.strike >= 0.3)]
            for _ in range(slots[side][group]):
                s = _pick(cands, used_af, r, weights(setup))
                if s is None:
                    break
                used_af.add(s.airframe.id)
                add(s, side, group)
    return {"squadrons": squadrons, "formations": _formations(setup, th)}


def _formations(setup: Setup, th: dict) -> list[dict]:
    from cwdc.engine.factory import THEATERS
    base = T.load_data(THEATERS[CAMPAIGN_THEATER[setup.theatre]][1])
    lead = th["generator"]["lead"]
    nations_on = set(setup.active_nations())
    out = []
    for f in copy.deepcopy(base.get("formations", [])):
        n = nation_of_country(f["country"])
        side = "west" if f["side"] == "blue" else "east"
        if n not in nations_on:
            f["country"] = country_of(lead[side])
        out.append(f)
    return out


# ---------------------------------------------------------------------------- objectives
@lru_cache(maxsize=1)
def _unit_index() -> dict[str, tuple[str, str]]:
    """DCS vehicle type -> (section, entry id) in ground.yaml."""
    idx: dict[str, tuple[str, str]] = {}
    g = era._ground()
    for section in ("air_defence", "ground", "naval"):
        for k, spec in g.get(section, {}).items():
            for u in spec["units"]:
                idx.setdefault(u, (section, k))
    return idx


def _class(section: str, spec: dict) -> str:
    return spec["kind"] if section == "air_defence" else spec["role"]


def _available(section: str, year: int, side: str, nations, inst) -> dict[str, dict]:
    if section == "air_defence":
        return era.air_defence_in(year, side=side, nations_on=nations, installed=inst)
    if section == "naval":
        return era.naval_in(year, side=side, nations_on=nations, installed=inst)
    return era.ground_in(year, side=side, nations_on=nations, installed=inst)


def _replacement(section: str, cls: str, year: int, side: str, nations, inst, r: random.Random,
                 newer_than: int | None = None) -> tuple[str, dict] | None:
    chain = [cls] + (AD_FALLBACK if section == "air_defence" else GROUND_FALLBACK).get(cls, [])
    for nat in (nations, None):                         # own nation first, then anyone on the side
        avail = _available(section, year, side, nat, inst)
        for c in chain:
            opts = sorted((k, v) for k, v in avail.items() if _class(section, v) == c
                          and (newer_than is None or v["years"][0] > newer_than))
            if opts:
                return r.choice(opts)
        if newer_than is not None:
            return None
    return None


def _site_units(spec: dict) -> list[dict]:
    """Lay out an air-defence system: radars in the middle, launchers on a 150 m ring."""
    import math
    units = spec["units"]
    radars = [u for u in units if not any(x in u.lower() for x in (" ln", "launcher", "volhov", "2p25", "5p85"))]
    launchers = [u for u in units if u not in radars]
    out = [{"type": u, "dx": i * 40, "dz": 0} for i, u in enumerate(radars)]
    for i, u in enumerate(launchers):
        a = 2 * math.pi * i / max(1, len(launchers))
        out.append({"type": u, "dx": round(150 * math.cos(a)), "dz": round(150 * math.sin(a))})
    return out


def _site_label(name: str) -> str:
    for word in (" site", " battery", " position"):
        i = name.find(word)
        if i > 0:
            return name[i:]
    return " site " + name


def adapt_theatre(setup: Setup, seed: int) -> dict:
    """The theatre data with every objective equipped for the year and the nations in the war."""
    th = copy.deepcopy(theatre_data(setup))
    r = rng(seed, 0, f"adapt:{setup.year}")
    inst = setup.installed()
    nations_on = set(setup.active_nations())
    lead = th["generator"]["lead"]
    year = setup.year
    idx = _unit_index()
    g = era._ground()
    keep = []
    for o in th["objectives"]:
        side = "west" if o["side"] == "blue" else "east"
        n = nation_of_country(o["country"])
        if n not in nations_on:
            n = lead[side]
            o["country"] = country_of(n)
        nat = [n]
        if o["kind"] == "sam":
            votes = {}
            for u in o["units"]:
                if u["type"] in idx and idx[u["type"]][0] == "air_defence":
                    votes[idx[u["type"]][1]] = votes.get(idx[u["type"]][1], 0) + 1
            sys_id = max(votes, key=votes.get)
            spec = g["air_defence"][sys_id]
            avail = _available("air_defence", year, side, nat, inst)
            new = None
            if sys_id in avail:
                if r.random() < P_UPGRADE:
                    new = _replacement("air_defence", spec["kind"], year, side, nat, inst, r,
                                       newer_than=spec["years"][0])
            else:
                new = _replacement("air_defence", spec["kind"], year, side, nat, inst, r)
                if new is None:
                    continue                        # nothing in service: the site does not exist yet
            if new is not None:
                nid, nspec = new
                o["units"] = _site_units(nspec)
                o["threat_range_km"] = nspec["range_km"]
                o["name"] = (nid if nspec["kind"] != "aaa" else "AAA") + _site_label(o["name"])
                if nspec["kind"] == "aaa":
                    o["kind"] = "sam"               # still an air-defence target for the planner
            keep.append(o)
            continue
        units, swap = [], {}
        for u in o["units"]:
            hit = idx.get(u["type"])
            if hit is None:
                units.append(u)                     # statics (stations, warehouses) are timeless
                continue
            section, key = hit
            spec = g[section][key]
            if u["type"] in swap:                   # one decision per type per objective
                rep = swap[u["type"]]
            elif key in _available(section, year, side, nat, inst):
                rep = None
                if r.random() < P_MODERNISE:
                    rep = _replacement(section, _class(section, spec), year, side, nat, inst, r,
                                       newer_than=spec["years"][0] + 7)
                rep = rep or (key, spec)
            else:
                rep = _replacement(section, _class(section, spec), year, side, nat, inst, r)
            swap[u["type"]] = rep
            if rep is not None and rep[0] == key:
                units.append(u)
                continue
            if rep is not None:
                nu = dict(u)
                nu["type"] = rep[1]["units"][-1]
                units.append(nu)
        if units:
            o["units"] = units
            if o["kind"] == "ship":                 # the group's reach is its best air defence
                o["threat_range_km"] = max(g["naval"][idx[u["type"]][1]]["range_km"]
                                           for u in units if u["type"] in idx)
            keep.append(o)
    th["objectives"] = keep
    # dates: same season, the chosen year
    th["start"] = f"{year}{th['start'][4:]}"
    if th.get("crisis"):
        th["crisis"]["start"] = f"{year}{th['crisis']['start'][4:]}"
    th["name"] = f"{th['generator'].get('title', 'Central Front')} {year}"
    return th
