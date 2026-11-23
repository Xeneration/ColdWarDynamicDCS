"""Regression tests against log lines recorded from a real DCS flight of Spike C (2026-11-23).

Flight: player F-4E from Hahn killed both MiG-21s (AIM-7E-2, then AIM-9J), one MiG pilot
ejected, the player landed at Hahn and clipped a fence on the way. 34 events.
"""
from pathlib import Path

import pytest

from cwdc.ingest.results import is_collision, is_scenery, kill_weapon, load_result_file, parse_log_lines
from tests.luamock import hook_runtime

FIXTURE = Path(__file__).parent / "fixtures" / "spike_c_flight.log"


@pytest.fixture(scope="module")
def result():
    with FIXTURE.open(encoding="utf-8") as f:
        return parse_log_lines(f)["spike-c-001"]


def test_full_run_reassembled(result):
    assert result["ended"] is True
    assert result["start"]["tracked_groups"] == ["BLUE-Hawk1", "RED-Fishbed1", "RED-Armor1"]
    assert len(result["events"]) == 34
    groups = {g["name"]: g for g in result["snapshot"]["groups"]}
    assert groups["RED-Fishbed1"]["exists"] is False
    assert len(groups["RED-Armor1"]["units"]) == 4
    assert len(groups["BLUE-Hawk1"]["units"]) == 2


def test_air_kills_credited_with_weapons(result):
    events = result["events"]
    kills = [e for e in events if e["event"] == "S_EVENT_KILL" and not is_scenery(e["target"])]
    assert [(k["initiator"]["player"], k["target"]["type"], kill_weapon(events, k)) for k in kills] == [
        ("Player", "MiG-21Bis", "HB-AIM-7E-2"),
        ("Player", "MiG-21Bis", "AIM-9J"),   # DCS omitted the weapon; recovered from the HIT
    ]


def test_fence_strike_is_scenery_collision(result):
    fence = next(e for e in result["events"]
                 if e["event"] == "S_EVENT_KILL" and is_scenery(e["target"]))
    assert fence["target"]["type"] == "BARBED_FENCE_CRASH"
    assert is_collision(fence)


def test_ejection_and_parachute_positions_recorded(result):
    ej = next(e for e in result["events"] if e["event"] == "S_EVENT_EJECTION")
    chute = next(e for e in result["events"] if e["event"] == "S_EVENT_LANDING_AFTER_EJECTION")
    assert ej["initiator"]["pos"]["lat"] == pytest.approx(50.156, abs=0.01)
    assert chute["initiator"]["pos"]["lon"] == pytest.approx(7.888, abs=0.01)


def test_hook_reproduces_parser_on_real_lines(tmp_path, result):
    # Sim.getLogHistory returns bare messages, without dcs.log's timestamp prefix
    messages = [line.split("): ", 1)[1].rstrip("\n")
                for line in FIXTURE.read_text(encoding="utf-8").splitlines(keepends=True)]
    hook, _ = hook_runtime(tmp_path, messages)
    hook.execute("CALLBACKS.onSimulationStart(); REALTIME = 10; CALLBACKS.onSimulationFrame()")
    hook.execute("CALLBACKS.onSimulationStop()")
    written = load_result_file(tmp_path / "CWDC" / "results" / "spike-c-001.json")
    for key in ("start", "snapshot", "end", "events", "ended"):
        assert written[key] == result[key], key
