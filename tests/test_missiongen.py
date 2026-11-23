import logging
import zipfile

import dcs
import pytest

from cwdc.missiongen import loadouts, spike

logging.getLogger().setLevel(logging.CRITICAL)  # pydcs livery parse noise


@pytest.mark.parametrize("name", sorted(loadouts.loadouts()))
def test_all_loadouts_resolve(name):
    pylons = loadouts.resolve(name)
    assert pylons and all(c.startswith("{") or c.startswith("<") or c for c in pylons.values())


def test_unknown_store_is_rejected(monkeypatch):
    bad = {"BAD": {"task": "CAP", "pylons": {5: "AIM-9J Sidewinder IR AAM"}}}  # no AIM-9 in a Sparrow well
    monkeypatch.setattr(loadouts, "loadouts", lambda *a: bad)
    with pytest.raises(loadouts.LoadoutError):
        loadouts.resolve("BAD")


def test_spike_mission_roundtrip(tmp_path):
    path = spike.generate(tmp_path)
    with zipfile.ZipFile(path) as z:
        names = z.namelist()
    assert any(n.endswith("cwdc_runtime.lua") for n in names)

    m = dcs.Mission()
    m.load_file(str(path))
    assert m.terrain.name == "GermanyCW"
    assert m.start_time.year == 1976

    player = m.find_group(spike.PLAYER_GROUP)
    assert player.units[0].type == "F-4E-45MC"
    assert player.units[0].skill == dcs.unit.Skill.Player
    assert player.units[0].pylons == {p: {"CLSID": c} for p, c in loadouts.resolve("CAP").items()}

    assert m.find_group(spike.MIG_GROUP).units[0].pylons, "MiG-21s should be armed"
    assert len(m.find_group(spike.ARMOR_GROUP).units) == 4

    preds = [a.predicate for t in m.triggerrules.triggers for a in t.actions]
    assert preds == ["a_do_script", "a_do_script_file"]


def test_do_script_text_is_inline_not_a_dictionary_key(tmp_path):
    """Regression: DCS executes a_do_script's 'text' verbatim (no l10n lookup).

    A DictKey there produced: [string "DictKey_Translation_5"]:1: '=' expected near '<eof>'
    """
    from dcs import lua
    path = spike.generate(tmp_path)
    with zipfile.ZipFile(path) as z:
        mission = lua.loads(z.read("mission").decode("utf-8"))["mission"]
    actions = [a for rule in mission["trigrules"].values() for a in rule["actions"].values()]
    do_script = next(a for a in actions if a["predicate"] == "a_do_script")
    assert do_script["text"].startswith("CWDC_CONFIG = {")
    assert 'mission_id = "spike-c-001"' in do_script["text"]
    assert "DictKey" not in mission["trig"]["actions"][1]


@pytest.mark.parametrize("name", sorted(loadouts.loadouts()))
def test_loadout_combinations_match_module_payloads(name):
    """Regression (live flight): DCS silently stripped SEAD Shrikes combined with a
    centreline tank. Every store pair must appear together in a Heatblur payload."""
    assert loadouts.unseen_pairs(name) == []
