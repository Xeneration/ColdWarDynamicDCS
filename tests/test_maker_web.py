"""Campaign maker web API (Phase D): slider data, live preview, creating a war from the maker."""
import pytest
from fastapi.testclient import TestClient

from cwdc.era import modules
from cwdc.web import app as webapp

OWNED = ["F-100D", "F-4E-45MC", "A-10A", "A-4E-C", "AJS37", "F-5E-3", "F-14B", "UH-1H"]


def _installed():
    inst = modules.Installed(tech={"WWII Units"}, terrains={"GermanyColdWar", "Syria"})
    for t in OWNED:
        inst.flyable[t] = "mod" if t == "A-4E-C" else t
    inst.mods.add("mod")
    return inst


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("CWDC_CAMPAIGNS_DIR", str(tmp_path / "campaigns"))
    monkeypatch.setenv("CWDC_DCS_WRITEDIR", str(tmp_path / "dcs"))
    app = webapp.create_app(auto_ingest=False, watcher=False, installed=_installed())
    with TestClient(app) as c:
        yield c


def test_maker_info(client):
    r = client.get("/api/maker").json()
    assert r["years"] == [1950, 1991]
    bars = {b["id"]: b for b in r["bars"]}
    assert bars["F-100D"]["owned"] == ["F-100D"] and bars["MiG-21"]["owned"] == []
    assert bars["Jaguar"]["stand_in"]
    assert {t["id"]: t["available"] for t in r["theatres"]} == {"germany": True, "syria": True, "golan": True}
    syria = client.get("/api/maker?theatre=syria").json()
    assert {"Turkey", "Syria"} <= {n["name"] for n in syria["nations"]}
    assert "Sweden" not in {n["name"] for n in syria["nations"]}
    p = client.post("/api/maker/preview", json={"theatre": "syria", "year": 1976}).json()
    assert p["ok"] and any(x["nation"] == "Turkey" for x in p["squadrons"]["west"])
    assert {p["id"] for p in r["presets"]} >= {"fulda-1976", "sabre-days-1957"}
    assert any(n["name"] == "Sweden" and n["toggle"] for n in r["nations"])
    assert "Turkey" not in {n["name"] for n in r["nations"]}             # Southern Flank only


def test_preview_by_year(client):
    early = client.post("/api/maker/preview", json={"year": 1953}).json()
    assert not early["ok"] and "1953" in early["error"]
    p = client.post("/api/maker/preview", json={"year": 1966, "seed": 4}).json()
    assert p["ok"] and p["player"]["type"] == "F-100D" and len(p["wing"]) >= 2
    assert p["squadrons"]["west"] and p["squadrons"]["east"]
    assert "SA-2 Guideline" in p["air_defence"]["east"]
    fams = {f["family"] for f in p["weapons"][0]["families"]}
    assert "AIM-9B" in fams and "AGM-65 Maverick" not in fams
    old = client.post("/api/maker/preview", json={"year": 1957}).json()
    assert any("flak" in n for n in old["notes"]) and not any("SA-" in s for s in old["air_defence"]["east"])
    a10 = client.post("/api/maker/preview", json={"year": 1985, "player_airframe": "A-10A"}).json()
    assert a10["player"]["type"] == "A-10A"
    assert {o["id"] for o in a10["player_options"]} >= {"A-10A", "F-4E"}


def test_create_from_the_maker_and_from_a_preset(client):
    r = client.post("/api/campaigns", json={"name": "Maker 1966", "seed": 4, "pilot": "Tester",
                                            "era": {"year": 1966, "player_airframe": "F-100D"}})
    assert r.status_code == 200, r.text
    o = client.get(f"/api/campaigns/{r.json()['id']}").json()
    assert o["time"].startswith("1966") and o["player"]["aircraft"] == "F-100D Super Sabre"
    assert o["player"]["wing"]
    r = client.post("/api/campaigns", json={"name": "Hogs", "preset": "hog-country-1985"})
    o = client.get(f"/api/campaigns/{r.json()['id']}").json()
    assert o["time"].startswith("1985") and o["player"]["aircraft"] == "A-10A Thunderbolt II"
    r = client.post("/api/campaigns", json={"name": "Classic", "preset": "fulda-1976"})
    o = client.get(f"/api/campaigns/{r.json()['id']}").json()
    assert o["player"]["squadron"] == "496th TFS" and o["player"]["wing"] == []
    bad = client.post("/api/campaigns", json={"name": "Too early", "era": {"year": 1951}})
    assert bad.status_code == 400
    assert client.post("/api/campaigns", json={"name": "x", "preset": "nope"}).status_code == 400
