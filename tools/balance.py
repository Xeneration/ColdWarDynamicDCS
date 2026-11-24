"""Balance report for the strategic layer (Phase 2 step 9).

Plays whole wars with every sortie simulated and reports what the targets in
docs/design.md care about: per-sortie loss rates (by side, task and cause), how the
front moves over the first days, who attacks where, OCA damage and outcomes.

    python -m tools.balance --seeds 6 --days 21
    python -m tools.balance --seeds 6 --days 21 --crisis   # open with the pre-war crisis (Phase 4)
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict

from cwdc.engine import outcome, resolver, turn
from cwdc.engine.factory import new_campaign
from cwdc.model import AirframeStatus, Side

REPORT_DAYS = (1, 3, 7, 14, 21)


def run(seed: int, days: int, stats: dict, crisis: bool = False) -> dict:
    c = new_campaign(f"Balance {seed}", seed=seed, crisis=crisis)
    crisis_info = None
    if crisis:
        while not c.at_war:
            tension, deadline = c.tension, c.crisis_deadline_turn
            turn.end_turn(c)
        trigger = "deadline" if c.turn >= deadline and tension < 100 else "tension"
        crisis_info = {"turns": c.war_start_turn - 1, "trigger": trigger}
        stats["crisis"].append(crisis_info)
    tpd = outcome.turns_per_day(c)
    sorties = Counter()
    lost_air = Counter()
    by_task = defaultdict(lambda: [0, 0])          # (side, task) -> [sorties, losses]
    fronts = {}
    blue_attacks = 0
    orig_lose = resolver._Sim.lose

    def lose(self, f, slot, killer, where, weapon):
        if slot in self.alive[f.id]:
            cause = "air" if weapon == "AAM" else ("AAA" if weapon == "AAA" else "SAM")
            stats["cause"][(f.side, cause)] += 1
            by_task[(f.side, f.task.value)][1] += 1
            lost_air[f.side] += 1
        return orig_lose(self, f, slot, killer, where, weapon)

    resolver._Sim.lose = lose
    try:
        for t in range(days * tpd):
            for p in c.packages.values():
                if p.description.startswith("OCA"):
                    stats["oca"][p.side] += 1
            for f in c.flights.values():
                sorties[f.side] += len(f.airframe_ids)
                by_task[(f.side, f.task.value)][0] += len(f.airframe_ids)
            turn.end_turn(c)
            blue_attacks += sum(1 for s in c.sectors.values() if s.attacker is Side.BLUE)
            day = t // tpd + 1
            if (t + 1) % tpd == 0 and day in REPORT_DAYS:
                fronts[day] = {s.id[:4]: round(s.offset_km) for s in c.sectors.values()}
            if c.outcome:
                break
    finally:
        resolver._Sim.lose = orig_lose
    destroyed = Counter(c.squadrons[a.squadron_id].side for a in c.airframes.values()
                        if a.status is AirframeStatus.DESTROYED)
    for k, v in by_task.items():
        stats["task"][k][0] += v[0]
        stats["task"][k][1] += v[1]
    for s in Side:
        stats["sorties"][s] += sorties[s]
        stats["lost_air"][s] += lost_air[s]
        stats["destroyed"][s] += destroyed[s]
    o = c.outcome
    return {"seed": seed, "outcome": f"{o.title} (day {outcome.war_day(c)})" if o else "ongoing",
            "fronts": fronts, "blue_attack_turns": blue_attacks, "crisis": crisis_info,
            "air": {s.value: f"{outcome.air_strength(c, s):.0%}" for s in Side}}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", type=int, default=4)
    ap.add_argument("--first-seed", type=int, default=1)
    ap.add_argument("--days", type=int, default=21)
    ap.add_argument("--crisis", action="store_true", help="open each campaign with the pre-war crisis")
    args = ap.parse_args()
    stats = {"cause": Counter(), "task": defaultdict(lambda: [0, 0]), "sorties": Counter(),
             "lost_air": Counter(), "destroyed": Counter(), "crisis": [], "oca": Counter()}
    outcomes = Counter()
    for seed in range(args.first_seed, args.first_seed + args.seeds):
        r = run(seed, args.days, stats, args.crisis)
        outcomes[r["outcome"].split(" (")[0]] += 1
        cr = f"; crisis {r['crisis']['turns']} turns ({r['crisis']['trigger']})" if r["crisis"] else ""
        print(f"seed {r['seed']}: {r['outcome']}; air left B {r['air']['blue']} R {r['air']['red']}; "
              f"NATO attack sector-turns {r['blue_attack_turns']}{cr}")
        for day, fr in r["fronts"].items():
            print(f"    day {day:>2}: " + " ".join(f"{k} {v:+d}" for k, v in fr.items()))
    print("\nPer-sortie losses (in the air) and aircraft destroyed in total (incl. on the ground):")
    for s in Side:
        n = stats["sorties"][s] or 1
        causes = ", ".join(f"{cause} {stats['cause'][(s, cause)] / n:.1%}" for cause in ("air", "SAM", "AAA"))
        print(f"  {s.value:4}: {stats['sorties'][s]} sorties, air losses {stats['lost_air'][s] / n:.1%} "
              f"({causes}); destroyed {stats['destroyed'][s]}")
    if stats["crisis"]:
        turns = [x["turns"] for x in stats["crisis"]]
        trig = Counter(x["trigger"] for x in stats["crisis"])
        print(f"Crisis: {min(turns)}-{max(turns)} turns (mean {sum(turns) / len(turns):.1f}, "
              f"{sum(turns) / len(turns) / 4:.1f} days); war started by {dict(trig)}")
    print(f"OCA packages: NATO {stats['oca'][Side.BLUE]}, Warsaw Pact {stats['oca'][Side.RED]}")
    print("Outcomes: " + ", ".join(f"{k} {v}" for k, v in outcomes.most_common()))
    print("By task (sorties, loss rate):")
    for (s, task), (n, l) in sorted(stats["task"].items(), key=lambda kv: (kv[0][0].value, kv[0][1])):
        print(f"  {s.value:4} {task:<13} {n:>5}  {l / max(n, 1):.1%}")


if __name__ == "__main__":
    main()
