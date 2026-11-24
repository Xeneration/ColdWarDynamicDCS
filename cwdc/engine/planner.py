"""Air tasking: both sides plan every sortie of the turn (extensibility principle 1).

Phase 2 step 5, "air commanders take 2". Each side:
  1. mans its barrier-CAP stations (doctrine decides how many);
  2. scores its *needs* - CAS where an enemy attack threatens a sector, BAI/strike on
     enemy formations elsewhere, interdiction of the supply lines feeding enemy
     offensives, SEAD and strikes on air-defence and radar sites;
  3. fills the needs in priority order from squadrons that can fly them, building
     **packages**: the main flight plus a SEAD flight if the target sits under an
     enemy SAM umbrella, and an escort if enemy fighters can reach it.

Step 6 adds OCA: strikes on enemy airfields that host squadrons (engine.airbases).
Sortie rate is limited by each squadron's `sorties`, its ready aircraft and rested crews
(pilots, and WSOs for two-seat types - engine.crews), its
airbase's damage (a closed base flies nothing) and the period (night: CAP only).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from cwdc import theater as T
from cwdc.engine import airbases, crews, rng, weather
from cwdc.model import (Campaign, CrewRole, Flight, FlightStatus, Objective, ObjectiveKind, Package,
                        PilotStatus, Pos, Side, Squadron, Task)

FLIGHT_SIZE = 2
SORTIES = {"player": 4, "default": 2}     # player squadron: CAP, OCA/battlefield, alert reserve
STATION_NAMES = ["Alpha", "Bravo", "Charlie", "Delta"]
ENEMY_FIGHTER_REACH = 120_000     # enemy CAP stations this close to a target justify an escort
OCA_BASE, OCA_PER_SQUADRON = 2.0, 1.5

# Kept for callers that ask which objective kinds a task can attack.
TARGET_KINDS = {
    Task.STRIKE: {ObjectiveKind.DEPOT, ObjectiveKind.ARTILLERY, ObjectiveKind.ARMOR,
                  ObjectiveKind.COMMAND, ObjectiveKind.EWR, ObjectiveKind.RAIL,
                  ObjectiveKind.AIRBASE},
    Task.INTERDICTION: {ObjectiveKind.RAIL, ObjectiveKind.CONVOY, ObjectiveKind.DEPOT},
    Task.CAS: {ObjectiveKind.ARMOR, ObjectiveKind.ARTILLERY},
    Task.BAI: {ObjectiveKind.ARMOR, ObjectiveKind.ARTILLERY, ObjectiveKind.DEPOT,
               ObjectiveKind.CONVOY},
    Task.SEAD: {ObjectiveKind.SAM},
}

# Which squadron tasks can fly a need, in order of preference.
CAN_FLY = {
    Task.CAS: [Task.CAS, Task.BAI, Task.STRIKE],
    Task.BAI: [Task.BAI, Task.STRIKE, Task.CAS],
    Task.STRIKE: [Task.STRIKE, Task.BAI, Task.INTERDICTION],
    Task.INTERDICTION: [Task.INTERDICTION, Task.STRIKE, Task.BAI],
    Task.SEAD: [Task.SEAD],
    Task.ARMED_RECON: [Task.CAS, Task.BAI, Task.STRIKE],
}
ARMED_RECON_PER_DAY = 1      # NATO: one road hunt a day against a Warsaw Pact supply column

DOCTRINE = {
    # NATO: one barrier CAP (the Hawk belt does the rest), escorts and SEAD for packages
    Side.BLUE: {"cap_stations": 1, "cas": 1.0, "bai": 1.0, "interdiction": 1.0, "strike": 1.0, "oca": 0.8,
                "escort": True, "sead_support": True,
                "oca_per_day": 1,           # a counter-air package a day when the air war calls for it
                "alert_reserve": 1},        # sorties held back for scrambles / sweeps
    # Warsaw Pact: GCI-directed fighters on CAP, mass on the battlefield, few escorts,
    # and a doctrine of hitting NATO airfields to win the air battle on the ground
    Side.RED: {"cap_stations": 2, "cas": 1.2, "bai": 1.0, "interdiction": 0.8, "strike": 0.8, "oca": 1.0,
               "escort": False, "sead_support": True},
}


@dataclass
class Need:
    task: Task
    target: Objective
    score: float
    label: Optional[str] = None        # e.g. "OCA" for a strike on an airfield
    reason: str = ""                   # plain words for the player: why this target


# ----------------------------------------------------------------------------- helpers

def period(c: Campaign) -> str:
    h = c.time.hour
    if 5 <= h < 11:
        return "dawn"
    if 11 <= h < 17:
        return "day"
    if 17 <= h < 21:
        return "dusk"
    return "night"


def _flyable(sq: Squadron) -> bool:
    return bool(T.airframe_catalogue().get(sq.aircraft_type, {}).get("flyable"))


def _feeds_attack(c: Campaign, o: Objective) -> bool:
    """Does this logistics target supply a sector where its side is attacking?"""
    nodes = set()
    if o.supply_node:
        nodes.add(o.supply_node)
    if o.supply_link:
        lk = next((l for l in c.supply_links if l.id == o.supply_link), None)
        if lk:
            nodes.add(lk.b)
    return any(sec.attacker is o.side and sec.depots.get(o.side) in nodes
               for sec in c.sectors.values())


def _sam_cover(c: Campaign, target: Objective, enemy: Side) -> list[Objective]:
    return [o for o in c.objectives.values() if o.side is enemy and o.kind is ObjectiveKind.SAM
            and not o.destroyed and o.id != target.id and o.pos.dist(target.pos) <= o.threat_range_m]


def _enemy_fighters_near(c: Campaign, pos: Pos, enemy: Side) -> bool:
    return any(p.dist(pos) <= ENEMY_FIGHTER_REACH for p in c.cap_stations.get(enemy, []))


# ----------------------------------------------------------------------------- needs

def needs(c: Campaign, side: Side) -> list[Need]:
    from cwdc.engine.ground import power
    doc = DOCTRINE[side]
    enemy = side.enemy
    out: list[Need] = []
    live = [o for o in c.objectives.values() if o.side is enemy and not o.destroyed]

    for o in live:
        frac = len(o.alive_units) / len(o.units)
        if o.formation_id and o.kind in (ObjectiveKind.ARMOR, ObjectiveKind.ARTILLERY):
            sec = c.sectors.get(o.sector_id) if o.sector_id else None
            if sec is not None:
                own, theirs = power(c, sec.id, side), power(c, sec.id, enemy)
                if sec.attacker is enemy:                       # they are attacking us here
                    crit = theirs / max(own, 1.0)
                    out.append(Need(Task.CAS, o, (4 + 3 * crit) * frac * doc["cas"],
                                    reason=f"they are attacking in the {sec.name} "
                                           f"({crit:.1f} : 1 against our troops)"))
                    continue
            push = 2.0 if sec is not None and sec.attacker is side else 0.0   # support our offensive
            why = (f"supports our own attack in the {sec.name}" if push
                   else f"wears down their forces in the {sec.name}" if sec else "enemy formation")
            out.append(Need(Task.BAI, o, (2 + 0.5 * o.value + push) * frac * doc["bai"], reason=why))
        elif o.kind in (ObjectiveKind.RAIL, ObjectiveKind.CONVOY, ObjectiveKind.DEPOT):
            feeds = _feeds_attack(c, o)
            s = 2 + 0.5 * o.value + (4 if feeds else 0)
            out.append(Need(Task.INTERDICTION, o, s * frac * doc["interdiction"],
                            reason="cuts the supplies feeding their offensive" if feeds
                            else "cuts an enemy supply line"))
        elif o.kind is ObjectiveKind.SAM:
            out.append(Need(Task.SEAD, o, (1.5 + 0.3 * o.value) * frac,
                            reason="this missile site threatens our aircraft"))
        elif o.kind in (ObjectiveKind.EWR, ObjectiveKind.COMMAND):
            out.append(Need(Task.STRIKE, o, (1 + 0.3 * o.value) * frac * doc["strike"],
                            reason="blinds their radar / command network" if o.kind is ObjectiveKind.EWR
                            else "disrupts their command"))
        elif o.kind is ObjectiveKind.AIRBASE and o.airbase_id:
            ab = c.airbases[o.airbase_id]
            based = sum(1 for sq in c.squadrons.values() if sq.base_id == ab.id and sq.side is enemy)
            if based and ab.damage < airbases.CLOSED:            # nothing to gain on an empty / shut base
                s = (OCA_BASE + OCA_PER_SQUADRON * based) * (1 - ab.damage) * doc["oca"]
                out.append(Need(Task.STRIKE, o, s, label="OCA",
                                reason=f"{based} enemy squadron(s) fly from here"))
    return sorted(out, key=lambda n: -n.score)


# ----------------------------------------------------------------------------- planning

class _Pool:
    """Remaining sorties, aircraft and crews of one side's squadrons this turn."""

    def __init__(self, c: Campaign, side: Side, r):
        self.c = c
        self.left: dict[str, int] = {}
        self.airframes: dict[str, list] = {}
        self.pilots: dict[str, list] = {}
        self.wsos: dict[str, list] = {}
        for sq in sorted((s for s in c.squadrons.values() if s.side is side), key=lambda s: s.id):
            afs = c.squadron_airframes(sq.id)
            r.shuffle(afs)
            pls = sorted(crews.available(c, sq.id, CrewRole.PILOT), key=crews.schedule_order)
            wsos = sorted(crews.available(c, sq.id, CrewRole.WSO), key=crews.schedule_order)                 if crews.two_seat(sq) else None
            self.airframes[sq.id], self.pilots[sq.id], self.wsos[sq.id] = afs, pls, wsos
            n = sq.sorties or (SORTIES["player"] if sq.player_squadron else SORTIES["default"])
            if wsos is not None:
                n = min(n, len(wsos) // FLIGHT_SIZE)
            n = airbases.sorties(c, sq.base_id, n)                # a damaged base launches less
            n = int(n * weather.launch_factor(c.weather))          # fog: fewer launches
            self.left[sq.id] = min(n, len(afs) // FLIGHT_SIZE, len(pls) // FLIGHT_SIZE)

    def pick(self, tasks: list[Task], near: Pos | None = None, exclude: set[str] = frozenset()):
        """Best squadron able to fly one of `tasks`: specialists first, then nearest."""
        best = None
        for sq_id, n in self.left.items():
            if n <= 0 or sq_id in exclude:
                continue
            sq = self.c.squadrons[sq_id]
            # role discipline: only a need's first two acceptable tasks (no strike squadron on CAS)
            options = [t for t in tasks[:2] if t in sq.tasks]
            if not options:
                continue
            spec = sq.tasks.index(options[0])                   # lower = more of a specialist
            dist = self.c.airbases[sq.base_id].pos.dist(near) if near else 0.0
            key = (tasks.index(options[0]), spec, dist)
            if best is None or key < best[0]:
                best = (key, sq, options[0])
        return (best[1], best[2]) if best else (None, None)

    def take(self, sq: Squadron) -> tuple[list, list, list]:
        self.left[sq.id] -= 1
        afs = [self.airframes[sq.id].pop(0) for _ in range(FLIGHT_SIZE)]
        pls = [self.pilots[sq.id].pop(0) for _ in range(FLIGHT_SIZE)]
        wsos = [self.wsos[sq.id].pop(0) for _ in range(FLIGHT_SIZE)] if self.wsos[sq.id] is not None else []
        return afs, pls, wsos


def plan_turn(c: Campaign) -> None:
    """Replace the current ATO with a fresh plan for this turn."""
    c.packages.clear()
    c.flights.clear()
    r = rng(c.seed, c.turn, "plan")
    night = period(c) == "night"
    pools: dict[Side, _Pool] = {}
    reserves: dict[Side, dict[str, int]] = {}

    for side in Side:
        doc = DOCTRINE[side]
        pool = pools[side] = _Pool(c, side, r)

        # 1. barrier CAP
        stations = c.cap_stations.get(side, [])[:doc["cap_stations"]]
        for idx, st in enumerate(stations):
            sq, task = pool.pick([Task.CAP], near=st)
            if sq:
                pkg = _package(c, side, Task.CAP, f"Barrier CAP, station {STATION_NAMES[idx % 4]}", area=st)
                pkg.reason = "keeps enemy aircraft away from our side of the front"
                _flight(c, pool, sq, Task.CAP, pkg, area=st)
        # the Warsaw Pact's first blow after a crisis (campaigns started at war are already past it)
        opening = side is Side.RED and c.war_turn == 1 and c.war_start_turn > 1
        if (night and not opening) or not c.at_war:
            continue      # the F-4E/MiGs of 1976 do little at night; no strikes before the war
        # alert reserve: the player's squadron holds a sortie back for scrambles and sweeps
        reserve = {}
        for sq_id in [s.id for s in c.squadrons.values() if s.side is side and s.player_squadron]:
            if doc.get("alert_reserve") and pool.left.get(sq_id, 0) > doc["alert_reserve"]:
                reserve[sq_id] = doc["alert_reserve"]
                pool.left[sq_id] -= doc["alert_reserve"]
        reserves[side] = reserve

        # 2. needs, in priority order, each with its escort and SEAD
        taken: set[str] = set()
        side_needs = needs(c, side)
        if opening:                                   # open the war on NATO's airfields
            side_needs.sort(key=lambda n: n.label != "OCA")
        forced_oca = None
        if doc.get("oca_per_day") and oca_due(c, side):
            oca = [n for n in side_needs if n.label == "OCA"]
            if oca:                                   # doctrine: this one is flown, not out-scored
                forced_oca = oca[0].target.id
                side_needs.remove(oca[0])
                side_needs.insert(0, oca[0])
        special = player_special(c) if side is Side.BLUE else None
        special_need = None
        if special in ("iron_hand", "precision", "runway") and not c.counters.get(f"special:{c.time:%Y%m%d}") \
                and not (special == "runway" and forced_oca):
            cand = [n for n in side_needs if n.task is Task.SEAD] if special == "iron_hand" else \
                [n for n in side_needs if n.label == "OCA"] if special == "runway" else \
                [n for n in side_needs if n.task is Task.STRIKE
                 and n.target.kind in (ObjectiveKind.EWR, ObjectiveKind.COMMAND)]
            if cand:
                special_need = cand[0].target.id
                side_needs.remove(cand[0])
                side_needs.insert(1 if forced_oca else 0, cand[0])
        player_left = sum(n for sq_id, n in pool.left.items() if c.squadrons[sq_id].player_squadron)
        player_busy = (1 if forced_oca else 0) + (1 if special_need else 0)
        if side is Side.BLUE and c.counters.get(f"recon:{c.time:%Y%m%d}", 0) < ARMED_RECON_PER_DAY \
                and player_left - player_busy >= 2:
            columns = [n for n in side_needs if n.target.kind is ObjectiveKind.CONVOY]
            if columns:                               # the daily road hunt is flown, not out-scored
                side_needs.remove(columns[0])
                side_needs.insert(player_busy, columns[0])     # after the OCA and the special
        wx = weather.strike_factor(c.weather)
        for need in side_needs:
            if need.target.id in taken:
                continue
            recon = (side is Side.BLUE and need.target.kind is ObjectiveKind.CONVOY
                     and c.counters.get(f"recon:{c.time:%Y%m%d}", 0) < ARMED_RECON_PER_DAY)
            sq, task = pool.pick(CAN_FLY[need.task], near=need.target.pos)
            if recon:                                 # a road hunt instead of a set-piece attack
                own = {s.id for s in c.squadrons.values() if s.side is side and not s.player_squadron}
                rsq, _ = pool.pick(CAN_FLY[Task.ARMED_RECON], near=need.target.pos, exclude=own)
                rsq = rsq or pool.pick(CAN_FLY[Task.ARMED_RECON], near=need.target.pos)[0]
                if rsq is not None and rsq.player_squadron and pool.left[rsq.id] < 2:
                    rsq = None                        # keep the player's last sortie for the battlefield
                if rsq is not None:
                    sq, task = rsq, Task.ARMED_RECON
                    need.label = "Armed recon"
                    need.reason = "hunt the supply columns on the roads behind their front"
            if need.target.id == forced_oca and special in ("oca", "runway") \
                    or need.target.id == special_need:   # the player's squadron's special of the day
                own = [s.id for s in c.squadrons.values() if s.side is side and not s.player_squadron]
                psq, ptask = pool.pick(CAN_FLY[need.task], near=need.target.pos, exclude=set(own))
                if psq is not None:
                    sq, task = psq, ptask
            if sq is None:
                continue
            if wx < 1.0 and r.random() > wx:
                pool.left[sq.id] -= 1                 # weather scrub: that sortie stays on the ground
                continue
            taken.add(need.target.id)
            if task is Task.ARMED_RECON:
                key = f"recon:{c.time:%Y%m%d}"
                c.counters[key] = c.counters.get(key, 0) + 1
            if need.target.id == forced_oca:
                key = f"oca:{side.value}:{c.time:%Y%m%d}"
                c.counters[key] = c.counters.get(key, 0) + 1
            variant = None
            if sq.player_squadron and special == "runway" and need.target.id in (forced_oca, special_need):
                need.label, variant = "Runway attack", "Runway"
                need.reason = "crater their runway: a closed field launches nothing"
                c.counters[f"special:{c.time:%Y%m%d}"] = 1
            elif sq.player_squadron and need.target.id == special_need:
                need.label = "Iron Hand" if special == "iron_hand" else "Precision strike"
                variant = "Precision" if special == "precision" else None
                c.counters[f"special:{c.time:%Y%m%d}"] = 1
            pkg = _package(c, side, task, f"{need.label or task.value} - {need.target.name}",
                           target=need.target)
            pkg.reason = need.reason
            main = _flight(c, pool, sq, task, pkg, target=need.target)
            if variant:
                fits = T.airframe_catalogue().get(sq.aircraft_type, {}).get("loadouts", {})
                main.loadout = fits.get(variant, main.loadout)
            if task is Task.SEAD:
                continue
            if doc["sead_support"]:
                for sam in _sam_cover(c, need.target, side.enemy)[:1]:
                    ssq, _ = pool.pick([Task.SEAD], near=sam.pos)
                    if ssq and sam.id not in taken:
                        taken.add(sam.id)
                        _flight(c, pool, ssq, Task.SEAD, pkg, target=sam, supports=main.id)
            if (doc["escort"] or need.target.value >= 4) and _enemy_fighters_near(c, need.target.pos, side.enemy):
                # a forced OCA flown by the player's squadron is escorted by someone else, so the
                # squadron keeps a sortie for the battlefield
                spare = {sq.id} if sq.player_squadron and pool.left[sq.id] < 2 else set()
                esq, _ = pool.pick([Task.ESCORT, Task.CAP], near=need.target.pos, exclude=spare)
                if esq:
                    _flight(c, pool, esq, Task.ESCORT, pkg, area=need.target.pos, supports=main.id)
            _describe(c, pkg)
    if not c.at_war:
        _crisis_ops(c, pools, r)
    elif not night:
        for side, reserve in reserves.items():         # release the alert reserve...
            for sq_id, n in reserve.items():
                pools[side].left[sq_id] += n
        _fighter_ops(c, pools, r)                       # ...for scrambles and sweeps


INTERCEPT_LEAD = 60_000      # m: scramble to meet a raid this far short of its target
INTERCEPT_REACH = 60_000     # m: raids on targets this close to one of our airfields are intercepted
MAX_INTERCEPTS = 2
P_ESCORT_NOT_SWEEP = 0.5     # the released alert sortie escorts a bare package rather than sweeping
P_INTERCEPT = 0.6            # chance a battlefield raid is intercepted rather than a sweep flown


def _fighter_ops(c: Campaign, pools: dict, r) -> None:
    """NATO fighter tasking after both sides have planned: scramble against Warsaw Pact raids
    on our airfields (and targets near them), else sweep ahead of our own strike packages."""
    side = Side.BLUE
    pool = pools.get(side)
    if pool is None:
        return
    from cwdc.engine import rescue
    for p in rescue.evaders(c, side)[:1]:              # a downed crew comes first
        others = {s.id for s in c.squadrons.values() if s.side is side and not s.player_squadron}
        sq, _ = pool.pick([Task.CAS, Task.CAP], near=p.down_pos, exclude=others)
        sq = sq or pool.pick([Task.CAS, Task.CAP], near=p.down_pos)[0]
        if sq is None:
            break
        pkg = _package(c, side, Task.RESCAP, f"RESCAP - cover the rescue of {p.rank} {p.name}",
                       area=p.down_pos)
        pkg.reason = f"{p.rank} {p.name} is down behind enemy lines and evading capture"
        f = _flight(c, pool, sq, Task.RESCAP, pkg, area=p.down_pos)
        f.rescue_of = p.id
    bases = [ab.pos for ab in c.airbases.values() if ab.side is side]
    raids = []
    for p in c.packages.values():
        if p.side is side.enemy and p.target_id and p.flight_ids:
            tgt = c.objectives[p.target_id]
            deep = tgt.kind not in (ObjectiveKind.ARMOR, ObjectiveKind.ARTILLERY) \
                or any(tgt.pos.dist(b) <= INTERCEPT_REACH for b in bases)
            raids.append((not deep, p, tgt))          # deep raids first, then battlefield ones
    raids.sort(key=lambda x: x[0])
    raids = [(p, tgt) for _, p, tgt in raids]
    battlefield = raids and raids[0][1].kind in (ObjectiveKind.ARMOR, ObjectiveKind.ARTILLERY)
    if battlefield and r.random() > P_INTERCEPT:     # deep raids are always met; battlefield
        raids = []                                    # ones sometimes give way to a sweep
    for p, tgt in raids[:MAX_INTERCEPTS]:
        raid = c.flights[p.flight_ids[0]]
        red_base = c.airbases[c.squadrons[raid.squadron_id].base_id].pos
        area = _toward_pos(tgt.pos, red_base, INTERCEPT_LEAD)
        sq, _ = pool.pick([Task.CAP], near=area)
        if sq is None:
            break
        pkg = _package(c, side, Task.INTERCEPT, f"Scramble - intercept {raid.callsign} raiding {tgt.name}",
                       area=area)
        pkg.reason = "a Warsaw Pact raid is inbound: meet it before it reaches the target"
        f = _flight(c, pool, sq, Task.INTERCEPT, pkg, area=area)
        f.intercepts = raid.id
    if raids:
        return
    strikes = [p for p in c.packages.values() if p.side is side and p.target_id
               and _enemy_fighters_near(c, c.objectives[p.target_id].pos, side.enemy)]
    if not strikes:
        return
    bare = [p for p in strikes if not any(c.flights[f].task is Task.ESCORT for f in p.flight_ids)]
    if bare and r.random() < P_ESCORT_NOT_SWEEP:      # escort an unprotected package instead
        p = bare[0]
        main = c.flights[p.flight_ids[0]]
        sq, _ = pool.pick([Task.ESCORT, Task.CAP], near=c.objectives[p.target_id].pos)
        if sq is not None:
            _flight(c, pool, sq, Task.ESCORT, p, area=c.objectives[p.target_id].pos, supports=main.id)
            if "(with" not in p.description:
                _describe(c, p)
            return
    tgt = c.objectives[strikes[0].target_id]
    station = min(c.cap_stations.get(side.enemy, []), key=lambda s: s.dist(tgt.pos), default=None)
    if station is None:
        return
    sq, _ = pool.pick([Task.CAP], near=station)
    if sq is None:
        return
    main = c.flights[strikes[0].flight_ids[0]]
    pkg = _package(c, side, Task.SWEEP, f"Fighter sweep - clear the MiG patrol ahead of {main.callsign}",
                   area=station)
    pkg.reason = "enemy fighters patrol near our strike's target: clear them out first"
    _flight(c, pool, sq, Task.SWEEP, pkg, area=station)


def _toward_pos(a: Pos, b: Pos, dist: float) -> Pos:
    d = a.dist(b) or 1.0
    if d <= dist:
        return b
    return Pos(x=a.x + (b.x - a.x) * dist / d, z=a.z + (b.z - a.z) * dist / d)


SPECIALS = ("oca", "iron_hand", "runway", "precision")   # the player's squadron rotates daily


def player_special(c: Campaign) -> str | None:
    """Which special strike the player's squadron flies today (rotates with the war day)."""
    from cwdc.engine import outcome
    return SPECIALS[(outcome.war_day(c) - 1) % len(SPECIALS)] if c.at_war else None


OCA_OPENING_TURNS = 8        # the first two days of the war: hit back at their airfields
OCA_RAID_MEMORY = 4          # turns: one of our airfields was raided this recently


def oca_justified(c: Campaign, side: Side) -> bool:
    """Doctrine trigger for a counter-air package: the war's opening days, a recent raid on one
    of our airfields, or the air balance slipping."""
    from cwdc.engine import outcome
    if c.war_turn and c.war_turn <= OCA_OPENING_TURNS:
        return True
    if any(ab.side is side and ab.hit_turn >= 0 and ab.hit_turn >= c.turn - OCA_RAID_MEMORY
           for ab in c.airbases.values()):
        return True
    return outcome.air_strength(c, side) < outcome.air_strength(c, side.enemy)


def oca_due(c: Campaign, side: Side) -> bool:
    doc = DOCTRINE[side]
    if not c.at_war or period(c) == "night":
        return False
    done = c.counters.get(f"oca:{side.value}:{c.time:%Y%m%d}", 0)
    return done < doc.get("oca_per_day", 0) and oca_justified(c, side)


PROBES = {"dawn": (1, 2), "day": (1, 3), "dusk": (0, 1), "night": (0, 1)}   # per turn, by period
PROBE_DEPTH = 8_000          # m: probes reach this far over the border
PROBE_FRONT_RANGE = 150_000  # m: probes stay on the stretch of border NATO's CAP stations watch
MAX_QRA = 2


def probe_points(c: Campaign) -> list[Pos]:
    """Points just over the border (NATO side) along the watched stretch of it."""
    stations = c.cap_stations.get(Side.BLUE, [])
    out = []
    for lat, lon in c.front_line:
        p = T.latlon_to_pos(lat, lon)
        if stations and min(p.dist(s) for s in stations) > PROBE_FRONT_RANGE:
            continue
        q = Pos(x=p.x, z=p.z - PROBE_DEPTH)                     # DCS z is east: west = NATO side
        if T.side_at_pos(c.front_line, q) is Side.BLUE:
            out.append(q)
    return out


def _crisis_ops(c: Campaign, pools: dict[Side, _Pool], r) -> None:
    """Crisis tasking: Warsaw Pact border probes and the NATO QRA scrambled against them."""
    points = probe_points(c)
    if not points:
        return
    n = r.randint(*PROBES[period(c)])
    for k, area in enumerate(r.sample(points, min(n, len(points)))):
        sq, _ = pools[Side.RED].pick([Task.CAP], near=area)
        if sq is None:
            break
        lat, lon = T.pos_to_latlon(area)
        pkg = _package(c, Side.RED, Task.RECON, f"Border probe near {lat:.2f}N {lon:.2f}E", area=area)
        pkg.reason = "tests NATO's air defence and photographs the border"
        probe = _flight(c, pools[Side.RED], sq, Task.RECON, pkg, area=area)
        if k >= MAX_QRA:
            continue
        qsq, _ = pools[Side.BLUE].pick([Task.CAP], near=area)
        if qsq is None:
            continue
        q = _package(c, Side.BLUE, Task.QRA, f"QRA - intercept and shadow {probe.callsign}", area=area)
        q.reason = "a Warsaw Pact aircraft is heading for the border: identify it and see it off"
        qf = _flight(c, pools[Side.BLUE], qsq, Task.QRA, q, area=area)
        qf.intercepts = probe.id


def _package(c: Campaign, side: Side, task: Task, desc: str, target: Objective | None = None,
             area: Pos | None = None) -> Package:
    pid = c.next_id("P")
    pkg = Package(id=pid, side=side, task=task, description=desc, flight_ids=[],
                  target_id=target.id if target else None, area=area)
    c.packages[pid] = pkg
    return pkg


def _flight(c: Campaign, pool: _Pool, sq: Squadron, task: Task, pkg: Package,
            target: Objective | None = None, area: Pos | None = None,
            supports: str | None = None) -> Flight:
    afs, pls, wsos = pool.take(sq)
    n = c.counters.get(f"cs:{sq.id}:{c.turn}", 0) + 1
    c.counters[f"cs:{sq.id}:{c.turn}"] = n
    fid = c.next_id("F")
    loadouts = T.airframe_catalogue().get(sq.aircraft_type, {}).get("loadouts", {})
    f = Flight(id=fid, package_id=pkg.id, side=sq.side, squadron_id=sq.id, task=task,
               callsign=f"{sq.callsign} {n}", airframe_ids=[a.id for a in afs],
               pilot_ids=[p.id for p in pls], wso_ids=[p.id for p in wsos], target_id=target.id if target else None, area=area,
               loadout=loadouts.get(task.value) or loadouts.get(
                   "CAP" if task in (Task.ESCORT, Task.QRA, Task.RECON) else ""),
               player_eligible=sq.player_squadron and _flyable(sq), supports=supports)
    c.flights[fid] = f
    pkg.flight_ids.append(fid)
    return f


def _describe(c: Campaign, pkg: Package) -> None:
    support = [c.flights[f] for f in pkg.flight_ids[1:]]
    if support:
        pkg.description += " (with " + ", ".join(
            f"{'escort' if f.task is Task.ESCORT else f.task.value} {f.callsign}" for f in support) + ")"


def package_flights(c: Campaign, flight_id: str) -> list[Flight]:
    f = c.flights[flight_id]
    return [c.flights[x] for x in c.packages[f.package_id].flight_ids]


# ----------------------------------------------------------------------------- player seat

def assign_player(c: Campaign, flight_id: str) -> Flight:
    """Put the player pilot in the lead seat of a flight; the displaced pilot stands down."""
    f = c.flights[flight_id]
    if not f.player_eligible:
        raise ValueError(f"{flight_id} ({f.callsign}) is not a flight you can fly")
    player = c.player_pilot()
    if player.status is not PilotStatus.ACTIVE:
        raise ValueError(f"{player.name} is {player.status.value} and cannot fly")
    for other in c.flights.values():          # leave any flight chosen earlier this turn
        if other.id != f.id:
            release_player(c, other.id)
    if player.id not in f.pilot_ids:
        f.displaced_pilot_id = f.pilot_ids[0]
        f.pilot_ids[0] = player.id
    f.status = FlightStatus.ASSIGNED_PLAYER
    return f


def release_player(c: Campaign, flight_id: str) -> None:
    """Undo assign_player: the stood-down pilot takes the lead seat back."""
    f = c.flights[flight_id]
    player = c.player_pilot()
    if player.id in f.pilot_ids and f.displaced_pilot_id:
        f.pilot_ids[f.pilot_ids.index(player.id)] = f.displaced_pilot_id
        f.displaced_pilot_id = None
    if f.status is FlightStatus.ASSIGNED_PLAYER:
        f.status = FlightStatus.PLANNED
