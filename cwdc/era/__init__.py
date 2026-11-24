"""The era database: what flies, what is carried and what is on the ground, by year (1950-1991).

Data lives in cwdc/data/era/*.yaml (airframes, weapons, air defence and ground units, nations) and
cwdc/data/payloads/*.json (shipped payloads per DCS type). This module answers the questions the
campaign maker and generator ask:

  * airframes_in(year, nations)      which airframes each nation flies that year
  * dcs_type(airframe, installed)    the DCS type that flies it (stand-ins, mods)
  * weapon_year(store)               when a store entered service (None = unknown)
  * payloads_for(dcs_type, year)     shipped payloads whose every store existed that year
  * air_defence_in / ground_in       SAMs, AAA and ground units that year
  * slider(installed)                bars for the year slider (airframes + weapon sub-bars)
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import yaml

from cwdc.era import a4ec  # noqa: F401  (registers the A-4E-C type with pydcs)
from cwdc.era.payloads import shipped

ERA_DIR = Path(__file__).resolve().parent.parent / "data" / "era"
FIRST_YEAR, LAST_YEAR = 1950, 1991
SIDES = ("west", "east")
MOD_TYPES = {"A-4E-C"}               # DCS types that exist only when their mod is installed


def _load(name: str) -> dict:
    return yaml.safe_load((ERA_DIR / name).read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------- airframes
@dataclass(frozen=True)
class Airframe:
    id: str
    name: str
    side: str
    role: str
    dcs: tuple[str, ...]
    service: dict[str, tuple[int, int]]
    player: tuple[str, ...] = ()
    stand_in: bool = False
    carrier: bool = False
    air: float = 0.0
    strike: float = 0.0

    @property
    def years(self) -> tuple[int, int]:
        return min(a for a, _ in self.service.values()), max(b for _, b in self.service.values())

    def nations_in(self, year: int, nations=None) -> list[str]:
        return [n for n, (a, b) in self.service.items() if a <= year <= b and (nations is None or n in nations)]


@lru_cache(maxsize=1)
def airframes() -> dict[str, Airframe]:
    out = {}
    for k, v in _load("airframes.yaml")["airframes"].items():
        out[k] = Airframe(id=k, name=v["name"], side=v["side"], role=v["role"], dcs=tuple(v["dcs"]),
                          service={n: tuple(y) for n, y in v["service"].items()},
                          player=tuple(v.get("player", ())), stand_in=bool(v.get("stand_in")),
                          carrier=bool(v.get("carrier")), air=float(v.get("air", 0)),
                          strike=float(v.get("strike", 0)))
    return out


@lru_cache(maxsize=1)
def nations() -> dict[str, dict]:
    return _load("nations.yaml")["nations"]


def default_nations(theatre: str, year: int, sweden: bool = False) -> list[str]:
    """Nations the campaign maker ticks for a theatre and year."""
    out = []
    for n, spec in nations().items():
        if theatre not in spec["theatres"]:
            continue
        if n == "Sweden" and not sweden:
            continue
        lo, hi = spec.get("default_on", (FIRST_YEAR, LAST_YEAR))
        if lo <= year <= hi:
            out.append(n)
    return out


def all_dcs_types() -> set[str]:
    return {t for a in airframes().values() for t in a.dcs + a.player}


def _type_exists(t: str) -> bool:
    from dcs.helicopters import helicopter_map
    from dcs.planes import plane_map
    return t in plane_map or t in helicopter_map


def dcs_type(af: Airframe, installed=None) -> str | None:
    """The DCS type that flies `af`: the first of its `dcs` list that is available.
    Mod types need the mod installed (`installed` from era.modules.detect(); None = no mods)."""
    for t in af.dcs:
        if t in MOD_TYPES and (installed is None or not installed.owns(t)):
            continue
        if _type_exists(t):
            return t
    return None


def player_types(af: Airframe, installed) -> list[str]:
    """Flyable variants of `af` the player owns."""
    return [t for t in af.player if installed is not None and installed.owns(t) and _type_exists(t)]


@dataclass(frozen=True)
class InService:
    airframe: Airframe
    nation: str
    dcs_type: str


def airframes_in(year: int, nations_on=None, side: str | None = None, strict: bool = False,
                 installed=None) -> list[InService]:
    """(airframe, nation, DCS type) for everything in front-line service in `year`."""
    out = []
    for af in airframes().values():
        if (side and af.side != side) or (strict and af.stand_in):
            continue
        t = dcs_type(af, installed)
        if t is None:
            continue
        out.extend(InService(af, n, t) for n in af.nations_in(year, nations_on))
    return out


ROLE_FLIGHT = {        # cruise km/h, cruise alt m, low alt m
    "fighter": (850, 6000, 500), "interceptor": (900, 8000, 800), "multirole": (850, 6000, 300),
    "attack": (750, 3000, 200), "bomber": (850, 7000, 150), "helicopter": (200, 300, 50),
    "attack_helicopter": (220, 200, 30), "awacs": (600, 9000, 9000), "tanker": (600, 8000, 8000),
    "transport": (500, 6000, 3000)}
TWO_SEAT = {"F-4E-45MC", "F-4E", "F-14A-135-GR", "F-14A-135-GR-Early", "F-14A-95-GR", "F-14B", "F-14A",
            "Tornado IDS", "Su-24M", "A6E", "Tu-22M3"}


def catalogue_defaults() -> dict[str, dict]:
    """Airframe-catalogue entries (theater.airframe_catalogue) for every DCS type in the era
    database: flight profile by role, crew, ratings and player-flyable. Hand-written entries in
    airframes.yaml take precedence."""
    out: dict[str, dict] = {}
    flyable = {t for a in airframes().values() for t in a.player}
    for af in airframes().values():
        kmh, alt, low = ROLE_FLIGHT[af.role]
        for t in af.dcs + af.player:
            if t in out:
                continue
            out[t] = {"air_rating": af.air or 0.5, "strike_rating": af.strike or 0.5, "flyable": t in flyable,
                      "crew": 2 if t in TWO_SEAT else 1, "cruise_kmh": kmh, "cruise_alt_m": alt,
                      "low_alt_m": low, "loadouts": {}, "era_role": af.role}
    return out


# ---------------------------------------------------------------------------- weapons
@lru_cache(maxsize=1)
def _weapon_rules() -> list[tuple[re.Pattern, int, str]]:
    rules = []
    for fam in _load("weapons.yaml")["weapons"]:
        for pat in fam["match"]:
            rules.append((re.compile(pat, re.I), int(fam["year"]), fam["family"]))
    return rules


@lru_cache(maxsize=1)
def _clsid_names() -> dict[str, str]:
    from dcs import weapons_data
    return {v["clsid"]: v["name"] for v in vars(weapons_data.Weapons).values()
            if isinstance(v, dict) and "clsid" in v and "name" in v}


def store_label(clsid: str) -> str:
    """'<CLSID> <pydcs name>' - what the weapons.yaml patterns match against."""
    return f"{clsid} {_clsid_names().get(clsid, '')}".strip()


@lru_cache(maxsize=None)
def weapon_info(clsid: str) -> tuple[int, str] | None:
    """(introduction year, family) of a store; None when no rule matches."""
    label = store_label(clsid)
    for pat, year, fam in _weapon_rules():
        if pat.search(label):
            return year, fam
    return None


def weapon_year(clsid: str) -> int | None:
    info = weapon_info(clsid)
    return info[0] if info else None


def payload_year(pylons: dict[int, str]) -> int | None:
    """First year a payload is possible: the latest of its stores (None if any is unknown)."""
    years = [weapon_year(c) for c in pylons.values()]
    return None if any(y is None for y in years) else max(years, default=FIRST_YEAR)


def payloads_for(dcs_type_: str, year: int) -> list[dict]:
    """Shipped payloads of a DCS type whose every store existed in `year`."""
    out = []
    for p in shipped(dcs_type_):
        y = payload_year(p["pylons"])
        if y is not None and y <= year:
            out.append({**p, "year": y})
    return out


NOT_WEAPONS = {"fuel", "equipment", "ECM pod", "chaff/flare", "targeting pod", "recon pod"}
ARM = {"AGM-45 Shrike", "AGM-78 Standard ARM", "AGM-88 HARM", "Kh-58", "Kh-28"}


def _is_a2a(family: str) -> bool:
    return family.startswith(("AIM-", "R-"))


def pick_payload(dcs_type_: str, year: int, kind: str) -> dict | None:
    """The most-shipped payload of `dcs_type_` that existed in `year` and suits `kind`:
    "air" (air-to-air only), "sead" (carries an anti-radiation missile) or "ground"."""
    best, best_score = None, -1.0
    for p in payloads_for(dcs_type_, year):
        fams = [weapon_info(c)[1] for c in p["pylons"].values()]
        a2a = sum(1 for f in fams if _is_a2a(f))
        ground = sum(1 for f in fams if f not in NOT_WEAPONS and not _is_a2a(f))
        arm = sum(1 for f in fams if f in ARM)
        if kind == "air":
            ok, score = a2a > 0 and ground == 0, a2a
        elif kind == "sead":
            ok, score = arm > 0, arm * 2 + a2a * 0.1
        else:
            ok, score = ground > 0 and arm == 0, ground + a2a * 0.2
        if ok:
            score += min(p.get("count", 1), 50) / 100           # widely shipped = proven
            if score > best_score:
                best, best_score = p, score
    return best


def weapon_bars(dcs_type_: str) -> list[dict]:
    """Weapon families a DCS type carries in its shipped payloads, with their first year."""
    fams: dict[str, int] = {}
    for p in shipped(dcs_type_):
        for c in p["pylons"].values():
            info = weapon_info(c)
            if info and info[1] not in ("fuel", "equipment"):
                fams[info[1]] = min(info[0], fams.get(info[1], 9999))
    return [{"family": f, "year": y} for f, y in sorted(fams.items(), key=lambda kv: (kv[1], kv[0]))]


# ---------------------------------------------------------------------------- ground
@lru_cache(maxsize=1)
def _ground() -> dict:
    return _load("ground.yaml")


def _in_year(spec: dict, year: int, side: str | None, nations_on, installed) -> bool:
    a, b = spec["years"]
    if not a <= year <= b or (side and spec["side"] != side):
        return False
    if nations_on is not None and spec.get("nations") and not set(spec["nations"]) & set(nations_on):
        return False
    req = spec.get("requires")
    if req and (installed is None or req not in installed.tech):
        return False
    return True


def air_defence_in(year: int, side: str | None = None, nations_on=None, installed=None) -> dict[str, dict]:
    return {k: v for k, v in _ground()["air_defence"].items() if _in_year(v, year, side, nations_on, installed)}


def ground_in(year: int, side: str | None = None, nations_on=None, installed=None) -> dict[str, dict]:
    return {k: v for k, v in _ground()["ground"].items() if _in_year(v, year, side, nations_on, installed)}


# ---------------------------------------------------------------------------- slider
def slider(installed=None, theatre: str | None = None) -> list[dict]:
    """One bar per airframe (service span), with weapon sub-bars, ownership and stand-in flags."""
    bars = []
    theatre_nations = None
    if theatre:
        theatre_nations = {n for n, s in nations().items() if theatre in s["theatres"]}
    for af in airframes().values():
        service = {n: y for n, y in af.service.items() if theatre_nations is None or n in theatre_nations}
        if not service:
            continue
        t = dcs_type(af, installed)
        owned = player_types(af, installed)
        weapons = weapon_bars(owned[0]) if owned else (weapon_bars(t) if t else [])
        start, end = min(a for a, _ in service.values()), max(b for _, b in service.values())
        bars.append({"id": af.id, "name": af.name, "side": af.side, "role": af.role,
                     "start": start, "end": end, "nations": sorted(service),
                     "dcs_type": t, "owned": owned, "stand_in": af.stand_in, "available": t is not None,
                     "weapons": [w for w in weapons if w["year"] <= end]})
    bars.sort(key=lambda b: (SIDES.index(b["side"]), b["start"], b["name"]))
    return bars
