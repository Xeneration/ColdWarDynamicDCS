"""Campaign actions shared by the CLI and the web UI (Phase 3).

Each action loads nothing and prints nothing: it takes a Campaign, changes and saves it,
and returns what happened. ActionError carries a message meant for the player.
"""
from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from pathlib import Path

from cwdc import dcs_paths, store
from cwdc.model import Campaign, MissionRecord


class ActionError(Exception):
    pass


@dataclass
class Flown:
    record: MissionRecord
    notes: list[str] = field(default_factory=list)


def check_not_over(c: Campaign) -> None:
    if c.outcome is not None:
        raise ActionError(f"The war is over: {c.outcome.title}.")


def load(campaign_id: str | None = None) -> tuple[Campaign, list[str]]:
    """Load a campaign and pick up theater data added since it was created."""
    from cwdc.engine.factory import sync_theater
    try:
        c = store.load(campaign_id)
    except FileNotFoundError as e:
        raise ActionError(str(e)) from None
    added = sync_theater(c)
    if added:
        store.save(c)
    return c, added


def list_campaigns() -> list[dict]:
    root = store.campaigns_root()
    out = []
    if not root.is_dir():
        return out
    for d in sorted(root.iterdir()):
        f = d / "campaign.json"
        if not f.is_file():
            continue
        try:
            c = Campaign.model_validate_json(f.read_text(encoding="utf-8"))
        except Exception:                      # a broken or foreign file: skip, don't crash the list
            continue
        out.append({"id": c.id, "name": c.name, "turn": c.turn, "time": c.time.isoformat(),
                    "outcome": c.outcome.title if c.outcome else None,
                    "modified": f.stat().st_mtime})
    return out


def current_id() -> str | None:
    marker = store.campaigns_root() / "current.txt"
    return marker.read_text(encoding="utf-8").strip() if marker.is_file() else None


def create(name: str, seed: int = 1976, pilot: str = "Player", force: bool = False) -> Campaign:
    from cwdc.engine.factory import new_campaign, slug
    if not name.strip():
        raise ActionError("the campaign needs a name")
    if (store.campaign_dir(slug(name)) / "campaign.json").exists() and not force:
        raise ActionError(f"campaign '{slug(name)}' exists - choose overwrite to replace it")
    c = new_campaign(name, seed=seed, player_name=pilot or "Player")
    old = store.campaign_dir(c.id)
    if old.exists():                           # overwrite: drop the old campaign's snapshots too
        shutil.rmtree(old)
    store.save(c, snapshot=True)
    return c


def fly(c: Campaign, flight_id: str, start: str = "runway", bot: bool = False) -> Flown:
    from cwdc.engine import planner
    from cwdc.missiongen import builder
    check_not_over(c)
    fid = flight_id.upper()
    if fid not in c.flights:
        raise ActionError(f"no flight {fid} this turn")
    if start not in builder.TAXI_TIME:
        raise ActionError(f"unknown start {start!r} (runway, ramp or air)")
    notes = []
    if c.pending_mission and c.pending_mission.player_flight_id != fid:
        notes.append(f"replacing pending mission {c.pending_mission.mission_id}")
    if bot:
        for other in c.flights:                # the player sits this one out
            planner.release_player(c, other)
    else:
        try:
            planner.assign_player(c, fid)
        except ValueError as e:
            raise ActionError(str(e)) from None
    if not dcs_paths.hook_is_current():
        notes.append(f"hook installed/updated: {dcs_paths.install_hook()}")
    previous = c.pending_mission
    rec = builder.build(c, fid, dcs_paths.missions_dir(), start=start, bot=bot)
    if previous and previous.miz_path != rec.miz_path:
        Path(previous.miz_path).unlink(missing_ok=True)     # superseded, never flown
    c.pending_mission = rec
    store.save(c)
    return Flown(rec, notes)


def result_path(c: Campaign) -> Path | None:
    rec = c.pending_mission
    return dcs_paths.results_dir() / f"{rec.mission_id}.json" if rec else None


def result_status(c: Campaign) -> dict:
    """Has DCS written a usable result for the pending mission yet?"""
    from cwdc.ingest import results
    rec = c.pending_mission
    if rec is None:
        return {"pending": False}
    path = result_path(c)
    out = {"pending": True, "mission_id": rec.mission_id, "file": str(path), "exists": path.is_file(),
           "fresh": False, "final": False, "events": 0}
    if out["exists"]:
        try:
            data = results.load_result_file(path)
        except Exception:
            return out                          # being written right now
        out["fresh"] = data.get("run_started", 0) >= rec.generated_at - 60
        out["final"] = bool(data.get("final") or data.get("ended"))
        out["events"] = len(data.get("events", []))
    return out


def load_result(c: Campaign, from_log: bool = False) -> tuple[dict, list[str]]:
    from cwdc.ingest import results
    rec = c.pending_mission
    path = result_path(c)
    notes, data = [], None
    if not from_log and path.is_file():
        data = results.load_result_file(path)
        if data.get("run_started", 0) < rec.generated_at - 60:
            notes.append(f"ignoring {path.name}: it predates the generated mission")
            data = None
    if data is None:
        log = dcs_paths.saved_games_dcs() / "Logs" / "dcs.log"
        if log.is_file():
            data = results.from_dcs_log(log, rec.mission_id)
            if data:
                notes.append(f"result rebuilt from {log}")
    if data is None:
        raise ActionError(f"no results for {rec.mission_id} yet. Fly the mission, or skip the "
                          "turn to let it play out without you.")
    if not data.get("final") and not data.get("ended"):
        notes.append("WARNING: the result is not final (DCS still running or crashed?) - "
                     "using what was captured")
    return data, notes


def ingest(c: Campaign, from_log: bool = False) -> tuple[list[str], list[str]]:
    """Apply the flown mission's results and close the turn. Returns (debrief, notes)."""
    from cwdc.engine import turn
    from cwdc.ingest.flown import results_from_dcs
    if not c.pending_mission:
        raise ActionError("no pending mission - generate one first")
    data, notes = load_result(c, from_log)
    dcs_results, destroyed = results_from_dcs(c, c.pending_mission, data)
    lines = turn.end_turn(c, dcs_results, destroyed)
    store.save(c, snapshot=True)
    return lines, notes


def skip(c: Campaign) -> tuple[list[str], list[str]]:
    """Sit the turn out: everything is simulated. Returns (debrief, notes)."""
    from cwdc.engine import planner, turn
    check_not_over(c)
    notes = []
    if c.pending_mission:
        planner.release_player(c, c.pending_mission.player_flight_id)
        notes.append(f"discarding pending mission {c.pending_mission.mission_id}")
    lines = turn.end_turn(c)
    store.save(c, snapshot=True)
    return lines, notes


def rollback(campaign_id: str, turn: int) -> Campaign:
    try:
        return store.rollback(campaign_id, turn)
    except FileNotFoundError:
        raise ActionError(f"no snapshot for turn {turn}") from None


def snapshot_turns(campaign_id: str) -> list[int]:
    d = store.campaign_dir(campaign_id) / "turns"
    return sorted(int(p.stem) for p in d.glob("*.json") if p.stem.isdigit()) if d.is_dir() else []
