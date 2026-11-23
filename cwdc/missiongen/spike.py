"""Phase 0 / Spike C: a minimal test mission to prove the generate -> fly -> results loop.

Player: 2x F-4E (warm start, Hahn AB), CAP loadout, 1976.
Threat: 2x MiG-21bis orbiting ~70 km east of Hahn.
Target: tracked Soviet armour group (3x T-55 + ZSU-23-4) near Sprendlingen.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

import dcs
from dcs import countries, task
from dcs.mapping import Point
from dcs.mission import Mission, StartType
from dcs.planes import F_4E_45MC, MiG_21Bis
from dcs.terrain import Germany
from dcs.unit import Skill
from dcs.vehicles import AirDefence, Armor

from cwdc.missiongen import coalitions, loadouts
from cwdc.missiongen.runtime_inject import inject_runtime

MISSION_ID = "spike-c-001"
PLAYER_GROUP = "BLUE-Hawk1"
MIG_GROUP = "RED-Fishbed1"
ARMOR_GROUP = "RED-Armor1"


def build() -> Mission:
    terrain = Germany()
    m = Mission(terrain)
    m.start_time = datetime(1976, 8, 14, 8, 0)
    m.weather.clouds_density = 0
    m.weather.wind_at_ground.speed = 2

    coalitions.setup(m)
    blue = m.country(countries.USA.name)
    red = m.country(countries.USSR.name)

    hahn = terrain.airports["Hahn"]

    # --- player flight -------------------------------------------------
    player = m.flight_group_from_airport(
        blue, PLAYER_GROUP, F_4E_45MC, hahn,
        maintask=task.CAP, start_type=StartType.Warm, group_size=2,
    )
    player.units[0].set_player()
    player.units[1].skill = Skill.High
    loadouts.apply(player, "CAP")
    for u in player.units:
        u.set_property("Quality", 85)   # well-used 1976 airframe
    cap_point = Point(-440000, -890000, terrain)
    wp = player.add_waypoint(cap_point, 6000, 800, name="CAP")
    wp.tasks.append(task.OrbitAction(6000, 800, task.OrbitAction.OrbitPattern.RaceTrack))
    player.land_at(hahn)

    # --- threat: MiG-21bis pair -----------------------------------------
    migs = m.flight_group_inflight(
        red, MIG_GROUP, MiG_21Bis, Point(-430000, -870000, terrain),
        altitude=5000, speed=750, maintask=task.CAP, group_size=2,
    )
    migs.load_task_default_loadout(task.CAP)
    for u in migs.units:
        u.skill = Skill.Average
    orbit_wp = migs.add_waypoint(Point(-432000, -880000, terrain), 5000, 750, name="Orbit")
    orbit_wp.tasks.append(task.OrbitAction(5000, 750, task.OrbitAction.OrbitPattern.Circle))

    # --- ground target ---------------------------------------------------
    armor_pos = Point(-452000, -905000, terrain)
    armor = m.vehicle_group(red, ARMOR_GROUP, Armor.T_55, armor_pos, heading=270, group_size=3)
    shilka = m.vehicle(f"{ARMOR_GROUP} AAA", AirDefence.ZSU_23_4_Shilka)
    shilka.position = Point(armor_pos.x + 60, armor_pos.y + 60, terrain)
    armor.add_unit(shilka)

    # --- briefing + runtime ---------------------------------------------
    m.set_description_text(
        "COLD WAR DYNAMIC CAMPAIGN - SPIKE C (test mission)\n\n"
        "14 AUG 1976. Purpose: verify that mission results are written back to the campaign.\n"
        "Take off from Hahn, engage the MiG-21 pair orbiting east, optionally attack the "
        "armour group near Sprendlingen, then land back at Hahn and exit the mission normally."
    )
    m.set_description_bluetask_text("CAP east of Hahn. Weapons free. RTB Hahn and land.")
    inject_runtime(m, MISSION_ID, [PLAYER_GROUP, MIG_GROUP, ARMOR_GROUP])
    return m


def generate(out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "CWDC_SpikeC.miz"
    build().save(str(path))
    return path


if __name__ == "__main__":
    import sys
    from cwdc.dcs_paths import missions_dir
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else missions_dir()
    print(generate(target))
