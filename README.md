# ColdWarDynamicDCS

A dynamic campaign for **DCS World** set in the mid-1970s on the **Cold War Germany** map, built
around the **Heatblur F-4E Phantom II**.

DCS has no dynamic campaign engine. This project is an external campaign manager. It runs the
strategic war, generates each mission as a `.miz` into your DCS Missions folder, and reads the
results back after you fly, so the war moves on.

> Status: **Phase 1**, a playable command-line campaign loop. See [docs/design.md](docs/design.md)
> for the full design and roadmap.

## Playing (Phase 1)
```powershell
.\.venv\Scripts\python.exe -m cwdc new --pilot "Your Name"   # start a campaign
.\.venv\Scripts\python.exe -m cwdc ato                       # this turn's tasking; * = you can fly it
.\.venv\Scripts\python.exe -m cwdc fly F003                  # writes the .miz into Missions\ColdWarDynamic
#   add --start ramp (parking) or --start air (~50 nm out); default is a hot runway start
#   ...fly it in DCS, exit normally...
.\.venv\Scripts\python.exe -m cwdc ingest                    # your results + the simulated rest -> next turn
```
Other commands:
- `status` shows the war situation, squadrons, the ground war, supply, airfields, objectives and news. Once the war is decided, it shows the war summary instead.
- `roster` shows your squadron's pilots and WSOs, with their fatigue.
- `skip` sits the turn out and lets it play out without you.
- `rollback <turn>` restores the snapshot from the start of that turn.
- `fly <ID> --bot` and `autorun <N>` play missions or turns with no player, for testing.
- `python -m tools.balance --seeds 6 --days 21` plays whole wars and reports per-sortie loss rates, how the front moves, and the outcomes.

The war ends in one of four ways:
- **Warsaw Pact victory:** it reaches the Main, breaks through 100 km deep, or exhausts NATO air power.
- **NATO victory:** after day 7, the border is restored everywhere.
- **Ceasefire on day 21:** judged by how much West German territory the Warsaw Pact holds.
- **Each campaign rolls the Warsaw Pact's main axis**, and reinforcement timetables slip.

Campaign saves live in `Saved Games\DCS\CWDC\campaigns\<id>\`. That folder holds
`campaign.json` plus one snapshot per turn in `turns\NNN.json`.

Your in-game pilot name comes from your DCS Logbook profile (DCS main menu → Logbook), not
from the campaign.

**Re-flying:** if you fly the same generated mission more than once before ingesting, the
*latest* run counts. Once you've ingested, the mission is closed. (`rollback` is the
deliberate way back.)

## Requirements
- DCS World with the Cold War Germany map and the F-4E
- Python 3.12+
- No mods are needed. DCS's `MissionScripting.lua` does **not** need to be desanitised.

## Setup
```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m pytest
```

## Spike C: results round-trip test
```powershell
.\.venv\Scripts\python.exe -m cwdc spike
```
1. This installs `Saved Games\DCS\Scripts\Hooks\CWDC-hook.lua` and writes
   `Saved Games\DCS\Missions\ColdWarDynamic\CWDC_SpikeC.miz`.
2. Fly the mission, then exit it normally.
3. Check the result:
   ```powershell
   .\.venv\Scripts\python.exe -m cwdc show-result
   ```

## How results get back out of DCS
- `cwdc/runtime/lua/cwdc_runtime.lua` is embedded in every mission. It records events and
  snapshots tracked groups, and writes them out as tagged JSON lines in the DCS log (`env.info`).
- `cwdc/runtime/lua/CWDC-hook.lua` is a GUI hook. It reads those lines with `Sim.getLogHistory()`
  and writes `Saved Games\DCS\CWDC\results\<mission_id>.json`.
- If the hook misses anything, `python -m cwdc show-result --from-log` rebuilds the result from
  `Saved Games\DCS\Logs\dcs.log`. Run it before restarting DCS, because DCS overwrites that log
  at startup.
