"""Embed the CWDC Lua runtime into a pydcs Mission."""
from __future__ import annotations

from dcs.action import DoScript, DoScriptFile, EndMission
from dcs.condition import FlagIsTrue
from dcs.mission import Mission
from dcs.translation import String
from dcs.triggers import TriggerOnce, TriggerStart

from cwdc.dcs_paths import RUNTIME_LUA

END_FLAG = 9001


def _lua_str(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def lua_value(v) -> str:
    """Python value -> Lua literal (dicts with str keys, lists, str, numbers, bools)."""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return repr(v)
    if isinstance(v, str):
        return _lua_str(v)
    if isinstance(v, dict):
        return "{ " + ", ".join(f"{k} = {lua_value(x)}" for k, x in v.items()) + " }"
    return "{ " + ", ".join(lua_value(x) for x in v) + " }"


def config_lua(mission_id: str, tracked_groups: list[str], roe: str = "weapons_free",
               end_when: dict | None = None, tracked_statics: list[str] | None = None,
               intercept: dict | None = None, magic: dict | None = None,
               rescue: dict | None = None, fac: dict | None = None) -> str:
    groups = ", ".join(_lua_str(g) for g in tracked_groups)
    statics = ", ".join(_lua_str(s) for s in tracked_statics or [])
    lines = ["CWDC_CONFIG = {",
             f"  mission_id = {_lua_str(mission_id)},",
             f"  roe = {_lua_str(roe)},",
             f"  tracked_groups = {{ {groups} }},",
             f"  tracked_statics = {{ {statics} }},"]
    if intercept:
        fg = ", ".join(_lua_str(g) for g in intercept["focus"])
        tg = ", ".join(_lua_str(g) for g in intercept["targets"])
        lines.append(f"  intercept = {{ focus = {{ {fg} }}, targets = {{ {tg} }},"
                     f" range = {int(intercept.get('range', 3700))}, hold = {int(intercept.get('hold', 60))} }},")
    if magic:
        lines.append(f"  magic = {lua_value(magic)},")
    if rescue:
        lines.append(f"  rescue = {lua_value(rescue)},")
    if fac:
        lines.append(f"  fac = {lua_value(fac)},")
    if end_when:
        eg = ", ".join(_lua_str(g) for g in end_when["groups"])
        lines.append(f"  end_when = {{ groups = {{ {eg} }}, time_limit = {int(end_when['time_limit'])},"
                     f" flag = {int(end_when.get('flag', END_FLAG))} }},")
    lines.append("}")
    return "\n".join(lines) + "\n"


def inject_runtime(mission: Mission, mission_id: str, tracked_groups: list[str],
                   roe: str = "weapons_free", end_when: dict | None = None,
                   tracked_statics: list[str] | None = None, intercept: dict | None = None,
                   magic: dict | None = None, rescue: dict | None = None,
                   fac: dict | None = None) -> None:
    """Add a mission-start trigger that defines CWDC_CONFIG and runs the runtime.

    With `end_when` (bot missions) a second trigger ends the mission when the
    runtime raises the end flag.
    """
    trig = TriggerStart(comment="CWDC runtime")
    # DCS runs a_do_script's "text" field verbatim (the editor never resolves it through
    # the l10n dictionary), so the code must be inline: String(code), NOT mission.string(code).
    trig.add_action(DoScript(String(config_lua(mission_id, tracked_groups, roe, end_when,
                                               tracked_statics, intercept, magic, rescue, fac))))
    key = mission.map_resource.add_resource_file(RUNTIME_LUA)
    trig.add_action(DoScriptFile(key))
    mission.triggerrules.triggers.append(trig)
    if end_when:
        end = TriggerOnce(comment="CWDC auto end")
        end.add_condition(FlagIsTrue(int(end_when.get("flag", END_FLAG))))
        end.add_action(EndMission(text=String("CWDC bot mission complete")))
        mission.triggerrules.triggers.append(end)
