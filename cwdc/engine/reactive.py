"""Missions in the enemy's turn: NATO air power answering what the Warsaw Pact does.

When a Warsaw Pact attack is going in somewhere on the front, the NATO plan answers it:

  * tic      Troops in contact: CAS on the lead battalion of the attack, a FAC talks you on and
             friendly armour is in contact a couple of km away. Enough vehicles destroyed and the
             attack in that sector stalls this turn.
  * hold     the same where the attack is about to reach a river line (chokepoints.yaml):
             "Hold the Fulda river at Fulda" - stopping it there counts for more.
  * battery  counter-battery: the guns firing the attack in, silenced from the air.
  * helo     helicopter hunt: a Mi-8/Mi-24 assault wave leapfrogging the front; unless it is
             broken up, the attack gets a lift.

Raids on our side are already met by scrambles (planner._fighter_ops); they are named here by
what they meet: "Hunt the raid" (attack aircraft over the battlefield), "Bomber intercept",
"Airfield defence" (a raid on your own field).

Each answer is a `Reaction` on the campaign. Its score comes from the air war of the turn - the
simulation or the DCS mission - and `sector_factor` turns it into a change in the sector's ground
battle (engine.ground).
"""
from __future__ import annotations

from functools import lru_cache

from cwdc import theater as T
from cwdc.model import Campaign, Objective, ObjectiveKind, Reaction, Sector, Side

LABEL = {"tic": "Troops in contact", "hold": "Hold the line", "battery": "Counter-battery",
         "helo": "Helicopter hunt", "hunt": "Hunt the raid", "bomber": "Bomber intercept",
         "airfield": "Airfield defence"}
TIC_POINTS = 2.0             # vehicle points destroyed on the lead battalion to break the attack
BATTERY_POINTS = 1.5
STALL = {"tic": 0.8, "hold": 0.65, "battery": 0.9}   # red attack power when the answer succeeds
HELO_LIFT = 1.1              # red attack power when a helicopter assault goes in unopposed / unbroken
HELO_FROM_YEAR = 1967        # Mi-8 assault squadrons in the GSFG
HELO_SUCCESS = 0.5           # share of the helicopters to shoot down to break up the assault
P_HELO = 0.5                 # chance a red attack gets a helicopter assault this turn
P_HELO_SIM = 0.55            # simulated hunt: success chance per air rating point
HUNT_BEHIND = 10_000         # m: the helicopter hunt patrols this far on our side of the front
HOLD_WINDOW_KM = 6.0         # the attack is this close short of a chokepoint (or just past it)
KEEP = 40                    # reactions kept in the campaign


@lru_cache(maxsize=1)
def _chokepoints() -> dict:
    return T.load_data("era/chokepoints.yaml")


def chokepoint(c: Campaign, sec: Sector) -> str | None:
    """The river line the attack in this sector is about to reach, if any."""
    theatre = (c.era or {}).get("theatre", "germany")
    for cp in _chokepoints().get(theatre, {}).get(sec.id, []):
        if -1.0 <= cp["km"] - sec.offset_km <= HOLD_WINDOW_KM:
            return cp["name"]
    return None


def attacking_sectors(c: Campaign) -> list[Sector]:
    """Sectors where the Warsaw Pact attacked last turn, the deepest push first."""
    secs = [s for s in c.sectors.values() if s.attacker is Side.RED]
    return sorted(secs, key=lambda s: (s.id != c.main_effort, -s.offset_km))


def this_turn(c: Campaign, kind: str | None = None) -> list[Reaction]:
    return [x for x in c.reactions if x.turn == c.turn and (kind is None or x.kind == kind)]


def register(c: Campaign, kind: str, sector_id: str, package_id: str | None = None,
             target_id: str | None = None, name: str = "") -> Reaction:
    needed = {"tic": TIC_POINTS, "hold": TIC_POINTS, "battery": BATTERY_POINTS, "helo": HELO_SUCCESS}.get(kind, 0)
    x = Reaction(kind=kind, turn=c.turn, sector_id=sector_id, package_id=package_id, target_id=target_id,
                 name=name, needed=needed)
    c.reactions = (c.reactions + [x])[-KEEP:]
    return x


# ----------------------------------------------------------------------------- planning
def promote(c: Campaign, needs: list, insert_at: int = 0) -> list:
    """NATO: put the answer to the Warsaw Pact's attack at the head of the battlefield needs.

    The lead battalion of the deepest attack becomes "Troops in contact" (or "Hold the <river>");
    failing that, the guns of an attacking sector a counter-battery mission. One a turn, so the
    wing keeps sorties for escorts and its other work. Returns the promoted needs.
    """
    promoted = []
    for sec in attacking_sectors(c):
        tic = next((n for n in needs if n.target.kind is ObjectiveKind.ARMOR and n.target.side is Side.RED
                    and n.target.sector_id == sec.id), None)
        if tic is None:
            continue
        cp = chokepoint(c, sec)
        form = c.formations.get(tic.target.formation_id) if tic.target.formation_id else None
        enemy = form.name if form else "Warsaw Pact armour"
        if cp:
            tic.reaction, tic.label, tic.chokepoint = "hold", f"Hold the {cp}", cp
            tic.reason = f"{enemy} is about to force the {cp}: stop the lead battalion there"
        else:
            tic.reaction, tic.label = "tic", "Troops in contact"
            tic.reason = f"{enemy} is attacking in the {sec.name}: our troops need air support now"
        tic.sector_id = sec.id
        promoted.append(tic)
        break
    for sec in ([] if promoted else attacking_sectors(c)):   # one battlefield answer a turn
        bat = next((n for n in needs if n.target.kind is ObjectiveKind.ARTILLERY and n.target.side is Side.RED
                    and n.target.sector_id == sec.id and n not in promoted), None)
        if bat is not None:
            bat.reaction, bat.label, bat.sector_id = "battery", "Counter-battery", sec.id
            bat.reason = f"these guns are firing the attack in the {sec.name}: silence them"
            promoted.append(bat)
            break
    for n in reversed(promoted):
        needs.remove(n)
        needs.insert(min(insert_at, len(needs)), n)
    return promoted


def helo_assault(c: Campaign, r) -> Sector | None:
    """The sector where Warsaw Pact helicopters lift troops over the front this turn, if any."""
    year = c.time.year
    if year < HELO_FROM_YEAR:
        return None
    secs = attacking_sectors(c)
    if not secs or r.random() >= P_HELO:
        return None
    return secs[0]


def helo_types(year: int) -> list[str]:
    """The assault wave: Mi-24 gunships (from 1972) with Mi-8 troop carriers."""
    return (["Mi-24V", "Mi-24V"] if year >= 1972 else []) + ["Mi-8MT", "Mi-8MT"]


def raid_label(c: Campaign, raid, target: Objective, home_bases: set[str]) -> str:
    """What a scramble against this raid is called."""
    if target.kind is ObjectiveKind.AIRBASE and target.airbase_id in home_bases:
        return "airfield"
    sq = c.squadrons[raid.squadron_id]
    role = T.airframe_catalogue().get(sq.aircraft_type, {}).get("era_role")
    if sq.airframe:
        from cwdc import era
        af = era.airframes().get(sq.airframe)
        role = af.role if af else role
    if role == "bomber" or sq.aircraft_type in ("Tu-22M3", "Su-24M", "Tu-95MS", "Tu-16"):
        return "bomber"
    if target.kind in (ObjectiveKind.ARMOR, ObjectiveKind.ARTILLERY):
        return "hunt"
    return ""


# ----------------------------------------------------------------------------- outcomes
def note_kill(c: Campaign, o: Objective, points: float) -> None:
    """A vehicle of `o` was destroyed from the air (simulated or in DCS)."""
    for x in this_turn(c):
        if x.target_id == o.id and x.kind in ("tic", "hold", "battery"):
            x.score += points


def simulate(c: Campaign, flown: set[str], r) -> None:
    """Helicopter hunts nobody flew in DCS: decided on the hunting flight's air rating."""
    from cwdc.engine.resolver import _rating
    for x in this_turn(c, "helo"):
        if x.flown:
            continue
        pkg = c.packages.get(x.package_id) if x.package_id else None
        flights = [c.flights[f] for f in (pkg.flight_ids if pkg else []) if f in c.flights and f not in flown]
        if not flights:
            continue
        p = min(0.9, P_HELO_SIM * max(_rating(c, f, "air_rating") for f in flights))
        x.score = 1.0 if r.random() < p else 0.0


def dcs_results(c: Campaign, mission, data: dict) -> list[str]:
    """Score the reactions flown in DCS (helicopters downed). Returns debrief lines."""
    lines = []
    snap = (data or {}).get("snapshot") or {}
    present = {u["name"] for g in snap.get("groups", []) for u in g.get("units", [])}
    dead = {e.get("initiator", {}).get("name") for e in (data or {}).get("events", [])
            if e.get("event") in ("S_EVENT_DEAD", "S_EVENT_CRASH", "S_EVENT_UNIT_LOST")}
    for x in this_turn(c, "helo"):
        pkg = c.packages.get(x.package_id) if x.package_id else None
        if not x.helos or pkg is None or mission.player_flight_id not in pkg.flight_ids:
            continue
        x.flown = True                         # DCS decides this one, not the simulation
        down = [u for u in x.helos if u in dead or (snap and u not in present)]
        x.score = len(down) / len(x.helos)
        lines.append(f"Helicopter hunt: {len(down)} of {len(x.helos)} assault helicopters shot down"
                     + (" - the assault is broken up." if x.score >= x.needed else "."))
    return lines


def sector_factor(c: Campaign, sec: Sector) -> tuple[float, list[str]]:
    """Multiplier on the Warsaw Pact's attack power in this sector this turn, with report lines."""
    f, lines = 1.0, []
    for x in this_turn(c):
        if x.sector_id != sec.id:
            continue
        if x.kind in STALL:
            what = {"tic": "the lead battalion", "hold": f"the attack at the {x.name}",
                    "battery": "the artillery"}[x.kind]
            if x.score >= x.needed:
                f *= STALL[x.kind]
                lines.append(f"  {sec.name}: air support broke up {what} ({x.score:.1f} vehicle points destroyed) "
                             "- the attack stalls.")
            elif x.package_id:
                lines.append(f"  {sec.name}: the air strike on {what} did not stop it.")
        elif x.kind == "helo":
            if x.score >= x.needed:
                lines.append(f"  {sec.name}: the helicopter assault was broken up.")
            else:
                f *= HELO_LIFT
                lines.append(f"  {sec.name}: Warsaw Pact helicopters landed troops behind our lines.")
    return f, lines
