"""Harvest real pylon CLSIDs for an aircraft type from existing .miz files.

pydcs does not know every rack combination the Heatblur F-4E supports
(e.g. MER/TER bomb racks). Missions shipped with the module do, so we scan
them and collect every (pylon, CLSID) pair used by the given unit type.

Usage:
    python tools/harvest_payloads.py [--type F-4E-45MC] [--out cwdc/data/f4e_clsids.json] DIR [DIR ...]
    python -m tools.harvest_payloads --owned     # all installed modules -> cwdc/data/payloads/
"""
from __future__ import annotations

import argparse
import json
import zipfile
from collections import defaultdict
from pathlib import Path

from dcs import lua


def iter_units(mission: dict):
    for coal in mission.get("coalition", {}).values():
        for country in coal.get("country", {}).values():
            for cat in ("plane", "helicopter"):
                for group in country.get(cat, {}).get("group", {}).values():
                    for unit in group.get("units", {}).values():
                        yield unit


def harvest(dirs: list[Path], unit_type: str) -> dict:
    pylons: dict[int, dict[str, dict]] = defaultdict(dict)
    payloads: set[tuple] = set()
    files = 0
    for d in dirs:
        for miz in d.rglob("*.miz"):
            try:
                with zipfile.ZipFile(miz) as z:
                    text = z.read("mission").decode("utf-8", errors="replace")
                mission = lua.loads(text)["mission"]
            except Exception as exc:  # malformed / encrypted missions
                print(f"skip {miz.name}: {exc}")
                continue
            files += 1
            for unit in iter_units(mission):
                if unit.get("type") != unit_type:
                    continue
                payloads.add(tuple(sorted(
                    (int(n), p["CLSID"]) for n, p in unit.get("payload", {}).get("pylons", {}).items()
                    if p.get("CLSID") and p["CLSID"] != "<CLEAN>")))
                for num, py in unit.get("payload", {}).get("pylons", {}).items():
                    clsid = py.get("CLSID")
                    if clsid:
                        entry = pylons[int(num)].setdefault(clsid, {"count": 0, "seen_in": []})
                        entry["count"] += 1
                        if miz.name not in entry["seen_in"] and len(entry["seen_in"]) < 3:
                            entry["seen_in"].append(miz.name)
    return {"unit_type": unit_type, "missions_scanned": files,
            "pylons": {str(k): v for k, v in sorted(pylons.items())},
            # every distinct full payload seen: used to check store *combinations*
            "payloads": [list(map(list, p)) for p in sorted(payloads)]}


def harvest_owned() -> None:
    r"""Refresh cwdc/data/payloads/*.json for every era airframe, from:
      * every installed module's (and mod's) shipped missions and UnitPayloads,
      * DCS World\CoreMods\aircraft\*\UnitPayloads (module authors' payloads, installed for all),
      * DCS World\MissionEditor\data\scripts\UnitPayloads (AI types).
    The user's own Saved Games\DCS\MissionEditor\UnitPayloads is never read. Read-only."""
    from cwdc import era
    from cwdc.era import modules, payloads
    inst = modules.detect()
    wanted = era.all_dcs_types()
    merged: dict[str, dict] = {}
    sources: dict[str, list[str]] = {}

    def merge(got: dict, src: str) -> None:
        for t, rec in got.items():
            m = merged.setdefault(t, {"missions": 0, "payloads": payloads.Counter(), "named": {}})
            m["missions"] += rec["missions"]
            m["payloads"].update(rec["payloads"])
            for k, v in rec["named"].items():
                m["named"].setdefault(k, v)
            sources.setdefault(t, []).append(src)

    for folder, path in sorted(inst.module_dirs.items()):
        types = {t for t, f in inst.flyable.items() if f == folder} & wanted
        if types:
            merge(payloads.harvest_dirs([path], types), f"{'mod' if folder in inst.mods else 'module'} {folder}")
    root = modules.install_dir()
    merge(payloads.harvest_dirs([root / "CoreMods" / "aircraft"], wanted), "CoreMods UnitPayloads")
    merge(payloads.harvest_dirs([root / "MissionEditor" / "data" / "scripts"], wanted),
          "MissionEditor UnitPayloads")
    for t in sorted(merged):
        rec = merged[t]
        if not rec["payloads"]:
            continue
        for f in payloads.write({t: rec}, ", ".join(sources[t])):
            print(f"{f.name}: {len(rec['payloads'])} payloads ({', '.join(sources[t])})")


def main() -> None:
    import sys
    if "--owned" in sys.argv:
        harvest_owned()
        return
    ap = argparse.ArgumentParser()
    ap.add_argument("dirs", nargs="+", type=Path)
    ap.add_argument("--type", default="F-4E-45MC")
    ap.add_argument("--out", type=Path)
    args = ap.parse_args()
    result = harvest(args.dirs, args.type)
    text = json.dumps(result, indent=2)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text, encoding="utf-8")
        print(f"wrote {args.out} ({result['missions_scanned']} missions)")
    else:
        print(text)


if __name__ == "__main__":
    main()
