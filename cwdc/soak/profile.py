"""Soak instance profiles: Saved Games\\DCS.cwdc_soak<N>, separate from the player's DCS folder.

Each profile is a DCS write directory started with
    DCS.exe --server --norender -w DCS.cwdc_soak<N>
It holds a private server config, the results hook, the soak hook and the lobby mission.
"""
from __future__ import annotations

import json
import os
import shutil
import time
from pathlib import Path

from cwdc import dcs_paths

SOAK_HOOK_LUA = dcs_paths.LUA_DIR / "CWDC-soak-hook.lua"
LOBBY = "CWDC_Lobby.miz"
BASE_PORT = 10308


def saved_games() -> Path:
    return Path(os.environ.get("USERPROFILE", Path.home())) / "Saved Games"


def profile_name(instance: int) -> str:
    return f"DCS.cwdc_soak{instance}"


def profile_dir(instance: int, root: Path | None = None) -> Path:
    return (root or saved_games()) / profile_name(instance)


def soak_dir(profile: Path) -> Path:
    return profile / "CWDC" / "soak"


def _lua(v) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return repr(v)
    if isinstance(v, str):
        return '"' + v.replace("\\", "\\\\").replace('"', '\\"') + '"'
    if isinstance(v, list):
        return "{" + ", ".join(f"[{i}] = {_lua(x)}" for i, x in enumerate(v, 1)) + "}"
    if isinstance(v, dict):
        return "{" + ", ".join(f'["{k}"] = {_lua(x)}' for k, x in v.items()) + "}"
    raise TypeError(type(v))


def server_settings(instance: int, lobby: Path) -> str:
    cfg = {
        "name": f"CWDC soak {instance}", "description": "ColdWarDynamicDCS unattended test",
        "password": f"cwdc-soak-{os.getpid()}-{instance}", "isPublic": False, "maxPlayers": 1,
        "port": BASE_PORT + instance, "bind_address": "127.0.0.1", "mode": 0,
        "missionList": [str(lobby)], "listStartIndex": 1, "listLoop": False, "listShuffle": False,
        "require_pure_textures": False, "require_pure_clients": False, "require_pure_models": False,
        "require_pure_scripts": False,
        "advanced": {"resume_mode": 2, "allow_change_skin": False, "event_Connect": False,
                     "event_Ejecting": False, "event_Kill": False, "event_Takeoff": False,
                     "event_Role": False, "event_Crash": False, "allow_ownship_export": False,
                     "allow_object_export": False, "allow_sensor_export": False,
                     "disable_events": False, "pause_on_load": False, "pause_without_clients": False},
    }
    return "cfg = " + _lua(cfg) + "\n"


def build_lobby(path: Path) -> Path:
    import logging
    from dcs import Mission
    from datetime import datetime
    from cwdc import theater as T
    from cwdc.missiongen import coalitions
    logging.getLogger().setLevel(logging.CRITICAL)
    m = Mission(T.terrain())
    coalitions.setup(m)
    m.start_time = datetime(1976, 8, 14, 12, 0)
    m.set_description_text("ColdWarDynamicDCS soak lobby: waiting for the next mission.")
    path.parent.mkdir(parents=True, exist_ok=True)
    m.save(str(path))
    return path


def prepare(instance: int, root: Path | None = None) -> Path:
    """Create or refresh the soak profile. Returns its directory."""
    p = profile_dir(instance, root)
    hooks = p / "Scripts" / "Hooks"
    hooks.mkdir(parents=True, exist_ok=True)
    (p / "Config").mkdir(parents=True, exist_ok=True)
    shutil.copyfile(dcs_paths.HOOK_LUA, hooks / dcs_paths.HOOK_LUA.name)
    shutil.copyfile(SOAK_HOOK_LUA, hooks / SOAK_HOOK_LUA.name)
    lobby = p / "Missions" / LOBBY
    if not lobby.is_file():
        build_lobby(lobby)
    (p / "Config" / "serverSettings.lua").write_text(server_settings(instance, lobby), encoding="utf-8")
    sd = soak_dir(p)
    sd.mkdir(parents=True, exist_ok=True)
    for f in ("command.json", "heartbeat.json"):
        (sd / f).unlink(missing_ok=True)
    (p / "CWDC" / "results").mkdir(parents=True, exist_ok=True)
    return p


class Control:
    """The file channel to one instance's soak hook."""

    def __init__(self, profile: Path):
        self.dir = soak_dir(profile)
        self.seq = int(time.time())              # monotonic across restarts of the runner

    def send(self, **cmd) -> int:
        self.seq += 1
        self.dir.mkdir(parents=True, exist_ok=True)
        tmp = self.dir / "command.json.tmp"
        tmp.write_text(json.dumps({"seq": self.seq, **cmd}), encoding="utf-8")
        os.replace(tmp, self.dir / "command.json")
        return self.seq

    def heartbeat(self) -> dict | None:
        try:
            return json.loads((self.dir / "heartbeat.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
