"""Guard rails for the step 9 balance (see tools/balance.py for the full report)."""
from collections import Counter, defaultdict

from cwdc.model import Side
from tools import balance


def test_first_week_stays_in_the_tuned_band():
    stats = {"cause": Counter(), "task": defaultdict(lambda: [0, 0]), "sorties": Counter(),
             "lost_air": Counter(), "destroyed": Counter()}
    runs = [balance.run(seed, 7, stats) for seed in (2, 3, 4, 5)]     # 4 seeds: 2 were too noisy
    for side in Side:
        rate = stats["lost_air"][side] / stats["sorties"][side]
        assert 0.01 <= rate <= 0.06, f"{side.value} loses {rate:.1%} per sortie"
    for r in runs:
        day3 = r["fronts"][3]
        assert max(day3.values()) < 60          # the front moves, but does not collapse in hours
        assert min(day3.values()) >= -10        # NATO restores the border, it does not invade
