--[[
  ColdWarDynamicDCS - soak-test hook (unattended AI missions)
  Installed ONLY into a soak profile (Saved Games\DCS.cwdc_soak<N>) by `cwdc soak`;
  never into your normal Saved Games\DCS.

  DCS runs in server mode (DCS.exe --server --norender -w DCS.cwdc_soak<N>), where
  net.load_mission() is available. The campaign manager drives it through files:
      CWDC\soak\command.json    {"seq": n, "load": "<miz path>"} | {"seq": n, "abort": true}
                                | {"seq": n, "exit": true}
      CWDC\soak\heartbeat.json  written every few seconds: state, mission, model time
  Between missions the server sits in CWDC_Lobby.miz, an empty mission, so there
  are always simulation frames to poll from.
]]

local Sim = Sim or DCS

local S = {
  dir = nil, lobby = nil, seq_done = -1, last_poll = -1e9, last_beat = -1e9,
  state = "starting", mission = "", ended = 0, loads = 0,
}

local function log(msg) net.log("[CWDC-soak] " .. tostring(msg)) end

local function realtime()
  if Sim.getRealTime then return Sim.getRealTime() end
  return os.clock()
end

local function soak_dir()
  if not S.dir then
    local base = lfs.writedir() .. "CWDC"
    lfs.mkdir(base)
    lfs.mkdir(base .. "\\soak")
    S.dir = base .. "\\soak\\"
    S.lobby = lfs.writedir() .. "Missions\\CWDC_Lobby.miz"
  end
  return S.dir
end

local function read_file(path)
  local f = io.open(path, "r")
  if not f then return nil end
  local s = f:read("*a")
  f:close()
  return s
end

local function write_atomic(path, text)
  local tmp = path .. ".tmp"
  local f = io.open(tmp, "w")
  if not f then return end
  f:write(text)
  f:close()
  os.remove(path)
  os.rename(tmp, path)
end

local function is_lobby(name)
  return name and string.find(string.lower(name), "cwdc_lobby", 1, true) ~= nil
end

local function beat()
  local mt = 0
  if Sim.getModelTime then mt = Sim.getModelTime() end
  local paused = Sim.getPause and Sim.getPause() or false
  write_atomic(soak_dir() .. "heartbeat.json", net.lua2json({
    written_at = os.time(), state = S.state, mission = S.mission, model_time = mt,
    paused = paused, seq_done = S.seq_done, missions_ended = S.ended, loads = S.loads,
    server = Sim.isServer and Sim.isServer() or false,
  }))
  S.last_beat = realtime()
end

local function load(path, why)
  S.loads = S.loads + 1
  local ok, res = pcall(net.load_mission, path)
  log("load " .. tostring(path) .. " (" .. why .. "): " .. tostring(ok) .. " " .. tostring(res))
  return ok and res ~= false
end

local function poll_command()
  local text = read_file(soak_dir() .. "command.json")
  if not text then return end
  local ok, cmd = pcall(net.json2lua, text)
  if not ok or type(cmd) ~= "table" or type(cmd.seq) ~= "number" or cmd.seq <= S.seq_done then
    return
  end
  S.seq_done = cmd.seq
  if cmd.exit then
    log("exit requested")
    beat()
    Sim.exitProcess()
  elseif cmd.abort then
    load(S.lobby, "abort")
  elseif cmd.load then
    S.state = "loading"
    load(cmd.load, "command " .. cmd.seq)
  end
  beat()
end

local cb = {}

function cb.onSimulationStart()
  soak_dir()
  S.mission = Sim.getMissionFilename and Sim.getMissionFilename() or ""
  S.state = is_lobby(S.mission) and "lobby" or "running"
  if Sim.setPause then Sim.setPause(false) end     -- a server with no clients may start paused
  log("started " .. tostring(S.mission) .. " (" .. S.state .. ")")
  beat()
end

function cb.onSimulationFrame()
  local now = realtime()
  if now - S.last_poll < 1 then return end
  S.last_poll = now
  if Sim.getPause and Sim.getPause() then Sim.setPause(false) end
  poll_command()
  if now - S.last_beat >= 5 then beat() end
end

-- An End Mission trigger leaves a server sitting on the debrief: go back to the lobby.
-- Loading the lobby stops the old mission, so the results hook writes its final result.
function cb.onNetMissionEnd()
  if is_lobby(S.mission) then return end
  S.ended = S.ended + 1
  S.state = "ended"
  beat()
  load(S.lobby, "mission ended")
end

function cb.onSimulationStop()
  if not is_lobby(S.mission) then S.state = "stopped" end
  beat()
end

Sim.setUserCallbacks(cb)
log("loaded")
