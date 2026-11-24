--[[
  ColdWarDynamicDCS - mission runtime
  Runs inside the (sanitised) DCS mission scripting environment.
  Embedded into every generated .miz by the campaign generator, which
  defines CWDC_CONFIG before this file is executed:

    CWDC_CONFIG = {
      mission_id = "c001-t004",
      tracked_groups = { "RED-SAM-1", "BLUE-Pkg1-Strike", ... },
      roe = "weapons_free" | "weapons_tight",
      intercept = { focus = {...}, targets = {...}, range = m, hold = s },   -- crisis QRA
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
    -- ROE watch (weapons tight): the first Warsaw Pact shot is a hostile act that clears NATO
    -- to return fire; a player firing before that is an ROE violation.
    if e.id == world.event.S_EVENT_SHOT and CWDC.config.roe == "weapons_tight" and rec.initiator then
      if rec.initiator.coalition == 1 and not CWDC.hostile_act then
        CWDC.hostile_act = true
        rec.hostile_act = true
        trigger.action.outText("HOSTILE ACT: Warsaw Pact aircraft have opened fire. "
          .. "You are cleared to return fire.", 20)
      elseif rec.initiator.player and not CWDC.hostile_act then
        rec.roe_violation = true
        trigger.action.outText("ROE VIOLATION: weapons release while WEAPONS TIGHT has been logged.", 15)
      end
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

local function snapshot_statics()
  local out = {}
  for _, name in ipairs(CWDC.config.tracked_statics or {}) do
    local s = StaticObject and StaticObject.getByName(name)
    local exists = s ~= nil and safe(function() return s:isExist() end) == true
    out[#out + 1] = { name = name, exists = exists,
                      life = exists and (safe(function() return s:getLife() end) or 0) or 0 }
  end
  return out
end

function CWDC.snapshot()
  return { model_time = timer.getTime(), abs_time = timer.getAbsTime(), groups = snapshot_groups(),
           statics = snapshot_statics() }
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
-- Crisis QRA: an intercept counts once a focus aircraft has stayed within
-- `range` m of a target (probe) aircraft for `hold` s. Recorded as a
-- CWDC_INTERCEPT event (initiator = interceptor, target = probe).
------------------------------------------------------------------------
CWDC.intercepted = {}
local function units_of(gname)
  local out = {}
  local g = Group.getByName(gname)
  if g and g:isExist() then
    for _, u in ipairs(g:getUnits() or {}) do
      if u and u:isExist() then out[#out + 1] = u end
    end
  end
  return out
end

function CWDC.intercept_check(step)
  local ic = CWDC.config.intercept
  if not ic then return end
  local mine = {}
  for _, gname in ipairs(ic.focus or {}) do
    for _, u in ipairs(units_of(gname)) do mine[#mine + 1] = u end
  end
  CWDC.intercept_held = CWDC.intercept_held or {}
  for _, tname in ipairs(ic.targets or {}) do
    if not CWDC.intercepted[tname] then
      local pair = nil
      for _, tu in ipairs(units_of(tname)) do
        local tp = tu:getPoint()
        for _, mu in ipairs(mine) do
          local mp = mu:getPoint()
          local d = math.sqrt((tp.x - mp.x) ^ 2 + (tp.y - mp.y) ^ 2 + (tp.z - mp.z) ^ 2)
          if d <= (ic.range or 3700) then pair = { mu, tu } end
        end
      end
      local held = pair and (CWDC.intercept_held[tname] or 0) + step or 0
      CWDC.intercept_held[tname] = held
      if pair and held >= (ic.hold or 60) then
        CWDC.intercepted[tname] = true
        CWDC.seq = CWDC.seq + 1
        local rec = { seq = CWDC.seq, t = timer.getTime(), id = -1, event = "CWDC_INTERCEPT",
                      initiator = object_info(pair[1]), target = object_info(pair[2]) }
        CWDC.events[#CWDC.events + 1] = rec
        CWDC.emit("EV", rec)
        trigger.action.outText("Intercept complete: " .. tostring(rec.target.type or tname)
          .. " identified and shadowed.", 15)
      end
    end
  end
end

if CWDC.config.intercept then
  timer.scheduleFunction(function(_, t)
    if CWDC.ended then return nil end
    local ok, err = pcall(CWDC.intercept_check, 5)
    if not ok then log("intercept error: " .. tostring(err)) end
    return t + 5
  end, nil, timer.getTime() + 5)
end

------------------------------------------------------------------------
-- RESCAP: the survivor pops green smoke when the rescue helicopter is
-- within 5 km; a pickup counts once the helicopter has been on the ground
-- within `radius` m of the survivor for `hold` s (CWDC_PICKUP event).
--   CWDC_CONFIG.rescue = { helo = "...", x = .., z = .., radius = 300, hold = 20 }
------------------------------------------------------------------------
CWDC.rescue_state = { smoke = false, held = 0, done = false }

function CWDC.rescue_check(step)
  local rc = CWDC.config.rescue
  local st = CWDC.rescue_state
  if not rc or st.done then return end
  local g = Group.getByName(rc.helo)
  if not g or not g:isExist() then return end
  for _, u in ipairs(g:getUnits() or {}) do
    if u and u:isExist() then
      local p = u:getPoint()
      local d = math.sqrt((p.x - rc.x) ^ 2 + (p.z - rc.z) ^ 2)
      if d <= 5000 and not st.smoke then
        st.smoke = true
        local y = 0
        if land and land.getHeight then
          y = safe(function() return land.getHeight({ x = rc.x, y = rc.z }) end) or 0
        end
        if trigger.action.smoke then
          trigger.action.smoke({ x = rc.x, y = y, z = rc.z }, (trigger.smokeColor or {}).Green or 0)
        end
        trigger.action.outText("Survivor: green smoke is out - I hear the helicopter!", 15)
      end
      if d <= (rc.radius or 300) and not u:inAir() then
        st.held = st.held + step
      else
        st.held = 0
      end
      if st.held >= (rc.hold or 20) then
        st.done = true
        CWDC.seq = CWDC.seq + 1
        local rec = { seq = CWDC.seq, t = timer.getTime(), id = -2, event = "CWDC_PICKUP",
                      initiator = object_info(u) }
        CWDC.events[#CWDC.events + 1] = rec
        CWDC.emit("EV", rec)
        trigger.action.outText("Dustoff 1: survivor aboard, heading home!", 20)
      end
      return
    end
  end
end

if CWDC.config.rescue then
  timer.scheduleFunction(function(_, t)
    if CWDC.ended then return nil end
    local ok, err = pcall(CWDC.rescue_check, 5)
    if not ok then log("rescue error: " .. tostring(err)) end
    return t + 5
  end, nil, timer.getTime() + 5)
end

------------------------------------------------------------------------
-- FAC: an airborne forward air controller marks the CAS target with white
-- smoke (FAC_OFFSET m off it) once the flight is within `range`, and talks
-- it on; marks again every `interval` s while the target lives. Players can
-- ask for a new mark on the F10 menu.
--   CWDC_CONFIG.fac = { callsign, target = group, focus = {groups}, range, offset, interval, menu }
------------------------------------------------------------------------
CWDC.fac_state = { checked_in = false, last_mark = -1e9 }
local DIRS = { "north", "north-east", "east", "south-east", "south", "south-west", "west", "north-west" }

local function fac_target()
  local g = Group.getByName((CWDC.config.fac or {}).target or "")
  if not g or not g:isExist() then return nil end
  for _, u in ipairs(g:getUnits() or {}) do
    if u and u:isExist() then return u, #g:getUnits() end
  end
end

function CWDC.fac_mark(forced)
  local fc = CWDC.config.fac
  local tgt, n = fac_target()
  if not fc or not tgt then return false end
  local tp = tgt:getPoint()
  local ang = math.rad((math.floor(timer.getTime()) * 47) % 360)      -- varies mark to mark
  local off = fc.offset or 400
  local sp = { x = tp.x + math.cos(ang) * off, y = tp.y, z = tp.z + math.sin(ang) * off }
  if trigger.action.smoke then
    trigger.action.smoke(sp, (trigger.smokeColor or {}).White or 2)
  end
  -- direction from the smoke to the target
  local b = math.deg(math.atan2(tp.z - sp.z, tp.x - sp.x))
  if b < 0 then b = b + 360 end
  local dir = DIRS[math.floor((b + 22.5) / 45) % 8 + 1]
  CWDC.fac_state.last_mark = timer.getTime()
  trigger.action.outText(string.format("%s: %s. Target is %d vehicle%s, %d metres %s of the smoke. "
      .. "Cleared hot.", fc.callsign or "FAC", forced and "New mark, white smoke" or "Marking with white smoke",
      n, n == 1 and "" or "s", off, dir), 25)
  return true
end

function CWDC.fac_check()
  local fc = CWDC.config.fac
  local tgt = fac_target()
  if not fc or not tgt then return end
  local tp = tgt:getPoint()
  local close = false
  for _, gname in ipairs(fc.focus or {}) do
    local g = Group.getByName(gname)
    if g and g:isExist() then
      for _, u in ipairs(g:getUnits() or {}) do
        if u and u:isExist() then
          local p = u:getPoint()
          if math.sqrt((p.x - tp.x) ^ 2 + (p.z - tp.z) ^ 2) <= (fc.range or 27800) then close = true end
        end
      end
    end
  end
  if not close then return end
  if not CWDC.fac_state.checked_in then
    CWDC.fac_state.checked_in = true
    trigger.action.outText((fc.callsign or "FAC") .. ": I have you in sight. Stand by for the mark.", 15)
  end
  if timer.getTime() - CWDC.fac_state.last_mark >= (fc.interval or 300) then CWDC.fac_mark(false) end
end

if CWDC.config.fac then
  local fc = CWDC.config.fac
  if fc.menu and missionCommands then
    for _, gname in ipairs(fc.focus or {}) do
      local g = Group.getByName(gname)
      local gid = g and safe(function() return g:getID() end)
      if gid then
        missionCommands.addCommandForGroup(gid, (fc.callsign or "FAC") .. ": request new mark", nil,
          function() CWDC.fac_mark(true) end, nil)
      end
    end
  end
  timer.scheduleFunction(function(_, t)
    if CWDC.ended then return nil end
    local ok, err = pcall(CWDC.fac_check)
    if not ok then log("fac error: " .. tostring(err)) end
    return t + 10
  end, nil, timer.getTime() + 10)
end

------------------------------------------------------------------------
-- Bomb impacts (runway attacks): every bomb released by the watched groups
-- is followed until it hits; its last position is a CWDC_IMPACT event.
--   CWDC_CONFIG.impacts = { groups = { ... } }
------------------------------------------------------------------------
CWDC.impact_watch = {}
local function watched_group(name)
  for _, g in ipairs(((CWDC.config.impacts or {}).groups) or {}) do
    if g == name then return true end
  end
  return false
end

function CWDC.track_bomb(weapon, shooter)
  local last = safe(function() return weapon:getPoint() end)
  local function step(_, t)
    if safe(function() return weapon:isExist() end) then
      last = safe(function() return weapon:getPoint() end) or last
      return t + 0.1
    end
    if last then
      CWDC.seq = CWDC.seq + 1
      local rec = { seq = CWDC.seq, t = timer.getTime(), id = -3, event = "CWDC_IMPACT",
                    initiator = shooter, pos = { x = last.x, z = last.z },
                    weapon = safe(function() return weapon:getTypeName() end) }
      CWDC.events[#CWDC.events + 1] = rec
      CWDC.emit("EV", rec)
    end
    return nil
  end
  timer.scheduleFunction(step, nil, timer.getTime() + 0.1)
end

if CWDC.config.impacts then
  local impact_handler = {}
  function impact_handler:onEvent(e)
    if not e or e.id ~= world.event.S_EVENT_SHOT or not e.weapon or not e.initiator then return end
    local gname = safe(function() return e.initiator:getGroup():getName() end)
    if not gname or not watched_group(gname) then return end
    local cat = safe(function() return e.weapon:getDesc().category end)
    if cat ~= 3 then return end                     -- Weapon.Category.BOMB
    CWDC.track_bomb(e.weapon, object_info(e.initiator))
  end
  world.addEventHandler(impact_handler)
end

------------------------------------------------------------------------
-- MAGIC: ground-controlled intercept over the F10 radio menu
--   CWDC_CONFIG.magic = { callsign = "MAGIC", side = 2, groups = {player groups},
--                         radars = {friendly EWR / SAM / fighter groups},
--                         bullseye = { x = .., z = .., name = .. }, threat_nm = 25 }
-- Contacts come only from what friendly radars hold (getDetectedTargets), so a
-- low flyer under the coverage is invisible to MAGIC too.
------------------------------------------------------------------------
local M_TO_NM, M_TO_KFT = 1 / 1852, 3.28084 / 1000
CWDC.magic = { threat_on = {}, called = {} }

local function bearing(from, to)
  local b = math.deg(math.atan2(to.z - from.z, to.x - from.x))
  if b < 0 then b = b + 360 end
  return b
end

local function aspect(tgt, toward)
  -- hot / flank / beam / cold: target's track against the line from it to `toward`
  local v = safe(function() return tgt:getVelocity() end)
  if not v or (v.x * v.x + v.z * v.z) < 100 then return "" end
  local p = tgt:getPoint()
  local track = bearing({ x = 0, z = 0 }, v)
  local diff = math.abs(((track - bearing(p, toward)) + 540) % 360 - 180)
  if diff <= 30 then return "hot" elseif diff <= 70 then return "flanking"
  elseif diff <= 110 then return "beaming" end
  return "cold"
end

function CWDC.magic_contacts()
  local cfg = CWDC.config.magic or {}
  local side, out = cfg.side or 2, {}
  for _, gname in ipairs(cfg.radars or {}) do
    local g = Group.getByName(gname)
    local ctl = g and safe(function() return g:getController() end)
    local seen = ctl and safe(function() return ctl:getDetectedTargets(Controller.Detection.RADAR) end) or {}
    for _, d in ipairs(seen) do
      local o = d.object
      if o and safe(function() return o:isExist() end) and safe(function() return o:getCoalition() end) ~= side
          and safe(function() return o:inAir() end) then
        out[o:getName()] = o
      end
    end
  end
  return out
end

local function lead_of(gname)
  local g = Group.getByName(gname)
  if not g or not g:isExist() then return nil end
  for _, u in ipairs(g:getUnits() or {}) do
    if u and u:isExist() then return u end
  end
end

local function braa(from_unit, tgt)
  local a, b = from_unit:getPoint(), tgt:getPoint()
  local range = math.sqrt((b.x - a.x) ^ 2 + (b.z - a.z) ^ 2) * M_TO_NM
  return string.format("BRAA %03d/%d, %d thousand%s", math.floor(bearing(a, b) + 0.5) % 360,
    math.floor(range + 0.5), math.floor(b.y * M_TO_KFT + 0.5),
    (function() local s = aspect(tgt, a) return s ~= "" and (", " .. s) or "" end)()), range
end

local function say(gname, text, secs)
  local g = Group.getByName(gname)
  local gid = g and safe(function() return g:getID() end)
  if gid then trigger.action.outTextForGroup(gid, text, secs or 15) end
  CWDC.magic.last = text
end

local function callsign_of(gname)
  local cs = string.match(gname, "^%S+%s+(.+)$")
  return cs or gname
end

function CWDC.magic_bogey_dope(gname)
  local cfg = CWDC.config.magic or {}
  local me = lead_of(gname)
  if not me then return end
  local best, best_r, best_text = nil, math.huge, nil
  for _, o in pairs(CWDC.magic_contacts()) do
    local text, r = braa(me, o)
    if r < best_r then best, best_r, best_text = o, r, text end
  end
  local who = callsign_of(gname) .. ", " .. (cfg.callsign or "MAGIC")
  if not best then say(gname, who .. ", picture clean.") return end
  say(gname, who .. ", bogey dope: single group " .. best_text .. ", hostile.")
end

function CWDC.magic_picture(gname)
  local cfg = CWDC.config.magic or {}
  local who = callsign_of(gname) .. ", " .. (cfg.callsign or "MAGIC")
  local bull = cfg.bullseye
  local groups = {}
  for _, o in pairs(CWDC.magic_contacts()) do
    local gn = safe(function() return o:getGroup():getName() end) or o:getName()
    groups[gn] = groups[gn] or { n = 0, unit = o }
    groups[gn].n = groups[gn].n + 1
  end
  local lines, count = {}, 0
  for _, gr in pairs(groups) do
    count = count + 1
    local p = gr.unit:getPoint()
    local where = ""
    if bull then
      local r = math.sqrt((p.x - bull.x) ^ 2 + (p.z - bull.z) ^ 2) * M_TO_NM
      where = string.format("bullseye %03d/%d", math.floor(bearing(bull, p) + 0.5) % 360, math.floor(r + 0.5))
    end
    lines[#lines + 1] = string.format("%s, %d thousand, %s", where, math.floor(p.y * M_TO_KFT + 0.5),
      gr.n > 1 and (gr.n .. " contacts") or "single")
  end
  if count == 0 then say(gname, who .. ", picture clean.") return end
  say(gname, who .. ", picture: " .. count .. " group" .. (count > 1 and "s" or "") .. ". "
      .. table.concat(lines, "; ") .. ". Hostile.", 20)
end

function CWDC.magic_toggle_threat(gname)
  CWDC.magic.threat_on[gname] = not CWDC.magic.threat_on[gname]
  say(gname, callsign_of(gname) .. ", " .. ((CWDC.config.magic or {}).callsign or "MAGIC")
      .. ", threat calls " .. (CWDC.magic.threat_on[gname] and "on." or "off."))
end

function CWDC.magic_threat_check()
  local cfg = CWDC.config.magic or {}
  local now = timer.getTime()
  for gname, on in pairs(CWDC.magic.threat_on) do
    local me = on and lead_of(gname)
    if me then
      for name, o in pairs(CWDC.magic_contacts()) do
        local text, r = braa(me, o)
        local key = gname .. "|" .. name
        if r <= (cfg.threat_nm or 25) and now - (CWDC.magic.called[key] or -1e9) >= 60 then
          CWDC.magic.called[key] = now
          say(gname, callsign_of(gname) .. ", " .. (cfg.callsign or "MAGIC") .. ", threat, " .. text .. ".")
        end
      end
    end
  end
end

if CWDC.config.magic and missionCommands then
  local cfg = CWDC.config.magic
  for _, gname in ipairs(cfg.groups or {}) do
    local g = Group.getByName(gname)
    local gid = g and safe(function() return g:getID() end)
    if gid then
      local menu = missionCommands.addSubMenuForGroup(gid, cfg.callsign or "MAGIC")
      missionCommands.addCommandForGroup(gid, "Bogey dope", menu, CWDC.magic_bogey_dope, gname)
      missionCommands.addCommandForGroup(gid, "Picture", menu, CWDC.magic_picture, gname)
      missionCommands.addCommandForGroup(gid, "Threat calls on/off", menu, CWDC.magic_toggle_threat, gname)
    end
  end
  timer.scheduleFunction(function(_, t)
    if CWDC.ended then return nil end
    local ok, err = pcall(CWDC.magic_threat_check)
    if not ok then log("magic error: " .. tostring(err)) end
    return t + 15
  end, nil, timer.getTime() + 15)
end

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
