"""One data row per soak mission: what DCS did, what the resolver predicted, and anomalies."""
from __future__ import annotations

import math
import re
from pathlib import Path

from cwdc.model import Campaign, MissionRecord, Side

PREDICT_DRAWS = 40
EARLY_LOSS_S = 120          # a focus aircraft lost this soon after start: spawn or collision problem
NEAR_TARGET_M = 20_000
ATTACK_TASKS = {"Strike", "CAS", "BAI", "SEAD", "OCA", "Interdiction"}
LOG_ERROR = re.compile(r"SCRIPTING ERROR|stack traceback|\[CWDC-log\].*(error|WARNING)|"
                       r"\[CWDC-hook\].*cannot|\[CWDC-soak\].*load .* false", re.I)


def _side_metrics(c: Campaign, results, focus_id: str) -> dict:
    out = {"blue_lost": 0, "red_lost": 0, "blue_air_kills": 0, "red_air_kills": 0,
           "blue_ground_kills": 0, "red_ground_kills": 0,
           "focus_lost": 0, "focus_air_kills": 0, "focus_ground_kills": 0}
    for r in results:
        f = c.flights.get(r.flight_id)
        if f is None:
            continue
        s = "blue" if f.side is Side.BLUE else "red"
        air = sum(1 for k in r.kills if k.air)
        ground = len(r.kills) - air
        out[f"{s}_lost"] += len(r.airframes_lost)
        out[f"{s}_air_kills"] += air
        out[f"{s}_ground_kills"] += ground
        if r.flight_id == focus_id:
            out["focus_lost"] += len(r.airframes_lost)
            out["focus_air_kills"] += air
            out["focus_ground_kills"] += ground
    return out


def predict(c: Campaign, rec: MissionRecord, draws: int = PREDICT_DRAWS) -> dict:
    """Mean resolver outcome for the flights DCS will fly. Works on copies: `c` is untouched."""
    from cwdc.engine import resolver
    dump = c.model_dump_json()
    total: dict[str, float] = {}
    for i in range(draws):
        cc = Campaign.model_validate_json(dump)
        cc.seed = c.seed * 1000 + 7 + i
        flights = [cc.flights[fid] for fid in rec.spawned_flights if fid in cc.flights]
        m = _side_metrics(cc, resolver.resolve(cc, flights), rec.player_flight_id)
        for k, v in m.items():
            total[k] = total.get(k, 0) + v
    return {k: v / draws for k, v in total.items()}


def _dist(a: dict | None, x: float, z: float) -> float | None:
    if not a or "x" not in a:
        return None
    return math.hypot(a["x"] - x, a["z"] - z)


def observe(c: Campaign, rec: MissionRecord, data: dict, time_limit: int | None = None) -> dict:
    """What happened in DCS, in the same terms as predict(), plus timings and anomaly flags."""
    from cwdc.ingest.flown import results_from_dcs
    res, _ = results_from_dcs(c, rec, data)
    m = _side_metrics(c, res, rec.player_flight_id)
    focus = c.flights[rec.player_flight_id]
    units = {n for n, e in rec.manifest.items()
             if e.get("kind") == "airframe" and e.get("flight") == focus.id}
    events = data.get("events", [])
    snap = data.get("snapshot") or {}
    end_t = max([snap.get("model_time", 0)] + [(data.get("end") or {}).get("model_time", 0)]
                + [e.get("t", 0) for e in events])
    start_t = min([e.get("t", 0) for e in events] or [0])
    shots = [e for e in events if e["event"] == "S_EVENT_SHOT" and (e.get("initiator") or {}).get("name") in units]
    hits_on = [e for e in events if e["event"] == "S_EVENT_HIT" and (e.get("target") or {}).get("name") in units]
    losses = [e for e in events if e["event"] in ("S_EVENT_CRASH", "S_EVENT_DEAD", "S_EVENT_UNIT_LOST",
                                                   "S_EVENT_EJECTION")
              and (e.get("initiator") or {}).get("name") in units]
    m.update(model_time_s=round(end_t), events=len(events), focus_shots=len(shots),
             t_first_shot=round(shots[0]["t"] - start_t) if shots else None,
             t_first_hit_on_focus=round(hits_on[0]["t"] - start_t) if hits_on else None,
             all_shots=sum(1 for e in events if e["event"] == "S_EVENT_SHOT"))

    near = None
    if focus.target_id and focus.target_id in c.objectives:
        tp = c.objectives[focus.target_id].pos
        ds = [_dist((e.get("initiator") or {}).get("pos"), tp.x, tp.z) for e in shots]
        for g in snap.get("groups", []):
            for u in g.get("units", []):
                if u.get("name") in units:
                    ds.append(_dist(u.get("pos"), tp.x, tp.z))
        ds = [d for d in ds if d is not None]
        near = round(min(ds) / 1000, 1) if ds else None
    m["closest_to_target_km"] = near

    flags = []
    if not (data.get("final") or data.get("ended")):
        flags.append("not_final")
    if time_limit and end_t >= time_limit:
        flags.append("time_limit")
    if any(e.get("t", 0) - start_t < EARLY_LOSS_S for e in losses):
        flags.append("early_loss")
    if focus.task.value in ATTACK_TASKS and not shots:
        flags.append("no_weapons_released")
    if focus.task.value in ATTACK_TASKS and near is not None and near * 1000 > NEAR_TARGET_M:
        flags.append("never_near_target")
    if m["all_shots"] == 0:
        flags.append("nothing_happened")
    m["flags"] = flags
    return m


def log_errors(log: Path, offset: int, limit: int = 40) -> tuple[list[str], int]:
    """Error lines written to dcs.log since `offset`. Returns (lines, new offset)."""
    if not log.is_file():
        return [], 0
    size = log.stat().st_size
    if size < offset:                     # DCS restarted and rewrote the log
        offset = 0
    with log.open("rb") as f:
        f.seek(offset)
        text = f.read().decode("utf-8", "replace")
    lines = [ln.strip()[:300] for ln in text.splitlines() if LOG_ERROR.search(ln)]
    return lines[:limit], size
