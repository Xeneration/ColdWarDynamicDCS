"""Mock DCS Lua environments (mission scripting env + GUI hook env) using LuaJIT via lupa.

DCS runs Lua 5.1; lupa.luajit21 is 5.1-compatible, so syntax and semantics match.
The two environments share a Python list standing in for the DCS log: the mission's
env.info() appends to it and the hook's Sim.getLogHistory() reads from it.
"""
from __future__ import annotations

import os
from pathlib import Path

from lupa import luajit21

from cwdc.dcs_paths import HOOK_LUA, RUNTIME_LUA

MISSION_ENV_MOCK = r"""
MSGS, GROUPS, T = {}, {}, 0
env = { info = function(s) end }
world = {
  event = { S_EVENT_SHOT = 1, S_EVENT_HIT = 2, S_EVENT_TAKEOFF = 3, S_EVENT_LAND = 4,
            S_EVENT_CRASH = 5, S_EVENT_EJECTION = 6, S_EVENT_DEAD = 8, S_EVENT_PILOT_DEAD = 9,
            S_EVENT_MISSION_END = 12, S_EVENT_BIRTH = 15, S_EVENT_ENGINE_SHUTDOWN = 18,
            S_EVENT_KILL = 28, S_EVENT_UNIT_LOST = 30, S_EVENT_LANDING_AFTER_EJECTION = 31,
            S_EVENT_RUNWAY_TAKEOFF = 54, S_EVENT_RUNWAY_TOUCH = 55 },
  addEventHandler = function(h)
    HANDLERS = HANDLERS or {}
    HANDLERS[#HANDLERS + 1] = h
    HANDLER = HANDLERS[1]          -- the main CWDC recorder
  end,
}
-- deliver an event to every registered handler, like DCS does
function fire(e) for _, h in ipairs(HANDLERS) do h:onEvent(e) end end
coord = { LOtoLL = function(p) return 50 + p.x / 1e6, 7 + p.z / 1e6, p.y end }
SCHEDS, FLAGS = {}, {}
timer = { getTime = function() return T end, getAbsTime = function() return 28800 + T end,
          scheduleFunction = function(f, a, t)
            SCHED = { f = f, a = a, t = t }
            SCHEDS[#SCHEDS + 1] = SCHED
          end }
trigger = { action = { outText = function(t, d) MSGS[#MSGS + 1] = t end,
                       setUserFlag = function(f, v) FLAGS[f] = v end } }

-- advance mission time to `now`, running every scheduled function that is due
function run_due(now)
  T = now
  local ran = true
  while ran do
    ran = false
    for _, s in ipairs(SCHEDS) do
      if s.t and s.t <= now then
        s.t = s.f(s.a, s.t)
        ran = true
      end
    end
  end
end
GMSGS, MENU, DETECTED, NEXT_GID = {}, {}, {}, 0
trigger.action.outTextForGroup = function(gid, t, d) GMSGS[#GMSGS + 1] = { gid = gid, text = t } end
missionCommands = {
  addSubMenuForGroup = function(gid, name) MENU[#MENU + 1] = { gid = gid, name = name }; return name end,
  addCommandForGroup = function(gid, name, parent, fn, arg)
    MENU[#MENU + 1] = { gid = gid, name = name, fn = fn, arg = arg } end,
}
Controller = { Detection = { RADAR = 4 } }
Object = { Category = { UNIT = 1, WEAPON = 2, STATIC = 3, BASE = 4, SCENERY = 5 } }
function Object.getCategory(o) return o:getCategory() end

function run_scheduled()
  local nxt = SCHED.f(SCHED.a, T)
  SCHED.t = nxt
  return nxt
end

function mkunit(name, typ, coal, player, group, x, z)
  local u = { _alive = true }
  function u:getName() return name end
  function u:getTypeName() return typ end
  function u:getCoalition() return coal end
  function u:getCategory() return 1 end
  function u:getPlayerName() return player end
  function u:getGroup() return { getName = function() return group end } end
  function u:getLife() return self._alive and 10 or 0 end
  function u:getLife0() return 10 end
  function u:getPoint() return { x = x or 0, y = u._y or 1000, z = z or 0 } end
  function u:isExist() return self._alive end
  function u:inAir() return self._air or false end
  function u:getVelocity() return self._vel or { x = 0, y = 0, z = 0 } end
  function u:setPoint(nx, ny, nz) x, z = nx, nz; self._y = ny end
  function u:getFuel() return 0.6 end
  return u
end

function mkgroup(name, units)
  local g = { _units = units }
  NEXT_GID = NEXT_GID + 1
  local gid = NEXT_GID
  function g:getID() return gid end
  function g:getController()
    return { getDetectedTargets = function(self, kind) return DETECTED[name] or {} end }
  end
  function g:isExist() return true end
  function g:getUnits() return self._units end
  GROUPS[name] = g
  return g
end
Group = { getByName = function(n) return GROUPS[n] end }

STATICS = {}
function mkstatic(name, life)
  local s = { _life = life or 10 }
  function s:isExist() return true end
  function s:getLife() return self._life end
  function s:getName() return name end
  STATICS[name] = s
  return s
end
StaticObject = { getByName = function(n) return STATICS[n] end }

function mkweapon(typ) return { getTypeName = function() return typ end } end
"""


def mission_runtime(config_lua: str, logbuf: list[str] | None = None,
                    pre_runtime: str = "") -> luajit21.LuaRuntime:
    """Mission env. Every env.info() line is appended to `logbuf`."""
    lua = luajit21.LuaRuntime(unpack_returned_tuples=True)
    lua.execute(MISSION_ENV_MOCK)
    buf = logbuf if logbuf is not None else []
    lua.globals().env.info = lambda s: buf.append(s)
    lua.execute(config_lua)
    lua.execute(pre_runtime)
    lua.execute(RUNTIME_LUA.read_text(encoding="utf-8"))
    return lua


def hook_runtime(writedir: Path, logbuf: list[str] | None, overlap: bool = True):
    """GUI hook env reading `logbuf` through Sim.getLogHistory.

    With overlap=True each read re-delivers the previous last line, to check de-duplication.
    """
    lua = luajit21.LuaRuntime(unpack_returned_tuples=True)
    logs: list[str] = []

    def get_log_history(start):
        start = int(start)
        if overlap:
            start = max(0, start - 1)
        entries = [lua.table_from([0, "INFO", "SCRIPTING", m]) for m in logbuf[start:]]
        return lua.table_from(entries), len(logbuf)

    def mkdir(path):
        os.makedirs(path, exist_ok=True)
        return True

    g = lua.globals()
    g.PY_GETLOG = get_log_history if logbuf is not None else None
    g.PY_LOG = lambda s: logs.append(s)
    g.PY_MKDIR = mkdir
    g.PY_WRITEDIR = str(writedir) + os.sep
    lua.execute(r"""
      REALTIME = 0
      net = { log = PY_LOG }
      lfs = { writedir = function() return PY_WRITEDIR end, mkdir = PY_MKDIR }
      Sim = { setUserCallbacks = function(cb) CALLBACKS = cb end,
              getRealTime = function() return REALTIME end,
              getLogHistory = PY_GETLOG }
    """)
    lua.execute(HOOK_LUA.read_text(encoding="utf-8"))
    return lua, logs
