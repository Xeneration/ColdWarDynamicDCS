"""Locating the DCS Saved Games folder and installing the CWDC hook."""
from __future__ import annotations

import os
import shutil
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent
LUA_DIR = PACKAGE_DIR / "runtime" / "lua"
RUNTIME_LUA = LUA_DIR / "cwdc_runtime.lua"
HOOK_LUA = LUA_DIR / "CWDC-hook.lua"

_CANDIDATES = ("DCS", "DCS.openbeta", "DCS.release_server")


def saved_games_dcs() -> Path:
    """Return the DCS write directory (Saved Games\\DCS or a variant).

    Override with the CWDC_DCS_WRITEDIR environment variable.
    """
    override = os.environ.get("CWDC_DCS_WRITEDIR")
    if override:
        return Path(override)
    saved = Path(os.environ.get("USERPROFILE", Path.home())) / "Saved Games"
    for name in _CANDIDATES:
        p = saved / name
        if (p / "Config").is_dir():
            return p
    raise FileNotFoundError(f"No DCS folder found in {saved} (set CWDC_DCS_WRITEDIR)")


def missions_dir() -> Path:
    p = saved_games_dcs() / "Missions" / "ColdWarDynamic"
    p.mkdir(parents=True, exist_ok=True)
    return p


def results_dir() -> Path:
    return saved_games_dcs() / "CWDC" / "results"


def hook_path() -> Path:
    return saved_games_dcs() / "Scripts" / "Hooks" / HOOK_LUA.name


def install_hook() -> Path:
    """Copy (or update) the GUI hook into Saved Games\\DCS\\Scripts\\Hooks."""
    dest = hook_path()
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(HOOK_LUA, dest)
    results_dir().mkdir(parents=True, exist_ok=True)
    return dest


def hook_is_current() -> bool:
    dest = hook_path()
    return dest.is_file() and dest.read_bytes() == HOOK_LUA.read_bytes()
