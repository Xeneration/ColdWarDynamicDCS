import json
import re
import threading
import time
import zipfile
from pathlib import Path

import pytest

from cwdc.soak import profile as prof, record, report, runner


class FakeDCS:
    """Stands in for DCS + the two hooks: obeys command.json, writes heartbeat and results."""

    def __init__(self, profile: Path, respond: bool = True, die_after: int | None = None,
                 mode: str = "normal"):
        self.profile, self.respond, self.die_after, self.mode = profile, respond, die_after, mode
        self.aborts = 0
        self.alive, self.loaded = True, 0
        self.ctl = prof.soak_dir(profile)
        threading.Thread(target=self._loop, daemon=True).start()

    def poll(self):
        return None if self.alive else 1

    def kill(self):
        self.alive = False

    def wait(self, timeout=None):
        return 1

    def _beat(self, state, mt=0.0, seq=-1):
        self.ctl.mkdir(parents=True, exist_ok=True)
        (self.ctl / "heartbeat.json").write_text(json.dumps(
            {"written_at": time.time(), "state": state, "model_time": mt, "seq_done": seq}))

    def _loop(self):
        seq = -1
        self._beat("lobby")
        while self.alive:
            time.sleep(0.02)
            try:
                cmd = json.loads((self.ctl / "command.json").read_text())
            except (OSError, ValueError):
                self._beat("lobby", seq=seq)
                continue
            if cmd["seq"] <= seq:
                self._beat("lobby", seq=seq)
                continue
            seq = cmd["seq"]
            if cmd.get("exit"):
                self.alive = False
                return
            if cmd.get("abort"):
                self.aborts += 1
                continue                            # a truly stuck DCS ignores the abort
            if "load" in cmd and self.respond and self.mode != "normal":
                mt = 30.0
                while self.alive:                   # running forever: frozen or just endless
                    time.sleep(0.02)
                    if self.mode == "endless":
                        mt += 5
                    self._beat("running", mt, seq)
                    try:
                        if json.loads((self.ctl / "command.json").read_text()).get("abort"):
                            self.aborts = 1
                    except (OSError, ValueError):
                        pass
                return
            if "load" in cmd and self.respond:
                self.loaded += 1
                if self.die_after is not None and self.loaded > self.die_after:
                    self.alive = False
                    return
                with zipfile.ZipFile(cmd["load"]) as z:
                    mid = re.search(r'mission_id = \\?"([\w\-]+)', z.read("mission").decode()).group(1)
                self._beat("running", 30, seq)
                time.sleep(0.05)
                res = self.profile / "CWDC" / "results" / f"{mid}.json"
                res.write_text(json.dumps({"source": "hook", "final": True, "ended": True,
                                           "run_started": time.time(), "mission_id": mid,
                                           "snapshot": {"model_time": 1800, "groups": []},
                                           "events": []}))
                self._beat("lobby", 1800, seq)


@pytest.fixture
def soak_env(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "LOAD_TIMEOUT", 2)
    monkeypatch.setenv("CWDC_DCS_WRITEDIR", str(tmp_path / prof.profile_name(1)))
    monkeypatch.setenv("CWDC_CAMPAIGNS_DIR", str(tmp_path / "campaigns"))
    return tmp_path


def _instance(tmp, **kw):
    fakes = []

    def launcher(cmd):
        assert cmd[1:3] == ["--server", "--norender"]
        fakes.append(FakeDCS(prof.profile_dir(1, tmp), **kw))
        return fakes[-1]
    inst = runner.Instance(1, launcher=launcher, exe=Path("DCS.exe"), root=tmp, poll=0.02)
    return inst, fakes


def test_profile_is_private_and_has_both_hooks(tmp_path):
    p = prof.prepare(2, tmp_path)
    cfg = (p / "Config" / "serverSettings.lua").read_text()
    assert '["isPublic"] = false' in cfg and '"127.0.0.1"' in cfg and "CWDC_Lobby.miz" in cfg
    assert '["port"] = 10310' in cfg
    hooks = {f.name for f in (p / "Scripts" / "Hooks").iterdir()}
    assert hooks == {"CWDC-hook.lua", "CWDC-soak-hook.lua"}
    assert (p / "Missions" / prof.LOBBY).is_file()


def test_worker_flies_records_and_ingests(soak_env):
    inst, fakes = _instance(soak_env)
    run_dir = soak_env / "run"
    runner.run_worker(1, run_dir, until=time.time() + 1e6, cap_s=1800, seed=3,
                      instance=inst, max_missions=3, crisis=False)
    rows = [json.loads(x) for x in (run_dir / "i1" / "missions.jsonl").read_text().splitlines()]
    ms = [r for r in rows if r["kind"] == "mission"]
    assert len(ms) == 3 and all(m["status"] == "ok" for m in ms)
    assert [m["turn"] for m in ms] == [1, 2, 3]                       # each mission closed a turn
    assert len({m["task"] for m in ms}) > 1                            # task rotation
    assert all(m["predicted"] and m["observed"] for m in ms)
    assert not fakes[0].alive                                          # told to exit at the end
    secs = report.sections(run_dir)
    assert secs[0][2][0] == ["DCS missions", 3]
    report.write(run_dir)
    assert "DCS vs resolver" in (run_dir / "report.md").read_text()


def test_watchdog_restarts_a_crashed_instance(soak_env):
    inst, fakes = _instance(soak_env, die_after=1)
    runner.run_worker(1, soak_env / "run", until=time.time() + 1e6, cap_s=1800, seed=3,
                      instance=inst, max_missions=2)
    ms = [json.loads(x) for x in (soak_env / "run" / "i1" / "missions.jsonl").read_text().splitlines()]
    assert ms[0]["status"] == "ok" and ms[1]["status"] == "crashed"
    assert inst.restarts == 1 and len(fakes) == 2
    assert ms[1]["result_source"].startswith("none")                  # turn skipped, war goes on
    assert list((soak_env / "run" / "i1" / "anomalies").glob("*.miz"))


def test_load_that_never_starts_is_reported(soak_env):
    inst, _ = _instance(soak_env, respond=False)
    inst.start()
    assert inst.fly("x.miz", lambda: False, 1800) == "load_failed"
    inst.kill()


def test_prediction_leaves_campaign_unchanged():
    from cwdc.engine.factory import new_campaign
    from cwdc.missiongen import builder
    c = new_campaign("P", seed=5)
    fid = next(f.id for f in c.flights.values() if f.player_eligible)
    rec = builder.select(c, c.flights[fid])
    from cwdc.model import MissionRecord
    mr = MissionRecord(mission_id="m", turn=1, player_flight_id=fid, miz_path="x", generated_at=0,
                       spawned_flights=[f.id for f in rec.flights], bot=True)
    before = c.model_dump_json()
    p = record.predict(c, mr, draws=5)
    assert c.model_dump_json() == before
    assert set(p) >= {"focus_lost", "blue_lost", "red_lost"}


def test_log_errors_tracks_offset(tmp_path):
    log = tmp_path / "dcs.log"
    log.write_text("ok\n2026 ERROR SCRIPTING (Main): SCRIPTING ERROR boom\n")
    lines, off = record.log_errors(log, 0)
    assert len(lines) == 1 and off == log.stat().st_size
    assert record.log_errors(log, off)[0] == []


def test_stop_file_ends_the_worker(soak_env):
    inst, fakes = _instance(soak_env)
    run_dir = soak_env / "run"
    run_dir.mkdir()
    runner.stop_file(run_dir).write_text("stop")
    runner.run_worker(1, run_dir, until=time.time() + 1e6, cap_s=1800, seed=3, instance=inst)
    assert not (run_dir / "i1" / "missions.jsonl").exists() or all(
        json.loads(x)["kind"] == "campaign" for x in (run_dir / "i1" / "missions.jsonl").read_text().splitlines())
    assert not fakes[0].alive


@pytest.mark.parametrize("mode,expected", [("frozen", "frozen"), ("endless", "timeout")])
def test_stuck_missions_time_out(soak_env, monkeypatch, mode, expected):
    monkeypatch.setattr(runner, "FROZEN_S", 0.3)
    monkeypatch.setattr(runner, "ABORT_WAIT", 0.3)
    inst, fakes = _instance(soak_env, mode=mode)
    inst.start()
    t0 = time.time()
    assert inst.fly("x.miz", lambda: False, cap_s=10**6, timeout_s=1) == expected
    assert fakes[0].aborts == 1 and time.time() - t0 < 10
    inst.kill()
