"""Abstract resolution of sorties that were not flown in DCS (extensibility principle 2).

Produces SortieResults with the same structure as flown missions, so both go
through engine.apply.apply_results. Deliberately simple and tunable (Phase 2
refines it): engagements are resolved in order CAP-vs-CAP, interceptions,
SAMs, then weapons effects. A kill on one side is always the matching loss on
the other.
"""
from __future__ import annotations

import random

from cwdc import theater as T
from cwdc.engine import rng
from cwdc.model import (Campaign, Flight, Kill, Objective, ObjectiveKind, PilotFate, PilotStatus,
                        Pos, Side, SortieResult, Task)

CONTACT_RANGE = 120_000
P_CAP_CONTACT = 0.5
P_INTERCEPT = 0.5
P_LEAK = 0.3                     # with an escort, chance interceptors still reach the strikers
BASE_AIR_LOSS = 0.15
P_SAM = 0.07
P_AAA = 0.03
P_WEAPON = 0.45
P_SEAD_HIT = 0.4
AAA_TYPES = ("ZSU", "Vulcan", "Gepard", "Strela", "Chaparral", "ZU-23")
RADAR_HINTS = ("SNR", "1S91", " sr", " tr", "p-19", "pcp", "str")


def _rating(c: Campaign, f: Flight, key: str) -> float:
    t = c.squadrons[f.squadron_id].aircraft_type
    return T.airframe_catalogue().get(t, {}).get(key, 0.8)


class _Sim:
    def __init__(self, c: Campaign, flights: list[Flight], r: random.Random):
        self.c, self.r = c, r
        self.flights = flights
        self.res = {f.id: SortieResult(flight_id=f.id, source="sim") for f in flights}
        self.alive = {f.id: list(range(len(f.airframe_ids))) for f in flights}
        self.destroyed: list[str] = []

    # ------------------------------------------------------------------ helpers
    def where(self, f: Flight) -> Pos:
        return f.area if f.area else self.c.objectives[f.target_id].pos

    def lose(self, f: Flight, slot: int, killer: Flight | None, where: Pos, weapon: str) -> None:
        if slot not in self.alive[f.id]:
            return
        self.alive[f.id].remove(slot)
        res = self.res[f.id]
        res.airframes_lost.append(f.airframe_ids[slot])
        res.pilot_fates.append(self.fate(f.pilot_ids[slot], where))
        if killer is not None and self.alive.get(killer.id):
            ks = self.r.choice(self.alive[killer.id])
            self.res[killer.id].kills.append(Kill(
                pilot_id=killer.pilot_ids[ks], target=f.airframe_ids[slot],
                target_type=self.c.squadrons[f.squadron_id].aircraft_type, weapon=weapon, air=True))

    def fate(self, pilot_id: str, where: Pos) -> PilotFate:
        side = self.c.pilots[pilot_id].side
        if self.r.random() > 0.6:
            return PilotFate(pilot_id=pilot_id, status=PilotStatus.KIA, note="did not eject")
        friendly = T.side_at_pos(self.c.front_line, where) is side
        roll = self.r.random()
        if friendly:
            st = PilotStatus.ACTIVE if roll < 0.8 else PilotStatus.WIA
            return PilotFate(pilot_id=pilot_id, status=st, note="ejected and was recovered")
        st = PilotStatus.POW if roll < 0.7 else PilotStatus.MIA
        return PilotFate(pilot_id=pilot_id, status=st, note="ejected over enemy territory")

    def air_combat(self, a: Flight, b: Flight, a_factor: float = 1.0) -> None:
        ra, rb = _rating(self.c, a, "air_rating") * a_factor, _rating(self.c, b, "air_rating")
        pa = min(0.4, max(0.03, BASE_AIR_LOSS * rb / ra))
        pb = min(0.4, max(0.03, BASE_AIR_LOSS * ra / rb))
        where = self.where(b) if b.task is Task.CAP else self.where(a)
        for s in list(self.alive[a.id]):
            if self.r.random() < pa:
                self.lose(a, s, b, where, "AAM")
        for s in list(self.alive[b.id]):
            if self.r.random() < pb:
                self.lose(b, s, a, where, "AAM")

    def kill_units(self, o: Objective, n: int, prefer_radar: bool = False) -> int:
        cands = [i for i, u in enumerate(o.units)
                 if u.alive and f"{o.id}#{i}" not in self.destroyed]
        if prefer_radar:
            cands.sort(key=lambda i: not any(h in o.units[i].type for h in RADAR_HINTS))
        else:
            self.r.shuffle(cands)
        hit = cands[:n]
        self.destroyed.extend(f"{o.id}#{i}" for i in hit)
        return len(hit)

    def sam_alive(self, o: Objective) -> float:
        alive = [i for i, u in enumerate(o.units) if u.alive and f"{o.id}#{i}" not in self.destroyed]
        return len(alive) / len(o.units)

    # ------------------------------------------------------------------ phases
    def run(self) -> None:
        caps = {s: [f for f in self.flights if f.side is s and f.task is Task.CAP] for s in Side}
        strikes = [f for f in self.flights if f.target_id]
        engaged: set[str] = set()

        # 1. CAP vs CAP sweeps
        for a in caps[Side.BLUE]:
            for b in caps[Side.RED]:
                if a.id in engaged or b.id in engaged:
                    continue
                if a.area.dist(b.area) <= CONTACT_RANGE and self.r.random() < P_CAP_CONTACT:
                    engaged |= {a.id, b.id}
                    self.air_combat(a, b)

        # 2. interception of strike packages by enemy CAP
        for f in strikes:
            tgt = self.c.objectives[f.target_id]
            hostile = [x for x in caps[f.side.enemy] if self.alive[x.id] and x.id not in engaged
                       and x.area.dist(tgt.pos) <= CONTACT_RANGE]
            friendly_cover = [x for x in caps[f.side] if self.alive[x.id]
                              and x.area.dist(tgt.pos) <= CONTACT_RANGE]
            if hostile and f.supports is None:          # support flights ride with their package
                p = max(0.15, P_INTERCEPT - 0.15 * len(friendly_cover))
                if self.r.random() < p:
                    interceptor = self.r.choice(hostile)
                    engaged.add(interceptor.id)
                    escorts = [x for x in self.flights if x.supports == f.id
                               and x.task is Task.ESCORT and self.alive[x.id]]
                    if escorts:
                        # the escort takes the fight; only some interceptors leak through
                        self.air_combat(escorts[0], interceptor)
                        if self.alive[interceptor.id] and self.r.random() < P_LEAK:
                            self.air_combat(f, interceptor, a_factor=0.7)
                    else:
                        self.air_combat(f, interceptor, a_factor=0.7)   # bomb-laden strikers

        # 3. SEAD first (so it can suppress), then everyone else runs the SAM gauntlet
        suppressed: set[str] = set()
        ordered = sorted(strikes, key=lambda f: f.task is not Task.SEAD)
        for f in ordered:
            tgt = self.c.objectives[f.target_id]
            sams = [o for o in self.c.objectives.values() if o.side is f.side.enemy
                    and o.kind is ObjectiveKind.SAM and not o.destroyed
                    and o.pos.dist(tgt.pos) <= o.threat_range_m]
            for sam in sams:
                p = P_SAM * self.sam_alive(sam) * (0.5 if sam.id in suppressed else 1.0)
                for s in list(self.alive[f.id]):
                    if self.r.random() < p:
                        self.lose(f, s, None, tgt.pos, sam.name)
            aaa = [u for u in tgt.units if u.alive and any(a in u.type for a in AAA_TYPES)]
            for _ in aaa:
                for s in list(self.alive[f.id]):
                    if self.r.random() < P_AAA:
                        self.lose(f, s, None, tgt.pos, "AAA")

            # 4. weapons effects
            n_alive = len(self.alive[f.id])
            if not n_alive:
                continue
            if f.task is Task.SEAD:
                hits = sum(self.r.random() < P_SEAD_HIT for _ in range(n_alive))
                if hits:
                    suppressed.add(tgt.id)
                    n = self.kill_units(tgt, hits, prefer_radar=True)
                    self._ground_kills(f, tgt, n)
            else:
                p = P_WEAPON * _rating(self.c, f, "strike_rating")
                hits = sum(self.r.random() < p for _ in range(2 * n_alive))
                n = self.kill_units(tgt, hits)
                self._ground_kills(f, tgt, n)

    def _ground_kills(self, f: Flight, o: Objective, n: int) -> None:
        if not n or not self.alive[f.id]:
            return
        for uid in self.destroyed[-n:]:
            s = self.r.choice(self.alive[f.id])
            idx = int(uid.split("#")[1])
            self.res[f.id].kills.append(Kill(pilot_id=f.pilot_ids[s], target=uid,
                                             target_type=o.units[idx].type, air=False))

    def results(self) -> list[SortieResult]:
        from cwdc.ingest.flown import _summary
        out = []
        for f in self.flights:
            r = self.res[f.id]
            r.objective_units_destroyed = [k.target for k in r.kills if not k.air]
            r.summary = _summary(self.c, f.callsign, r)
            out.append(r)
        return out


def resolve(c: Campaign, flights: list[Flight]) -> list[SortieResult]:
    sim = _Sim(c, flights, rng(c.seed, c.turn, "resolve"))
    sim.run()
    return sim.results()
