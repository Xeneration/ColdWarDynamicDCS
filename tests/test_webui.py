from fastapi.testclient import TestClient

from cwdc import theater as T
from cwdc.engine import turn
from cwdc.engine.factory import new_campaign
from cwdc.missiongen import loadouts
from cwdc.web import app as webapp, mapdata, views


def test_map_places_everything_on_its_own_side():
    c = new_campaign("UI", seed=4)
    turn.end_turn(c)
    m = mapdata.map_layers(c)
    for f in m["formations"]:
        assert T.side_at(c.front_line, *f["at"]).value == f["side"], f["name"]
    for ab in m["airbases"]:
        assert T.side_at(c.front_line, *ab["at"]).value == ab["side"]
    assert m["places"] and all(o["text"] for o in m["objectives"]) and all(s["text"] for s in m["sectors"])


def test_preview_changes_nothing_and_history_is_recorded():
    c = new_campaign("UI", seed=4)
    before = c.model_dump_json()
    fid = next(f.id for f in c.flights.values() if f.player_eligible)
    p = views.flight_preview(c, fid)
    assert p["friendly"] and p["distance_nm"] > 0
    assert c.model_dump_json() == before
    turn.end_turn(c)
    assert len(c.history) == 1 and c.debriefs[-1].stats["front"]
    assert views.ato(c)["packages"][0]["reason"]


def test_every_f4e_task_loadout_has_a_description():
    cat = T.airframe_catalogue()["F-4E-45MC"]["loadouts"]
    lo = loadouts.loadouts("F-4E-45MC")
    for name in set(cat.values()) - {"default"}:
        assert lo[name].get("description"), name


def test_open_folder_is_local_only(tmp_path, monkeypatch):
    monkeypatch.setenv("CWDC_CAMPAIGNS_DIR", str(tmp_path))
    with TestClient(webapp.create_app(watcher=False)) as cl:        # test client host is not 127.0.0.1
        assert cl.post("/api/open-missions-folder").status_code == 403
