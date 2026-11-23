import json

from cwdc.ingest.results import from_dcs_log, load_result_file, parse_log_lines
from cwdc.missiongen.runtime_inject import config_lua
from tests.luamock import hook_runtime, mission_runtime

UNITS = """
  P = mkunit("Hawk 1-1", "F-4E-45MC", 2, "Sam", "BLUE-Hawk1", -437000, -948000)
  W = mkunit("Hawk 1-2", "F-4E-45MC", 2, nil, "BLUE-Hawk1")
  M = mkunit("Fishbed 1-1", "MiG-21Bis", 1, nil, "RED-Fishbed1")
  A1 = mkunit("Armor 1", "T-55", 1, nil, "RED-Armor1")
  A2 = mkunit("Armor 2", "T-55", 1, nil, "RED-Armor1")
  mkgroup("BLUE-Hawk1", {P, W})
  mkgroup("RED-Armor1", {A1, A2})
"""

COMBAT = """
  T = 100
  HANDLER:onEvent({ id = world.event.S_EVENT_TAKEOFF, time = 100, initiator = P })
  HANDLER:onEvent({ id = world.event.S_EVENT_SHOT, time = 300, initiator = P,
                    weapon = mkweapon("AIM_7E2"), target = M })
  HANDLER:onEvent({ id = world.event.S_EVENT_KILL, time = 310, initiator = P, target = M,
                    weapon_name = "AIM_7E2" })
  A2._alive = false
  HANDLER:onEvent({ id = world.event.S_EVENT_DEAD, time = 400, initiator = A2 })
  HANDLER:onEvent({ id = 999, time = 401 })  -- unrecorded event id
  T = 500
"""


def _setup(roe="weapons_free", logbuf=None):
    # units must exist before the runtime takes its start-up snapshot
    return mission_runtime(config_lua("t-001", ["BLUE-Hawk1", "RED-Armor1"], roe),
                           logbuf, pre_runtime=UNITS)


def test_runtime_records_events_and_snapshots():
    lua = _setup()
    lua.execute(COMBAT)
    data = json.loads(lua.eval("CWDC_export()"))
    assert data["mission_id"] == "t-001"
    assert data["model_time"] == 500
    names = [e["event"] for e in data["events"]]
    assert names == ["S_EVENT_TAKEOFF", "S_EVENT_SHOT", "S_EVENT_KILL", "S_EVENT_DEAD"]
    shot = data["events"][1]
    assert shot["weapon"] == "AIM_7E2"
    assert shot["initiator"]["player"] == "Sam"
    assert shot["target"]["type"] == "MiG-21Bis"
    assert "roe_violation" not in shot
    assert data["events"][2]["weapon"] == "AIM_7E2"

    groups = {g["name"]: g for g in data["groups"]}
    assert len(groups["BLUE-Hawk1"]["units"]) == 2
    assert [u["name"] for u in groups["RED-Armor1"]["units"]] == ["Armor 1"]
    assert groups["BLUE-Hawk1"]["units"][0]["pos"]["lat"] > 49


def test_runtime_flags_roe_violation_when_weapons_tight():
    lua = _setup(roe="weapons_tight")
    lua.execute("""
      HANDLER:onEvent({ id = world.event.S_EVENT_SHOT, time = 10, initiator = P, weapon = mkweapon("AIM_9J") })
      HANDLER:onEvent({ id = world.event.S_EVENT_SHOT, time = 11, initiator = W, weapon = mkweapon("AIM_9J") })
    """)
    events = json.loads(lua.eval("CWDC_export()"))["events"]
    assert events[0]["roe_violation"] is True      # player
    assert "roe_violation" not in events[1]        # AI wingman
    assert lua.eval("#MSGS") == 1


def test_json_encoder_escapes_strings():
    lua = _setup()
    out = lua.eval('CWDC.encode({ s = "a\\"b\\\\c\\n", arr = {1, 2, 3}, empty = {} })')
    assert json.loads(out) == {"s": 'a"b\\c\n', "arr": [1, 2, 3], "empty": []}


def test_log_lines_rebuild_full_result_even_when_chunked():
    log = []
    lua = _setup(logbuf=log)
    lua.execute("CWDC.chunk_size = 40")   # force multi-part messages
    lua.execute(COMBAT)
    assert lua.eval("run_scheduled()") == 530   # periodic snapshot reschedules itself
    lua.execute("HANDLER:onEvent({ id = world.event.S_EVENT_MISSION_END, time = 600 })")
    assert any(" 2/" in line for line in log if line.startswith("[CWDC] "))

    res = parse_log_lines(log)["t-001"]
    assert res["ended"] is True
    assert res["start"]["tracked_groups"] == ["BLUE-Hawk1", "RED-Armor1"]
    assert [e["event"] for e in res["events"]] == [
        "S_EVENT_TAKEOFF", "S_EVENT_SHOT", "S_EVENT_KILL", "S_EVENT_DEAD"]
    groups = {g["name"]: g for g in res["snapshot"]["groups"]}
    assert len(groups["RED-Armor1"]["units"]) == 1


def test_dcs_log_fallback(tmp_path):
    log = []
    lua = _setup(logbuf=log)
    lua.execute(COMBAT)
    lua.execute("HANDLER:onEvent({ id = world.event.S_EVENT_MISSION_END, time = 600 })")
    dcs_log = tmp_path / "dcs.log"
    dcs_log.write_text("".join(
        f"2026-11-23 19:14:19.875 INFO    SCRIPTING (Main): {line}\n" for line in
        ["unrelated line", *log]), encoding="utf-8")
    res = from_dcs_log(dcs_log, "t-001")
    assert res["source"] == "dcs.log"
    assert len(res["events"]) == 4 and res["ended"]


def test_hook_writes_results_file(tmp_path):
    log = []
    hook, hook_logs = hook_runtime(tmp_path, log)
    hook.execute("CALLBACKS.onSimulationStart()")
    mission = _setup(logbuf=log)
    hook.execute("REALTIME = 10; CALLBACKS.onSimulationFrame()")
    result = tmp_path / "CWDC" / "results" / "t-001.json"
    assert result.is_file(), hook_logs
    first = load_result_file(result)
    assert first["final"] is False and first["start"]["mission_id"] == "t-001"

    mission.execute(COMBAT)
    hook.execute("REALTIME = 20; CALLBACKS.onSimulationFrame()")
    mission.execute("""
      HANDLER:onEvent({ id = world.event.S_EVENT_LAND, time = 900, initiator = P })
      HANDLER:onEvent({ id = world.event.S_EVENT_MISSION_END, time = 901 })
    """)
    hook.execute("REALTIME = 21; CALLBACKS.onSimulationStop()")
    final = load_result_file(result)
    assert final["final"] is True and final["ended"] is True
    # overlapping log reads must not duplicate events
    assert [e["event"] for e in final["events"]] == [
        "S_EVENT_TAKEOFF", "S_EVENT_SHOT", "S_EVENT_KILL", "S_EVENT_DEAD", "S_EVENT_LAND"]
    assert final == {**parse_log_lines(log)["t-001"], **{k: final[k] for k in
                     ("source", "hook_version", "final", "written_at", "run_started")}}
    assert not (tmp_path / "CWDC" / "results" / "t-001.json.tmp").exists()


def test_hook_ignores_non_cwdc_missions(tmp_path):
    log = ["some other mission log line"]
    hook, _ = hook_runtime(tmp_path, log)
    hook.execute("CALLBACKS.onSimulationStart()")
    hook.execute("REALTIME = 10; CALLBACKS.onSimulationFrame()")
    hook.execute("CALLBACKS.onSimulationStop()")
    results = tmp_path / "CWDC" / "results"
    assert not results.exists() or not any(results.iterdir())


def test_hook_survives_missing_log_api(tmp_path):
    hook, hook_logs = hook_runtime(tmp_path, None)
    hook.execute("CALLBACKS.onSimulationStart(); REALTIME = 5; CALLBACKS.onSimulationFrame()")
    hook.execute("CALLBACKS.onSimulationStop()")
    assert any("unavailable" in m for m in hook_logs)


def test_scenery_hits_dropped_but_scenery_kills_kept():
    lua = _setup()
    lua.execute("""
      TREE = { getName = function() return "101786000" end, getTypeName = function() return "POPLAR" end,
               getCoalition = function() return 0 end, getCategory = function() return 5 end,
               getPoint = function() return { x = 0, y = 0, z = 0 } end }
      HANDLER:onEvent({ id = world.event.S_EVENT_HIT, time = 1, initiator = M, target = TREE })
      HANDLER:onEvent({ id = world.event.S_EVENT_HIT, time = 2, initiator = P, target = M })
      HANDLER:onEvent({ id = world.event.S_EVENT_KILL, time = 3, initiator = P, target = TREE })
    """)
    events = json.loads(lua.eval("CWDC_export()"))["events"]
    assert [(e["event"], e["target"]["type"]) for e in events] == [
        ("S_EVENT_HIT", "MiG-21Bis"), ("S_EVENT_KILL", "POPLAR")]


def _bot(limit=7200, logbuf=None):
    cfg = config_lua("bot-1", ["BLUE-Hawk1"], end_when={"groups": ["BLUE-Hawk1"], "time_limit": limit})
    return mission_runtime(cfg, logbuf, pre_runtime=UNITS)


def test_bot_mission_ends_when_focus_flight_done():
    log = []
    lua = _bot(logbuf=log)
    lua.execute("""
      T = 1000
      fire({ id = world.event.S_EVENT_LAND, time = 1000, initiator = P })
    """)
    assert lua.eval("CWDC.end_requested") is False          # wingman still flying
    lua.execute("T = 1100; fire({ id = world.event.S_EVENT_UNIT_LOST, time = 1100, initiator = W })")
    assert lua.eval("CWDC.end_requested") is True
    assert any(" NOTE " in line for line in log)
    assert lua.eval("#CWDC.events") == 2                    # main recorder still saw both events
    lua.execute("run_due(1129)")
    assert lua.eval("FLAGS['9001']") is None                 # 30 s grace
    lua.execute("run_due(1130)")
    assert lua.eval("FLAGS['9001']") is True


def test_bot_mission_time_limit():
    lua = _bot(limit=120)
    lua.execute("run_due(60)")
    assert lua.eval("CWDC.end_requested") is False
    lua.execute("run_due(120)")
    assert lua.eval("CWDC.end_requested") is True
