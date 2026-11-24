"""The community A-4E-C Skyhawk mod as a pydcs plane type.

pydcs only knows official modules. The A-4E-C is declared here from the mod's own
A-4E-C.lua (dimensions, fuel, speed, five pylons, task list); its stores are not declared per
pylon, because loadouts for it come only from the mod's shipped payloads (cwdc/data/payloads),
written to units as raw {pylon: CLSID}.

Importing this module registers the type in dcs.planes.plane_map (idempotent).
"""
from __future__ import annotations

from typing import Set

from dcs import task
from dcs.planes import PlaneType, plane_map

POUNDS_TO_KG = 0.45359237


class A_4E_C(PlaneType):
    id = "A-4E-C"
    flyable = True
    height = 4.57
    width = 8.38
    length = 12.22
    fuel_max = 5440 * POUNDS_TO_KG
    max_speed = 300.83 * 3.6
    category = "Air"
    radio_frequency = 251
    livery_name = "A-4E-C"
    pylons: Set[int] = {1, 2, 3, 4, 5}
    tasks = [task.CAP, task.CAS, task.SEAD, task.Reconnaissance, task.GroundAttack, task.AFAC,
             task.RunwayAttack, task.AntishipStrike, task.Escort]
    task_default = task.CAS


def register() -> type[PlaneType]:
    plane_map.setdefault(A_4E_C.id, A_4E_C)
    return plane_map[A_4E_C.id]


register()
