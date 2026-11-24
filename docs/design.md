# ColdWarDynamicDCS — Design

## Context
DCS has no built-in dynamic campaign, and none of the existing ones cover the Cold War. This project is an external campaign manager that:
- keeps a persistent mid-1970s war on the **Cold War Germany** map, built around the **Heatblur F-4E**;
- writes each mission as a `.miz` file into the DCS Missions folder;
- reads back what happened after the player flies it, and moves the war forward.

## Phase 0 findings (2026-11-23)
- **Spike A: passed.** `dcs.terrain.Germany()` reports `GermanyCW` and has 227 airfields and helipads, including:
  - USAF F-4E bases: Spangdahlem, Hahn, Ramstein, Bitburg, Sembach, Zweibrücken;
  - Warsaw Pact bases: Wittstock, Parchim, Finow, Templin, Zerbst, Merseburg, Altes Lager, and many more.
- **Spike B: passed.**
  - pydcs `F_4E_45MC` lists most stores but not the MER/TER multi-racks.
  - `tools/harvest_payloads.py` scanned 155 Heatblur-shipped missions and recorded every (pylon, CLSID) pair in `cwdc/data/f4e_clsids.json`.
  - Period loadouts are defined in `cwdc/data/loadouts_f4e.yaml` and validated per pylon.
- **Bonus:** the HB F-4E exposes `Quality` (aircraft condition), `Wear` and `PersistentAircraftKey` as unit properties. These suit a campaign where airframes degrade.
- **Spike C, first live test (2026-11-23): failed, and the cause is now fixed.**
  - *Error dialog* `[string "DictKey_Translation_5"]:1: '=' expected`. DCS executes the `text` field of `a_do_script` verbatim and never resolves it through the l10n dictionary (`MissionEditor/modules/me_predicates.lua`). The script is now embedded inline with `String(code)`, and a regression test covers it.
  - *No results file.* ED's `API/Sim_ControlAPI.md` marks `net.dostring_in` as obsolete and unsafe, disabled unless `autoexec.cfg` whitelists it, so the hook could never reach the mission. **New channel:** the runtime writes tagged, chunked JSON lines with `env.info("[CWDC] <id> <KIND> <seq> <i>/<n> <json>")`. The hook reads them with the supported `Sim.getLogHistory()` API and writes the results file. Because the same lines land in `dcs.log`, `cwdc show-result --from-log` can rebuild a result even without the hook.
  - Testing: the runtime, the hook and the Python reassembler are unit-tested under LuaJIT (Lua 5.1, the same version DCS uses) against mocked DCS APIs, including chunked messages and overlapping log reads.
- **Spike C, second live test (2026-11-23): PASSED.**
  - `Sim.getLogHistory` does deliver `SCRIPTING` lines to hooks.
  - 2-part chunked snapshots reassembled correctly.
  - The hook file and the `dcs.log` rebuild were identical, and three restarts in one DCS session were handled.
  - Captured: both MiG kills with weapons, the MiG pilot's ejection and parachute landing with positions, the player's landing at Hahn, and the tracked-group snapshot at mission end.
  - The recorded lines are kept as a regression fixture: `tests/fixtures/spike_c_flight.log`.
  - Follow-ups this surfaced:
    - Scenery HITs (trees, fences, approach lights) are now dropped in the runtime. Scenery KILL/DEAD events are kept for bridge targets, and ingest classifies them.
    - Collisions show up as a "weapon" named after the aircraft type. `ingest.results.is_collision` flags them.
    - DCS sometimes leaves the weapon off a KILL event. `kill_weapon` falls back to the last HIT on the same target.
    - **Re-flying a mission ID overwrites its result.** The hook now stamps `run_started` on each run. Phase 1 must decide the policy: last attempt counts, or a re-flight is refused or flagged.
    - The AI wingman (warm start, in the player's group) only took off at t=588 s, about 8 minutes after the lead. Phase 1 mission generation should look at start types and AI wingman behaviour.
- **Environment:** this Windows IoT LTSC build had no winget. It was installed through `Microsoft.WinGet.Client` (`Repair-WinGetPackageManager`), then Git and Python 3.12 were installed with winget.

**Decisions made:**
- **Scenario:** a crisis that escalates into war.
- **Ground war:** resolved abstractly off-screen. Only the units that matter to a mission are spawned in DCS.
- **Players:** single-player first, with a data model that can grow to multiplayer.
- **Interface:** a local web UI.

**Environment found:**
- DCS 2.9.31, with the GermanyColdWar terrain, F-4E, F-100D, F-14, AJS37 and Supercarrier installed.
- `MissionScripting.lua` is stock (sanitised), so missions have no `io`/`lfs`.
- git and Python are **not installed**, so they are prerequisites.
- pydcs (`pydcs/dcs`) has a `dcs/terrain/germany` module, which is almost certainly the Cold War Germany map. This is checked in Phase 0.

## Architecture

```
┌──────────── Campaign Manager (Python 3.12) ─────────────┐
│ Web UI (FastAPI + vanilla JS + Leaflet)                  │
│   map / briefing+ATO / squadron / debrief / news         │
│ Strategic engine: turns, ground war, AI air commanders,  │
│   logistics, crisis/escalation, pilots                   │
│ Mission generator (pydcs) ──► Saved Games\DCS\Missions\  │
│ Results ingester ◄── Saved Games\DCS\CWDC\results\*.json │
└──────────────────────────────────────────────────────────┘
          │ .miz with embedded Lua runtime        ▲ JSON
          ▼                                       │
  DCS mission env: cwdc_runtime.lua ──► hook: Scripts\Hooks\CWDC.lua
  (event tracking, GCI, ROE, state table)   (polls via net.dostring_in, writes file)
```

### Key technical choices
- **Python + pydcs** writes the missions. This is the same proven approach DCS Liberation uses. Typed data models use **pydantic**.
- **Getting results back without desanitising DCS:**
  - A runtime script inside the mission records births, kills, losses, landings, ejections, unit health, and the positions of tracked groups.
  - It emits each record as a tagged JSON log line (`env.info`), plus a tracked-group snapshot every 30 s and at mission end.
  - A **GUI hook** (`Saved Games\DCS\Scripts\Hooks\CWDC-hook.lua`) reads those lines with `Sim.getLogHistory()` and writes `CWDC\results\<mission_id>.json`.
  - Fallback: the same data can be rebuilt from `dcs.log`. No `net.dostring_in` and no desanitising is involved.
- **Saved data:** one folder per campaign. It holds `campaign.json` (current state) plus a snapshot for every turn (`turns/NNN.json`), so a crashed or abandoned mission can be rolled back. The files are human-readable and easy to diff.
- **Map:** pydcs projection gives latitude/longitude, drawn on Leaflet over real-world tiles (muted topo style). Overlays show the front-line trace, airbases, SAM rings and known targets. Enemy information shown is limited to what intel has detected (fog of war).
- **Assets:** DCS stock assets only. There is no Cold War asset pack dependency at first. The unit catalogue has an "era filter" that uses in-service years, so 1976 rules out the F-15, A-10, M1 and so on. The unit list is generated from pydcs and checked by hand.

## Campaign design

**Time:** one turn is about one mission period (dawn, day, dusk, night). A day has 3–4 turns. The 1970s F-4E is weak at night, so night turns are mostly AI tasking.

**Crisis phase (Day −N to Day 0):**
- A tension level (0–100) is driven by scripted and randomised events: border incidents, Warsaw Pact exercises, mobilisation intel.
- Player tasks in this phase:
  - QRA / Zulu alert intercepts of Warsaw Pact aircraft probing the ADIZ (shadow, ID, escort out);
  - recon along the inner German border;
  - CAP over the Berlin corridor.
- ROE is **weapons tight**. The runtime script detects any weapon release by the player. Firing early speeds up escalation and goes on the pilot's record.
- War breaks out on a randomised day with an opening Warsaw Pact strike on NATO airfields. The player may be sitting QRA when it happens.

**War phase:**
- The front is a set of sectors along the inner German border (Fulda, Hof, Göttingen, North German Plain edge, as the map allows). Each sector holds abstract divisions/brigades with strength, supply and readiness.
- Ground combat is resolved per sector each turn with Lanchester-style attrition, adjusted by:
  - air support delivered (CAS/BAI);
  - interdiction of supply (bridges, rail, depots);
  - SAM/AAA cover.
- Sectors push the front line forward or back.
- **AI air commanders** on both sides build an ATO (air tasking order) each turn from their objectives and available assets:
  - OCA (offensive counter-air) against airfields;
  - CAP/barrier CAP;
  - CAS/BAI;
  - interdiction;
  - SEAD;
  - recon;
  - escort.
- The player picks one tasked package for their flight. Every other flight is resolved abstractly, or spawned as AI in the player's mission if it overlaps in space and time.

**Resources:**
- Aircraft and pilot pools per squadron, with replacements arriving over time.
- Munitions stocks, which matter for scarce AGM-65A/AIM-7E.
- Airbase damage: runway cuts and repair.
- Pilot outcome by where they come down: ejecting over friendly territory means rescued, over enemy territory means POW or MIA.

**Immersion:**
- Generated briefing text and kneeboard pages (flight plan, frequencies, codewords, threat map) rendered with Pillow.
- Period-correct loadouts that respect stock levels.
- Daily intel summaries and "Stars and Stripes"-style news.
- Squadron roster with kills, sorties and awards.
- Seasonal weather.

**Order of battle 1976:**
- NATO: USAF F-4E (player squadron), F-4E Wild Weasel-type SEAD with Shrikes, F-100/F-5 where historically plausible, stock NATO SAMs and AAA (Hawk, Chaparral/Vulcan, Gepard/Roland gated by year).
- Warsaw Pact: MiG-21bis, MiG-23MLD, MiG-25PD, MiG-27, Su-17, Su-24, Mi-8/24, SA-2/3/6/8/9, ZSU-23-4, T-55/T-62/T-72, BMP-1.
- The exact list is checked against the stock assets that pydcs exposes and against which airfields lie on the map.

**AI behaviour (checked 2026-11-23):**
- I searched for an existing mod that overhauls DCS AI aircraft behaviour and found **none**. What exists:
  - forum threads complaining about "UFO" AI;
  - ED's own BFM update in 2.8;
  - ED's promised AI flight model rework, the GFM (General Flight Model), not yet released;
  - `necr0n/dcs-make-flyable`, which only lists SFM tweaks as a goal.
- So the plan has **no mod-install step**. Instead the campaign tames the AI itself:
  - **Generator side:** realistic skill spread (mostly Average/Good); heavy fuel/drop tanks and full loads on Warsaw Pact fighters; sensible ROE/reaction-to-threat options; restricted afterburner use.
  - **Runtime side:** GCI-dependent Soviet tactics and doctrine-style engagement logic in the runtime script (e.g. break off after one pass, bingo fuel rules).
  - **Optional script handicap:** extra simulated weight on known UFO types, an idea taken from community practice (e.g. MiG-21). This needs testing.
- Revisit if ED ships the GFM or a community AI mod appears.

## Phase 1 status (2026-11-23): playable command-line loop
- **Model** (`cwdc/model/campaign.py`):
  - Campaign → airbases, squadrons, pilots, airframes, objectives (individual units), and the supply network schema.
  - The ATO is Packages → Flights for **both** sides.
  - One `SortieResult` type for flown and simulated sorties.
  - `MissionRecord` keeps a manifest from DCS unit names back to campaign entities.
- **Data:**
  - `theater_fulda_1976.yaml`: inner German border trace, CAP stations, 5 airbases, 8 objectives, 4 supply nodes.
  - `oob_fulda_1976.yaml`: 2 F-4E squadrons; MiG-21bis, MiG-23MLD and MiG-27K regiments.
  - `airframes.yaml`: per-airframe catalogue with tasks → loadouts and combat ratings.
- **Engine:**
  - `factory` creates a campaign.
  - `planner` plans every sortie on both sides (night turns are CAP only).
  - `resolver` resolves the abstract sorties in order: CAP sweeps, interceptions, SAM/AAA, weapons effects. Kills always match losses on the other side.
  - `apply` is a single code path for all results.
  - `turn` handles repairs, WIA recovery, daily replacements, the clock and re-planning.
- **Mission builder** (`missiongen/builder.py`) spawns the player flight plus everything overlapping it:
  - enemy CAP within 90 km of the route;
  - enemy strikes on targets within 60 km of a player CAP station, together with their targets;
  - friendly flights on targets within 25 km;
  - objectives near the target, and SAMs whose range covers the route.

  AI strikes launch 7 minutes in, so they arrive alongside the player. Low-level waypoints use radar altitude.
- **Ingest** (`ingest/flown.py`) maps DCS events back through the manifest:
  - losses and damage;
  - pilot fate by where the parachute landed (recovered, POW, KIA or MIA);
  - kills credited to pilots;
  - ground units destroyed, including units missing from the final snapshot.
- **Re-flight policy:** the latest run before ingest counts. The mission closes on ingest.
- **Known gaps, deferred to Phase 2:**
  - Ground objectives are never reinforced or rebuilt, so the target list runs dry after ~2 days.
  - No ground war or front movement.
  - Supply is stored but not simulated.
  - One crew member per F-4E (no WSO). Fixed in Phase 2 step 7.
  - Simple abstract combat odds that need balancing.

### First live Phase 1 flight (2026-11-23): findings and fixes
The flight was a strike from Hahn on the Geisa artillery. Ingest worked end to end. The mission itself was unplayable:

| Problem | Cause | Fix |
|---|---|---|
| AI F-4E "crashed after takeoff", crew ejected | Ford 1 and Ford 2 were air-started at the **same coordinates and altitude** and collided at t=1 s. The intended 7-minute launch delay was ignored, because DCS takes an air group's spawn time from its first waypoint's ETA. | Each spawned flight gets its own lateral offset and altitude band, and aircraft inside a flight are spread out. Delays are written to wp0 ETA. AI strikes now air-start at the distance that gets them to the target *with* the player, instead of over their own base. |
| Collision counted as an "air kill" on a friendly F-4E | Ingest credited every KILL event. | Collisions and same-side kills give no credit. Losses are still counted. |
| AI wingman (Enfield 1-2) taxied for ~7 minutes | Ramp start (also seen in Spike C) | The player flight now defaults to a **hot runway start**. `--start ramp` picks adjacent parking spots, and `--start air` starts ~50 nm from the target. |
| Callsign showed "Enfield" / "New callsign" | The campaign callsign "Buick" isn't a DCS western callsign, and the pilot name comes from the player's DCS Logbook profile. | Squadron callsigns must be DCS western callsigns (496th TFS → **Colt**), and units get matching radio callsigns. The pilot name is set in DCS → Logbook. |
| 119 nm transit with nothing happening | Hahn is far from the Fulda front. | The 496th TFS disperses to **Rhein-Main AB** (Frankfurt, 72 nm to Geisa). |
| Overwhelmed by MiG CAP; AI strikers jettisoned and fought | Every enemy CAP near the route spawned (4 flights, 8 MiGs at once), and AI strikers had default ROE. | Enemy CAP flights roll for contact, with at most 2 per mission and the second as a +10 min wave. Friendly CAP on the route now joins strike missions. AI strikers get "attack assigned targets only", "evade fire" and "no jettison". |

### Second live flight (2026-11-23): findings and fixes
The second flight was a Colt 2 strike from Rhein-Main. The player crashed during the attack, so that run was not ingested and is being re-flown.

| Problem | Cause | Fix |
|---|---|---|
| Ford 2 (SEAD) carried only 3 Sparrows and 2 Shrikes, and never fired a Shrike | The SEAD loadout combined 4 Shrikes with a centreline tank, a combination no Heatblur payload uses, and DCS silently strips stores from invalid combinations. The same check found two more bad combinations: Sidewinders beside a TER (CAS_ROCKEYE) or beside an LAU-88 (BAI_MAVERICK). | All 7 loadouts now match the combinations in Heatblur's 131 distinct shipped payloads. Missile variants (AIM-7E/E-2/F, AIM-9 family) count as interchangeable. A test enforces this (`loadouts.unseen_pairs`). |
| Friendly CAP (Colt 1) only engaged after the MiGs had fired: it shot first at t=706 s, the MiGs at 652 s, and Ford 2 died at 679–690 s | It orbited its planned station 40 km behind the front, the strikers were 70 km ahead, and no side had early-warning radar. | CAP covering a player strike now orbits 30 km short of the target, with *Engage targets in zone* (60 km around the target). EWR sites spawn on both sides: Wasserkuppe (NATO, FPS-117 standing in) and Großer Inselsberg (Warsaw Pact, 55G6). |
| AI strikers died without fighting back | ROE was "attack assigned targets only". | ROE is now "assigned target first, weapons free": they keep their bombs (no jettison) and shoot back. |

The SA-9 (Strela-1) with the Geisa artillery shot down Ford 1-1 and damaged Colt 2-2. That's working as intended.

## Extensibility principles (binding for Phase 1 onwards)
The campaign state is the war. Each DCS mission is only a window onto one slice of it. Phase 1 may fill in only a few mission types, but its data model must follow these rules from the start, because retrofitting them later is the expensive part.

1. **Every sortie exists.**
   - Each turn, both sides build a full ATO of `Package` → `Flight` → `Task`: CAP, strike, SEAD, recon, CAS/BAI, interdiction, escort, airlift, convoy escort, and so on.
   - The player picks *any* flight they can fly. Nothing is tied to "the player's F-4E mission".
2. **One results pipeline.**
   - Flights the player doesn't fly are resolved by the abstract combat model: detection, SAM and fighter threat, escort, skill, weather.
   - That model produces the **same results structure** as a flown mission (losses, kills, BDA, pilot fates, cargo delivered).
   - Both go through the same `ingest` code into the campaign state. Flown and simulated sorties differ only in fidelity.
3. **The mission contains whatever overlaps it.**
   - The generator spawns the player's flight plus every flight, convoy and site that overlaps it in time and space, as AI. Examples: enemy CAP on the route, SAMs at the target, a friendly package ahead of you.
   - Everything else stays abstract.
4. **Persistent people and airframes.**
   - Squadron rosters hold individual pilots (sorties, kills, fatigue, status KIA/MIA/POW/WIA) and individual airframes, linked to the HB F-4E `Quality`/`Wear` properties.
   - Simulated losses remove real roster entries.
5. **Logistics is a network, not a number.**
   - Depots, airfields, rail and road links and their capacities feed supply to front-line sectors.
   - Convoys, trains and airlift are entities that move each turn and can be intercepted. Ground combat consumes supply.
   - Interdiction, convoy escort and transport missions therefore all act on the same model.
6. **Aircraft-agnostic player slots.**
   - Loadouts, mission types and the briefing and kneeboard templates come from a per-airframe catalogue. `loadouts_f4e.yaml` becomes one entry in that catalogue.
   - Adding a flyable type means adding data, not code.
7. **The runtime tracks objectives, not just groups.**
   - The in-mission script reports generic outcomes: group status, zone arrival (convoy reached depot), cargo/landing at a destination, and target destroyed.
   - New mission types reuse these instead of adding special cases.

**Logistics flying:**
- AI airlift uses the period-correct AI `C-130` (pydcs `C_130`).
- For player-flown transport, the **C-130J-30** (pydcs `C_130J_30`, flyable) is an **accepted anachronism**: it stands in for the C-130E/H that USAFE actually flew in 1976. The era filter allows it as a configurable exception, enabled by default when the module is installed.
- Era-correct helicopter alternatives, if they're added later: UH-1H and Mi-8.

## Repo layout
```
ColdWarDynamicDCS/
  pyproject.toml            # uv/pip, Python 3.12, deps: pydcs, pydantic, fastapi, uvicorn, pillow, pytest
  cwdc/
    model/                  # pydantic: Campaign, Theater, Side, Squadron, Pilot, Unit, Sector, Objective, Package
    data/                   # theater/germany_cw.yaml (bases, sectors, objectives), oob_1976.yaml, loadouts_f4e.yaml, units_era.yaml
    engine/                 # turn.py, ground.py, air_commander.py, logistics.py, crisis.py, intel.py
    missiongen/             # builder.py (pydcs), flights.py, groundspawn.py, briefing.py, kneeboard.py
    runtime/lua/            # cwdc_runtime.lua (embedded in .miz), CWDC-hook.lua (installed into Saved Games)
    ingest/                 # results.py: JSON -> losses/kills/pilot status -> engine
    dcs_paths.py            # locate Saved Games\DCS, install hook, output folders
    web/                    # FastAPI app + static/ (JS, Leaflet, CSS)
  tools/                    # extract_airfields.py, extract_f4e_payloads.py, list_stock_units.py
  tests/                    # engine unit tests, missiongen smoke tests (generate + parse .miz), ingest fixtures
  docs/design.md            # this design, kept up to date
```

## Roadmap
**Phase 0: Setup and spikes (start here once approved)**
1. Install Git and Python 3.12 with winget. You approve the installs.
2. Clone `ColdWarDynamicDCS`. **I need the GitHub URL or owner.**
3. Set up the project skeleton, `.gitignore` and `docs/design.md`.
4. Spike A: confirm that `dcs.terrain.germany` is GermanyCW, and dump its airfields into `tools/extract_airfields.py` output.
5. Spike B: pull the F-4E pylon/weapon CLSIDs out of `Mods\aircraft\F-4E` (read-only) and compare them with pydcs's F-4E definitions.
6. Spike C: generate a minimal `.miz` (player F-4E cold start, one MiG-21 target, runtime script embedded), install the hook, and have **you** fly it. Confirm that the results JSON appears with the right kill/landing data.

**Phase 1: Playable loop on the command line**
- A hard-coded small theater.
- `cwdc new` creates a campaign, `cwdc generate` writes the next mission, `cwdc ingest` processes the results.
- One player mission type each for CAP and strike, with losses carried over between missions.

**Phase 2: Strategic engine** (detailed plan, 2026-11-23)

Testing is by `cwdc autorun N` (abstract turns in seconds) plus occasional `cwdc fly <ID> --bot` AI-only DCS missions. The player is not needed to develop this phase. Build in this order; each step lands with tests and an autorun sanity check.

1. **Ground war: sectors and formations** (`engine/ground.py`)
   - The front is cut into **sectors** along the inner German border: Göttingen, Fulda Gap, Rhön/Meiningen and Hof corridor.
   - Each sector holds **formations** for both sides, e.g. 11th ACR and 3rd Armored Division brigades against 8th Guards Army regiments. Each formation has combat strength, posture (attack, defend or reserve), readiness and supply.
   - Each turn, every sector resolves Lanchester-style attrition. It is modified by force ratio, posture, terrain, supply, artillery, and the CAS/BAI damage that turn's flights dealt to that sector's units.
   - The result moves the sector's **front offset** (km gained or lost).
   - The front-line polyline is rebuilt from the sector offsets, so `side_at` changes as the front moves. Airbases, objectives and supply nodes can be **overrun and change hands**.
2. **Objectives belong to formations.**
   - Today's fixed objectives (the Vacha armour, the Geisa artillery, ...) become the visible sub-units of formations, positioned relative to the moving front.
   - Destroying their vehicles reduces the parent formation's strength.
   - Formations rebuild and re-spawn sub-units from supply. This fixes "the target list runs dry after ~2 days".
3. **Reinforcement schedules**
   - Warsaw Pact: second-echelon armies arrive from day 3–4.
   - NATO: REFORGER units arrive later.
   - Both are data-driven, from the OOB file.
4. **Logistics flow** (`engine/logistics.py`)
   - Each turn supply flows along `SupplyLink`s, from rear rail heads to forward depots to formations, limited by link capacity and damage.
   - Low supply cuts combat power and regeneration.
   - **Interdiction targets** become real: bridges and rail junctions are objectives tied to links. Damage reduces capacity, and engineers repair it over turns.
   - Convoys appear as moving objectives on road links. This is the hook for later player airlift and logistics missions (C-130J).
5. **Air commanders, take 2** (`engine/planner.py`). The planner scores needs, then builds **multi-flight packages** (strike + escort + SEAD) instead of single flights. Needs considered:
   - CAS where a sector is critical;
   - interdiction of the links feeding the strongest enemy push;
   - air superiority against enemy CAP strength;
   - OCA against airbases.

   Sortie rate is limited by aircraft and crews, airbase status and the period (night). Both sides use the same logic, with doctrine weights per side.
6. **Airbase damage**
   - OCA strikes cut runways and parking, which reduces a base's sortie rate. Repair takes turns.
   - Overrun bases are lost: their squadrons relocate, or their aircraft are lost.
7. **Crews**
   - Two-seat F-4E crews (pilot + WSO), each a persistent person.
   - Fatigue limits sorties per day. Recovery and rescue rules stay as in Phase 1.
8. **Campaign outcome**
   - Warsaw Pact victory: the front reaches the Main/Rhine line (Frankfurt), or NATO air power is exhausted.
   - NATO victory: the front holds for N days and then a counter-offensive regains the border.
   - The end state, with a war summary, is shown in `status`.
9. **Balance pass** using `autorun` statistics: per-sortie loss rates of roughly 1–4%, a front that moves tens of km over the first days rather than collapsing in hours, and air losses that respond to SEAD and escort.

**Progress (2026-11-23): steps 1–3 done** (`engine/ground.py`)
- **Sectors:** four (Göttingen, Fulda, Rhön/Meiningen, Hof), each owning a stretch of the inner German border.
  - Each sector's `offset_km` pushes its points along the normal pointing into NATO territory.
  - The rebuilt polyline drives `side_at`.
- **Formations:** 18 in total, of which 11 are reinforcements. Their strength is abstract.
  - The attacker is the side with the better odds: the Warsaw Pact attacks at 0.9× terrain-adjusted odds, NATO counter-attacks at 1.6×.
  - Attrition is Lanchester-style, and a prepared defence in good terrain takes fewer losses.
  - Ground gained is 5 km per 1.0 of odds above 0.8, capped at 8 km per turn.
  - A supply-by-depth stand-in limits deep advances until step 4.
- **Visible elements:** objectives with `formation_id` move with their sector. Air kills cost the formation `unit_points × 1.5`, and elements rebuild while the formation lives. The "target list runs dry" problem is fixed.
- **Overruns:** airbases change hands, their squadrons evacuate and lose 25% of their aircraft on the ground, and damaged aircraft are lost. Static sites and supply nodes behind the new front are lost.
- **Reinforcements:**
  - Warsaw Pact: 1st Guards Tank Army from turns 9 and 13, and the 20th Guards Army from turns 17 and 25 (committed where the attack is going best).
  - NATO: 8th ID turn 7, 3rd ID turn 11, 1st AD turn 15, then REFORGER 1st Cav turn 21 and 2nd AD turn 29 (committed to the most threatened sector).
- **First balance read**, 4 seeds × 10 days with no player:
  - The Fulda Gap is the main axis: about 5 km by day 2, 25 km by day 4 and 50 km by day 7, stabilising around 55 km.
  - Rhön sees small NATO counter-attacks; Göttingen and Hof hold.
  - No airbase falls.
  - The full balance pass is step 9. Simulated air losses currently favour the Warsaw Pact, roughly 2:1.
- **Tooling:** `status` shows the ground war, and `autorun` shows ground power and front position per turn. Older campaigns pick up the ground war automatically through `sync_theater`.

**Progress (2026-11-23): step 4 done** (`engine/logistics.py`)
- **Network:** 12 nodes and 10 links.
  - NATO: the Frankfurt rail head feeds depots at Kassel, Bad Hersfeld, Schweinfurt and Bayreuth, plus a second-line depot at Alsfeld.
  - Warsaw Pact: the Leipzig rail head and the Erfurt hub feed Nordhausen, Eisenach, Meiningen and Plauen.
- **Each turn:**
  - Production arrives at the rail heads.
  - Flow moves downstream in BFS order. Each link carries at most capacity × (1 − damage), limited by space at the far end, and a node shares its stock between outgoing links by capacity.
  - Formations draw from their sector depot: 0.15 per strength point, ×1.5 when attacking. What they get, times a depot-distance factor, smooths into `Formation.supply`, which the ground war uses. This replaces the supply-by-depth stand-in.
  - Engineers repair 15% of link damage per turn.
- **Interdiction:**
  - Each link has one target: a rail yard (station, warehouses and fuel tank as DCS **statics**, with an AAA vehicle) or a halted road column.
  - The fraction of the target destroyed becomes link damage, and the target rebuilds as the link is repaired.
  - Hitting a depot burns stock.
  - If a depot is overrun, the sector draws from the nearest friendly node.
- **Planning:** the planner flies **Interdiction** (23rd TFS and the MiG-27s), preferring targets that feed an enemy offensive.
- **DCS side:**
  - Statics spawn one per structure, named `<objective>#<i>`.
  - AI flights get a `Bombing` task on structure sites.
  - The runtime snapshots `tracked_statics`, and ingest counts a missing or dead static as destroyed.
  - This is still unverified in DCS: a bot mission would confirm it.
- **Bug found by the new tests:** the AI focus flight of a `--bot` mission had no attack task, because it was treated as the player's flight. Fixed.
- **Balance read** (4 seeds, 10 days):
  - Outcomes now vary with interdiction. In some seeds Fulda stalls at about 7 km and Rhön breaks to about 16 km; in others Fulda reaches 36–42 km.
  - Every seed reaches a stalemate after about day 7, with no NATO counter-offensive yet. That's for step 9.

**Progress (2026-11-23): step 5 done** (`engine/planner.py`)
- **Needs-based air commanders:**
  - Each side mans its barrier-CAP stations first (doctrine: NATO 1, relying on escorts and the Hawk belt; Warsaw Pact 2).
  - Then it scores needs:
    - **CAS** against enemy formation elements in sectors where the enemy is attacking, weighted by enemy/own power;
    - **BAI** elsewhere, with a bonus where its own side is attacking;
    - **interdiction**, with a bonus for links feeding an enemy offensive;
    - **SEAD** against SAMs;
    - **strikes** on EWR and command sites.
  - Needs are filled in priority order by the best squadron: specialists first, then the nearest. Role discipline means a squadron only flies a need's first two acceptable tasks, so the 23rd TFS doesn't drop CAS bombs.
- **Packages:** a main flight, plus a **SEAD** flight if the target sits under an enemy SAM umbrella, plus an **escort** if enemy CAP is within 120 km. NATO escorts everything threatened; the Warsaw Pact only escorts high-value targets.
- **Sortie limits:** sorties per squadron, ready aircraft and crews, and night (CAP only).
- **Resolver:** support flights ride with their package. Interceptors meet the escort first, and only 30% leak through to the strikers. SEAD still suppresses the covering SAM.
- **Mission builder:**
  - The player's whole package is spawned.
  - AI escorts of AI packages get an `Escort` task on their main flight.
  - If the player flies the escort (or SEAD support), the main AI flight launches overhead the player's base after the taxi time, so they join up.
  - Escorts fly high (no low-level waypoints).
- **Bug caught by a test:** in bot missions, AI escorts weren't attached to the focus flight. Fixed, and the test now checks the escorted group id.
- **Balance read** (4 seeds, 10 days):
  - Air losses are lower and more even: 20–34 per side, against NATO 38–60 before.
  - The Fulda front ends at 11–40 km.
  - One seed produces a NATO counter-offensive 36 km into the GDR at Göttingen.
- **Still to do:**
  - OCA against airbases arrived with step 6 (below).
  - Doctrine weights will be tuned in step 9.

**Progress (2026-11-24): step 6 done** (`engine/airbases.py`)
- **Airfield objectives:** every airbase has one (`ab_<id>`):
  - four parked aircraft of the resident squadron's type, as DCS plane statics on the base's outermost parking slots, so they never block the runway;
  - two AAA guns (Vulcan / ZSU-23-4) just outside them.
- **Damage:** destroying a parked aircraft, flown or simulated, is one event:
  - the base takes 15% damage;
  - one ready aircraft of a squadron based there is destroyed on the ground.
- **Effects:**
  - Damage cuts the sortie rate (`round(sorties × (1 − damage))`). At 70% the base is closed and flies nothing.
  - Repair takes 10% a turn, except in a turn the base was hit. Parked aircraft and guns return as it recovers, so it can be hit again.
- **Overruns:** an overrun base changes hands wrecked (closed) and its squadrons evacuate as before.
- **Planning:**
  - **OCA** needs target enemy airfields that host squadrons: score `(2 + 1.5 × squadrons) × (1 − damage) × doctrine`, with NATO at 0.8 and the Warsaw Pact at 1.3.
  - They're flown as Strike packages with escort and SEAD like any other.
  - Empty bases (Hahn) and closed bases aren't targeted.
- **Mission builder:**
  - Plane statics are supported.
  - AI `Bombing` aims at the centroid of the structures rather than the objective reference point, which also helps rail yards.
  - The player's ramp slots avoid parking slots holding statics.
  - `status` lists damaged airfields (`--all`: every airfield).
- **Balance read** (4 seeds, 10 days):
  - The Warsaw Pact flies 12–20 OCA raids. Rhein-Main and Spangdahlem peak at 30–50% damage, losing 11–23 aircraft on the ground.
  - NATO flies none: its six sorties a turn go to CAS and interdiction, which score higher. That's left to step 9's doctrine tuning.
- **Known gaps:**
  - Runway craters from bombs that miss everything produce no event, so a flown OCA only counts what it destroys.
  - Static aircraft death detection (exists / life ≤ 0) is unverified in DCS.
  - A player squadron whose base is closed sits the turn out (relocating on a long closure is a later refinement).

**Progress (2026-11-24): step 7 done** (`engine/crews.py`)
- **Two-seat crews:**
  - Types with `crew: 2` in `airframes.yaml` (the F-4E) fly with a pilot and a **WSO**, each a persistent person with their own sorties, kills, fatigue and fate.
  - Flights carry `wso_ids` alongside `pilot_ids`.
  - Each F-4E squadron gets as many WSOs as pilots, plus WSO replacements. Older campaigns get theirs on load.
  - Pilots and WSOs settle into regular pairs because both are scheduled freshest-first.
- **Fatigue:**
  - Each sortie adds 30%. A turn on the ground takes off 15%, or 30% at night.
  - Crews at 80% or more aren't scheduled, which works out to about two sorties a day sustained, with a third possible on a fresh day. A day never exceeds three.
  - Tired crews fight worse, down to −25% air and strike effectiveness at full fatigue.
  - The player isn't auto-scheduled; `fly` warns when the player is tired.
- **Fates:**
  - The resolver ejects a lost aircraft's crew together, or not at all. Each person then has their own luck (recovered/WIA, or POW/MIA).
  - DCS ingest maps the F-4E's `F-4E_Parachute_Pilot` and `F-4E_Parachute_WSO` landings to each seat, so a WSO who drifts across the front is captured while the pilot is recovered. These names are confirmed from the live flights' logs.
- **Kills** are shared by the crew (USAF practice).
- **Display:**
  - The mission manifest records each aircraft's WSO.
  - The briefing lists the crews.
  - `roster` shows pilots and WSOs with fatigue.
  - `status` shows WSO counts.
- **Balance read** (3 seeds, 10 days):
  - Aircraft losses are unchanged from step 6 (NATO 39–57).
  - NATO aircrew losses rose to 46–74, because a Phantom now costs two people.

**Progress (2026-11-24): step 8 done** (`engine/outcome.py`)
- **Victory conditions** live in the theater's `victory` block and are checked at the end of every turn.
- **Warsaw Pact victory** comes in three ways:
  - **The Main is reached:** Frankfurt, Aschaffenburg or Würzburg falls into Warsaw Pact territory.
  - **Breakthrough:** a sector breaks through 100 km deep.
  - **Air exhaustion:** NATO's serviceable aircraft drop below 30% of establishment.
- **Front geometry note:** the "Fulda Gap" sector (the Hersfeld stretch) pushes west toward Giessen. The drive on the Main comes through Rhön: Schweinfurt falls at 45 km, Würzburg at 80 and Aschaffenburg at 95. So the breakthrough rule covers a deep Fulda push.
- **NATO victory:** after 7 days, every sector is back on or beyond the border.
- **Ceasefire** at day 21, judged by mean Warsaw Pact penetration:
  - 20 km or more: Warsaw Pact gains;
  - 5 km or less: NATO holds;
  - otherwise a stalemate.
- **Once decided:**
  - `Campaign.outcome` is set and the ATO is cleared.
  - `end_turn` refuses further turns.
  - `ato`, `fly`, `skip` and `autorun` say the war is over, and `autorun` stops on the deciding turn.
- **`status` while the war lasts:**
  - war day;
  - distance from the front to the nearest Main city;
  - deepest penetration against the breakthrough depth;
  - both sides' air strength.
- **`status` once it's over:** the war summary, including:
  - outcome, reason and duration;
  - the final front;
  - per side: sorties, aircraft lost, air kills, aircrew killed/missing/captured, ground strength and objectives lost;
  - the player's record, including missions flown in DCS (a new `flown_in_dcs` counter).
- **Balance read** (4 seeds, full wars):
  - **Seed 1:** stalemate ceasefire on day 21, with Fulda at +40 km.
  - **Seeds 2–4:** Warsaw Pact victory by NATO air exhaustion on days 15–21. NATO loses 82–99 aircraft against 38–67.
  - **NATO never wins:** the Fulda front stalls at about +40 km and never returns to the border.
  - **For step 9:** NATO's per-sortie attrition, and its thin order of battle (two F-4E squadrons), are the obvious levers. So is a NATO counter-offensive once Warsaw Pact formations wear down.

What's deliberately left for later phases: the web UI (Phase 3), the crisis phase, the MAGIC controller and kneeboards (Phase 4), and the immersion lockdown (Phase 5).

**Phase 3: Web UI**
- Campaign creation, theater map, briefing/ATO selection, generate-mission button, automatic ingest by watching the results folder, debrief and squadron pages.

**Phase 4: Crisis phase and immersion**
- Tension model, QRA/intercept/recon missions, ROE enforcement, the war-start event.
- Briefings, kneeboards, news and weather.
- **"MAGIC" radar controller** (requested 2026-11-23, modelled on the Heatblur and Contention servers), part of `cwdc_runtime.lua`:
  - An F10 radio menu for each player group (`missionCommands.addCommandForGroup`).
  - **Bogey dope:** BRAA (bearing, range in nm, altitude in thousands of feet, aspect hot/cold/flank) from the player to the nearest hostile aircraft *that friendly radar holds*.
  - **Picture:** hostile groups as bullseye calls.
  - Optional automatic threat calls when a bandit closes inside a set range.
  - Replies are sent as `trigger.action.outTextForGroup`. Recorded voice clips may come later.
  - Detection is realistic, not god's-eye: contacts come from friendly radar emitters (EWR sites, SAM search radars, AI fighters' radars) via `Controller:getDetectedTargets`, so low flyers can hide under the coverage. The theater therefore needs friendly EWR/GCI sites.
  - The callsign is configurable and defaults to **MAGIC**. In 1976 Europe the real equivalent was ground GCI/CRC; NATO E-3s arrived in 1977–82.

**Phase 5: Polish**
- Balance passes, campaign presets (start date 1974–79, other squadrons), packaging into a one-click launcher, multiplayer client slots.
- **Immersion lockdown, once all phases are finished** (requested 2026-11-23; kept on for now to help testing):
  - Generated missions force DCS options: **labels off**, and **F10 map without aircraft or contact icons** (own aircraft only, or map only).
  - Uses pydcs `mission.forced_options`. It becomes a campaign "realism" setting that defaults to on in the release.

## Verification
- **Engine and ingest:** pytest with recorded JSON fixtures, so turn resolution is deterministic given a seed.
- **Mission generator:** smoke tests generate a `.miz`, reload it with pydcs, and check groups, payloads and the embedded scripts.
- **End-to-end (manual, per phase):**
  1. Generate a mission.
  2. You fly it in DCS.
  3. Confirm the hook wrote the results JSON.
  4. Ingest it and check in the UI that losses, kills and the front line updated.
- **The Phase 0 Spike C round trip must pass before Phase 1 starts.** It is the riskiest assumption.

