"""Shipped payloads: the loadouts module authors actually built, harvested per DCS unit type.

DCS silently strips stores from combinations nobody has shipped (found in live F-4E testing),
so campaign loadouts are drawn only from payloads that exist in a module's own files:

  * every .miz shipped in the module's folder (DCS World\\Mods\\aircraft\\<module>\\**\\*.miz,
    and Saved Games\\DCS\\Mods\\aircraft\\<mod>\\** for mods), and
  * the module's UnitPayloads\\*.lua (named payloads; the A-4E-C ships these).

The harvest is written to cwdc/data/payloads/<type>.json so the campaign never needs the DCS
install at run time; `tools/harvest_payloads.py --owned` refreshes it.
"""
from __future__ import annotations

import json
import re
import zipfile
from collections import Counter  # noqa: F401  (re-exported for tools)
from functools import lru_cache
from pathlib import Path

PAYLOAD_DIR = Path(__file__).resolve().parent.parent / "data" / "payloads"


def _lua():
    from lupa import luajit21
    return luajit21.LuaRuntime(unpack_returned_tuples=True)


def _py(obj):
    """Lua table -> Python (dicts; integer keys kept as ints)."""
    if hasattr(obj, "items"):
        return {k: _py(v) for k, v in obj.items()}
    return obj


def _units(mission: dict):
    for coal in (mission.get("coalition") or {}).values():
        for country in (coal.get("country") or {}).values():
            for cat in ("plane", "helicopter"):
                for group in ((country.get(cat) or {}).get("group") or {}).values():
                    for unit in (group.get("units") or {}).values():
                        yield unit


def _pylons(raw: dict) -> tuple:
    return tuple(sorted((int(n), p["CLSID"]) for n, p in (raw or {}).items()
                        if isinstance(p, dict) and p.get("CLSID") and p["CLSID"] != "<CLEAN>"))


def harvest_dirs(dirs: list[Path], types: set[str] | None = None) -> dict[str, dict]:
    """{unit type: {"missions": n, "payloads": Counter[(pylon, clsid)...], "named": {}}}."""
    out: dict[str, dict] = {}
    lua = _lua()
    for d in dirs:
        for miz in sorted(Path(d).rglob("*.miz")):
            try:
                with zipfile.ZipFile(miz) as z:
                    text = z.read("mission").decode("utf-8", errors="replace")
                mission = _py(lua.execute(text + "\nreturn mission"))
            except Exception:                       # encrypted / malformed missions
                continue
            seen = set()
            for unit in _units(mission):
                t = unit.get("type")
                if not t or (types is not None and t not in types):
                    continue
                rec = out.setdefault(t, {"missions": 0, "payloads": Counter(), "named": {}})
                if t not in seen:
                    rec["missions"] += 1
                    seen.add(t)
                pl = _pylons((unit.get("payload") or {}).get("pylons"))
                if pl:
                    rec["payloads"][pl] += 1
        for lua_file in sorted(Path(d).rglob("UnitPayloads/*.lua")):
            try:
                data = _py(lua.execute(lua_file.read_text(encoding="utf-8", errors="replace")))
            except Exception:
                continue
            t = (data or {}).get("unitType") or (data or {}).get("name")
            if not t or (types is not None and t not in types):
                continue
            rec = out.setdefault(t, {"missions": 0, "payloads": Counter(), "named": {}})
            for p in (data.get("payloads") or {}).values():
                pl = tuple(sorted((int(v["num"]), v["CLSID"]) for v in (p.get("pylons") or {}).values()
                                  if v.get("CLSID") and v["CLSID"] != "<CLEAN>"))
                if pl:
                    rec["named"][p.get("name", f"payload {len(rec['named']) + 1}")] = pl
                    rec["payloads"][pl] += 1
    return out


def safe_name(unit_type: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", unit_type)


def write(harvest: dict[str, dict], source: str, out_dir: Path = PAYLOAD_DIR) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for t, rec in sorted(harvest.items()):
        names = {pl: n for n, pl in rec["named"].items()}
        payloads = [{"name": names.get(pl), "count": c, "pylons": {str(p): cl for p, cl in pl}}
                    for pl, c in sorted(rec["payloads"].items(), key=lambda kv: (-kv[1], kv[0]))]
        path = out_dir / f"{safe_name(t)}.json"
        path.write_text(json.dumps({"unit_type": t, "source": source, "missions_scanned": rec["missions"],
                                    "payloads": payloads}, indent=1), encoding="utf-8")
        written.append(path)
    return written


@lru_cache(maxsize=None)
def shipped(unit_type: str) -> list[dict]:
    """Shipped payloads of a unit type: [{"name", "count", "pylons": {pylon int: CLSID}}]."""
    path = PAYLOAD_DIR / f"{safe_name(unit_type)}.json"
    if not path.is_file():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    return [{**p, "pylons": {int(k): v for k, v in p["pylons"].items()}} for p in data["payloads"]]


def harvested_types() -> list[str]:
    return sorted(json.loads(p.read_text(encoding="utf-8"))["unit_type"] for p in PAYLOAD_DIR.glob("*.json"))
