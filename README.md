# ColdWarDynamicDCS

A dynamic campaign for **DCS World** set in the mid-1970s on the **Cold War Germany** map, built
around the **Heatblur F-4E Phantom II**.

DCS has no dynamic campaign engine. This project is an external campaign manager. It runs the
strategic war, generates each mission as a `.miz` into your DCS Missions folder, and reads the
results back after you fly, so the war moves on.

> Status: **Phases 1–4** done: the campaign engine, the web UI, unattended soak testing, and the
> crisis and immersion features. Phase 5 (polish) is next. See [docs/design.md](docs/design.md)
> for the full design and roadmap.
>
> **Next stage: the Cold War campaign maker (1950–1991).** Pick a year, and the war is built
> with the aircraft, weapons, SAMs and tanks of that year, flown by every West and East nation.
> The Fulda 1976 concept is frozen as the tag `v0.4-fulda-1976`. Phases A–E (era data, the generator,
> flying several airframes, the year-slider screen, missions in the enemy's turn) are done; next is the
> Syria theatre (Phase F). See
> [docs/design_era.md](docs/design_era.md).

## The campaign in brief (Phase 4)
- **The crisis (11 Aug 1976):** every new campaign opens 2–4 days before the war.
  - You fly border CAP and **QRA**: scramble, intercept Warsaw Pact border probes and shadow them back east. The ROE is **weapons tight**.
  - An intercept counts once you hold within 2 nm for 60 s.
  - Firing first gets your pilot grounded and raises tension. Shooting one down brings the war forward.
  - The war starts when tension reaches 100, or within 4 days anyway. It opens with Warsaw Pact raids on NATO airfields.
- **MAGIC:** ground radar control on the F10 radio menu, giving bogey dope (BRAA), a picture (bullseye Fulda) and threat calls. It only sees what friendly radars see.
- **Weather:** changes every turn. Low cloud, rain and dawn fog cut strike effect and sorties, and missions get matching DCS weather.
- **Briefings and kneeboards:** player missions include three kneeboard pages: a mission card with waypoints, threats and comms, and a route sketch.
- **News:** categorised and grouped by turn, with a daily item from the wider world.
- **OCA:** NATO flies about one airfield raid a day when the air war calls for it. Your squadron gets it where possible, and the ATO suggests an air start for it.
- `cwdc new --no-crisis` starts straight at war, for testing.

## Starting it (one click)
Double-click **ColdWarDynamic** on the desktop: it starts the web UI in the background, or reuses
it if it's already running, and opens it in your browser. **ColdWarDynamic + DCS** also starts
DCS World. **ColdWarDynamic overnight soak** runs the 8-hour, 2-instance soak test.
The shortcuts are created with `.\.venv\Scripts\python.exe -m cwdc shortcuts`; run it again if you
move the project folder. `cwdc start [--dcs]` does the same from a terminal.

## A war in any year (campaign maker)
In the web UI, press **New campaign**. Drag the year line across the chart of every airframe's service life (★ marks the ones you own). The panel beside it shows the war you'll get: your wing, both air forces, SAMs, tanks and what you can carry that year. Then press **Create campaign**. The same choices work from the command line:
```powershell
.\.venv\Scripts\python.exe -m cwdc new --year 1966
```
- **What you fly:** the first of your own modules in service that year, picked automatically. Choose one with `--aircraft F-100D` (or F-4E, A-4E, A-10A, Viggen, ...).
- **Options:** `--sweden` brings Sweden in on the West, and `--strict` drops stand-in aircraft.
- **Your wing:** you belong to a wing of up to three squadrons at your airfield, each flying another module you own that was in service, such as F-100D, A-4E and F-5 in 1966. All of their flights are on your ATO.
- **Loadouts:** they come from the modules' own shipped payloads, limited to weapons that existed that year. The briefing and kneeboard list what you carry.
- **Presets:** `--preset` with sabre-days-1957, phantoms-and-viggens-1975, hog-country-1985 or fulda-1976 (the original campaign).
- **Without `--year`:** you get the Fulda 1976 campaign exactly as before.

## Missions you can fly
Your squadron (496th TFS, F-4E) flies 4 sorties a turn. One is held back as an alert reserve for scrambles, escorts and sweeps. Each war day it also gets one "special", in rotation: an airfield attack, Iron Hand, a runway attack, then a precision strike.

| Mission | What you do | Loadout |
|---|---|---|
| Barrier CAP | Guard a patrol station | 4x AIM-7E-2, 4x AIM-9J, 3 tanks |
| QRA (crisis) | Intercept a border probe and shadow it, weapons tight | 4x AIM-7E, 4x AIM-9E, centreline tank |
| Scramble intercept | Meet an inbound raid; MAGIC vectors you | as QRA |
| Fighter sweep | Clear a MiG patrol ahead of a strike | as CAP |
| Escort | Protect a strike package | as CAP |
| CAS (with FAC) | Wildcat 21 marks the target with smoke and talks you on | 12x Mk-20 Rockeye, 2x AIM-7E-2 |
| BAI | Units just behind the front | 4x AGM-65A Maverick, 2x AIM-7E-2 |
| Armed recon | Hunt a moving supply column in a 30 km box | 12x Rockeye, 2x AIM-7E-2 |
| Interdiction | Rail yards, depots, columns | 12x Mk-82, 2x AIM-7E-2 |
| Strike / OCA | Radars, command posts, parked aircraft | 11x M117, 2x AIM-7E-2 |
| Runway attack | Crater a Warsaw Pact runway (bomb impacts are counted) | 11x M117 string |
| Precision strike | Radar or command post | 2x GBU-8 HOBOS (TV-guided), Pave Spike, 3x AIM-7E-2 |
| Iron Hand (SEAD) | Hunt a SAM site with Shrikes | 4x AGM-45A, 3x AIM-7E-2 |
| RESCAP | Cover Dustoff 1 (UH-1H) picking up a downed crew; a search party closes in | 12x Rockeye, 2x AIM-7E-2 |

**Missions in the enemy's turn.** When the Warsaw Pact attacks somewhere on the front, the plan answers it, and these go to your wing first:

| Mission | What you do | What it changes |
|---|---|---|
| Troops in contact | CAS on the lead battalion of the attack. A FAC talks you on, and friendly armour is 2.5 km away. | Stop it and that sector's attack stalls this turn. |
| Hold the (river) | The same, where the attack is about to force a river line, e.g. *Hold the Fulda river at Fulda*. | Counts for more. |
| Counter-battery | Silence the guns firing the attack in. | Weakens the attack. |
| Helicopter hunt | A Mi-24/Mi-8 assault wave crosses the front low, heading for a landing zone behind our lines. Shoot down half. | If it gets through, the attack gets a lift. |
| Hunt the raid / Bomber intercept / Airfield defence | Scrambles named by what they meet: attack aircraft over the front, bombers going deep, or a raid on your own field. | |

## Playing in the browser (Phase 3)
```powershell
.\.venv\Scripts\python.exe -m cwdc serve        # opens http://127.0.0.1:8076 in your browser
```
- **Map:** shaded relief with 1976 place names (base-map switcher top right). NATO-style symbols (blue rectangle = NATO, red diamond = Warsaw Pact) for divisions, sites and airbases, attack arrows, hatched ground taken, SAM danger rings. Detail appears as you zoom in; hover anything for a plain-English explanation; **Legend** bottom left.
- **What next** card (top of the sidebar) always says what to do now; **? Help** has the intro guide and a glossary.
- **ATO:** amber rows are flights you can fly. Pick a start (runway/ramp/air) and press **Fly**, or **Bot** for an AI-only test mission. Click a row to see its route on the map.
- **Mission:** shows the briefing and whether DCS has produced a result yet. With **Auto-ingest** on (the default), the turn closes by itself when you leave the mission in DCS. Otherwise press **Ingest now**, or **Ingest from dcs.log** if the hook missed it.
- **War:** shows the war situation (or the final summary), the ground war by sector and formation, air forces, airfields and supply.
- **Squadron:** shows your pilots and WSOs with their fatigue, and your aircraft.
- **Debrief:** shows every closed turn, plus **Roll back** to the start of an earlier turn.
- **News**, **New campaign** and **Skip turn** do what they say.

The server only listens on this PC (`--host` changes that) and reads the campaign from disk on every request, so the CLI below keeps working alongside it. `--no-auto-ingest` and `--no-browser` are available. The map tiles come from OpenStreetMap, so the background needs an internet connection; everything else is local.

## Overnight soak testing (unattended AI missions)
```powershell
.\.venv\Scripts\python.exe -m cwdc soak --hours 8 --instances 2   # leave it running, read the report in the morning
.\.venv\Scripts\python.exe -m cwdc soak-report                    # rebuild the latest run's report
```
- Each instance is a DCS server in no-render mode (`DCS.exe --server --norender -w DCS.cwdc_soak<N>`) with its own profile in `Saved Games\DCS.cwdc_soak<N>`. Your `Saved Games\DCS` and your campaigns are never used.
- Each instance plays scratch campaigns turn after turn. Every turn, a player-squadron flight is flown as an AI-only mission (air start, capped at `--cap-min`, 75 by default), the result is ingested, and the war moves on. Tasks are rotated so all of them get sampled.
- Meanwhile the main process plays fully simulated wars (`--sim-wars`).
- A watchdog restarts DCS if it crashes, hangs or fails to load a mission. That turn is skipped and logged.
- Results go to `Saved Games\CWDC-soak\<run>\`:
  - `i<N>/missions.jsonl`: one row per mission.
  - `i<N>/anomalies/`: the `.miz` and result of every odd mission, for replaying.
  - `report.html` / `report.md`: anomalies, DCS against the simulation per task (loss and kill rates), pacing, war outcomes, and `dcs.log` errors.
- The first time a profile is used, DCS may ask you to log in. This happened on instance 2 on 2026-11-24. If nobody answers the prompt, that instance's start-up times out and its worker stops, while the other instances carry on. So run a short soak once while you're at the PC, so every profile has a saved login.
- Findings from the first run and the mission fixes they led to are in [docs/design.md](docs/design.md) (search for "First overnight run").
- DCS servers can't accelerate time, so missions run in real time: roughly one per 30–60 min per instance. Each instance needs about 18 GB of RAM while flying a Cold War Germany mission, so run 2 instances on a 64 GB PC.

## Playing from the command line (Phase 1)
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
