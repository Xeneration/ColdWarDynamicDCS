"""Unattended soak runs: DCS server instances fly AI-only campaign missions back to back.

`cwdc soak` (parent) starts one `cwdc soak-worker` subprocess per instance, each with its own
CWDC_DCS_WRITEDIR (the soak profile) and CWDC_CAMPAIGNS_DIR (scratch campaigns), and plays
fully simulated wars itself while they run. Your own campaigns and Saved Games\\DCS are never used.
"""
from __future__ import annotations

import json
import os
import random
import shutil
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path

from cwdc.soak import profile as prof

LOAD_TIMEOUT = 300          # s for DCS to reach the lobby, or to start a mission
STALE_HEARTBEAT = 600       # s without a heartbeat: DCS is hung
ABORT_GRACE = 600           # s of model time past the cap before the runner aborts a mission
POLL = 10.0
MISSION_TIMEOUT_EXTRA = 20 * 60   # s of real time allowed beyond the mission cap (loading, slow sim)
FROZEN_S = 300              # s of real time with no model-time progress: the simulation is stuck
ABORT_WAIT = 180            # s for an aborted mission to reach the lobby before DCS is restarted


def runs_root() -> Path:
    return prof.saved_games() / "CWDC-soak"


def dcs_exe() -> Path:
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Eagle Dynamics\DCS World") as k:
            return Path(winreg.QueryValueEx(k, "Path")[0]) / "bin" / "DCS.exe"
    except OSError:
        return Path(r"C:\Program Files\Eagle Dynamics\DCS World\bin\DCS.exe")


class InstanceError(RuntimeError):
    pass


class Instance:
    """One DCS server process and its soak profile."""

    def __init__(self, n: int, launcher=subprocess.Popen, exe: Path | None = None,
                 root: Path | None = None, poll: float = POLL, clock=time.time, sleep=time.sleep):
        self.n, self.launcher, self.exe = n, launcher, exe or dcs_exe()
        self.profile = prof.prepare(n, root)
        self.control = prof.Control(self.profile)
        self.poll, self.clock, self.sleep = poll, clock, sleep
        self.proc = None
        self.restarts = 0

    @property
    def log(self) -> Path:
        return self.profile / "Logs" / "dcs.log"

    def alive(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def start(self) -> None:
        sd = prof.soak_dir(self.profile)
        for f in ("command.json", "heartbeat.json"):       # never replay a stale command
            (sd / f).unlink(missing_ok=True)
        self.proc = self.launcher([str(self.exe), "--server", "--norender", "-w",
                                   prof.profile_name(self.n)])
        t0 = self.clock()
        while self.clock() - t0 < LOAD_TIMEOUT:
            hb = self.control.heartbeat()
            if hb and hb.get("state") == "lobby":
                return
            if not self.alive():
                raise InstanceError(f"DCS exited during start-up (instance {self.n})")
            self.sleep(self.poll)
        raise InstanceError(f"DCS did not reach the lobby within {LOAD_TIMEOUT} s")

    def kill(self) -> None:
        if self.alive():
            self.proc.kill()
            try:
                self.proc.wait(60)
            except Exception:
                pass
        self.proc = None

    def restart(self) -> None:
        self.kill()
        self.restarts += 1
        self.start()

    def stop(self) -> None:
        if self.alive():
            self.control.send(exit=True)
            t0 = self.clock()
            while self.alive() and self.clock() - t0 < 90:
                self.sleep(self.poll)
        self.kill()

    def fly(self, miz: str, result_final, cap_s: int, stop=lambda: False,
            timeout_s: int | None = None) -> str:
        """Load `miz` and wait for it to finish. result_final() -> bool says whether the results
        hook has written the final result.

        Limits: model time past cap_s + ABORT_GRACE, real time past timeout_s, or model time
        frozen for FROZEN_S -> the mission is aborted to the lobby; if it still hasn't finished
        ABORT_WAIT later, DCS is considered stuck (the worker restarts it).
        Returns ok | aborted | timeout | frozen | load_failed | stalled | crashed."""
        timeout_s = timeout_s or cap_s + MISSION_TIMEOUT_EXTRA
        seq = self.control.send(load=miz)
        t0 = self.clock()
        started, why, abort_at = False, None, None
        last_mt, last_mt_at = -1.0, t0
        while True:
            self.sleep(self.poll)
            if not self.alive():
                return "crashed"
            hb = self.control.heartbeat() or {}
            now = self.clock()
            mt = hb.get("model_time", 0) or 0
            if hb.get("seq_done", -1) >= seq and hb.get("state") == "running":
                if not started:
                    started, last_mt, last_mt_at = True, mt, now
            if started and result_final() and hb.get("state") in ("lobby", "ended", "stopped"):
                return why or "ok"
            if now - hb.get("written_at", now) > STALE_HEARTBEAT:
                return "stalled"
            if not started:
                if now - t0 > LOAD_TIMEOUT:
                    return "load_failed"
                continue
            if mt > last_mt + 1:
                last_mt, last_mt_at = mt, now
            if why is None:
                if stop():
                    why = "aborted"
                elif mt > cap_s + ABORT_GRACE:
                    why = "aborted"
                elif now - t0 > timeout_s:
                    why = "timeout"
                elif hb.get("state") == "running" and now - last_mt_at > FROZEN_S:
                    why = "frozen"
                if why:
                    self.control.send(abort=True)
                    abort_at = now
            elif now - abort_at > ABORT_WAIT:
                return "stalled" if why == "aborted" else why


# ---------------------------------------------------------------------------- worker

def choose_flight(c, counts: Counter, r: random.Random) -> str | None:
    """A player-squadron flight, least-sampled task first."""
    cands = [f for f in c.flights.values() if f.player_eligible]
    if not cands:
        return None
    low = min(counts[f.task.value] for f in cands)
    pool = [f for f in cands if counts[f.task.value] == low]
    return r.choice(sorted(pool, key=lambda f: f.id)).id


def _append(path: Path, row: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, default=str) + "\n")


def stop_file(run_dir: Path) -> Path:
    return run_dir / "STOP"


def run_worker(n: int, run_dir: Path, until: float, cap_s: int, seed: int,
               instance: Instance | None = None, clock=time.time,
               max_missions: int | None = None, timeout_s: int | None = None,
               crisis: bool = True) -> None:
    import traceback
    from cwdc import actions
    from cwdc.engine import outcome
    from cwdc.soak import record
    out = run_dir / f"i{n}"
    rows = out / "missions.jsonl"
    keep = out / "anomalies"
    inst = instance or Instance(n)
    inst.start()
    r = random.Random(seed)
    counts: Counter = Counter()
    log_off = inst.log.stat().st_size if inst.log.is_file() else 0
    flown_n = 0
    stopping = lambda: stop_file(run_dir).exists()

    def time_left() -> bool:
        if stopping() or (max_missions is not None and flown_n >= max_missions):
            return False
        return clock() + cap_s + 2 * LOAD_TIMEOUT < until

    def say(msg: str) -> None:
        print(f"{time.strftime('%H:%M:%S')} i{n} {msg}", flush=True)

    try:
        while time_left():
            c = actions.create(f"Soak {n}-{seed}", seed=seed, force=True, crisis=crisis)
            say(f"campaign seed {seed}")
            errors_in_row = 0
            while c.outcome is None and time_left() and errors_in_row < 3:
                fid = choose_flight(c, counts, r)
                if fid is None:
                    actions.skip(c)
                    continue
                f = c.flights[fid]
                row = {"kind": "mission", "instance": n, "seed": seed, "turn": c.turn,
                       "time": c.time.isoformat(), "flight": fid, "task": f.task.value,
                       "target": f.target_id, "aircraft": len(f.airframe_ids), "status": "error"}
                rec, status = None, "error"
                try:
                    rec = actions.fly(c, fid, start="air", bot=True, time_limit=cap_s).record
                    row.update(mission_id=rec.mission_id, spawned=len(rec.spawned_flights),
                               package=[c.flights[x].task.value for x in rec.spawned_flights
                                        if c.flights[x].package_id == f.package_id and x != fid],
                               predicted=record.predict(c, rec))
                    t0 = clock()
                    status = inst.fly(rec.miz_path, lambda: actions.result_status(c).get("final"),
                                      cap_s, stop=stopping, timeout_s=timeout_s)
                    row.update(status=status, wall_s=round(clock() - t0))
                    try:
                        data, _ = actions.load_result(c)
                        row["observed"] = record.observe(c, rec, data, cap_s)
                        row["result_source"] = data.get("source", "hook")
                        actions.ingest(c)
                    except actions.ActionError as e:
                        row["observed"], row["result_source"] = None, f"none: {e}"
                        actions.skip(c)
                    errors_in_row = 0
                except Exception:                       # never let one bad turn end the night
                    row["error"] = traceback.format_exc()[-2000:]
                    errors_in_row += 1
                    say("ERROR " + row["error"].strip().splitlines()[-1])
                    try:
                        if c.outcome is None:
                            actions.skip(c)
                    except Exception:
                        errors_in_row = 3
                errs, log_off = record.log_errors(inst.log, log_off)
                row["log_errors"] = errs
                counts[f.task.value] += 1
                obs = row.get("observed") or {}
                say(f"T{row['turn']} {f.task.value} {fid}: {status}, {row.get('wall_s', 0) // 60} min, "
                    f"flags {obs.get('flags', [])}")
                if rec and (status != "ok" or errs or obs.get("flags")):
                    keep.mkdir(parents=True, exist_ok=True)
                    if Path(rec.miz_path).is_file():
                        shutil.copy2(rec.miz_path, keep / Path(rec.miz_path).name)
                    res = inst.profile / "CWDC" / "results" / f"{rec.mission_id}.json"
                    if res.is_file():
                        shutil.copy2(res, keep / res.name)
                if rec:
                    Path(rec.miz_path).unlink(missing_ok=True)
                _append(rows, row)
                flown_n += 1
                if status in ("crashed", "stalled", "load_failed", "timeout", "frozen") \
                        or not inst.alive():
                    for attempt in range(3):
                        try:
                            inst.restart()
                            break
                        except InstanceError as e:
                            say(f"restart failed: {e}")
                    else:
                        raise InstanceError("DCS cannot be restarted - worker stops")
                    log_off = 0
            _append(rows, {"kind": "campaign", "instance": n, "seed": seed, "turns": c.turn - 1,
                           "outcome": c.outcome.title if c.outcome else "unfinished",
                           "day": outcome.war_day(c)})
            say(f"campaign seed {seed}: {c.outcome.title if c.outcome else 'unfinished'}")
            seed += 1
    finally:
        inst.stop()


# ---------------------------------------------------------------------------- parent

def sim_war(seed: int) -> dict:
    from cwdc.engine import outcome, turn
    from cwdc.engine.factory import new_campaign
    from cwdc.model import AirframeStatus, Side
    c = new_campaign(f"Sim {seed}", seed=seed)
    limit = 30 * outcome.turns_per_day(c)
    for _ in range(limit):
        turn.end_turn(c)
        if c.outcome:
            break
    lost = Counter(c.squadrons[a.squadron_id].side.value for a in c.airframes.values()
                   if a.status is AirframeStatus.DESTROYED)
    return {"kind": "sim_war", "seed": seed, "outcome": c.outcome.title if c.outcome else "unfinished",
            "day": outcome.war_day(c), "main_effort": c.main_effort,
            "air": {s.value: round(outcome.air_strength(c, s), 3) for s in Side},
            "lost": dict(lost), "front": {s.id: round(s.offset_km, 1) for s in c.sectors.values()}}


def run(hours: float, instances: int, cap_min: int, seed: int, out: Path | None = None,
        sim_wars: int = 200, timeout_min: int | None = None) -> Path:
    run_dir = (out or runs_root()) / time.strftime("%Y%m%d-%H%M")
    run_dir.mkdir(parents=True, exist_ok=True)
    until = time.time() + hours * 3600
    (run_dir / "run.json").write_text(json.dumps(
        {"started": time.time(), "until": until, "instances": instances, "cap_min": cap_min,
         "seed": seed}), encoding="utf-8")
    procs = []
    for n in range(1, instances + 1):
        env = dict(os.environ, CWDC_DCS_WRITEDIR=str(prof.profile_dir(n)),
                   CWDC_CAMPAIGNS_DIR=str(run_dir / f"i{n}" / "campaigns"))
        cmd = [sys.executable, "-u", "-m", "cwdc", "soak-worker", "--instance", str(n), "--run-dir",
               str(run_dir), "--until", str(until), "--cap-min", str(cap_min),
               "--seed", str(seed + 1000 * n)] + (["--timeout-min", str(timeout_min)] if timeout_min else [])
        logf = (run_dir / f"worker{n}.log").open("w", encoding="utf-8")
        procs.append(subprocess.Popen(cmd, env=env, stdout=logf, stderr=subprocess.STDOUT))
        print(f"instance {n} started (worker pid {procs[-1].pid})")
        time.sleep(60 if n < instances else 0)              # stagger the terrain loads
    s = seed
    while (time.time() < until and s - seed < sim_wars and not stop_file(run_dir).exists()
           and any(p.poll() is None for p in procs)):
        _append(run_dir / "sim_wars.jsonl", sim_war(s))
        s += 1
    for p in procs:
        p.wait()
    from cwdc.soak import report
    path = report.write(run_dir)
    print(f"report: {path}")
    return run_dir
