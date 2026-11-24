"""Resolve named loadouts to pylon CLSIDs, per airframe type.

Each pylon accepts a specific set of stores. For a type, the valid
(pylon, weapon name) -> CLSID pairs come from:
  * the pydcs plane class pylon definitions, and
  * for the F-4E, CLSIDs harvested from Heatblur-shipped missions (f4e_clsids.json),
    which include the MER/TER rack combinations pydcs does not list.

Named loadouts live in the file named by the type's `loadout_file` in airframes.yaml.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

import yaml
from dcs import planes, weapons_data

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
F4E = "F-4E-45MC"
HARVESTED = {F4E: "f4e_clsids.json"}


class LoadoutError(ValueError):
    pass


@lru_cache(maxsize=1)
def _clsid_names() -> dict[str, str]:
    return {
        v["clsid"]: v["name"]
        for v in vars(weapons_data.Weapons).values()
        if isinstance(v, dict) and "clsid" in v and "name" in v
    }


@lru_cache(maxsize=None)
def pylon_index(aircraft: str = F4E) -> dict[int, dict[str, str]]:
    """{pylon: {weapon name: CLSID}} of stores known to fit each pylon of `aircraft`."""
    plane = planes.plane_map[aircraft]
    index: dict[int, dict[str, str]] = {}
    for num in plane.pylons:
        cls = getattr(plane, f"Pylon{num}", None)
        if cls is None:
            continue
        for attr in dir(cls):
            val = getattr(cls, attr)
            if isinstance(val, tuple) and len(val) == 2 and isinstance(val[1], dict):
                index.setdefault(num, {})[val[1]["name"]] = val[1]["clsid"]
    if aircraft in HARVESTED:
        harvested = json.loads((DATA_DIR / HARVESTED[aircraft]).read_text(encoding="utf-8"))
        names = _clsid_names()
        for num, clsids in harvested["pylons"].items():
            for clsid in clsids:
                name = names.get(clsid)
                if name:
                    index.setdefault(int(num), {}).setdefault(name, clsid)
    return index


@lru_cache(maxsize=None)
def loadouts(aircraft: str = F4E) -> dict[str, dict]:
    from cwdc.theater import airframe_catalogue
    spec = airframe_catalogue().get(aircraft, {})
    if "loadout_file" not in spec:
        return {}
    data = yaml.safe_load((DATA_DIR / spec["loadout_file"]).read_text(encoding="utf-8"))
    return data["loadouts"]


def resolve(name: str, aircraft: str = F4E) -> dict[int, str]:
    """Return {pylon: CLSID} for a named loadout, validating every store."""
    available = loadouts(aircraft)
    try:
        spec = available[name]
    except KeyError:
        raise LoadoutError(f"{aircraft}: unknown loadout {name!r}; have {sorted(available)}") from None
    index = pylon_index(aircraft)
    out: dict[int, str] = {}
    for pylon, weapon in spec["pylons"].items():
        pylon = int(pylon)
        clsid = index.get(pylon, {}).get(weapon)
        if clsid is None:
            raise LoadoutError(f"{aircraft} {name}: {weapon!r} is not a known store for pylon {pylon}")
        out[pylon] = clsid
    return out


def _family(clsid: str) -> str:
    """Store family: missile variants are interchangeable for fit checks (AIM-7E/E-2/F...)."""
    import re
    name = _clsid_names().get(clsid, clsid)
    name = re.sub(r"AIM-7\w*(-\d)?", "AIM-7", name)
    name = re.sub(r"AIM-9\w*", "AIM-9", name)
    return name


@lru_cache(maxsize=None)
def _seen_pairs(aircraft: str) -> frozenset:
    if aircraft not in HARVESTED:
        return frozenset()
    data = json.loads((DATA_DIR / HARVESTED[aircraft]).read_text(encoding="utf-8"))
    pairs = set()
    for payload in data.get("payloads", []):
        items = sorted((int(p), _family(c)) for p, c in payload)
        for i, a in enumerate(items):
            for b in items[i + 1:]:
                pairs.add((a, b))
    return frozenset(pairs)


def unseen_pairs(name: str, aircraft: str = F4E) -> list[tuple]:
    """Store pairs in a loadout that no shipped mission ever combines.

    DCS silently drops stores from invalid combinations (found in live testing:
    SEAD Shrikes + centreline tank came out as 2 Shrikes and 3 Sparrows), so a
    loadout should only combine stores that real module-author payloads combine.
    """
    seen = _seen_pairs(aircraft)
    if not seen:
        return []
    items = sorted((p, _family(c)) for p, c in resolve(name, aircraft).items())
    return [(a, b) for i, a in enumerate(items) for b in items[i + 1:] if (a, b) not in seen]


def apply(group, name: str, aircraft: str = F4E) -> None:
    """Load a named loadout onto every unit of a pydcs flying group."""
    pylons = resolve(name, aircraft)
    for unit in group.units:
        unit.pylons = {p: {"CLSID": c} for p, c in pylons.items()}


# ---------------------------------------------------------------------------- per-flight payload
AIR_TASK_NAMES = {"CAP", "Escort", "QRA", "Intercept", "Sweep", "Recon"}
PICK_ORDER = {"air": ("air", "clean"), "sead": ("sead", "ground", "clean"), "ground": ("ground", "clean"),
              "antiship": ("antiship", "ground", "clean")}


def _store_text(clsid: str) -> str:
    name = _clsid_names().get(clsid)
    if not name:                               # mod stores (A-4E-C) have no pydcs name
        return clsid.strip("{}").replace("_", " ")
    return name.split(" - ")[0].strip()


def describe(pylons: dict[int, str]) -> str:
    """'6x Mk-82, 2x AIM-9B, 2x Fuel Tank 120 gallons' (most numerous first)."""
    from collections import Counter
    counts = Counter(_store_text(c) for c in pylons.values())
    return ", ".join(f"{n}x {s}" if n > 1 else s for s, n in counts.most_common()) or "clean (guns only)"


def flight_payload(c, f) -> dict:
    """The stores a flight carries: {"pylons": {pylon: CLSID} or None, "text", "source"}.

    * a named loadout (airframes.yaml / loadouts_*.yaml) - unless a campaign-maker war is set
      before its newest store existed;
    * campaign-maker wars: the most-shipped payload of the year that suits the task
      (era.pick_payload), else guns and tanks, else clean - never a DCS default that may be
      decades too new;
    * otherwise None: the builder lets DCS load its task default.
    """
    from cwdc import era
    type_id = c.squadrons[f.squadron_id].aircraft_type
    year = c.time.year if c.era else None
    if f.loadout and f.loadout != "default" and loadouts(type_id):
        pylons = resolve(f.loadout, type_id)
        named_year = era.payload_year(pylons)
        if year is None or (named_year is not None and named_year <= year):
            spec = loadouts(type_id)[f.loadout]
            return {"pylons": pylons, "text": spec.get("description") or describe(pylons), "source": "named"}
    if year is None:
        return {"pylons": None, "text": "standard load for the task", "source": "default"}
    task = f.task.value
    kind = "air" if task in AIR_TASK_NAMES else "sead" if task == "SEAD" else \
        "antiship" if task == "Anti-ship" else "ground"
    for k in PICK_ORDER[kind]:                 # a strike flight never swaps its bombs for missiles
        p = era.pick_payload(type_id, year, k)
        if p is not None:
            return {"pylons": dict(p["pylons"]), "text": describe(p["pylons"]), "source": "era"}
    return {"pylons": {}, "text": "clean (guns only)", "source": "era"}
