--[[
  ColdWarDynamicDCS - GUI hook
  Install to: Saved Games\DCS\Scripts\Hooks\CWDC-hook.lua
  (the campaign manager installs/updates it automatically)

  The mission runtime (cwdc_runtime.lua) writes tagged log lines:
      [CWDC] <mission_id> <KIND> <msg_seq> <part>/<parts> <json chunk>
  This hook reads them back through Sim.getLogHistory() - a supported hook
  API, so neither net.dostring_in nor a desanitised MissionScripting.lua is
  needed - reassembles them, and writes
      Saved Games\DCS\CWDC\results\<mission_id>.json
  every few seconds while flying and once more when the simulation stops.
]]

local Sim = Sim or DCS
local getLogHistory = Sim and Sim.getLogHistory

local H = {
  log_index = 0,
  current = nil,      -- mission record being collected
  last_poll = -1e9,
  last_write = -1e9,
  poll_interval = 1,  -- seconds (real time)
  write_interval = 5,
}

local PATTERN = "%[CWDC%] (%S+) (%u+) (%d+) (%d+)/(%d+) (.*)$"

local function log(msg)
  net.log("[CWDC-hook] " .. tostring(msg))
end

local function realtime()
  if Sim.getRealTime then return Sim.getRealTime() end
  return os.clock()
end

local function new_mission(id)
  return { id = id, run_started = os.time(), start = nil, snap = nil, end_info = nil, events = {},
           ended = false, partial = {}, seen = {}, dirty = true }
end

local function handle(mid, kind, seq, part, parts, data)
  local m = H.current
  if kind == "START" and part == 1 then
    m = new_mission(mid)          -- a (re)started mission resets collection
    H.current = m
    log("collecting mission " .. mid)
  elseif not m or m.id ~= mid then
    m = new_mission(mid)
    H.current = m
  end
  local key = seq .. "/" .. part
  if m.seen[key] then return end  -- overlapping log reads
  m.seen[key] = true

  local payload = data
  if parts > 1 then
    local p = m.partial[seq] or { got = 0, chunks = {} }
    m.partial[seq] = p
    p.chunks[part] = data
    p.got = p.got + 1
    if p.got < parts then return end
    payload = table.concat(p.chunks)
    m.partial[seq] = nil
  end

  if kind == "START" then m.start = payload
  elseif kind == "EV" then m.events[#m.events + 1] = payload
  elseif kind == "SNAP" then m.snap = payload
  elseif kind == "END" then m.ended = true; m.end_info = payload
  end
  m.dirty = true
end

local function poll()
  if not getLogHistory then return end
  local ok, hist, last = pcall(getLogHistory, H.log_index)
  if not ok or type(hist) ~= "table" then return end
  for _, entry in ipairs(hist) do
    local msg = entry
    if type(entry) == "table" then msg = entry.message or entry[4] end
    if type(msg) == "string" then
      local mid, kind, seq, part, parts, data = string.match(msg, PATTERN)
      if mid then
        handle(mid, kind, tonumber(seq), tonumber(part), tonumber(parts), data)
      end
    end
  end
  if type(last) == "number" then H.log_index = last end
end

local function results_dir()
  local base = lfs.writedir() .. "CWDC"
  lfs.mkdir(base)
  lfs.mkdir(base .. "\\results")
  return base .. "\\results\\"
end

local function write(final)
  local m = H.current
  if not m then return end
  local path = results_dir() .. m.id .. ".json"
  local tmp = path .. ".tmp"
  local f = io.open(tmp, "w")
  if not f then
    log("cannot open " .. tmp)
    return
  end
  f:write('{"source":"hook","hook_version":2'
    .. ',"final":' .. (final and "true" or "false")
    .. ',"written_at":' .. tostring(os.time())
    .. ',"mission_id":"' .. m.id .. '"'
    .. ',"run_started":' .. tostring(m.run_started)
    .. ',"ended":' .. (m.ended and "true" or "false")
    .. ',"start":' .. (m.start or "null")
    .. ',"snapshot":' .. (m.snap or "null")
    .. ',"end":' .. (m.end_info or "null")
    .. ',"events":[' .. table.concat(m.events, ",") .. ']}')
  f:close()
  os.remove(path)
  os.rename(tmp, path)
  m.dirty = false
  H.last_write = realtime()
end

local callbacks = {}

function callbacks.onSimulationStart()
  H.current = nil
  poll()
end

function callbacks.onSimulationFrame()
  local now = realtime()
  if now - H.last_poll < H.poll_interval then return end
  H.last_poll = now
  poll()
  if H.current and H.current.dirty and now - H.last_write >= H.write_interval then
    write(false)
  end
end

function callbacks.onSimulationStop()
  poll()
  if H.current then
    write(true)
    log("final result written for " .. H.current.id .. " (" .. #H.current.events .. " events)")
  end
  H.current = nil
end

Sim.setUserCallbacks(callbacks)
if getLogHistory then
  log("loaded")
else
  log("loaded, but Sim.getLogHistory is unavailable - results must be recovered from dcs.log")
end
