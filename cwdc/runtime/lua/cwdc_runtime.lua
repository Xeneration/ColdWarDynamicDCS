--[[
  ColdWarDynamicDCS - mission runtime
  Runs inside the (sanitised) DCS mission scripting environment.
  Embedded into every generated .miz by the campaign generator, which
  defines CWDC_CONFIG before this file is executed:

    CWDC_CONFIG = {
      mission_id = "c001-t004",
      tracked_groups = { "RED-SAM-1", "BLUE-Pkg1-Strike", ... },
      roe = "weapons_free" | "weapons_tight",
    }

  It never touches the file system. Data leaves the mission as tagged log lines
  (env.info) of the form

      [CWDC] <mission_id> <KIND> <msg_seq> <part>/<parts> <json chunk>

  KIND is START, EV (one event), SNAP (tracked-group snapshot) or END.
  The GUI hook reads them back with Sim.getLogHistory() and writes the results
  file; the campaign manager can also rebuild them from dcs.log as a fallback.
]]

CWDC = CWDC or {}
CWDC.version = 2
CWDC.config = CWDC_CONFIG or {}
CWDC.mission_id = string.gsub(CWDC.config.mission_id or "unknown", "%s", "_")
CWDC.events = {}
CWDC.seq = 0
CWDC.msg_seq = 0
CWDC.ended = false
CWDC.chunk_size = 1500
CWDC.snapshot_interval = 30

local function log(msg)
  env.info("[CWDC-log] " .. tostring(msg))
end

------------------------------------------------------------------------
-- Minimal JSON encoder (no json lib available in the mission env)
------------------------------------------------------------------------
local function json_escape(s)
  s = string.gsub(s, '[%c"\\]', function(c)
    local map = { ['"'] = '\\"', ['\\'] = '\\\\', ['\n'] = '\\n', ['\r'] = '\\r', ['\t'] = '\\t' }
    return map[c] or string.format("\\u%04x", string.byte(c))
  end)
  return s
end

local function is_array(t)
  local n = 0
  for k, _ in pairs(t) do
    if type(k) ~= "number" then return false end
    n = n + 1
  end
  for i = 1, n do
    if t[i] == nil then return false end
  end
  return true
end

local function encode(v)
  local tv = type(v)
  if tv == "nil" then return "null"
  elseif tv == "boolean" then return v and "true" or "false"
  elseif tv == "number" then
    if v ~= v or v == math.huge or v == -math.huge then return "null" end
    return string.format("%.10g", v)
  elseif tv == "string" then return '"' .. json_escape(v) .. '"'
  elseif tv == "table" then
    local parts = {}
    if next(v) == nil then return "[]" end
    if is_array(v) then
      for i = 1, #v do parts[#parts + 1] = encode(v[i]) end
      return "[" .. table.concat(parts, ",") .. "]"
    end
    for k, val in pairs(v) do
      parts[#parts + 1] = '"' .. json_escape(tostring(k)) .. '":' .. encode(val)
    end
    return "{" .. table.concat(parts, ",") .. "}"
  end
  return "null"
end
CWDC.encode = encode

-- Emit a table as one or more tagged log lines.
function CWDC.emit(kind, tbl)
  local s = encode(tbl)
  CWDC.msg_seq = CWDC.msg_seq + 1
  local size = CWDC.chunk_size
  local parts = math.max(1, math.ceil(#s / size))
  for i = 1, parts do
    env.info(string.format("[CWDC] %s %s %d %d/%d %s", CWDC.mission_id, kind, CWDC.msg_seq,
      i, parts, string.sub(s, (i - 1) * size + 1, i * size)))
  end
end

------------------------------------------------------------------------
-- Helpers
------------------------------------------------------------------------
local function safe(f, ...)
  local ok, res = pcall(f, ...)
  if ok then return res end
  return nil
end

local function pos_of(obj)
  local p = safe(function() return obj:getPoint() end)
  if not p then return nil end
  local lat, lon, alt = coord.LOtoLL(p)
  return { x = p.x, z = p.z, alt = p.y, lat = lat, lon = lon }
end

local function object_info(obj)
  if not obj then return nil end
  local info = {}
  info.name = safe(function() return obj:getName() end)
  info.type = safe(function() return obj:getTypeName() end)
  info.coalition = safe(function() return obj:getCoalition() end)
  info.category = safe(function() return obj:getCategory() end)
  if Object.getCategory(obj) == Object.Category.UNIT then
    info.player = safe(function() return obj:getPlayerName() end)
    info.group = safe(function() return obj:getGroup():getName() end)
    info.life = safe(function() return obj:getLife() end)
    info.life0 = safe(function() return obj:getLife0() end)
  end
  info.pos = pos_of(obj)
  return info
end

local EVENT_NAMES = {}
for name, id in pairs(world.event) do EVENT_NAMES[id] = name end

-- Events worth recording. Everything else is ignored to keep the log small.
local RECORD = {
  [world.event.S_EVENT_SHOT] = true,
  [world.event.S_EVENT_HIT] = true,
  [world.event.S_EVENT_TAKEOFF] = true,
  [world.event.S_EVENT_LAND] = true,
  [world.event.S_EVENT_CRASH] = true,
  [world.event.S_EVENT_EJECTION] = true,
  [world.event.S_EVENT_DEAD] = true,
  [world.event.S_EVENT_PILOT_DEAD] = true,
  [world.event.S_EVENT_BIRTH] = true,
  [world.event.S_EVENT_ENGINE_SHUTDOWN] = true,
  [world.event.S_EVENT_KILL] = true,
  [world.event.S_EVENT_UNIT_LOST] = true,
}
if world.event.S_EVENT_LANDING_AFTER_EJECTION then
  RECORD[world.event.S_EVENT_LANDING_AFTER_EJECTION] = true
end
if world.event.S_EVENT_RUNWAY_TAKEOFF then
  RECORD[world.event.S_EVENT_RUNWAY_TAKEOFF] = true
  RECORD[world.event.S_EVENT_RUNWAY_TOUCH] = true
end

------------------------------------------------------------------------
-- Event handler
------------------------------------------------------------------------
local handler = {}
function handler:onEvent(e)
  if not e then return end
  if e.id == world.event.S_EVENT_MISSION_END then
    pcall(CWDC.finish)
    return
  end
  if not RECORD[e.id] then return end
  -- Hits on scenery (trees, fences, approach lights) are pure noise. Scenery KILL/DEAD
  -- events are kept: bridges and other map objects can be campaign targets.
  if e.id == world.event.S_EVENT_HIT and e.target and Object.Category.SCENERY
      and safe(function() return Object.getCategory(e.target) end) == Object.Category.SCENERY then
    return
  end
  local ok, err = pcall(function()
    CWDC.seq = CWDC.seq + 1
    local rec = {
      seq = CWDC.seq,
      t = e.time or timer.getTime(),
      id = e.id,
      event = EVENT_NAMES[e.id] or tostring(e.id),
      initiator = object_info(e.initiator),
      target = object_info(e.target),
    }
    if e.weapon then
      rec.weapon = safe(function() return e.weapon:getTypeName() end)
    elseif e.weapon_name then
      rec.weapon = e.weapon_name
    end
    if e.place then
      rec.place = safe(function() return e.place:getName() end)
    end
    -- ROE watch: weapon release by a player while weapons tight
    if e.id == world.event.S_EVENT_SHOT and CWDC.config.roe == "weapons_tight"
        and rec.initiator and rec.initiator.player then
      rec.roe_violation = true
      trigger.action.outText("ROE VIOLATION: weapons release while WEAPONS TIGHT has been logged.", 15)
    end
    CWDC.events[#CWDC.events + 1] = rec
    CWDC.emit("EV", rec)
  end)
  if not ok then log("event handler error: " .. tostring(err)) end
end
world.addEventHandler(handler)

------------------------------------------------------------------------
-- State snapshot of tracked groups
------------------------------------------------------------------------
local function snapshot_groups()
  local out = {}
  for _, gname in ipairs(CWDC.config.tracked_groups or {}) do
    local g = Group.getByName(gname)
    local entry = { name = gname, exists = false, units = {} }
    if g and g:isExist() then
      entry.exists = true
      for _, u in ipairs(g:getUnits() or {}) do
        if u and u:isExist() then
          entry.units[#entry.units + 1] = {
            name = u:getName(),
            type = u:getTypeName(),
            life = u:getLife(),
            life0 = u:getLife0(),
            in_air = u:inAir(),
            pos = pos_of(u),
            fuel = safe(function() return u:getFuel() end),
          }
        end
      end
    end
    out[#out + 1] = entry
  end
  return out
end

function CWDC.snapshot()
  return { model_time = timer.getTime(), abs_time = timer.getAbsTime(), groups = snapshot_groups() }
end

function CWDC.finish()
  if CWDC.ended then return end
  CWDC.ended = true
  CWDC.emit("SNAP", CWDC.snapshot())
  CWDC.emit("END", { model_time = timer.getTime(), events = #CWDC.events })
end

-- Full state as one JSON string (debugging / tests; not used by the hook).
function CWDC_export()
  local ok, res = pcall(function()
    return encode({
      schema = 1,
      mission_id = CWDC.mission_id,
      model_time = timer.getTime(),
      abs_time = timer.getAbsTime(),
      events = CWDC.events,
      groups = snapshot_groups(),
      ended = CWDC.ended,
    })
  end)
  if ok then return res end
  log("export error: " .. tostring(res))
  return encode({ schema = 1, mission_id = CWDC.mission_id, error = tostring(res) })
end

------------------------------------------------------------------------
-- Start-up
------------------------------------------------------------------------
CWDC.emit("START", {
  schema = 2,
  runtime_version = CWDC.version,
  mission_id = CWDC.mission_id,
  roe = CWDC.config.roe,
  tracked_groups = CWDC.config.tracked_groups or {},
  model_time = timer.getTime(),
  abs_time = timer.getAbsTime(),
})
CWDC.emit("SNAP", CWDC.snapshot())

timer.scheduleFunction(function(_, t)
  if CWDC.ended then return nil end
  local ok, err = pcall(function() CWDC.emit("SNAP", CWDC.snapshot()) end)
  if not ok then log("snapshot error: " .. tostring(err)) end
  return t + CWDC.snapshot_interval
end, nil, timer.getTime() + CWDC.snapshot_interval)

------------------------------------------------------------------------
-- Bot missions: end automatically (no player to exit the mission)
--   CWDC_CONFIG.end_when = { groups = {...}, time_limit = s, flag = n }
-- When every aircraft of those groups has landed or been lost, or at the
-- time limit, the runtime sets `flag`; a mission trigger then ends it.
------------------------------------------------------------------------
CWDC.end_pending = nil
CWDC.end_requested = false

function CWDC.request_end(reason)
  if CWDC.end_requested then return end
  CWDC.end_requested = true
  local w = CWDC.config.end_when or {}
  CWDC.emit("NOTE", { model_time = timer.getTime(), note = "auto end: " .. reason })
  trigger.action.outText("CWDC: " .. reason .. " - mission ends in 30 s.", 30)
  timer.scheduleFunction(function()
    trigger.action.setUserFlag(tostring(w.flag or 9001), true)
    return nil
  end, nil, timer.getTime() + 30)
end

local END_EVENTS = {
  [world.event.S_EVENT_LAND] = true, [world.event.S_EVENT_UNIT_LOST] = true,
  [world.event.S_EVENT_CRASH] = true, [world.event.S_EVENT_DEAD] = true,
  [world.event.S_EVENT_EJECTION] = true, [world.event.S_EVENT_PILOT_DEAD] = true,
}

if CWDC.config.end_when then
  CWDC.end_pending = {}
  for _, gname in ipairs(CWDC.config.end_when.groups or {}) do
    local g = Group.getByName(gname)
    if g then
      for _, u in ipairs(g:getUnits() or {}) do CWDC.end_pending[u:getName()] = true end
    end
  end
  local end_handler = {}
  function end_handler:onEvent(e)
    if not e or not END_EVENTS[e.id] or not e.initiator then return end
    local ok, name = pcall(function() return e.initiator:getName() end)
    if ok and name and CWDC.end_pending[name] then
      CWDC.end_pending[name] = nil
      if next(CWDC.end_pending) == nil then CWDC.request_end("focus flight complete") end
    end
  end
  world.addEventHandler(end_handler)
  local limit = CWDC.config.end_when.time_limit
  if limit then
    timer.scheduleFunction(function(_, t)
      if CWDC.end_requested then return nil end
      if t >= limit then CWDC.request_end("time limit reached") return nil end
      return t + 60
    end, nil, timer.getTime() + 60)
  end
end

if CWDC.mission_id == "unknown" then
  log("WARNING: CWDC_CONFIG missing - mission_id unknown")
end
log("runtime loaded, mission_id=" .. CWDC.mission_id)
