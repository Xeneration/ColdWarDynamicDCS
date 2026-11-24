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

**Progress (2026-11-24): step 9 done (balance pass); Phase 2 complete**
- **Tooling:** `tools/balance.py` plays whole wars across seeds. It reports:
  - per-sortie air losses by side, cause and task;
  - the front on days 1/3/7/14/21;
  - NATO attack activity, air strength and outcomes.
- **Baseline problems found:**
  - **Air losses** were 7–8% per sortie for both sides.
  - **NATO lost about 200 aircraft on the ground**, more than in the air, to Warsaw Pact OCA. That's what exhausted its air power.
  - **The front froze at day 7.** Red kept "attacking" at effective odds of 0.7, which gained nothing, and 1% regeneration refilled both sides.
  - **NATO never counter-attacked**, because it needed 1.6:1 raw odds.
  - **Air power barely touched the ground war:** NATO air removed about 7% of Warsaw Pact strength in 14 days, and fronts were the same with air kills switched off.
- **Changes:**
  - **Resolver:** base air loss 0.15 → 0.07, SAM 0.07 → 0.05, AAA 0.03 → 0.015.
  - **OCA:**
    - a parked-aircraft hit is a real aircraft only 40% of the time (shelters, dispersal, decoys);
    - 12% damage per hit;
    - Warsaw Pact OCA weight 1.3 → 1.0.
  - **Ground: commanders attack on *expected* odds,** that is power ÷ (defender × defence × terrain):
    - Red attacks at 0.85 or better, NATO at 0.9 or better;
    - otherwise both dig in.
  - **Culmination:**
    - attacking costs 4% readiness a turn, and not attacking restores 3% (minimum 40%);
    - regeneration is 0.6% a turn.
  - **Defence bonus:** a defender on ground taken this war has a hasty 1.05 defence instead of the prepared 1.3, so counter-offensives can roll a salient back.
  - **NATO forward defence:**
    - NATO restores the border and goes at most 10 km into the GDR (it was 40 km);
    - NATO commits reinforcements to the deepest penetration (over 5 km), otherwise to the most threatened sector.
  - **Air-to-ground weight:** `AIR_KILL_SCALE` 1.5 → 4, since a visible vehicle stands for a company-sized slice. CAS and BAI now matter, and so do the player's strikes.
  - **War plan per campaign** (`war_plan` in the theater):
    - the 1st Guards Tank Army's two divisions go to a rolled main axis: Fulda 40%, Rhön 35%, Göttingen 15%, Hof 10%;
    - reinforcement arrivals slip (Warsaw Pact ±2 turns, NATO −2/+4).
- **Result** (8 seeds, 21 days):
  - **Per-sortie air losses:** NATO 3.5% (air 2.3, SAM 0.5, AAA 0.7), Warsaw Pact 4.2%.
  - **Outcomes:** 1 Warsaw Pact victory (a Fulda collapse, day 8), 1 ceasefire with Warsaw Pact gains, 4 stalemates, 2 ceasefires with NATO holding.
  - **The front:** typically +2 to +23 km by day 3. Salients get pinched back as NATO concentrates, and the Warsaw Pact shifts to quieter sectors.
  - **Guard rail:** `tests/test_balance.py` keeps the first week inside this band.
- **Still open:**
  - **NATO never wins decisively.**
  - **NATO flies no OCA:** its six sorties a turn always find higher-value CAS and interdiction needs, so the player never gets an OCA tasking yet.
  - **NATO's order of battle is thin** (two F-4E squadrons against three Warsaw Pact squadrons). More squadrons or a REFORGER air deployment would be the next lever, best judged once real flights show how much one player changes things.

What's deliberately left for later phases: the web UI (Phase 3), the crisis phase, the MAGIC controller and kneeboards (Phase 4), and the immersion lockdown (Phase 5).

**Phase 3: Web UI**
- Campaign creation, theater map, briefing/ATO selection, generate-mission button, automatic ingest by watching the results folder, debrief and squadron pages.

**Progress (2026-11-24): Phase 3 first cut done** (`cwdc/web/`, `cwdc/actions.py`)
- **`cwdc/actions.py`:** a shared action layer used by both the CLI and the web server.
  - Actions: create, fly, result status, load result, ingest, skip, rollback, list campaigns.
  - The CLI commands are now thin printers over it, so both front ends behave identically.
- **Model:**
  - `MissionRecord.briefing` keeps the briefing written into the .miz.
  - `Campaign.debriefs` keeps the last 60 turns' debriefs, with a flown-in-DCS flag.
- **`cwdc serve`:** FastAPI on 127.0.0.1:8076 that opens the browser.
  - It reads the campaign from disk on every request, and one lock serialises the changing actions, so the CLI can be used alongside it.
  - **JSON API:** campaigns, overview, version (a cheap change check with result status), map layers, ATO, squadron, debriefs and snapshots, events; POST new, fly, ingest, skip, rollback and settings.
- **Auto-ingest:** a background watcher checks every 5 s.
  - It ingests a pending mission once the hook's result for it is fresh and `final`, which happens when the player leaves the mission.
  - It posts an event; the UI then shows a toast and opens the debrief.
  - It can be switched off in the UI or with `--no-auto-ingest`.
- **Front end:** a single page (`web/static/`: plain HTML/CSS/JS, Leaflet 1.9.4 from unpkg, OpenStreetMap tiles) with a map plus ATO / Mission / War / Squadron / Debrief / News tabs, a new-campaign dialog, skip and roll back.
  - **Map layers:** front and border, sector labels, airbases with damage, objectives by strength, SAM rings, NATO flights, Warsaw Pact flights (intel; off by default), supply network, CAP stations.
- **Verified:**
  - `tests/test_web.py` covers the API, mission generation, error codes, skip, debrief, rollback, and auto-ingest on and off.
  - A browser check against a throwaway campaign covered: generating a bot mission from the ATO, the Mission tab waiting on DCS, a simulated final result being auto-ingested (turn closed, debrief opened), and the War tab rendering.
- **Not yet:**
  - It hasn't been used with a real DCS flight.
  - There's no squadron selector for Warsaw Pact squadrons, and no per-flight briefing preview before generating.
  - The map draws Warsaw Pact-held territory only as the moved front line (no shaded area).
  - Phase 5's immersion lockdown will need an option to hide enemy intel layers.

**Progress (2026-11-24): web UI made self-explanatory**
- Map: Esri shaded relief default (topographic / dark / none switcher), 1976 place names, NATO-style SVG symbols (`symbols.js`) for formations (now on the map), sites and airbases; territory tint, hatched captured ground, attack arrows sized by odds; plain-English tooltips on everything (`web/mapdata.py`); legend; nm scale; zoom-dependent detail; selected flight shows route, distance/time and the SAM rings it crosses.
- Guidance: "what next" stepper card, first-run guide, glossary tooltips, Help dialog.
- ATO as package cards with the planner's reason (`Package.reason`), distance/time, threat level, loadout description, explained start options and a read-only mission preview (`GET .../flights/{id}`).
- Mission checklist with copy-file-name / open-folder (local only); War tab gauges, history charts (`Campaign.history`), sector cards, supply bars; crew cards; visual debriefs (`Debrief.stats`). Rhein-Main shown by its real name (theater `name`).
- Not yet: checked only at 800 px pane width and against a throwaway campaign; the dense Fulda area at zoom 7 still overlaps.

**Progress (2026-11-24): unattended soak testing (`cwdc soak`)**
- **Goal:** collect many AI-only DCS missions overnight, to find generation bugs and immersion problems and to calibrate the resolver against DCS.
- **Approach:** DCS in server mode with no rendering (`DCS.exe --server --norender -w DCS.cwdc_soak<N>`), using the normal install. No binary mod is involved.
  - `net.load_mission` (server only, per `API/Sim_ControlAPI.html`) lets a hook rotate missions forever.
  - `CWDC-soak-hook.lua` talks to the runner through `CWDC\soak\command.json` / `heartbeat.json`. Between missions it waits in an empty `CWDC_Lobby.miz`, and it unpauses the server on every mission start.
- **Ruled out:**
  - **Time acceleration:** there's no API for it, and none on servers.
  - **Single-player with Ctrl+Z keypresses** (the `dcs-unit-tester` fallback): it needs window focus and runs as one instance.
  - **A DLL mod:** undocumented, risks the integrity check, and breaks with every patch.
- **Runner** (`cwdc/soak/runner.py`):
  - The parent starts one worker subprocess per instance, each with its own `CWDC_DCS_WRITEDIR` and `CWDC_CAMPAIGNS_DIR`, and plays simulated wars meanwhile.
  - Each worker plays whole scratch campaigns, one bot mission per turn: an air start, a cap, and the least-sampled task first.
  - Watchdog: crashed, stalled or failed loads lead to a restart and a skipped turn. Past the cap, the mission is aborted to the lobby.
- **Data** (`record.py`):
  - The DCS outcome against the resolver's mean over 40 draws on a copy of the campaign;
  - timings;
  - flags: `early_loss`, `no_weapons_released`, `never_near_target`, `nothing_happened`, `time_limit`, `not_final`;
  - `dcs.log` errors since the mission loaded.
  - The report goes to `report.html` / `report.md`.
- **Spike** (2026-11-24, 1 instance):
  - The server started with no login or firewall prompt, and both hooks loaded.
  - The lobby loaded in about 20 s and started paused; the hook unpaused it.
  - `net.load_mission` from the hook loaded the bot mission in about 8 s, and the runtime and results hook collected as usual.
  - It runs at 1× real time. Working set: about 4 GB in the lobby, **about 18 GB in a Cold War Germany mission** (no rendering). With 62 GB, plan on 2 instances and at most 3.
- **First overnight run** (2026-11-24, 8 h, 2 instances):
  - Results: 19 DCS missions, no crashes or restarts, no `dcs.log` errors, and 200 simulated wars.
  - The focus flight lost 25–58% of its aircraft per mission in DCS, against about 4% in the resolver. Red losses were 5–20× higher too.
- **Loss analysis.** NATO lost 32 aircraft:
  - SA-6: 8;
  - SA-9: 7;
  - no hit at all below 700 m, so most likely the AI flying into terrain: 7;
  - MiG-21: 6;
  - ZSU-23-4: 2;
  - MiG-27: 1.
  - The Warsaw Pact lost 44 MiG-21s, 33 of them to AIM-7s.
- **What caused the losses:**
  - The SA-6 kills were escorts and covering CAPs orbiting at 4–6 km over the target's SA-6. They were not the low-level attackers.
  - Red CAP flights sometimes spawned inside Sparrow range and died at 85–90 s.
  - An AI focus escort was built before its package existed, so it orbited forever. AI CAPs and escorts never went home, so every Escort and many CAP missions ran to the time limit.
  - Enemy CAPs "in contact" just orbited their own station 60+ km away, so half the CAP missions saw nothing.
- **Fixes in `missiongen/builder.py`:**
  - Routes bend round enemy SAM rings that don't defend the target (`_path`/`_avoid`). This includes the map's route line, the air-start point and the way home.
  - CAP stations and escort holds stay outside SAM cover (`_clear`).
  - Escorts stay with the package as far as the IP, hold outside the target's SAM ring for 8 min, then go home.
  - Enemy fighters spawn at least 50 km from the player.
  - Enemy fighters in a mission engage around the player's station or target.
  - A CAP with no enemy at all gets a delayed sweep by the nearest enemy CAP flight.
  - An AI-flown focus CAP or escort orbits for 30 min, then lands.
  - **AI skill:** NATO crews and Warsaw Pact fighter pilots are Good, and Warsaw Pact strike crews are Average. Veterans (15+ sorties or 3+ kills) go up one level, and tired crews (fatigue at or above 0.6 × the grounding level) go down one.
- **Not fixed:**
  - The unexplained terrain crashes. These look like DCS AI behaviour.
  - One BAI flight that turned home without reaching its target, for no clear reason.
  - The resolver itself is not recalibrated yet. That waits for a second soak with these fixes, so the multipliers are fitted to the new missions.

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
- **NATO airfield raids (OCA)** (requested 2026-11-24). The step 9 balance pass showed NATO never flies OCA: its few sorties always find higher-scoring CAS and interdiction needs, so the player is never offered an airfield strike. The plan:
  - **A NATO counter-air effort in doctrine:** a guaranteed share of strike sorties for OCA (for example one package a day) when the Warsaw Pact air threat justifies it. "Justified" means its sortie rate, its raids on NATO bases, or air superiority slipping. This replaces competing on raw need score.
  - **Mission design for the player:**
    - a low-level ingress, with SEAD/escort where the target is covered;
    - a target briefing on the parked aircraft, shelters and AAA of Merseburg, Allstedt or Köthen;
    - reasonable transit lengths: an air start or a forward recovery base, since the Warsaw Pact fields are about 250–300 km from Rhein-Main.
  - **Likely needs a stronger NATO order of battle,** or a REFORGER air deployment, so OCA isn't paid for by the land battle.
  - **Validate in DCS** that static parked aircraft and their destruction are detected (the exists / life ≤ 0 check from step 6).

**Progress (2026-11-24): Phase 4 step 1 done: crisis phase** (`engine/crisis.py`)
- **Campaign state:** `phase` (crisis or war), `tension` (0–100), `war_start_turn`, `crisis_deadline_turn`, `war_turn`. The schema is now v2; older saves load as already at war.
- **How new campaigns start:**
  - Player campaigns (`actions.create`, `cwdc new`) open at the theater's `crisis.start` (11 Aug 06:00).
  - The war starts at the start of a turn once tension reaches 100, or at the rolled deadline (8–16 turns). So it always comes, on 13–15 Aug.
  - `new_campaign(crisis=False)` and `cwdc new --no-crisis` start straight at war, for tests and balance runs.
- **Tension:** it drifts up 3–7 per turn, and crisis events add or remove a few points and post news.
  - `raise_tension` and `bring_forward` are the hooks for air incidents (Step 2).
- **During the crisis:**
  - no ground war or logistics;
  - no outcome check;
  - CAP only in the planner;
  - nobody shoots in the resolver.
- **When the war starts,** `start_war`:
  - shifts the formations' arrival turns so the mobilisation timetables run from the war's start;
  - posts the war news;
  - makes `war_day` count from day 1.

**Progress (2026-11-24): Phase 4 step 2 done: crisis air operations and ROE**
- **Tasks:** `QRA` (NATO) and `RECON` (Warsaw Pact border probe). A QRA flight carries `intercepts`, the id of its probe.
- **Planner** (`_crisis_ops`):
  - Probes per turn by period: dawn 1–2, day 1–3, dusk 0–1, night 0–1. Each reaches 8 km over the border, on the stretch the NATO CAP stations watch.
  - Up to 2 get a QRA scrambled against them. The QRA is player-eligible.
- **Simulated probes** (`crisis.air_ops`):
  - QRA intercepts 80% of the time and a barrier CAP 40%. A probe seen off brings tension −2; an unopposed probe +3.
  - The chance of a bloodless incident (+8) rises from 2% to 10% with tension.
- **Missions:**
  - ROE weapons tight in `CWDC_CONFIG`, and every AI group set to return fire only.
  - Probes run in from the east: already inbound for a QRA, timed to arrive with the player otherwise. They fly to the turn point, along the border, then out east.
  - An AI QRA follows its probe for 15 min.
  - The briefing gives the ROE and the intruders.
- **Lua runtime:**
  - The first Warsaw Pact shot is a hostile act: it shows "cleared to return fire" and stops later NATO shots counting as violations.
  - A player shot before that is an `roe_violation`.
  - Holding within 2 nm of a probe for 60 s records a `CWDC_INTERCEPT` event.
- **Ingest** (`crisis.dcs_incidents`, called from `actions.ingest`):
  - **Breach:** tension +25, news, and the shooter is grounded (fatigue set above the grounding level).
  - **Breach with a Warsaw Pact aircraft shot down:** on top of that, the war starts within 2 turns.
  - **Hostile act:** tension +15.
  - **Each probe in the mission:** intercepted −2, not intercepted +3.

**Progress (2026-11-24): Phase 4 step 3 done: the war-start event**
- On the first war turn after a crisis, the Warsaw Pact's needs put OCA first, so its opening blow falls on NATO's airfields. This happens even if that turn is at night.
- Campaigns started directly at war are past that point, so they are unchanged.
- That turn's briefing opens with "WAR: the Warsaw Pact attacked at HHMM… ROE: WEAPONS FREE", and the news posts the war's start.

**Progress (2026-11-24): Phase 4 step 4 done: the MAGIC GCI controller** (`cwdc_runtime.lua`)
- **F10 menu:** each human player group gets **MAGIC → Bogey dope / Picture / Threat calls on/off**. Bot missions get none.
- **Contacts:** the union of `getDetectedTargets(RADAR)` over the friendly radars in the mission (EWR and SAM objective groups, friendly CAP, escort and QRA flights). Only airborne hostiles count, so low flyers outside radar cover stay invisible.
- **Bogey dope:** BRAA to the nearest contact. Bearing is true; range in nm; altitude in thousands of feet; aspect is hot within 30°, flanking within 70°, beaming within 110°, otherwise cold.
- **Picture:** one bullseye call per hostile group.
- **Threat calls:** every 15 s, for contacts within 25 nm, at most once a minute per contact.
- **Theater `gci` block:** the callsign (MAGIC) and the blue bullseye (Fulda). The builder sets the coalition bullseye and adds a GCI line to the briefing.
- **NATO radar sites:** still only `b_ewr_wasserkuppe`. I could not confirm another 1976 NATO radar site, so none was added.

**Progress (2026-11-24): Phase 4 step 5 done: weather** (`engine/weather.py`)
- **`Campaign.weather`:** the kind (clear, scattered, broken, overcast, rain or fog), cloud base, visibility, wind and temperature. It is rolled every turn from August transition weights.
- **Fog:** a 35% chance at dawn after a clear or scattered turn with wind of 8 kt or less. It lifts on the next turn.
- **Effects:**
  - **Strike factor:** 1.0 in clear or scattered cloud, 0.85 broken, 0.7 overcast (0.15 less below 900 m), 0.55 rain, 0.5 fog.
  - It multiplies the resolver's weapon effect.
  - In the planner, each attack need is scrubbed with probability 1 − factor, and a scrubbed sortie stays on the ground.
  - Fog halves the launches from every airfield.
- **Missions:** `apply_weather` maps the state onto a DCS cloud preset (Scattered, Broken, Overcast, OvercastAndRain), fog, visibility and winds at 3 levels. The briefing gets a "Weather:" line.

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

