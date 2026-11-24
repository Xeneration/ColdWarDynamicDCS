"""Era database (campaign maker Phase A): airframes, weapons, ground units and shipped payloads by
year, owned-module detection and the A-4E-C mod type."""
import logging
import tempfile
from pathlib import Path

import dcs
import pytest
from dcs import countries, vehicles
from dcs.helicopters import helicopter_map
from dcs.planes import plane_map

from cwdc import era
from cwdc.era import modules
from cwdc.era.payloads import harvested_types, shipped


def _families(pylons):
    return {era.weapon_info(c)[1] for c in pylons.values()}


def _installed(*types, mods=(), tech=()):
    inst = modules.Installed()
    for t in types:
        inst.flyable[t] = "mod" if t in mods else t
    inst.mods.update("mod" for _ in mods)
    inst.tech.update(tech)
    return inst


# ------------------------------------------------------------------ data integrity
def test_every_airframe_type_exists_in_dcs():
    for af in era.airframes().values():
        assert af.side in era.SIDES, af.id
        for t in af.dcs + af.player:
            assert t in plane_map or t in helicopter_map, (af.id, t)


def test_service_years_and_nations_are_valid():
    nations = era.nations()
    dcs_countries = {c.name for c in countries.country_dict.values()}
    for spec in nations.values():
        assert spec["country"] in dcs_countries, spec
    for af in era.airframes().values():
        for n, (a, b) in af.service.items():
            assert n in nations and nations[n]["side"] == af.side, (af.id, n)
            assert era.FIRST_YEAR <= a <= b <= era.LAST_YEAR, (af.id, n)


def test_every_ground_unit_exists_in_dcs():
    data = era._ground()
    for section in ("air_defence", "ground"):
        for k, spec in data[section].items():
            assert spec["side"] in era.SIDES, k
            for n in spec.get("nations", []):
                assert n in era.nations(), (k, n)
            for u in spec["units"]:
                assert u in vehicles.vehicle_map, (k, u)


def test_every_shipped_store_has_an_introduction_year():
    unknown = {era.store_label(c) for t in harvested_types() for p in shipped(t) for c in p["pylons"].values()
               if era.weapon_year(c) is None}
    assert not unknown


def test_owned_modules_have_harvested_payloads():
    for t in ("F-4E-45MC", "F-100D", "A-4E-C", "AJS37", "F-5E-3", "A-10A", "F-14B", "UH-1H", "Su-25T"):
        assert shipped(t), t


# ------------------------------------------------------------------ year filtering
def test_payloads_respect_weapon_years():
    early = era.payloads_for("F-100D", 1957)
    assert early and all(not _families(p["pylons"]) & {"AIM-9E", "AGM-45 Shrike", "Rockeye"} for p in early)
    assert any("Rockeye" in _families(p["pylons"]) for p in era.payloads_for("F-100D", 1970))
    for p in era.payloads_for("F-4E-45MC", 1966):
        assert "AGM-65 Maverick" not in _families(p["pylons"])
        assert all(era.weapon_year(c) <= 1966 for c in p["pylons"].values())
    assert len(era.payloads_for("F-4E-45MC", 1985)) > len(era.payloads_for("F-4E-45MC", 1970))


def test_airframes_in_service_by_year():
    def ids(year, **kw):
        return {s.airframe.id for s in era.airframes_in(year, **kw)}
    y57 = ids(1957)
    assert {"F-86F", "F-100D", "MiG-15", "MiG-17", "MiG-19"} <= y57
    assert not {"F-4E", "MiG-21", "A-10A", "F-15C", "Su-25"} & y57
    y85 = ids(1985)
    assert {"F-15C", "A-10A", "F-16A", "Tornado", "MiG-29", "Su-25", "MiG-27"} <= y85
    assert not {"F-86F", "MiG-15"} & y85
    assert "F-100D" in y85                                                     # Turkey flew it to 1987
    assert "F-100D" not in ids(1985, nations_on=era.default_nations("germany", 1985))
    assert "Viggen" not in ids(1975, nations_on=era.default_nations("germany", 1975))
    assert "Viggen" in ids(1975, nations_on=era.default_nations("germany", 1975, sweden=True))
    strict = ids(1975, strict=True)
    assert "F-104G" in ids(1975) and "F-104G" not in strict and "F-4E" in strict


def test_theatre_nations():
    assert "Turkey" in era.default_nations("syria", 1980) and "Syria" in era.default_nations("syria", 1980)
    assert "Turkey" not in era.default_nations("germany", 1980)
    assert "GDR" not in era.default_nations("germany", 1950) and "GDR" in era.default_nations("germany", 1960)


def test_stand_ins_and_mod_types():
    jag = era.airframes()["Jaguar"]
    assert jag.stand_in
    assert era.dcs_type(jag, None) == "F-5E-3"                                 # no mod: core stand-in
    assert era.dcs_type(jag, _installed("A-4E-C", mods=("A-4E-C",))) == "A-4E-C"
    assert era.dcs_type(era.airframes()["A-4E"], None) is None                  # mod-only airframe
    assert era.player_types(era.airframes()["F-4E"], _installed("F-4E-45MC")) == ["F-4E-45MC"]
    assert era.player_types(era.airframes()["F-4E"], _installed("F-100D")) == []


def test_air_defences_and_ground_by_year():
    ad57 = era.air_defence_in(1957, side="east")
    assert ad57 and {v["kind"] for v in ad57.values()} <= {"aaa", "ewr"}
    ad75 = era.air_defence_in(1975, side="east")
    assert {"SA-2", "SA-3", "SA-6", "SA-8", "ZSU-23-4"} <= set(ad75) and "SA-11" not in ad75
    assert "Hawk" in era.air_defence_in(1975, side="west") and "Patriot" not in era.air_defence_in(1975, side="west")
    assert "T-34-85" not in era.ground_in(1955, side="east")                   # WWII pack not known
    assert "T-34-85" in era.ground_in(1955, side="east", installed=_installed(tech=("WWII Units",)))
    assert "T-80" not in era.ground_in(1985, side="east", nations_on=["GDR"])
    assert "BMP-2" in era.ground_in(1985, side="east") and "BMP-1" not in era.ground_in(1960, side="east")


def test_slider_bars():
    inst = _installed("F-100D", "F-4E-45MC", "A-4E-C", mods=("A-4E-C",))
    bars = era.slider(inst, theatre="germany")
    by = {b["id"]: b for b in bars}
    assert by["F-100D"]["owned"] == ["F-100D"] and by["F-100D"]["start"] == 1956
    assert by["F-86F"]["owned"] == [] and by["F-86F"]["available"]
    assert by["Jaguar"]["stand_in"] and by["Jaguar"]["owned"] == ["A-4E-C"]
    assert "Turkey" not in by["F-4E"]["nations"]                               # Syria-only operator
    sides = [b["side"] for b in bars]
    assert sides == sorted(sides, key=era.SIDES.index)
    fams = [w["family"] for w in by["F-4E"]["weapons"]]
    assert "AIM-7" in fams and "fuel" not in fams


# ------------------------------------------------------------------ module detection
def test_detects_modules_mods_and_asset_packs(tmp_path):
    inst_dir, sg = tmp_path / "DCS World", tmp_path / "Saved Games" / "DCS"
    (inst_dir / "Mods" / "aircraft" / "F14").mkdir(parents=True)
    (inst_dir / "Mods" / "aircraft" / "F14" / "entry.lua").write_text(
        "make_flyable('F-14A-135-GR', x)\nmake_flyable( \"F-14B\" , y)")
    (inst_dir / "Mods" / "aircraft" / "Su-25T").mkdir()
    (inst_dir / "Mods" / "aircraft" / "Su-25T" / "entry.lua").write_text("MAC_flyable('Su-25T', c)")
    (inst_dir / "Mods" / "aircraft" / "NoEntry").mkdir()
    (inst_dir / "Mods" / "tech" / "WWII Units").mkdir(parents=True)
    (inst_dir / "Mods" / "terrains" / "Syria").mkdir(parents=True)
    (sg / "Mods" / "aircraft" / "A-4E-C").mkdir(parents=True)
    (sg / "Mods" / "aircraft" / "A-4E-C" / "entry.lua").write_text("make_flyable(\n  'A-4E-C', p)")
    inst = modules.detect(inst_dir, sg)
    assert set(inst.flyable) == {"F-14A-135-GR", "F-14B", "Su-25T", "A-4E-C"}
    assert inst.is_mod("A-4E-C") and not inst.is_mod("F-14B")
    assert "WWII Units" in inst.tech and "Syria" in inst.terrains


# ------------------------------------------------------------------ the A-4E-C in a mission
def test_a4ec_flies_in_a_generated_mission():
    logging.disable(logging.CRITICAL)
    payload = next(p for p in era.payloads_for("A-4E-C", 1965) if "AIM-9B" not in _families(p["pylons"]))
    m = dcs.Mission(dcs.terrain.Caucasus())
    g = m.flight_group_inflight(m.country("USA"), "Scooter 1", plane_map["A-4E-C"],
                                dcs.mapping.Point(0, 0, m.terrain), altitude=3000, speed=500, group_size=2)
    for u in g.units:
        u.pylons = {p: {"CLSID": c} for p, c in payload["pylons"].items()}
    path = Path(tempfile.mkdtemp()) / "a4.miz"
    m.save(str(path))
    m2 = dcs.Mission()
    m2.load_file(str(path))
    u = m2.country("USA").plane_group[0].units[0]
    assert u.type == "A-4E-C"
    assert {int(k): v["CLSID"] for k, v in u.pylons.items()} == payload["pylons"]
