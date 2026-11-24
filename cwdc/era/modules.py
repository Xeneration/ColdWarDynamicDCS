"""Which aircraft can the player fly? Read from the installed modules themselves.

Every DCS aircraft module declares its flyable types in its entry.lua with
make_flyable('TYPE', ...) (MAC_flyable for Flaming Cliffs-style ones). We scan
DCS World\\Mods\\aircraft (official modules) and Saved Games\\DCS\\Mods\\aircraft (mods such as
the A-4E-C), plus Mods\\tech for asset packs (WWII Units, Supercarrier).

Read-only: nothing in either folder is written.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

_FLYABLE = re.compile(r"\b(?:make_flyable|MAC_flyable)\s*\(\s*['\"]([^'\"]+)['\"]")
DEFAULT_INSTALL = Path(r"C:\Program Files\Eagle Dynamics\DCS World")


@dataclass
class Installed:
    flyable: dict[str, str] = field(default_factory=dict)      # DCS type -> module folder
    mods: set[str] = field(default_factory=set)                # module folders that are user mods
    tech: set[str] = field(default_factory=set)                # asset packs (Mods\tech folder names)
    terrains: set[str] = field(default_factory=set)            # Mods\terrains folder names
    module_dirs: dict[str, Path] = field(default_factory=dict)  # module folder -> path

    def owns(self, dcs_type: str) -> bool:
        return dcs_type in self.flyable

    def is_mod(self, dcs_type: str) -> bool:
        return self.flyable.get(dcs_type) in self.mods


def install_dir() -> Path:
    """DCS install folder (override with CWDC_DCS_INSTALL)."""
    env = os.environ.get("CWDC_DCS_INSTALL")
    if env:
        return Path(env)
    try:
        from cwdc.soak.runner import dcs_exe
        return dcs_exe().parent.parent
    except Exception:
        return DEFAULT_INSTALL


def _scan_aircraft(root: Path, inst: Installed, mod: bool) -> None:
    if not root.is_dir():
        return
    for d in sorted(p for p in root.iterdir() if p.is_dir()):
        entry = d / "entry.lua"
        if not entry.is_file():
            continue
        types = _FLYABLE.findall(entry.read_text(encoding="utf-8", errors="replace"))
        if not types:
            continue
        inst.module_dirs[d.name] = d
        if mod:
            inst.mods.add(d.name)
        for t in types:
            inst.flyable.setdefault(t, d.name)


def detect(install: Path | None = None, saved_games: Path | None = None) -> Installed:
    install = install or install_dir()
    if saved_games is None:
        try:
            from cwdc.dcs_paths import saved_games_dcs
            saved_games = saved_games_dcs()
        except FileNotFoundError:
            saved_games = None
    inst = Installed()
    _scan_aircraft(install / "Mods" / "aircraft", inst, mod=False)
    if saved_games is not None:
        _scan_aircraft(saved_games / "Mods" / "aircraft", inst, mod=True)
    for sub, dest in (("tech", inst.tech), ("terrains", inst.terrains)):
        p = install / "Mods" / sub
        if p.is_dir():
            dest.update(x.name for x in p.iterdir() if x.is_dir())
    return inst
