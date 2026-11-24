"""Local web UI (Phase 3): FastAPI JSON API + a single-page front end.

    cwdc serve            # http://127.0.0.1:8076, opens your browser

Every request loads the campaign from disk, so the CLI and the web UI can be used side by
side. Actions that change a campaign are serialised with one lock. A background watcher
ingests a pending mission automatically once DCS has written its final result (the
hook writes `final: true` when you leave the mission).
"""
from __future__ import annotations

import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from cwdc import actions
from cwdc.web import views

STATIC = Path(__file__).parent / "static"
POLL_SECONDS = 5.0


class NewCampaign(BaseModel):
    name: str = "Fulda Gap 1976"
    seed: int = 1976
    pilot: str = "Player"
    force: bool = False


class FlyRequest(BaseModel):
    flight_id: str
    start: str = "runway"
    bot: bool = False


class IngestRequest(BaseModel):
    from_log: bool = False


class RollbackRequest(BaseModel):
    turn: int


class Settings(BaseModel):
    auto_ingest: bool


class _State:
    def __init__(self, auto_ingest: bool):
        self.lock = threading.RLock()
        self.auto_ingest = auto_ingest
        self.events: list[dict] = []            # auto-ingest notifications for the UI
        self.stop = threading.Event()

    def event(self, kind: str, campaign: str, text: str, lines: list[str] | None = None) -> None:
        self.events.append({"id": len(self.events) + 1, "time": time.time(), "kind": kind,
                            "campaign": campaign, "text": text, "lines": lines or []})
        self.events = self.events[-50:]


def watch_once(state: _State) -> list[str]:
    """Ingest every pending mission whose DCS result is final. Returns campaign ids ingested."""
    done = []
    if not state.auto_ingest:
        return done
    for info in actions.list_campaigns():
        with state.lock:
            try:
                c, _ = actions.load(info["id"])
            except actions.ActionError:
                continue
            if not c.pending_mission or c.outcome:
                continue
            st = actions.result_status(c)
            if not (st["exists"] and st["fresh"] and st["final"]):
                continue
            mission = c.pending_mission.mission_id
            try:
                lines, _ = actions.ingest(c)
            except actions.ActionError as e:
                state.event("error", c.id, f"auto-ingest of {mission} failed: {e}")
                continue
            state.event("ingested", c.id, f"{mission} ingested automatically", lines)
            done.append(c.id)
    return done


def _watcher(state: _State) -> None:
    while not state.stop.wait(POLL_SECONDS):
        try:
            watch_once(state)
        except Exception as e:                   # never let the watcher die silently
            state.event("error", "", f"watcher: {e}")


def create_app(auto_ingest: bool = True, watcher: bool = True) -> FastAPI:
    state = _State(auto_ingest)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        t = None
        if watcher:
            t = threading.Thread(target=_watcher, args=(state,), daemon=True, name="cwdc-watcher")
            t.start()
        yield
        state.stop.set()

    app = FastAPI(title="ColdWarDynamicDCS", lifespan=lifespan)
    app.state.cwdc = state

    def load(cid: str):
        try:
            return actions.load(cid)[0]
        except actions.ActionError as e:
            raise HTTPException(404, str(e))
        except FileNotFoundError:
            raise HTTPException(404, f"no campaign {cid}")

    def act(fn, *a, **kw):
        try:
            with state.lock:
                return fn(*a, **kw)
        except actions.ActionError as e:
            raise HTTPException(400, str(e))

    # ------------------------------------------------------------------ read
    @app.get("/api/campaigns")
    def campaigns():
        return {"campaigns": actions.list_campaigns(), "current": actions.current_id()}

    @app.get("/api/campaigns/{cid}")
    def overview(cid: str):
        return views.overview(load(cid))

    @app.get("/api/campaigns/{cid}/version")
    def version(cid: str):
        c = load(cid)
        return {"version": views.version(c), "result": actions.result_status(c)}

    @app.get("/api/campaigns/{cid}/map")
    def map_layers(cid: str, enemy: bool = True):
        return views.map_layers(load(cid), show_enemy_flights=enemy)

    @app.get("/api/campaigns/{cid}/ato")
    def ato(cid: str):
        return views.ato(load(cid))

    @app.get("/api/campaigns/{cid}/squadron")
    def squadron(cid: str):
        return views.squadron(load(cid))

    @app.get("/api/campaigns/{cid}/debriefs")
    def debriefs(cid: str):
        return {"debriefs": views.debriefs(load(cid)), "snapshots": actions.snapshot_turns(cid)}

    @app.get("/api/events")
    def events(after: int = 0):
        return {"events": [e for e in state.events if e["id"] > after], "auto_ingest": state.auto_ingest}

    # ------------------------------------------------------------------ actions
    @app.post("/api/campaigns")
    def new(req: NewCampaign):
        c = act(actions.create, req.name, seed=req.seed, pilot=req.pilot, force=req.force)
        return {"id": c.id}

    @app.post("/api/campaigns/{cid}/fly")
    def fly(cid: str, req: FlyRequest):
        with state.lock:
            c = load(cid)
            flown = act(actions.fly, c, req.flight_id, start=req.start, bot=req.bot)
        rec = flown.record
        return {"mission_id": rec.mission_id, "miz_path": rec.miz_path, "file": Path(rec.miz_path).name,
                "notes": flown.notes, "briefing": rec.briefing}

    @app.post("/api/campaigns/{cid}/ingest")
    def ingest(cid: str, req: IngestRequest):
        with state.lock:
            c = load(cid)
            lines, notes = act(actions.ingest, c, req.from_log)
        return {"debrief": lines, "notes": notes}

    @app.post("/api/campaigns/{cid}/skip")
    def skip(cid: str):
        with state.lock:
            c = load(cid)
            lines, notes = act(actions.skip, c)
        return {"debrief": lines, "notes": notes}

    @app.post("/api/campaigns/{cid}/rollback")
    def rollback(cid: str, req: RollbackRequest):
        c = act(actions.rollback, cid, req.turn)
        return {"turn": c.turn}

    @app.post("/api/settings")
    def settings(req: Settings):
        state.auto_ingest = req.auto_ingest
        return {"auto_ingest": state.auto_ingest}

    # ------------------------------------------------------------------ front end
    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    @app.get("/")
    def index():
        return FileResponse(STATIC / "index.html")

    return app


def serve(host: str = "127.0.0.1", port: int = 8076, open_browser: bool = True,
          auto_ingest: bool = True) -> None:
    import webbrowser

    import uvicorn
    url = f"http://{host}:{port}/"
    print(f"ColdWarDynamicDCS web UI on {url}  (Ctrl+C to stop)")
    if not auto_ingest:
        print("(automatic ingest is off)")
    if open_browser:
        threading.Timer(1.0, webbrowser.open, args=(url,)).start()
    uvicorn.run(create_app(auto_ingest=auto_ingest), host=host, port=port, log_level="warning")
