"""Embed the CWDC Lua runtime into a pydcs Mission."""
from __future__ import annotations

from dcs.action import DoScript, DoScriptFile
from dcs.mission import Mission
from dcs.translation import String
from dcs.triggers import TriggerStart

from cwdc.dcs_paths import RUNTIME_LUA


def _lua_str(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def config_lua(mission_id: str, tracked_groups: list[str], roe: str = "weapons_free") -> str:
    groups = ", ".join(_lua_str(g) for g in tracked_groups)
    return (
        "CWDC_CONFIG = {\n"
        f"  mission_id = {_lua_str(mission_id)},\n"
        f"  roe = {_lua_str(roe)},\n"
        f"  tracked_groups = {{ {groups} }},\n"
        "}\n"
    )


def inject_runtime(mission: Mission, mission_id: str, tracked_groups: list[str],
                   roe: str = "weapons_free") -> None:
    """Add a mission-start trigger that defines CWDC_CONFIG and runs the runtime."""
    trig = TriggerStart(comment="CWDC runtime")
    # DCS runs a_do_script's "text" field verbatim (the editor never resolves it through
    # the l10n dictionary), so the code must be inline: String(code), NOT mission.string(code).
    trig.add_action(DoScript(String(config_lua(mission_id, tracked_groups, roe))))
    key = mission.map_resource.add_resource_file(RUNTIME_LUA)
    trig.add_action(DoScriptFile(key))
    mission.triggerrules.triggers.append(trig)
