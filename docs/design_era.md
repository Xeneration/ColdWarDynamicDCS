# Cold War campaign maker (1950–1991): design draft

This builds on the Fulda 1976 concept, which is frozen as the git tag `v0.4-fulda-1976` and the branch `fulda-1976`. The engine stays the same: turns, the ATO, the resolver, crews, ground war, crisis, weather, news, mission builder, runtime and ingest. What changes is that the war is **assembled from a year**, not hand-written for 1976.

## 1. The campaign maker (new-campaign screen)

1. **Theatre:**
   - **Central Front**: the Cold War Germany map.
   - **Southern Flank**: the Syria map. NATO (Turkey, the USAF at Incirlik, the RAF at Akrotiri and a US/French carrier group) faces Syria and a Soviet expeditionary force. It is fictional, with no real-war re-enactment.
2. **Year slider (1950–1991)** over the bar chart from the sketch:
   - **One bar per airframe,** spanning its front-line service years. Bars are grouped West / East.
   - **Bars inside each airframe bar,** one per weapon family, starting at its introduction year (for example AIM-9B 1956, AIM-7E 1963, Rockeye 1968, AGM-65A 1972, AIM-9L 1977, RB 04 1971, RB 75 1972).
   - **Colours:**
     - flyable and owned: bold;
     - AI-only (you don't own the module): faded;
     - flown by a stand-in (see §3): hatched.
   - **The vertical line** shows exactly what exists in that year. A side panel lists:
     - the nations, airframes and weapons in the war;
     - the SAMs and tanks;
     - "You can fly: F-100D, A-4E, UH-1H".
3. **Your side**: West or East. East is flyable with the Su-25T today, and with any MiG/Su module you add later.
4. **Nations** (toggles; defaults by year):
   - **West:** USA, UK, West Germany, France, Netherlands, Belgium, Canada, Denmark, Norway, Italy, Turkey. "Sweden joins the West" is a fun toggle that brings in the Viggen. France is on by default, game-first, even after 1966.
   - **East:** USSR, GDR, Poland, Czechoslovakia, Hungary; Syria on the Syria map.
5. **Your airframes:** detected automatically from the DCS install (`Mods/aircraft`) plus Saved Games mods (the A-4E-C). You can untick any.
6. **Start:** crisis (as at Fulda), or "bolt from the blue" (war on turn 1). Also the length, difficulty and a seed.
7. **Presets:** "Fulda 1976" (today's campaign), "Sabre Days 1957", "Phantoms and Viggens 1975", "Hog Country 1985" and "Southern Flank 1983". Each preset is a saved slider position plus toggles.

## 2. What the year drives

| System | Driven by the year |
|---|---|
| Air order of battle | Each nation's squadron templates carry `airframe by year`. For example USAFE 36 TFW Bitburg: F-86F to 1956, F-100D to 1966, F-4E to 1977, F-15 after. Squadron counts scale by era. |
| Loadouts | Built from each module's **shipped payloads** (parsed from DCS / the A-4E-C `UnitPayloads`), then filtered by weapon year. Anything not yet in service is dropped. The F-4E keeps its validated set. |
| Air defence | AAA only to 1958. SA-2 from 1959, SA-3 from 1961, SA-6 from 1970, SA-8 from 1974, ZSU-23-4 from 1965, SA-11 from 1983. West: Hawk from 1960, Rapier from 1971, Roland from 1981, Gepard from 1976. |
| Ground forces | T-34-85 then T-55, T-72B, T-80B. Leopard 1A3, M-60, Chieftain, M-1, Leopard 2. BTR-60, BMP-1 from 1966, BMP-2 from 1980. Only DCS-existing units are used, checked against pydcs. |
| Sensors and doctrine | GCI density, AWACS (E-3 from 1977, A-50 from 1984). Night ops restricted before about 1970. Air-to-air ROE is "visual ID" in the gun era. |
| Rescue | UH-1H from 1959 (before that, a stand-in Mi-8/UH-1H flagged as such). |

## 3. Stand-ins (fun over strict realism)

Many period types are not in DCS: F-84F, F-104G, F-105, Hunter, Lightning, Jaguar, Harrier, Buccaneer, A-7, F-111, MiG-17, Su-7, Il-28, Tu-16 and MiG-21 variants for AI. A **stand-in table** maps each one to the closest DCS type by role and era:
- F-104G becomes F-5E / MiG-21Bis-class;
- Jaguar / Harrier become the A-4E-C or AJS37;
- MiG-17 becomes MiG-15bis / MiG-19P;
- Il-28 / Tu-16 become Tu-22M3 / B-52H (marked).

The UI marks stand-ins hatched, and a "strict" toggle removes them. Squadrons keep their real name and nation either way.

## 4. Flying several airframes

- Your pilot belongs to a **wing**, a base with two to four squadrons. Every squadron in the wing whose airframe you own puts its sorties on your ATO.
  - Example, 1958 at Bitburg: F-100D CAS in the morning, then an F-86F escort in the afternoon.
- A "mixed-wing" toggle also lets any owned airframe on your side be offered from neighbouring bases, for example the A-4E-C with a US Navy/USMC detachment.
- Career, ranks and medals carry across airframes. Each squadron keeps its own roster, and you are cross-qualified.

## 5. The enemy's turn: reactive missions

The resolver already knows what the enemy plans this turn. Those plans now spawn missions for you:

| Mission | Trigger | What you do |
|---|---|---|
| **Hunt the raid** | Enemy CAS (Su-25, Su-17, MiG-27) planned on the front | CAP or intercept over the battle area before they hit our troops. Success cuts their ground-attack bonus. |
| **Troops in contact** | An enemy offensive moves a battalion forward | Time-critical CAS: rockets on an advancing column, with a FAC talk-on and a friendly unit in contact. The result feeds straight into the ground-war battle. |
| **Helicopter hunt** | Mi-24/Mi-8 assault planned | Low-level CAP over the front against the helicopter wave. |
| **Bomber intercept** | Deep raid by bombers (Tu-22M3 or a stand-in) | Long-range scramble, with escorts to get past. |
| **Airfield defence** | Enemy OCA aimed at your base | Scramble from your own field as the raid comes in (with or without warning). |
| **Counter-battery / FROG hunt** | Enemy artillery or missile pushes | Find and kill launchers before they fire again. |
| **Hold the bridge** | Enemy armour reaches a river line | CAS at a chokepoint. Losing it opens the breakthrough. |

Plus your own-initiative types that are still missing: **UH-1H missions you fly yourself** (CSAR pickup, insertion, resupply to a cut-off unit), **anti-ship** on the Syria coast (AJS37 RB 04, Harpoon era), **tanker and AWACS escort**, and **train busting** (if DCS trains are scriptable; to be checked).

## 6. More variety on top

- **Era events:** 1950s nuclear-capable bomber alerts, the first SA-2 shock (no warning gear before RWRs), carrier group arrival (Syria), a defector MiG landing at your base, a special-forces raid on your field.
- **Squadron life:** an aircraft conversion mid-war in long campaigns (for example F-100 to F-4 in 1966), plus replacement-crew quality by era.
- **Dynamic difficulty by era:** gun-and-AIM-9B fights in the '50s; SAM belts and EW in the '70s; look-down/shoot-down in the '80s.

## 7. Build order (proposed)

| Phase | Content |
|---|---|
| **A. Era data** | An era database: airframes with service years per nation, weapon intro years, SAMs, ground units, stand-ins. Module detection. Shipped-payload import for owned modules plus the A-4E-C (a pydcs custom type for the mod). Year filtering. Everything checked against pydcs/DCS files, with tests. |
| **B. Generator** | Build a theatre's OOB, air defences, ground forces and airfields from (theatre, year, nations, side). The Fulda 1976 data becomes the first preset, and output must match today's campaign. |
| **C. Multi-airframe player** | Wing model; a generic task→loadout choice per airframe/year; the builder and kneeboards are no longer F-4E-specific. |
| **D. Campaign-maker UI** | The year slider over airframe/weapon bars, the theatre/side/nation/airframe pickers, the "what's in this war" panel and presets. |
| **E. Reactive missions** | §5, wired into the ground war, plus UH-1H missions. |
| **F. Syria theatre** | Fronts, bases, Turkish/Cypriot NATO side, coast and naval targets, anti-ship. |
| **G. Balance and soak** | Per era (1957, 1966, 1975, 1985) and per theatre. |

## Module policy (decided 2026-11-24)

Treat **every DCS module as available**: the user owns many more than are installed. Any DCS-flyable airframe in the year window is offered as player-flyable. That includes the F-86F, MiG-15bis, MiG-19P, MiG-21bis, Mirage F1, F-5E, F-14, F-15C, F-16C, F/A-18C, MiG-29, Su-25T, Su-27, Mi-8, Mi-24P, Ka-50, SA342, AH-64D, Harrier and Mirage 2000C.

- **Detection** only warns ("not installed - install it before flying this squadron"). It never hides anything.
- **Loadouts** for modules that aren't installed come from pydcs' bundled payloads and weapon data, not from the DCS install.

## Installed today (for reference, 2026-11-24)

- **Flyable in the Cold War window:** F-100D, F-4E, F-5E, F-14, AJS37, A-10A, A-4E-C (mod), Su-25T (East), UH-1H.
- **Maps:** Cold War Germany, Syria, Caucasus, Normandy.
- The Mirage F1, MiG-21, F-86 and MiG-15 aren't owned, so they appear as AI (and flyable once you add the module).
- Nothing you own is flyable from 1950–1955: the slider shows that stretch as "AI only".

## Progress

### Phase A: era data (2026-11-24, done)

- **`cwdc/data/era/`:**
  - `airframes.yaml`: 60 airframes, West and East. Each has its service years per nation, its DCS type in order of preference, its flyable variants, a stand-in flag and air/strike ratings.
  - `weapons.yaml`: store introduction years as ordered regex rules.
  - `ground.yaml`: SAMs, AAA and EWRs, plus tanks, IFVs, artillery and trucks by year. WWII Units pack units are flagged with `requires`.
  - `nations.yaml`: nations, their DCS country, theatres and default years.
- **`cwdc/data/payloads/*.json`:** shipped payloads per DCS type, harvested by `python -m tools.harvest_payloads --owned`:
  - from every installed module's own missions, plus the A-4E-C's UnitPayloads;
  - from DCS's MissionEditor UnitPayloads for the AI types.
  - Every store in them has a year, which a test enforces.
- **`cwdc/era/`:**
  - `modules.py`: owned-module detection from each module's `entry.lua` (`make_flyable` / `MAC_flyable`), across DCS World and Saved Games mods, plus asset packs and maps. Read-only.
  - `a4ec.py`: the A-4E-C as a pydcs plane type.
  - `payloads.py`: harvest and load.
  - `__init__.py`: `airframes_in`, `dcs_type` (stand-ins and mods), `player_types`, `weapon_year`, `payloads_for(type, year)`, `air_defence_in`, `ground_in`, `default_nations` and `slider`.
- **Found on this machine:**
  - Flyable: F-100D, F-4E, F-5E, F-14A/B, AJS37, A-10A, A-4E-C (mod), Su-25T, UH-1H.
  - Asset packs: WWII Units and Supercarrier.
  - Maps: GermanyColdWar, Syria, Caucasus, Normandy.
- **Open for Phase C:**
  - AI-only types from modules you don't own (MiG-21Bis, MiG-15bis, MiG-19P, F-86F, Mirage F1, M-2000C, AV-8B, A-6E, L-39ZA) have no payload files on this install. Planned answer: hand-written, era-filtered AI loadouts, validated against pydcs pylon data. For AI, DCS does not strip stores the way it does for the player's module.
  - Transports, tankers and AWACS need no payloads.

### Phase B: the generator (2026-11-24, done)

- **`cwdc/engine/generate.py`:**
  - `Setup` holds theatre, year, side, nations, Sweden, strict, player airframe, plus the owned modules, mods and asset packs.
  - `oob()` builds the player's squadron, then per side 2+2 (West) and 3+2 (East) AI squadrons:
    - Fighters and strikers are drawn from the airframes in service, weighted by nation (USA and USSR heaviest). Stand-ins count half, bombers half, and a repeated airframe a quarter.
    - Squadrons are named per nation (`data/era/units.yaml`), and West ones get DCS western callsigns.
    - Each is based on the theatre's candidate airfields (`generator.bases`), matched to its nation, with the player nearest the front.
    - Tasks come from the role, with SEAD only once the type's anti-radiation missile exists that year.
  - Formations of nations not in the war pass to the lead nation.
  - `adapt_theatre()` re-equips each objective for the year:
    - **SAM sites:** keep their system if it is in service (35% chance of a newer one). Otherwise they get another system of the same class, falling back long → medium → short → AAA. Before 1959 every site is a flak battery. Each is re-laid out, with its range and name updated.
    - **Vehicles:** modernised per type (70% when a successor is 8+ years newer), or replaced by role.
    - **Dates:** the start and crisis dates move to the year.
- **Factory:**
  - `new_campaign(..., setup=)` builds a generated war and adds the squadrons' airfields. `Campaign.era` stores the setup.
  - `campaign_data(c)` gives `sync_theater` the adapted theatre, so nothing from 1976 reappears.
  - **The Fulda 1976 preset is unchanged:** its hand-written OOB and its balance numbers stay the same.
- **Squadrons:** they carry an era airframe id and their own air/strike ratings (stand-ins share a DCS type). The resolver uses those ratings.
- **Airframe catalogue:** era defaults (flight profile by role, crew, flyable) sit under `airframes.yaml`.
- **Loadouts:**
  - For a campaign-maker war, the builder uses a named loadout only if all its stores existed that year.
  - Otherwise it uses `era.pick_payload(type, year, air|ground|sead)`, the most-shipped era-valid payload.
  - Airfield flak is the newest AAA of the year.
- **Simulation:** it now counts period flak (Bofors, quad .50, KS-19, S-60, ZSU-57-2, Osa, Roland, Rapier).
- **Coalitions:** every nation in `nations.yaml`.
- **Presets** (`data/era/presets.yaml`): fulda-1976 (fixed), sabre-days-1957, phantoms-and-viggens-1975, hog-country-1985.
- **CLI:** `cwdc new --year Y [--aircraft ID] [--sweden] [--strict]` or `--preset ID`.
- **Balance** (`tools.balance --year Y`, 6 seeds × 21 days):

  | | NATO loss / sortie | WP loss / sortie |
  |---|---|---|
  | Preset | 3.4% | 5.1% |
  | 1957 | 3.4% | 3.8% |
  | 1966 | 4.0% | 2.4% |
  | 1976 | 3.2% | 4.6% |
  | 1985 | 3.2% | 5.2% |

  Outcomes in every era resemble the preset: mostly stalemate or NATO holds, with an occasional Warsaw Pact victory. Per-era tuning is Phase G.
- **Open:**
  - The East side (player) is refused for now.
  - AI types without shipped payloads (MiG-21, MiG-15, F-86, Mirage F1 ...) use pydcs task defaults, which are not year-filtered (Phase C).
  - Helicopters and carrier squadrons are not generated yet (carrier types only as the player's choice).

### Phase C: flying several airframes (2026-11-24, done)

- **The wing:**
  - `Squadron.player_wing` marks the rest of your wing. `generate.wing_choice` adds up to 2 squadrons flying other owned types in service with your side that year. Your nation, land-based types and real types (not stand-ins) come first.
  - With `mixed_wing` (on by default, game-first) any nation of the side and carrier types count, e.g. the A-4E or F-14 as a land-based US Navy detachment named VF-/VA-.
  - Wing squadrons share your home airfield and take an AI slot of their kind.
  - `planner.player_can_fly` offers you flights from your home squadron and the wing, for types you own. `assign_player` already seats you in any eligible flight (you are cross-qualified).
  - Example, 1966: F-100D (home), A-4E and F-5 (Norwegian F-5A stand-in).
  - Example, 1985: F-4E, F-5E and A-10A.
- **One payload decision per flight:** `loadouts.flight_payload(c, f)` is used by the mission builder, the briefing (new `Loadout:` line, also on the kneeboard) and the web ATO.
  - A named loadout is used when all its stores existed that year.
  - Otherwise the most-shipped period payload for the task (air / SEAD / ground, then guns and tanks only).
  - A campaign-maker war never falls back to DCS task defaults, which can be decades too new.
- **Payloads for AI types:**
  - The harvest now also reads `CoreMods\aircraft\*\UnitPayloads`, the module authors' payloads, installed whether or not you own the module. That covers the MiG-21, MiG-15, MiG-19, F-86, Mirage F1, M-2000C, AV-8B, A-6E, L-39 and Gazelle.
  - New store years: French Magic/R530/Super 530/SNEB/SAMP/BAP-100, Kh-66, BL-755, HOT, Mistral, Soviet and French ECM pods.
  - Training stores (practice bombs, captive rounds, ACMI pods) are a family of their own and are never loaded.
  - Nuclear bombs are excluded (year 2100).
- **Trimming:** a shipped payload with stores too new for the year is kept with those pylons emptied. That is a subset of a payload DCS accepts, and it is scored down per dropped store.
  - A 1976 F-14 flies 4x AIM-54A + 2x AIM-7F (the AIM-9Ls come in 1977).
  - A 1966 MiG-21 flies R-3S/R-3R.
  - A 1957 MiG-15 flies guns and tanks.
- **Balance check** (4 seeds x 21 days):

  | | NATO loss / sortie | WP loss / sortie |
  |---|---|---|
  | Preset | 3.3% | 4.8% |
  | 1966 | 3.3% | 4.9% |
  | 1985 | 4.7% | 3.4% |

  In 1985 the F-5E wing squadron takes a slot an F-15/F-16 would have had. Per-era tuning is Phase G.
- **Open:** helicopter squadrons (UH-1H missions) come with Phase E. The resolver still rates aircraft only, not their loadouts.

### Phase D: the campaign-maker screen (2026-11-24, done)

- **New campaign** in the web UI opens the maker. It replaces the old Fulda-only dialog.
  - **Top:** theatre buttons (Central Front; Southern Flank greyed until Phase F) and preset buttons. Fulda 1976 stays the original hand-built campaign.
  - **Left: the year slider over the chart.** Every airframe of the theatre gets a bar for its service life, grouped West/East:
    - Bold blue: you own it. Grey: AI only. Hatched: stand-in. Dashed: needs a mod you don't have.
    - Amber ticks mark each weapon's introduction.
    - Clicking a name expands that aircraft's weapon sub-bars, as in the sketch.
    - Dragging anywhere on the chart, or the range input, moves the amber year line. Bars outside the year dim, and nations switched off fade.
    - A filter shows all aircraft, those in service, or those you own.
  - **Right: "what's in this war"**, a live preview at about 0.1 s per year:
    - your aircraft (Automatic, or any owned type in service);
    - your wing (★ home, ☆ also yours to fly) and what each can carry that year;
    - both sides' squadrons (stand-ins marked);
    - air defence and ground forces of the year, and the SAM sites on the map;
    - notes (flak only before 1959, guns only before 1956, stand-ins);
    - nation checkboxes and toggles (Sweden joins, mixed wing, no stand-ins);
    - name, pilot, seed, crisis, overwrite. The name follows the year until you edit it.
- **API:** `GET /api/maker` (bars, nations, presets, theatres, owned types) and `POST /api/maker/preview`. `POST /api/campaigns` takes `preset` or `era` (a Setup). `create_app(installed=...)` lets tests inject the module list; it is otherwise detected on first use.
- **The guide and squadron text follow the campaign:** "It is August 1975 ... You fly the F-4E Phantom II ... Your wing also flies the F-5E Tiger II and AJS 37 Viggen". `overview.player` gains `aircraft` and `wing`.
- **Wing order:** land-based, real types, then the newest airframes (the year's signature aircraft), then your nation.
  - 1966: F-100D, F-5 and G.91 (flown by the A-4E-C).
  - 1975 with Sweden: F-4E, F-5E and Viggen.
  - 1985: F-4E, A-10A and F-5E.
- **Stale browser modules fixed:** the page now loads its scripts and styles from `/v/<build>/`, a path keyed on a hash of the static files. A browser can no longer pair a new page with old cached modules (found while testing: it kept serving the old guide.js). `/` is sent `no-cache`.
- **Checked in the browser** at 1440x900: the chart, dragging the year, expanding rows, presets, creating a 1975 war, and the guide text. No console errors.

### Phase E: missions in the enemy's turn (2026-11-24, done)

- **`cwdc/engine/reactive.py`:** NATO answers what the Warsaw Pact does. Each answer is a `Reaction` on the campaign (`Campaign.reactions`; `Package.reaction` marks its package).
  - **Troops in contact / Hold the (river):**
    - Where the Warsaw Pact attacked last turn (the main effort first, then the deepest push), its lead battalion in that sector is promoted to the head of NATO's battlefield needs, right after the forced OCA and the special of the day.
    - It is flown by the player's wing when it has a sortie.
    - When the attack is within 6 km of a chokepoint (`data/era/chokepoints.yaml`: Werra, Leine, Haune, Fulda, Kinzig, Saale, Main crossings), it becomes "Hold the ...".
  - **Counter-battery:** the guns of an attacking sector, when no troops-in-contact mission was found. Only one battlefield answer a turn, so the wing keeps its escorts.
  - **Helicopter hunt:**
    - From 1967 (Mi-8; Mi-24 gunships from 1972), with a 50% chance a turn, the deepest attack gets an assault wave.
    - A NATO patrol (wing first) hunts it 10 km on our side of the front. The hunters stay out of the simulation's fighter-versus-fighter phase, because they are low and looking for helicopters.
    - Unflown hunts are decided on the hunter's air rating.
  - **Scramble names:** `raid_label` names intercepts "Hunt the raid" (attack aircraft on our troops), "Bomber intercept" (bomber-role types) or "Airfield defence" (a raid on the field of your wing). An airfield defence scrambles the squadrons on that field, met 30 km out.
- **Effect on the ground war:**
  - `note_kill` (from `ground.destroy_from_air`, both simulated and DCS) adds the vehicle points destroyed on the target.
  - `sector_factor` multiplies the Warsaw Pact's attack power in the sector that turn, with a debrief line:

    | Answer succeeds | Warsaw Pact attack power |
    |---|---|
    | Troops in contact (≥ 2 points) | × 0.8 |
    | Hold the (river) | × 0.65 |
    | Counter-battery (≥ 1.5 points) | × 0.9 |
    | Helicopter assault not broken up | × 1.1 |

- **The DCS mission:**
  - **Troops in contact:** the enemy column drives on toward our lines, and "Friendly armour in contact" (the year's newest NATO tank ×2 and IFV, of the formation holding the sector) is 2.5 km ahead of it. Briefing: "TROOPS IN CONTACT" / "HOLD THE ...". The existing FAC (Wildcat 21) marks for CAS.
  - **Helicopter hunt:**
    - The assault wave (Mi-24V/Mi-8MT per year) starts 35 km east, flies at 60 m and lands 4 km beyond the hunt area. It is timed to arrive with you.
    - Its units go in the manifest as `reactive`.
    - `reactive.dcs_results` scores the share shot down (gone from the final snapshot, or dead/crash events), and the ingest credits the kills.
  - Briefing lines for Hunt the raid / Bomber intercept / Airfield defence.
- **Simulation:** a FAC-directed troops-in-contact attack meets half the flak (`FAC_AAA`, a single quick pass).
- **Balance:**
  - Blue losses over 7 days, 8 seeds: 4.6% with these missions, 4.4% without.
  - Over 21 days, 6 seeds:

    | | NATO loss / sortie | WP loss / sortie |
    |---|---|---|
    | Preset | 3.2% | 4.7% |
    | 1966 | 3.4% | 4.8% |
    | 1985 | 4.2% | 3.6% |

  - The balance guard test now averages 4 seeds instead of 2; 2 were within noise of its 6% band.
  - Outcomes moved a little: the preset's 6 seeds now give 2 Warsaw Pact gains. That needs a bigger sample in Phase G.
- **Still open:**
  - The FROG/Scud hunt: DCS has only `Scud_B`, too big and late for most eras. Counter-battery covers the guns.
  - Bridges as targets (unverified on the map).
  - UH-1H missions you fly yourself (helicopter squadrons in the wing).

### Phase F: the Southern Flank on the Syria map (2026-11-24, done; naval part still to come)

- **The engine works on any theatre:**
  - `theater.THEATRE_FILES` maps `Campaign.theater` to its data file. `theater.use(c)` / `activate()` switch the DCS terrain, the coordinate conversions and which side of the front is whose.
  - The active theatre is per thread, because the web UI serves several campaigns at once (found in browser testing: a map poll for a Germany campaign switched the map under a Syria campaign being built).
  - `use` is called by `store.load`, `new_campaign`, `turn.end_turn`, `planner.plan_turn` and `builder.build`.
  - A theatre file's `geometry` block:
    - `dcs_terrain`;
    - `blue_side` (west / north);
    - `lands` (for "km into Turkey");
    - `border_note`;
    - `blue_zones`: ground that stays NATO's whatever the front does, such as Cyprus;
    - `front_shift: sector`: a sector's front moves as one block. Point normals splay at the corner of Hatay, which let the Kilis advance wrap around Osmaniye.
  - Direction helpers `nato_dir` / `toward_nato` / `toward_enemy` replace "the enemy is east" in probes, helicopter waves, search parties and probe exits.
  - Crisis headline, war-start news, victory texts and the map's border tooltip come from the theatre. The map frames the campaign's front.
- **`data/theater_syria.yaml` + `data/oob_syria.yaml`:**
  - **Front:** the Turkish-Syrian border, from the coast at Kassab to Ceylanpinar, in four sectors: Hatay/Orontes, Kilis/Aleppo, Euphrates at Karkamis, Harran plain. Each has chokepoints (Orontes at Antakya, Belen Pass, Birecik ...).
  - **Ground forces:**
    - NATO: the Turkish 2nd Army brigades and divisions, plus the US Rapid Deployment Force (82nd Airborne, 24th Mech, 1st Marine Division) as reinforcements.
    - East: Syrian armoured and mechanized divisions, with the Soviet 103rd Guards Airborne and an expeditionary motor rifle division.
  - **Objectives:** HAWK, SA-2/3/6, EWRs, front battalions, artillery, the Aleppo headquarters, rail yards (Iskenderun, Gaziantep, Aleppo, Hama), depots and convoys, and a rail and road supply network.
  - **Victory:** the East reaching Iskenderun, Osmaniye or Adana, or a 100 km breakthrough.
  - **Airfields by nation:**
    - NATO: Incirlik, Gaziantep, Adana, Sanliurfa, Diyarbakir, Cukurova, Akrotiri.
    - East: Aleppo, Kuweires, Jirah, Taftanaz, Abu al-Duhur, Tabqa, Hama, Latakia.
  - **Nation weights:** Turkey and Syria fly most squadrons (Turkey 5, USA 3, UK 1.5; Syria 5, USSR 2).
  - **Dates:** the war season is October.
- **Campaign maker:** Southern Flank is selectable. Theatre buttons reload the chart and nations, the name follows the theatre, and the enemy is called "Syria and USSR". Presets switch theatre back as needed.
- **Balance** (`tools.balance --theatre syria --year Y`, 8 seeds x 21 days):

  | Year | NATO loss / sortie | East loss / sortie | Outcomes |
  |---|---|---|---|
  | 1976 | 2.5% | 5.0% | stalemate 5, East gains 3 |
  | 1985 | 3.2% | 4.9% | stalemate 5, East gains 2, NATO holds 1 |

  - The first cut collapsed in 3 days: Gaziantep, a victory city, was 40 km from the border and Kilis was at 2:1.
  - Fixes: victory cities moved deeper, one Syrian division moved to the Hatay mountains, Turkish brigades strengthened, reinforcements earlier.
  - The Central Front preset is unchanged (same balance numbers as before).
- **The sea** is the next section (F2).

### Phase F2: the war at sea (2026-11-24, done)

- **Warships by era:** `data/era/ground.yaml` gains a `naval` section of DCS ship types with service years, roles and their own air-defence reach.
  - West: Essex (WWII pack), Forrestal, Nimitz, Ticonderoga, Perry, Leander, Tarawa, Invincible, La Combattante, supply ships.
  - East: Kirov, Slava, Krivak (and as a Kotlin stand-in before 1970), Grisha, Osa (Tarantul stand-in), Ropucha, tankers.
  - `adapt_theatre` re-equips ship groups by role for the year. A group's threat range is its best ship's: a 1985 Soviet group with a Slava reaches 75 km.
- **The Southern Flank's ships** (`ObjectiveKind.SHIP`):
  - the 6th Fleet carrier group east of Cyprus;
  - a Turkish patrol group in Iskenderun Bay;
  - the Soviet Mediterranean Squadron task group off Latakia;
  - Syrian missile boats.
- **Anti-ship:**
  - `Task.ANTISHIP` and anti-ship needs. A carrier is worth more for each squadron on its deck.
  - Each side flies one naval strike a day (`NAVAL_STRIKES_PER_DAY`).
  - Squadrons get an "Anti-ship" task when they carry anti-ship weapons that year (Harpoon, Kormoran, Rb 04, Kh-22, Bullpup, Walleye, Maverick ...), and bombers always do.
  - `era.pick_payload(..., "antiship")` loads them.
  - In the simulation a ship group is a SAM site against its attackers. Each ship takes 3 hits, the main unit (a carrier's deck) 6, and the screen is sunk first.
- **Carriers:**
  - The theatre's `generator.carriers` puts US Navy types (F-14, A-4, A-6) on the deck when a carrier is in service that year (Forrestal from 1955; Essex with the WWII Units pack). `carrier_wing` adds a fighter and a strike squadron on deck.
  - `Airbase.carrier_of` names the ship objective; the deck gets no parking-area objective and is never overrun.
  - In DCS:
    - the carrier group steams west at 25 knots;
    - you start on the deck (`flight_group_from_unit`, runway or ramp start), or in the air;
    - every carrier flight recovers aboard, with a Land waypoint linked to the deck unit;
    - the briefing says "from the carrier (Forrestal)".
  - If the carrier is sunk, half the air wing goes down with her and the rest divert to the nearest land base.
- **Web:** ship symbols on the map, their air-defence rings, and the "Anti-ship" task described on the ATO.
- **Balance** (6 seeds x 21 days):

  | Year | NATO loss / sortie | East loss / sortie | Anti-ship losses | Outcomes |
  |---|---|---|---|---|
  | 1966 | 3.1% | 4.7% | East 10.2% | stalemate 3, East gains 2, NATO holds 1 |
  | 1980 | 2.6% | 6.7% | NATO 6.6%, East 11.0% | NATO holds 3, East gains 2, stalemate 1 |

  - The carrier survives 21 days in every run checked; its escorts usually do not.
  - The Central Front preset is unchanged.
- **Open:** submarines (Kilo) are not modelled. Carrier deck crowding (parking spots) is not checked.
