import json
import time

import pytest
from fastapi.testclient import TestClient

from cwdc import dcs_paths
from cwdc.web import app as webapp


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("CWDC_CAMPAIGNS_DIR", str(tmp_path / "campaigns"))
    monkeypatch.setenv("CWDC_DCS_WRITEDIR", str(tmp_path / "dcs"))
    app = webapp.create_app(auto_ingest=True, watcher=False)
    with TestClient(app) as c:
        c.app_state = app.state.cwdc
        yield c


def _new(client, name="Web Test", seed=3):
    r = client.post("/api/campaigns", json={"name": name, "seed": seed, "pilot": "Tester"})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def test_index_and_static_are_served(client):
    assert "ColdWarDynamic" in client.get("/").text
    assert client.get("/static/app.js").status_code == 200


def test_create_list_and_read_a_campaign(client):
    cid = _new(client)
    lst = client.get("/api/campaigns").json()
    assert lst["current"] == cid and [c["id"] for c in lst["campaigns"]] == [cid]
    o = client.get(f"/api/campaigns/{cid}").json()
    assert o["turn"] == 1 and o["player"]["name"] == "Tester" and o["pending"] is None
    assert o["situation"] and o["sectors"] and o["airfields"]
    m = client.get(f"/api/campaigns/{cid}/map").json()
    assert len(m["front"]) > 10 and m["airbases"] and m["flights"]
    assert all(-90 <= ll[0] <= 90 for ll in m["front"])
    a = client.get(f"/api/campaigns/{cid}/ato").json()
    assert any(f["eligible"] for f in a["flights"]) and all(f["side"] == "blue" for f in a["flights"])
    s = client.get(f"/api/campaigns/{cid}/squadron").json()
    assert s["pilots"] and s["wsos"] and s["airframes"]
    # same name again without overwrite is refused
    assert client.post("/api/campaigns", json={"name": "Web Test"}).status_code == 400


def test_fly_writes_mission_and_briefing(client):
    cid = _new(client)
    fid = next(f["id"] for f in client.get(f"/api/campaigns/{cid}/ato").json()["flights"] if f["eligible"])
    v0 = client.get(f"/api/campaigns/{cid}/version").json()["version"]
    r = client.post(f"/api/campaigns/{cid}/fly", json={"flight_id": fid, "start": "air"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["file"].endswith(".miz") and any("Crews:" in l for l in body["briefing"])
    assert (dcs_paths.missions_dir() / body["file"]).is_file()
    st = client.get(f"/api/campaigns/{cid}/version").json()
    assert st["version"] != v0 and st["result"]["pending"] and not st["result"]["exists"]
    o = client.get(f"/api/campaigns/{cid}").json()
    assert o["pending"]["flight_id"] == fid and o["pending"]["briefing"]
    # ingest with no result yet is a clear 400, not a crash
    r = client.post(f"/api/campaigns/{cid}/ingest", json={})
    assert r.status_code == 400 and "no results" in r.json()["detail"]


def test_bad_requests_are_400(client):
    cid = _new(client)
    assert client.post(f"/api/campaigns/{cid}/fly", json={"flight_id": "F999"}).status_code == 400
    assert client.post(f"/api/campaigns/{cid}/rollback", json={"turn": 42}).status_code == 400
    assert client.get("/api/campaigns/nope").status_code == 404


def test_skip_debrief_and_rollback(client):
    cid = _new(client)
    r = client.post(f"/api/campaigns/{cid}/skip")
    assert r.status_code == 200 and r.json()["debrief"][0].startswith("=== Turn 1")
    d = client.get(f"/api/campaigns/{cid}/debriefs").json()
    assert d["debriefs"][0]["turn"] == 1 and d["snapshots"] == [1, 2]
    assert client.get(f"/api/campaigns/{cid}").json()["turn"] == 2
    assert client.post(f"/api/campaigns/{cid}/rollback", json={"turn": 1}).json()["turn"] == 1


def _write_result(client, cid, final=True):
    o = client.get(f"/api/campaigns/{cid}").json()
    path = dcs_paths.results_dir() / f"{o['pending']['mission_id']}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"source": "hook", "final": final, "run_started": time.time() + 5,
                                "mission_id": o["pending"]["mission_id"], "events": [], "snapshot": {}}))


def test_watcher_auto_ingests_a_finished_mission(client):
    cid = _new(client)
    fid = next(f["id"] for f in client.get(f"/api/campaigns/{cid}/ato").json()["flights"] if f["eligible"])
    client.post(f"/api/campaigns/{cid}/fly", json={"flight_id": fid, "bot": True})
    _write_result(client, cid, final=False)               # DCS still running: leave it alone
    assert webapp.watch_once(client.app_state) == []
    _write_result(client, cid, final=True)                # mission ended
    assert webapp.watch_once(client.app_state) == [cid]
    o = client.get(f"/api/campaigns/{cid}").json()
    assert o["turn"] == 2 and o["pending"] is None
    ev = client.get("/api/events").json()["events"]
    assert ev[-1]["kind"] == "ingested" and ev[-1]["campaign"] == cid
    d = client.get(f"/api/campaigns/{cid}/debriefs").json()["debriefs"][0]
    assert d["flown_in_dcs"] is True


def test_auto_ingest_can_be_switched_off(client):
    cid = _new(client)
    fid = next(f["id"] for f in client.get(f"/api/campaigns/{cid}/ato").json()["flights"] if f["eligible"])
    client.post(f"/api/campaigns/{cid}/fly", json={"flight_id": fid, "bot": True})
    _write_result(client, cid)
    assert client.post("/api/settings", json={"auto_ingest": False}).json() == {"auto_ingest": False}
    assert webapp.watch_once(client.app_state) == []
    r = client.post(f"/api/campaigns/{cid}/ingest", json={})       # manual ingest still works
    assert r.status_code == 200 and r.json()["debrief"]
