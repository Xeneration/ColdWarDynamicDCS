"""Load mission results: from the hook's JSON file, or rebuilt from dcs.log.

Both sources produce the same shape:
    {
      "source": "hook" | "dcs.log",
      "mission_id": str,
      "final": bool,          # hook saw the simulation stop / log fully read
      "ended": bool,          # runtime saw S_EVENT_MISSION_END
      "start": {...} | None,  # START payload (config echo)
      "snapshot": {...} | None,  # latest tracked-group snapshot
      "end": {...} | None,
      "events": [ {...}, ... ],
    }
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Iterable

LINE_RE = re.compile(r"\[CWDC\] (\S+) ([A-Z]+) (\d+) (\d+)/(\d+) (.*)$")


class _Assembler:
    def __init__(self) -> None:
        self.runs: dict[str, dict] = {}

    def _new(self, mid: str) -> dict:
        run = {"source": "dcs.log", "mission_id": mid, "final": True, "ended": False,
               "start": None, "snapshot": None, "end": None, "events": [],
               "_partial": {}, "_seen": set()}
        self.runs[mid] = run
        return run

    def feed(self, line: str) -> None:
        m = LINE_RE.search(line.rstrip("\r\n"))
        if not m:
            return
        mid, kind, seq, part, parts, data = m.groups()
        seq, part, parts = int(seq), int(part), int(parts)
        run = self.runs.get(mid)
        if (kind == "START" and part == 1) or run is None:
            run = self._new(mid)       # last run of a mission in the log wins
        if (seq, part) in run["_seen"]:
            return
        run["_seen"].add((seq, part))
        if parts > 1:
            chunks = run["_partial"].setdefault(seq, {})
            chunks[part] = data
            if len(chunks) < parts:
                return
            data = "".join(chunks[i] for i in range(1, parts + 1))
            del run["_partial"][seq]
        payload = json.loads(data)
        if kind == "START":
            run["start"] = payload
        elif kind == "EV":
            run["events"].append(payload)
        elif kind == "SNAP":
            run["snapshot"] = payload
        elif kind == "END":
            run["ended"] = True
            run["end"] = payload

    def results(self) -> dict[str, dict]:
        out = {}
        for mid, run in self.runs.items():
            out[mid] = {k: v for k, v in run.items() if not k.startswith("_")}
        return out


def parse_log_lines(lines: Iterable[str]) -> dict[str, dict]:
    """Rebuild results for every CWDC mission found in log lines."""
    asm = _Assembler()
    for line in lines:
        asm.feed(line)
    return asm.results()


def from_dcs_log(path: Path, mission_id: str | None = None) -> dict | None:
    with path.open(encoding="utf-8", errors="replace") as f:
        runs = parse_log_lines(f)
    if mission_id:
        return runs.get(mission_id)
    return list(runs.values())[-1] if runs else None


SCENERY = 5   # DCS Object.Category.SCENERY


def is_scenery(obj: dict | None) -> bool:
    """Map objects (trees, fences, approach lights, bridges...)."""
    return bool(obj) and obj.get("category") == SCENERY


def is_collision(event: dict) -> bool:
    """DCS reports a collision as a 'weapon' named after the initiator's own type."""
    ini = event.get("initiator") or {}
    return bool(event.get("weapon")) and event.get("weapon") == ini.get("type")


def kill_weapon(events: list[dict], kill: dict) -> str | None:
    """Weapon for a KILL event; DCS often omits it, so fall back to the last HIT on that target."""
    if kill.get("weapon"):
        return kill["weapon"]
    target = (kill.get("target") or {}).get("name")
    for e in reversed(events):
        if e is kill or e["t"] > kill["t"]:
            continue
        if e["event"] == "S_EVENT_HIT" and (e.get("target") or {}).get("name") == target:
            return e.get("weapon")
    return None


def load_result_file(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    data.setdefault("events", [])
    return data
