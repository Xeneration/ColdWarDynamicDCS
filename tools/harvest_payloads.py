"""Harvest real pylon CLSIDs for an aircraft type from existing .miz files.

pydcs does not know every rack combination the Heatblur F-4E supports
(e.g. MER/TER bomb racks). Missions shipped with the module do, so we scan
them and collect every (pylon, CLSID) pair used by the given unit type.

Usage:
    python tools/harvest_payloads.py [--type F-4E-45MC] [--out cwdc/data/f4e_clsids.json] DIR [DIR ...]
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


def main() -> None:
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
