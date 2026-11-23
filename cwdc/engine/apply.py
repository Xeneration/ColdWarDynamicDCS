"""Apply SortieResults (flown or simulated - same code path) to the campaign state."""
from __future__ import annotations

from typing import Iterable

from cwdc.engine import crews
from cwdc.model import (AirframeStatus, Campaign, CrewRole, FlightStatus, PilotStatus, SortieResult)

REPAIR_TURNS = 2
WIA_RECOVERY_TURNS = 4


def destroy_unit(c: Campaign, uid: str) -> None:
    """A vehicle destroyed by air power (flown or simulated)."""
    from cwdc.engine.ground import destroy_from_air
    oid, idx = uid.split("#")
    obj = c.objectives[oid]
    was_destroyed = obj.destroyed
    if obj.units[int(idx)].alive:
        obj.units[int(idx)].alive = False
        destroy_from_air(c, obj, int(idx))
    if obj.destroyed and not was_destroyed:
        c.add_news(f"{obj.name} has been destroyed.")


def apply_results(c: Campaign, results: Iterable[SortieResult],
                  extra_destroyed: Iterable[str] = ()) -> list[str]:
    lines = []
    for r in results:
        f = c.flights[r.flight_id]
        for aid in f.airframe_ids:
            c.airframes[aid].sorties += 1
        for pid in crews.crew_of(f):
            c.pilots[pid].sorties += 1
        crews.flew(c, f)
        lines.append(f"[{f.side.value.upper()}] {r.summary}" + (" (flown in DCS)" if r.source == "dcs" else ""))
        for aid in r.airframes_lost:
            af = c.airframes[aid]
            af.status = AirframeStatus.DESTROYED
        for aid in r.airframes_damaged:
            af = c.airframes[aid]
            if af.status is not AirframeStatus.DESTROYED:
                af.status = AirframeStatus.DAMAGED
                af.repair_turns = REPAIR_TURNS
                af.quality = max(40, af.quality - 10)
        for fate in r.pilot_fates:
            p = c.pilots[fate.pilot_id]
            p.status = fate.status
            if fate.status is PilotStatus.WIA:
                p.recovery_turns = WIA_RECOVERY_TURNS
            if fate.status is not PilotStatus.ACTIVE or p.is_player:
                seat = " (WSO)" if p.role is CrewRole.WSO else ""
                lines.append(f"  {p.rank} {p.name}{seat} ({c.squadrons[p.squadron_id].name}): "
                             f"{fate.status.value.upper()} - {fate.note}")
        for k in r.kills:
            if k.pilot_id:                     # the crew shares the credit (USAF practice)
                for pid in filter(None, (k.pilot_id, crews.wso_for(f, k.pilot_id))):
                    p = c.pilots[pid]
                    if k.air:
                        p.air_kills += 1
                    else:
                        p.ground_kills += 1
        for uid in r.objective_units_destroyed:
            destroy_unit(c, uid)
        f.status = FlightStatus.DONE
    for uid in extra_destroyed:
        destroy_unit(c, uid)
    return lines
